from __future__ import annotations

from benchbox._vendor.sqlglot import exp

ORDER = 74


def _plain_double(node: exp.Expr) -> exp.Expr:
    if isinstance(node, (exp.Cast, exp.TryCast)):
        target = node.args["to"]
        if target.this == exp.DataType.Type.DOUBLE and not target.expressions:
            node.set("to", exp.DataType(this=exp.DataType.Type.USERDEFINED, kind="DOUBLE"))
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_plain_double)
