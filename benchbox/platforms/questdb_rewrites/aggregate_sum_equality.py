from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 240

TOLERANCE = "0.0001"


def applies(sql: str) -> bool:
    upper = sql.upper()
    return "SUM(" in upper.replace(" ", "") and upper.count("SELECT") > 2


def _is_scalar_subquery(node: exp.Expr) -> bool:
    return isinstance(node, exp.Subquery) and isinstance(node.this, exp.Select) and len(node.this.expressions) == 1


def _sums_floats(node: exp.Expr) -> bool:
    return node.find(exp.Sum) is not None


def _rewrite(node: exp.Expr) -> exp.Expr:
    if not isinstance(node, exp.EQ):
        return node
    left, right = node.this, node.expression
    if not (_is_scalar_subquery(left) and _is_scalar_subquery(right)):
        return node
    if not (_sums_floats(left) and _sums_floats(right)):
        return node
    difference = exp.Abs(this=exp.Sub(this=left.copy(), expression=right.copy()))
    return exp.Paren(this=exp.LTE(this=difference, expression=exp.Literal.number(TOLERANCE)))


def transform(expression: exp.Expr) -> exp.Expr:
    for node in reversed(list(expression.find_all(exp.EQ))):
        replacement = _rewrite(node)
        if replacement is node:
            continue
        if node is expression:
            return replacement
        node.replace(replacement)
    return expression
