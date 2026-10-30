from __future__ import annotations

from benchbox._vendor.sqlglot import exp

ORDER = 20


def _range(node: exp.Between) -> exp.Paren:
    operand = node.this
    return exp.Paren(
        this=exp.And(
            this=exp.GTE(this=operand.copy(), expression=node.args["low"].copy()),
            expression=exp.LTE(this=operand.copy(), expression=node.args["high"].copy()),
        )
    )


def transform(expression: exp.Expr) -> exp.Expr:
    for node in reversed(list(expression.find_all(exp.Between))):
        node.replace(_range(node))
    return expression
