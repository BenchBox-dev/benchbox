from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

ORDER = 30

_HAVING_KEYWORD = re.compile(r"\bhaving\b", re.IGNORECASE)


def applies(sql: str) -> bool:
    return _HAVING_KEYWORD.search(sql) is not None


_INNER_ARGS = ("from_", "joins", "laterals", "where", "group")
_OUTER_ARGS = ("with_", "distinct", "order", "limit", "offset")
_UNSUPPORTED_ARGS = ("qualify", "windows", "into", "pivots", "connect", "match", "sample", "prewhere", "locks")


def _is_aggregate(node: exp.Expr) -> bool:
    if isinstance(node, exp.Filter):
        return isinstance(node.this, exp.AggFunc)
    if not isinstance(node, exp.AggFunc):
        return False
    parent = node.parent
    if isinstance(parent, exp.Filter) and parent.this is node:
        return False
    return not (isinstance(parent, exp.Window) and parent.this is node)


def _column_parts(node: exp.Expr) -> tuple[str, str]:
    return node.table.lower(), node.name.lower()


class _Extractor:
    def __init__(self, groups: list[exp.Expr], aliases: dict[str, exp.Expr]) -> None:
        self._groups = groups
        self._aliases = aliases
        self.hidden: dict[object, tuple[str, exp.Expr]] = {}

    def _group_index(self, node: exp.Expr) -> int | None:
        if isinstance(node, exp.Literal):
            return None
        if not isinstance(node, exp.Column):
            return next((index for index, group in enumerate(self._groups) if group == node), None)
        table, name = _column_parts(node)
        columns = [
            (index, _column_parts(group)) for index, group in enumerate(self._groups) if isinstance(group, exp.Column)
        ]
        exact = [index for index, parts in columns if parts == (table, name)]
        if exact:
            return exact[0]
        loose = [index for index, parts in columns if parts[1] == name and (not table or not parts[0])]
        return loose[0] if len(loose) == 1 else None

    def _reference(self, key: object, node: exp.Expr, prefix: str) -> exp.Column:
        entry = self.hidden.get(key)
        if entry is None:
            entry = (f"{prefix}_{len(self.hidden) + 1}", node.copy())
            self.hidden[key] = entry
        return exp.column(entry[0])

    def replace(self, node: exp.Expr, expand_aliases: bool = False) -> exp.Expr:
        if isinstance(node, (exp.Subquery, exp.Query)):
            return node
        if (
            expand_aliases
            and isinstance(node, exp.Column)
            and not node.table
            and node.name.lower() in self._aliases
            and self._group_index(node) is None
        ):
            return self.replace(self._aliases[node.name.lower()].copy())
        if _is_aggregate(node):
            return self._reference(("agg", node), node, "qdb_agg")
        index = self._group_index(node)
        if index is not None:
            return self._reference(("group", index), self._groups[index], "qdb_grp")
        for name, child in list(node.args.items()):
            if isinstance(child, exp.Expr):
                node.set(name, self.replace(child, expand_aliases))
            elif isinstance(child, list):
                node.set(
                    name, [self.replace(item, expand_aliases) if isinstance(item, exp.Expr) else item for item in child]
                )
        return node


def _resolve_groups(select: exp.Select) -> list[exp.Expr] | None:
    projections = select.expressions
    aliases = {
        projection.alias.lower(): projection.this for projection in projections if isinstance(projection, exp.Alias)
    }
    resolved: list[exp.Expr] = []
    group = select.args.get("group")
    for item in group.expressions if group else []:
        if isinstance(item, exp.Literal) and not item.is_string:
            index = int(item.name)
            if not 1 <= index <= len(projections):
                return None
            target = projections[index - 1]
            resolved.append((target.this if isinstance(target, exp.Alias) else target).copy())
        elif isinstance(item, exp.Column) and not item.table and item.name.lower() in aliases:
            target = aliases[item.name.lower()]
            if isinstance(target, exp.Column) and target.name.lower() == item.name.lower():
                resolved.append(item.copy())
            else:
                resolved.append(target.copy())
        elif isinstance(item, (exp.Rollup, exp.Cube, exp.GroupingSets)):
            return None
        else:
            resolved.append(item.copy())
    return resolved


