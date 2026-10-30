from __future__ import annotations

from benchbox._vendor.sqlglot import exp

ORDER = 72

APPROX_PERCENTILE_PRECISION = 5


def _fraction(node: exp.PercentileCont, descending: bool) -> exp.Expr | None:
    fraction = node.this
    if not isinstance(fraction, exp.Literal) or fraction.is_string:
        return None
    if not descending:
        return fraction.copy()
    return exp.Literal.number(f"{1 - float(fraction.name):.10g}")


def _approx_percentile(node: exp.WithinGroup) -> exp.Expr | None:
    percentile = node.this
    order = node.expression
    if not isinstance(percentile, exp.PercentileCont) or not isinstance(order, exp.Order) or not order.expressions:
        return None
    key = order.expressions[-1]
    fraction = _fraction(percentile, bool(key.args.get("desc")))
    if fraction is None:
        return None
    return exp.Anonymous(
        this="approx_percentile",
        expressions=[key.this.copy(), fraction, exp.Literal.number(APPROX_PERCENTILE_PRECISION)],
    )


def transform(expression: exp.Expr) -> exp.Expr:
    for node in list(expression.find_all(exp.WithinGroup)):
        replacement = _approx_percentile(node)
        if replacement is not None:
            node.replace(replacement)
    return expression
