from __future__ import annotations

import re

from benchbox._vendor.sqlglot import exp

ORDER = 10

_UPPERCASE_UNIT = re.compile(r"(?i:date_trunc)\s*\(\s*'[^']*[A-Z]")


def applies(sql: str) -> bool:
    return _UPPERCASE_UNIT.search(sql) is not None


def _lowered(node: exp.TimestampTrunc) -> exp.Expr | None:
    unit = node.args.get("unit")
    if node.args.get("zone") is not None or not isinstance(unit, (exp.Var, exp.Literal)):
        return None
    return exp.Anonymous(this="date_trunc", expressions=[exp.Literal.string(unit.name.lower()), node.this])


def transform(expression: exp.Expr) -> exp.Expr:
    for node in reversed(list(expression.find_all(exp.TimestampTrunc))):
        replacement = _lowered(node)
        if replacement is None:
            continue
        if node is expression:
            return replacement
        node.replace(replacement)
    return expression


finalize = transform
