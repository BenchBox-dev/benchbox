from __future__ import annotations

from benchbox._vendor.sqlglot import exp

ORDER = 120


def transform(expression: exp.Expr) -> exp.Expr:
    if isinstance(expression, exp.Create) and str(expression.args.get("kind") or "").upper() == "TABLE":
        body = expression.args.get("expression")
        if isinstance(body, exp.Query) and not isinstance(body, exp.Subquery):
            expression.set("expression", exp.Subquery(this=body))
    return expression
