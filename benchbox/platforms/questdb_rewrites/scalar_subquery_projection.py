from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 90


def applies(sql: str) -> bool:
    return sql.upper().count("SELECT") > 1


def _own_aliases(select: exp.Select) -> set[str]:
    aliases: set[str] = set()
    for source in select.find_all(exp.Table, exp.Subquery):
        if source.alias_or_name:
            aliases.add(source.alias_or_name.lower())
    for table in select.find_all(exp.Table):
        aliases.add(table.name.lower())
    return aliases


def _is_correlated(subquery: exp.Subquery) -> bool:
    inner = subquery.this
    own = _own_aliases(inner)
    return any(column.table and column.table.lower() not in own for column in inner.find_all(exp.Column))


def _is_plain_aggregate(node: exp.Expr) -> bool:
    return node.find_ancestor(exp.Window) is None


def _returns_one_row(inner: exp.Select) -> bool:
    if inner.args.get("group") or inner.args.get("having") or inner.args.get("limit") or inner.args.get("offset"):
        return False
    if inner.args.get("from_") is None:
        return True
    return any(_is_plain_aggregate(node) for node in inner.expressions[0].find_all(exp.AggFunc))


def _is_scalar_position(subquery: exp.Subquery, select: exp.Select) -> bool:
    if not isinstance(subquery.this, exp.Select) or len(subquery.this.expressions) != 1:
        return False
    if not _returns_one_row(subquery.this):
        return False
    node = subquery
    while node.parent is not None and node.parent is not select:
        parent = node.parent
        if isinstance(parent, (exp.Exists, exp.In, exp.Subquery, exp.Select)):
            return False
        node = parent
    return node.parent is select and node.arg_key == "expressions"


def _projection_subqueries(select: exp.Select) -> list[exp.Subquery]:
    found: list[exp.Subquery] = []
    for item in select.expressions:
        for subquery in item.find_all(exp.Subquery):
            if _is_scalar_position(subquery, select) and not _is_correlated(subquery):
                found.append(subquery)
    return found


def _has_aggregate_scope(select: exp.Select, subquery: exp.Subquery) -> tuple[bool, bool]:
    inside_aggregate = subquery.find_ancestor(exp.AggFunc) is not None
    outside = [
        node
        for item in select.expressions
        for node in item.find_all(exp.AggFunc)
        if node.find_ancestor(exp.Subquery) is None and _is_plain_aggregate(node)
    ]
    return inside_aggregate, bool(outside) or select.args.get("group") is not None


def _rewrite_select(select: exp.Select, counter: list[int]) -> None:
    for subquery in _projection_subqueries(select):
        counter[0] += 1
        name = f"_qs{counter[0]}"
        inner = subquery.this.copy()
        inner.set("expressions", [exp.alias_(inner.expressions[0], "v")])
        source = exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(name)))
        reference = exp.column("v", table=name)
        inside_aggregate, aggregate_scope = _has_aggregate_scope(select, subquery)
        replacement = exp.Max(this=reference) if aggregate_scope and not inside_aggregate else reference
        subquery.replace(replacement)
        if select.args.get("from_") is None:
            select.set("from_", exp.From(this=source))
        else:
            joins = list(select.args.get("joins") or [])
            joins.append(exp.Join(this=source, kind="CROSS"))
            select.set("joins", joins)


def transform(expression: exp.Expr) -> exp.Expr:
    counter = [0]
    for select in reversed(list(expression.find_all(exp.Select))):
        _rewrite_select(select, counter)
    return expression
