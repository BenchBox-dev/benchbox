from __future__ import annotations

from benchbox._vendor.sqlglot import exp

from .grouping_sets import _own_nodes, _scope_roots, stage_aggregation

ORDER = 210


def _group_columns(select: exp.Select, group: exp.Group) -> list[exp.Expr] | None:
    columns: list[exp.Expr] = []
    for element in group.expressions:
        if isinstance(element, exp.Literal) and element.is_int:
            position = int(element.name) - 1
            if not 0 <= position < len(select.expressions):
                return None
            projection = select.expressions[position]
            element = projection.this if isinstance(projection, exp.Alias) else projection
        columns.append(element.copy())
    return columns


def _rewrite_select(select: exp.Select) -> exp.Expr | None:
    group = select.args.get("group")
    if group is None or not group.expressions:
        return None
    if not any(_own_nodes(root, select, exp.Window) for root in _scope_roots(select)):
        return None
    columns = _group_columns(select, group)
    return stage_aggregation(select, [columns]) if columns is not None else None


def transform(expression: exp.Expr) -> exp.Expr:
    for select in reversed(list(expression.find_all(exp.Select))):
        replacement = _rewrite_select(select)
        if replacement is None:
            continue
        if select is expression:
            expression = replacement
        else:
            select.replace(replacement)
    return expression
