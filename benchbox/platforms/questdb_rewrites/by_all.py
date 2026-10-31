from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

ORDER = 360

_ALL_KEYWORD = re.compile(r"\bALL\b", re.IGNORECASE)


def applies(sql: str) -> bool:
    return _ALL_KEYWORD.search(sql) is not None


def _is_aggregate(projection: exp.Expr) -> bool:
    return projection.find(exp.AggFunc) is not None or projection.find(exp.Window) is not None


def _is_all_reference(node: exp.Expr) -> bool:
    if isinstance(node, exp.Var):
        return node.name.upper() == "ALL"
    return isinstance(node, exp.Column) and not node.table and node.name.upper() == "ALL"


def _all_references(order: exp.Order) -> list[exp.Expr]:
    return [node for node in order.walk() if _is_all_reference(node)]


def _key(projection: exp.Expr) -> exp.Expr:
    if isinstance(projection, exp.Alias):
        return exp.column(projection.alias)
    return projection.copy()


def _value(projection: exp.Expr) -> exp.Expr:
    return (projection.this if isinstance(projection, exp.Alias) else projection).copy()


def _expand_group(select: exp.Select) -> None:
    group = select.args.get("group")
    if not isinstance(group, exp.Group) or not group.args.get("all"):
        return
    positions = [
        exp.Literal.number(index)
        for index, projection in enumerate(select.expressions, start=1)
        if not _is_aggregate(projection)
    ]
    if not positions:
        return
    group.set("all", None)
    group.set("expressions", positions)


def _expand_order(select: exp.Select) -> None:
    order = select.args.get("order")
    if not isinstance(order, exp.Order) or not _all_references(order):
        return
    items = []
    for projection in select.expressions:
        for template in order.expressions:
            item = template.copy()
            for reference in _all_references(item):
                reference.replace(_key(projection) if reference is item.this else _value(projection))
            items.append(item)
    order.set("expressions", items)


def transform(expression: exp.Expr) -> exp.Expr:
    for select in expression.find_all(exp.Select):
        _expand_group(select)
        _expand_order(select)
    return expression
