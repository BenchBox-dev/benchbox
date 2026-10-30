from __future__ import annotations

from benchbox._vendor.sqlglot import exp

ORDER = 10


def _lower_unit(node: exp.TimestampTrunc) -> exp.Expr | None:
    unit = node.args.get("unit")
    if node.args.get("zone") is not None or not isinstance(unit, (exp.Var, exp.Literal)):
        return None
    return exp.Anonymous(this="date_trunc", expressions=[exp.Literal.string(unit.name.lower()), node.this])


def transform(expression: exp.Expr) -> exp.Expr:
    for node in reversed(list(expression.find_all(exp.TimestampTrunc))):
        replacement = _lower_unit(node)
        if replacement is None:
            continue
        if node is expression:
            return replacement
        node.replace(replacement)
    return expression
