from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

ORDER = 230

_INEQUALITY = re.compile(r"<>|!=")


def applies(sql: str) -> bool:
    return _INEQUALITY.search(sql) is not None


_TRANSPARENT = (exp.And, exp.Or, exp.Paren)


def _in_filter_context(node: exp.Expr) -> bool:
    child = node
    parent = node.parent
    while isinstance(parent, _TRANSPARENT):
        child, parent = parent, parent.parent
    if isinstance(parent, (exp.Where, exp.Having, exp.Qualify)):
        return True
    if isinstance(parent, exp.Join):
        return parent.args.get("on") is child
    return isinstance(parent, exp.If) and parent.this is child


def _guard(operand: exp.Expr) -> exp.Expr | None:
    if isinstance(operand, (exp.Literal, exp.Boolean, exp.Subquery, exp.Select, exp.Any, exp.All)):
        return None
    return exp.Not(this=exp.Is(this=operand.copy(), expression=exp.Null()))


def _rewrite(node: exp.Expr) -> exp.Expr:
    if not isinstance(node, exp.NEQ) or not _in_filter_context(node):
        return node
    guards = [guard for guard in (_guard(node.this), _guard(node.expression)) if guard is not None]
    if not guards:
        return node
    condition: exp.Expr = node.copy()
    for guard in guards:
        condition = exp.And(this=condition, expression=guard)
    return exp.Paren(this=condition)


def transform(expression: exp.Expr) -> exp.Expr:
    for node in reversed(list(expression.find_all(exp.NEQ))):
        replacement = _rewrite(node)
        if replacement is node:
            continue
        if node is expression:
            return replacement
        node.replace(replacement)
    return expression
