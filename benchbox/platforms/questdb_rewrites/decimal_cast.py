from __future__ import annotations

from benchbox._vendor.sqlglot import exp

ORDER = 110

_DECIMAL_TYPES = (
    exp.DataType.Type.DECIMAL,
    exp.DataType.Type.DECIMAL32,
    exp.DataType.Type.DECIMAL64,
    exp.DataType.Type.DECIMAL128,
    exp.DataType.Type.DECIMAL256,
)


def _decimal_to_double(node: exp.Expr) -> exp.Expr:
    if isinstance(node, (exp.Cast, exp.TryCast)) and node.args["to"].this in _DECIMAL_TYPES:
        node.set("to", exp.DataType(this=exp.DataType.Type.USERDEFINED, kind="DOUBLE"))
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_decimal_to_double)
