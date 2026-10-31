from __future__ import annotations

import re
from itertools import combinations, product

from benchbox.platforms.questdb_rewrites import exp

ORDER = 200

_GROUPING_SYNTAX = re.compile(r"\b(?:rollup|cube|grouping\s+sets)\b", re.IGNORECASE)

_GROUP_PREFIX = "__g"
_FLAG_PREFIX = "__f"
_AGG_PREFIX = "__a"
_ORDER_PREFIX = "__o"
_RESULT_ALIAS = "__staged"
_STAGE_ARGS = ("with_", "having", "qualify", "order", "limit", "offset", "distinct")


def applies(sql: str) -> bool:
    return _GROUPING_SYNTAX.search(sql) is not None


def _members(element: exp.Expr) -> list[exp.Expr]:
    if isinstance(element, exp.Tuple):
        return list(element.expressions)
    if isinstance(element, exp.Paren):
        return [element.this]
    return [element]


def _element_sets(element: exp.Expr) -> list[list[exp.Expr]]:
    if isinstance(element, exp.Rollup):
        parts = [_members(part) for part in element.expressions]
        return [[member for part in parts[:size] for member in part] for size in range(len(parts), -1, -1)]
    if isinstance(element, exp.Cube):
        parts = [_members(part) for part in element.expressions]
        sets = []
        for size in range(len(parts), -1, -1):
            for chosen in combinations(range(len(parts)), size):
                sets.append([member for index in chosen for member in parts[index]])
        return sets
    if isinstance(element, exp.GroupingSets):
        return [grouping_set for part in element.expressions for grouping_set in _grouping_set_parts(part)]
    return [[element]]


def _grouping_set_parts(part: exp.Expr) -> list[list[exp.Expr]]:
    if isinstance(part, (exp.Rollup, exp.Cube, exp.GroupingSets)):
        return _element_sets(part)
    return [_members(part) if not isinstance(part, exp.Tuple) or part.expressions else []]


def _same(left: exp.Expr, right: exp.Expr) -> bool:
    if left == right:
        return True
    if isinstance(left, exp.Column) and isinstance(right, exp.Column) and left.name.lower() == right.name.lower():
        return not left.table or not right.table or left.table.lower() == right.table.lower()
    return False


def _dedupe(items: list[exp.Expr]) -> list[exp.Expr]:
    unique: list[exp.Expr] = []
    for item in items:
        if not any(_same(item, known) for known in unique):
            unique.append(item)
    return unique


def _index(items: list[exp.Expr], target: exp.Expr) -> int:
    for position, item in enumerate(items):
        if _same(item, target):
            return position
    return -1


_GROUP_CONTAINERS = (exp.Group, exp.Rollup, exp.Cube, exp.GroupingSets, exp.Tuple)


def _is_group_element(literal: exp.Expr) -> bool:
    parent = literal.parent
    if isinstance(parent, exp.Paren):
        parent = parent.parent
    return isinstance(parent, _GROUP_CONTAINERS)


def _aliased_expression(select: exp.Select, column: exp.Column) -> exp.Expr | None:
    if column.table:
        return None
    for projection in select.expressions:
        if isinstance(projection, exp.Alias) and projection.alias.lower() == column.name.lower():
            inner = projection.this
            return None if _same(inner, column) else inner
    return None


def resolve_ordinals(select: exp.Select, group: exp.Group) -> exp.Group | None:
    resolved = group.copy()
    for literal in list(resolved.find_all(exp.Literal)):
        if not literal.is_int or not _is_group_element(literal):
            continue
        position = int(literal.name) - 1
        if not 0 <= position < len(select.expressions):
            return None
        projection = select.expressions[position]
        literal.replace((projection.this if isinstance(projection, exp.Alias) else projection).copy())
    for column in list(resolved.find_all(exp.Column)):
        if not _is_group_element(column):
            continue
        aliased = _aliased_expression(select, column)
        if aliased is not None:
            column.replace(aliased.copy())
    return resolved


def has_grouping_syntax(group: exp.Group) -> bool:
    if any(isinstance(element, (exp.Rollup, exp.Cube, exp.GroupingSets)) for element in group.expressions):
        return True
    return any(group.args.get(key) for key in ("rollup", "cube", "grouping_sets", "totals"))


def _grouping_sets(group: exp.Group) -> list[list[exp.Expr]] | None:
    if not any(isinstance(element, (exp.Rollup, exp.Cube, exp.GroupingSets)) for element in group.expressions):
        return None
    if any(group.args.get(key) for key in ("rollup", "cube", "grouping_sets", "totals")):
        return None
    return [
        _dedupe([member for part in parts for member in part])
        for parts in product(*map(_element_sets, group.expressions))
    ]


