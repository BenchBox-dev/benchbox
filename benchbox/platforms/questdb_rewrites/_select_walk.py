from __future__ import annotations

from collections.abc import Callable

from benchbox.platforms.questdb_rewrites import exp


def replace_selects(expression: exp.Expr, rewrite: Callable[[exp.Select], exp.Expr | None]) -> exp.Expr:
    for select in reversed(list(expression.find_all(exp.Select))):
        replacement = rewrite(select)
        if replacement is None:
            continue
        if select is expression:
            expression = replacement
        else:
            select.replace(replacement)
    return expression
