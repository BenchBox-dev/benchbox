from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 160


def applies(sql: str) -> bool:
    return "FILTER" in sql.upper()


def _guarded(value: exp.Expr, condition: exp.Expr) -> exp.Expr:
    return exp.Case(ifs=[exp.If(this=condition.copy(), true=value.copy())])


def _filtered_aggregate(node: exp.Filter) -> exp.Expr | None:
    aggregate = node.this
    where = node.expression
    if not isinstance(aggregate, exp.AggFunc) or not isinstance(where, exp.Where):
        return None
    condition = where.this
    argument = aggregate.this
    rewritten = aggregate.copy()
    if isinstance(argument, exp.Star):
        rewritten.set("this", _guarded(exp.Literal.number(1), condition))
    elif isinstance(argument, exp.Distinct) and len(argument.expressions) == 1:
        distinct = rewritten.this
        distinct.set("expressions", [_guarded(argument.expressions[0], condition)])
    elif argument is not None and not aggregate.expressions:
        rewritten.set("this", _guarded(argument, condition))
    else:
        return None
    return rewritten


def transform(expression: exp.Expr) -> exp.Expr:
    for node in reversed(list(expression.find_all(exp.Filter))):
        replacement = _filtered_aggregate(node)
        if replacement is None:
            continue
        if node is expression:
            return replacement
        node.replace(replacement)
    return expression