def _is_grouping_call(node: exp.Expr) -> bool:
    return isinstance(node, (exp.Grouping, exp.GroupingId))


def _grouping_arguments(node: exp.Expr) -> list[exp.Expr]:
    return list(node.expressions)


def _is_group_aggregate(node: exp.Expr) -> bool:
    if _is_grouping_call(node):
        return False
    parent = node.parent
    if isinstance(parent, exp.Window) and parent.this is node:
        return False
    ancestor = parent
    while ancestor is not None and not isinstance(ancestor, exp.Select):
        if isinstance(ancestor, exp.AggFunc) and _is_group_aggregate(ancestor):
            return False
        ancestor = ancestor.parent
    return True


def _own_nodes(root: exp.Expr, select: exp.Select, kind: type | tuple[type, ...]) -> list[exp.Expr]:
    return [node for node in root.find_all(kind) if node.find_ancestor(exp.Select) is select]


def _scope_roots(select: exp.Select) -> list[exp.Expr]:
    roots = list(select.expressions)
    for key in ("having", "qualify", "order"):
        if select.args.get(key) is not None:
            roots.append(select.args[key])
    return roots


def _collect_aggregates(select: exp.Select, roots: list[exp.Expr]) -> list[exp.Expr]:
    aggregates: list[exp.Expr] = []
    for root in roots:
        for node in _own_nodes(root, select, exp.AggFunc):
            if _is_group_aggregate(node) and _index(aggregates, node) < 0:
                aggregates.append(node.copy())
    return aggregates


def _grouping_calls(select: exp.Select, roots: list[exp.Expr]) -> list[exp.Expr]:
    return [node for root in roots for node in _own_nodes(root, select, (exp.Grouping, exp.GroupingId))]


def _build_level(
    select: exp.Select,
    grouping_set: list[exp.Expr],
    columns: list[exp.Expr],
    aggregates: list[exp.Expr],
    uses_grouping: bool,
) -> exp.Select:
    level = select.copy()
    for key in _STAGE_ARGS:
        level.set(key, None)
    present = [_index(grouping_set, column) >= 0 for column in columns]
    projections: list[exp.Expr] = [
        exp.alias_(column.copy() if kept else exp.Null(), f"{_GROUP_PREFIX}{position}")
        for position, (column, kept) in enumerate(zip(columns, present))
    ]
    if uses_grouping:
        projections += [
            exp.alias_(exp.Literal.number(0 if kept else 1), f"{_FLAG_PREFIX}{position}")
            for position, kept in enumerate(present)
        ]
    projections += [
        exp.alias_(aggregate.copy(), f"{_AGG_PREFIX}{position}") for position, aggregate in enumerate(aggregates)
    ]
    level.set("expressions", projections)
    level.set("group", exp.Group(expressions=[member.copy() for member in grouping_set]) if grouping_set else None)
    return level


def _flag_expression(node: exp.Expr, columns: list[exp.Expr]) -> exp.Expr:
    arguments = _grouping_arguments(node)
    terms: list[exp.Expr] = []
    for offset, argument in enumerate(arguments):
        flag = exp.column(f"{_FLAG_PREFIX}{_index(columns, argument)}")
        weight = 2 ** (len(arguments) - 1 - offset)
        terms.append(flag if weight == 1 else exp.Mul(this=flag, expression=exp.Literal.number(weight)))
    total = terms[0]
    for term in terms[1:]:
        total = exp.Add(this=total, expression=term)
    return exp.Paren(this=total) if len(terms) > 1 else total


def _make_lowering(columns: list[exp.Expr], aggregates: list[exp.Expr]):
    def substitute(node: exp.Expr) -> exp.Expr:
        if isinstance(node, exp.AggFunc) and _is_group_aggregate(node):
            return exp.column(f"{_AGG_PREFIX}{_index(aggregates, node)}")
        if _is_grouping_call(node):
            return _flag_expression(node, columns)
        position = _index(columns, node)
        if position >= 0 and not isinstance(node, exp.Literal):
            return exp.column(f"{_GROUP_PREFIX}{position}")
        return node

    def lower(node: exp.Expr) -> exp.Expr:
        if isinstance(node, exp.Select):
            return node
        replacement = substitute(node)
        if replacement is not node:
            return replacement
        for key, child in list(node.args.items()):
            if isinstance(child, list):
                node.set(key, [lower(item) if isinstance(item, exp.Expr) else item for item in child])
            elif isinstance(child, exp.Expr):
                node.set(key, lower(child))
        return node

    return lambda expression: lower(expression.copy())


