from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 303

_DECIMAL_TYPES = (
    exp.DataType.Type.DECIMAL,
    exp.DataType.Type.DECIMAL32,
    exp.DataType.Type.DECIMAL64,
    exp.DataType.Type.DECIMAL128,
    exp.DataType.Type.DECIMAL256,
)
_COMPARISONS = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)
_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div)


def _is_decimal(node: exp.Expr) -> bool:
    if isinstance(node, exp.Paren):
        return _is_decimal(node.this)
    if isinstance(node, (exp.Cast, exp.TryCast)):
        return node.args["to"].this in _DECIMAL_TYPES
    if isinstance(node, _ARITHMETIC):
        return _is_decimal(node.this) and _is_decimal(node.expression)
    return False


def _fractional_literal(node: exp.Expr) -> exp.Literal | None:
    if isinstance(node, exp.Neg):
        return _fractional_literal(node.this)
    if isinstance(node, exp.Literal) and not node.is_string and "." in node.this and not node.this.endswith("m"):
        return node
    return None


def _to_decimal_literal(node: exp.Expr) -> exp.Expr:
    if isinstance(node, exp.Neg):
        return exp.Neg(this=_to_decimal_literal(node.this))
    return exp.Literal.number(f"{node.this}m")


def _rewrite(node: exp.Expr) -> exp.Expr:
    if not isinstance(node, _COMPARISONS):
        return node
    left, right = node.this, node.expression
    if _is_decimal(left) and _fractional_literal(right) is not None:
        node.set("expression", _to_decimal_literal(right))
    elif _is_decimal(right) and _fractional_literal(left) is not None:
        node.set("this", _to_decimal_literal(left))
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_rewrite)
