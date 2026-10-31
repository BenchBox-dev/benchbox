from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 50

_SECONDS_PER_DAY = "86400"


def applies(sql: str) -> bool:
    upper = sql.upper()
    return "CURRENT_DATE" in upper or "EPOCH" in upper


_INTEGER_TYPES = (exp.DType.BIGINT, exp.DType.INT, exp.DType.SMALLINT, exp.DType.TINYINT)
_MICROSECONDS_PER_DAY = "86400000000.0"
_MICROSECONDS_PER_SECOND = "1000000.0"


def _timestamp_operand(node: exp.Expr) -> exp.Expr:
    if isinstance(node, exp.Cast) and node.to.is_type(exp.DType.TIMESTAMP):
        node = node.this
    return node.transform(_rewrite)


def _is_point_in_time(node: exp.Expr) -> bool:
    if isinstance(node, exp.Cast) and node.to.is_type(exp.DType.TIMESTAMP, exp.DType.DATE, exp.DType.DATETIME):
        return True
    if node.find(exp.Interval) is not None or node.find(exp.Literal) is not None:
        return False
    return isinstance(node, (exp.Column, exp.Max, exp.Min, exp.CurrentDate, exp.CurrentTimestamp))


def _epoch_difference(node: exp.Expr) -> tuple[exp.Expr, exp.Expr] | None:
    if not isinstance(node, exp.Extract) or node.this.name.upper() != "EPOCH":
        return None
    difference = node.expression
    while isinstance(difference, exp.Paren):
        difference = difference.this
    if not isinstance(difference, exp.Sub):
        return None
    if not (_is_point_in_time(difference.this) and _is_point_in_time(difference.expression)):
        return None
    return difference.this, difference.expression


def _datediff(unit: str, later: exp.Expr, earlier: exp.Expr) -> exp.Expr:
    return exp.Anonymous(
        this="datediff",
        expressions=[exp.Literal.string(unit), _timestamp_operand(earlier), _timestamp_operand(later)],
    )


def _fraction(microseconds: exp.Expr, divisor: str) -> exp.Expr:
    as_double = exp.Cast(this=microseconds, to=exp.DataType.build("DOUBLE"))
    return exp.Paren(this=exp.Div(this=as_double, expression=exp.Literal.number(divisor), typed=True, safe=False))


def _day_difference(node: exp.Expr) -> exp.Expr | None:
    if not isinstance(node, exp.Div) or not isinstance(node.expression, exp.Literal):
        return None
    if node.expression.is_string or node.expression.name != _SECONDS_PER_DAY:
        return None
    operands = _epoch_difference(node.this)
    if operands is None:
        return None
    parent = node.parent
    if isinstance(parent, exp.Cast) and parent.to.is_type(*_INTEGER_TYPES):
        return _datediff("d", *operands)
    return _fraction(_datediff("u", *operands), _MICROSECONDS_PER_DAY)


def _epoch_of_difference(node: exp.Expr) -> exp.Expr | None:
    operands = _epoch_difference(node)
    if operands is None:
        return None
    return _fraction(_datediff("u", *operands), _MICROSECONDS_PER_SECOND)


def _current_date() -> exp.Expr:
    today = exp.Anonymous(
        this="date_trunc", expressions=[exp.Literal.string("day"), exp.Anonymous(this="now", expressions=[])]
    )
    return exp.Cast(this=today, to=exp.DataType.build("DATE"))


def _rewrite(node: exp.Expr) -> exp.Expr:
    replacement = _day_difference(node)
    if replacement is None:
        replacement = _epoch_of_difference(node)
    if replacement is not None:
        return replacement
    if isinstance(node, exp.CurrentDate):
        return _current_date()
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_rewrite)