def _always_one_row(select: exp.Select) -> bool:
    if select.args.get("group") is not None or select.args.get("having") is not None:
        return False
    if select.args.get("limit") is not None or select.args.get("offset") is not None:
        return False
    projection = select.expressions[0]
    return any(not isinstance(agg.parent, exp.Window) for agg in projection.find_all(exp.AggFunc))


def _hoist_scalar_subqueries(condition: exp.Expr) -> tuple[exp.Expr, list[exp.Join]]:
    joins: list[exp.Join] = []
    holder = exp.Paren(this=condition)

    def visit(node: exp.Expr) -> None:
        for child in list(node.iter_expressions()):
            if (
                isinstance(child, exp.Subquery)
                and isinstance(child.this, exp.Select)
                and len(child.this.expressions) == 1
                and _always_one_row(child.this)
                and not isinstance(node, (exp.In, exp.Exists, exp.Any, exp.All))
            ):
                index = len(joins) + 1
                value = f"qdb_sub_{index}"
                source = child.this.copy()
                projection = source.expressions[0]
                source.set(
                    "expressions",
                    [exp.alias_(projection.this if isinstance(projection, exp.Alias) else projection, value)],
                )
                table = f"qdb_scalar_{index}"
                joins.append(
                    exp.Join(
                        this=exp.Subquery(this=source, alias=exp.TableAlias(this=exp.to_identifier(table))),
                        kind="CROSS",
                    )
                )
                child.replace(exp.column(value, table=table))
            elif not isinstance(child, (exp.Subquery, exp.Query)):
                visit(child)

    visit(holder)
    return holder.this, joins


def _output_alias(original: exp.Expr, replaced: exp.Expr) -> exp.Expr:
    if isinstance(original, exp.Alias):
        return replaced
    name = original.output_name
    if not name and isinstance(original, exp.Anonymous):
        name = original.name
    elif not name and isinstance(original, exp.TimestampTrunc):
        name = "date_trunc"
    elif not name and isinstance(original, exp.Func):
        name = original.sql_name().lower()
    if not name:
        return replaced
    return exp.alias_(replaced, name)


def _rewrite_select(select: exp.Select) -> exp.Select | None:
    if any(select.args.get(arg) for arg in _UNSUPPORTED_ARGS):
        return None
    if any(isinstance(projection, exp.Star) or projection.is_star for projection in select.expressions):
        return None
    groups = _resolve_groups(select)
    if groups is None:
        return None
    aliases = {
        projection.alias.lower(): projection.this
        for projection in select.expressions
        if isinstance(projection, exp.Alias)
    }
    extractor = _Extractor(groups, aliases)
    having = select.args["having"].this.copy()

    projections = [_output_alias(projection, extractor.replace(projection.copy())) for projection in select.expressions]
    where, scalar_joins = _hoist_scalar_subqueries(extractor.replace(having, expand_aliases=True))

    order = select.args.get("order")
    new_order = None
    if order is not None:
        new_order = order.copy()
        for ordered in new_order.expressions:
            target = ordered.this
            if isinstance(target, exp.Literal) and not target.is_string:
                continue
            if isinstance(target, exp.Column) and not target.table and target.name.lower() in aliases:
                continue
            ordered.set("this", extractor.replace(target))

    inner = exp.Select(
        expressions=[exp.alias_(node, name) for name, node in extractor.hidden.values()],
    )
    for arg in _INNER_ARGS:
        value = select.args.get(arg)
        if value is not None:
            inner.set(arg, value.copy())
    group_clause = select.args.get("group")
    if group_clause is not None:
        inner.set("group", exp.Group(expressions=[group.copy() for group in groups]))
    if not inner.expressions:
        inner.set("expressions", [exp.alias_(exp.Count(this=exp.Star()), "qdb_agg_0")])

    outer = exp.Select(expressions=projections)
    outer.set(
        "from_", exp.From(this=exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier("qdb_having"))))
    )
    if scalar_joins:
        outer.set("joins", scalar_joins)
    outer.set("where", exp.Where(this=where))
    for arg in _OUTER_ARGS:
        value = select.args.get(arg)
        if value is not None:
            outer.set(arg, new_order if arg == "order" else value.copy())
    return outer


def transform(expression: exp.Expr) -> exp.Expr:
    for select in reversed(list(expression.find_all(exp.Select))):
        if select.args.get("having") is None:
            continue
        rewritten = _rewrite_select(select)
        if rewritten is None:
            continue
        if select is expression:
            expression = rewritten
        else:
            select.replace(rewritten)
    return expression
