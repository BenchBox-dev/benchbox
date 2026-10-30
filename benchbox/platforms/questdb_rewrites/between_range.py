from __future__ import annotations

from decimal import Decimal, InvalidOperation

from benchbox.platforms.questdb_rewrites import exp

ORDER = 20


def _exact(node: exp.Expr) -> Decimal | None:
    if isinstance(node, exp.Paren):
        return _exact(node.this)
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return Decimal(node.name)
        except InvalidOperation:
            return None
    if isinstance(node, exp.Neg):
        value = _exact(node.this)
        return None if value is None else -value
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul)):
        left, right = _exact(node.this), _exact(node.expression)
        if left is None or right is None:
            return None
        if isinstance(node, exp.Add):
            return left + right
        if isinstance(node, exp.Sub):
            return left - right
        return left * right
    return None


def _bound(node: exp.Expr) -> exp.Expr:
    if isinstance(node, exp.Literal):
        return node.copy()
    value = _exact(node)
    if value is None:
        return node.copy()
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".") or "0"
    literal = exp.Literal.number(text.lstrip("-"))
    return exp.Neg(this=literal) if value < 0 else literal


def _interval(operand: exp.Expr, low: exp.Expr, high: exp.Expr) -> exp.Paren:
    return exp.Paren(
        this=exp.And(
            this=exp.GTE(this=operand.copy(), expression=low.copy()),
            expression=exp.LTE(this=operand.copy(), expression=high.copy()),
        )
    )


def _range(node: exp.Between) -> exp.Expr:
    low = _bound(node.args["low"])
    high = _bound(node.args["high"])
    forward = _interval(node.this, low, high)
    if node.args.get("symmetric") is True:
        return exp.Paren(this=exp.Or(this=forward, expression=_interval(node.this, high, low)))
    return forward


def transform(expression: exp.Expr) -> exp.Expr:
    for node in reversed(list(expression.find_all(exp.Between))):
        node.replace(_range(node))
    return expression
