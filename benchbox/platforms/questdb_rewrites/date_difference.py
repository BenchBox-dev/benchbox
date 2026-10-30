from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 50

_SECONDS_PER_DAY = "86400"


def applies(sql: str) -> bool:
    upper = sql.upper()
    return "CURRENT_DATE" in upper or "EPOCH" in upper


def _timestamp_operand(node: exp.Expr) -> exp.Expr:
    if isinstance(node, exp.Cast) and node.to.is_type(exp.DType.TIMESTAMP):
        node = node.this
    return node.transform(_rewrite)


def _day_difference(node: exp.Expr) -> exp.Expr | None:
    if not isinstance(node, exp.Div) or not isinstance(node.expression, exp.Literal):
        return None
    if node.expression.is_string or node.expression.name != _SECONDS_PER_DAY:
        return None
    extract = node.this
    if not isinstance(extract, exp.Extract) or extract.this.name.upper() != "EPOCH":
        return None
    difference = extract.expression
    if not isinstance(difference, exp.Sub):
        return None
    return exp.Anonymous(
        this="datediff",
        expressions=[
            exp.Literal.string("d"),
            _timestamp_operand(difference.expression),
            _timestamp_operand(difference.this),
        ],
    )


def _epoch_of_difference(node: exp.Expr) -> exp.Expr | None:
    if not isinstance(node, exp.Extract) or node.this.name.upper() != "EPOCH":
        return None
    difference = node.expression
    while isinstance(difference, exp.Paren):
        difference = difference.this
    if not isinstance(difference, exp.Sub):
        return None
    return exp.Anonymous(
        this="datediff",
        expressions=[
            exp.Literal.string("s"),
            _timestamp_operand(difference.expression),
            _timestamp_operand(difference.this),
        ],
    )


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
