from __future__ import annotations

from benchbox._vendor.sqlglot import exp

ORDER = 10


def _lower_unit(node: exp.Expr) -> exp.Expr:
    if isinstance(node, exp.TimestampTrunc) and node.args.get("unit") is not None:
        unit = node.args["unit"].name.lower()
        return exp.Anonymous(this="date_trunc", expressions=[exp.Literal.string(unit), node.this])
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_lower_unit)