def _scope_windows(node: exp.Expr):
    if isinstance(node, exp.Select):
        return
    if isinstance(node, exp.Window):
        yield node
    for child in node.args.values():
        for item in child if isinstance(child, list) else [child]:
            if isinstance(item, exp.Expr):
                yield from _scope_windows(item)


def _hoist_window_orderings(roots: list[exp.Expr]) -> list[exp.Expr]:
    hoisted: list[exp.Expr] = []
    for root in roots:
        for window in list(_scope_windows(root)):
            order = window.args.get("order")
            for item in order.expressions if order is not None else []:
                if isinstance(item.this, (exp.Column, exp.Literal)):
                    continue
                position = _index(hoisted, item.this)
                if position < 0:
                    hoisted.append(item.this.copy())
                    position = len(hoisted) - 1
                item.set("this", exp.column(f"{_ORDER_PREFIX}{position}"))
    return hoisted


def _build_outer(select: exp.Select, union: exp.Expr, lower) -> exp.Select:
    projections = []
    for projection in select.expressions:
        lowered = lower(projection.this if isinstance(projection, exp.Alias) else projection)
        if isinstance(projection, (exp.Alias, exp.Column)):
            projections.append(exp.alias_(lowered, projection.alias_or_name))
        else:
            projections.append(exp.alias_(lowered, projection.sql(dialect="questdb"), quoted=True))
    clauses: dict[str, exp.Expr] = {}
    if select.args.get("having") is not None:
        clauses["where"] = exp.Where(this=lower(select.args["having"].this))
    if select.args.get("qualify") is not None:
        clauses["qualify"] = exp.Qualify(this=lower(select.args["qualify"].this))
    if select.args.get("order") is not None:
        clauses["order"] = lower(select.args["order"])
    hoisted = _hoist_window_orderings([*projections, *clauses.values()])
    if hoisted:
        staged = [exp.alias_(exp.column(projection.alias), projection.alias) for projection in union.selects]
        derived = [exp.alias_(expression, f"{_ORDER_PREFIX}{position}") for position, expression in enumerate(hoisted)]
        base = exp.Subquery(this=union, alias=exp.TableAlias(this=exp.to_identifier(f"{_RESULT_ALIAS}_base")))
        union = exp.select(*staged, *derived).from_(base)
    source = exp.Subquery(this=union, alias=exp.TableAlias(this=exp.to_identifier(_RESULT_ALIAS)))
    outer = exp.select(*projections).from_(source)
    if select.args.get("distinct") is not None:
        outer.set("distinct", select.args["distinct"].copy())
    for key, clause in clauses.items():
        outer.set(key, clause)
    for key in ("limit", "offset", "with_"):
        if select.args.get(key) is not None:
            outer.set(key, select.args[key].copy())
    return outer


def stage_aggregation(select: exp.Select, sets: list[list[exp.Expr]]) -> exp.Expr | None:
    if any(isinstance(projection, exp.Star) for projection in select.expressions):
        return None
    columns = _dedupe([member for grouping_set in sets for member in grouping_set])
    roots = _scope_roots(select)
    aggregates = _collect_aggregates(select, roots)
    calls = _grouping_calls(select, roots)
    if not aggregates and any(not grouping_set for grouping_set in sets):
        return None
    if any(_index(columns, argument) < 0 for call in calls for argument in _grouping_arguments(call)):
        return None
    levels = [_build_level(select, grouping_set, columns, aggregates, bool(calls)) for grouping_set in sets]
    union: exp.Expr = levels[0]
    for level in levels[1:]:
        union = exp.Union(this=union, expression=level, distinct=False)
    return _build_outer(select, union, _make_lowering(columns, aggregates))


def _rewrite_select(select: exp.Select) -> exp.Expr | None:
    group = select.args.get("group")
    if group is None:
        return None
    resolved = resolve_ordinals(select, group)
    sets = _grouping_sets(resolved) if resolved is not None else None
    return stage_aggregation(select, sets) if sets is not None else None


def transform(expression: exp.Expr) -> exp.Expr:
    selects = list(expression.find_all(exp.Select))
    for select in reversed(selects):
        replacement = _rewrite_select(select)
        if replacement is None:
            continue
        if select is expression:
            expression = replacement
        else:
            select.replace(replacement)
    return expression
