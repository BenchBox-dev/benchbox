from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

ORDER = 361

_RANGE_FRAME = re.compile(r"\brange\b", re.IGNORECASE)
_INTERVAL = re.compile(r"\binterval\b", re.IGNORECASE)
_AMOUNT_UNIT = re.compile(r"^\s*(\d+)\s+([A-Za-z]+)\s*$")


def applies(sql: str) -> bool:
    return _RANGE_FRAME.search(sql) is not None and _INTERVAL.search(sql) is not None


def _amount_and_unit(interval: exp.Interval) -> tuple[str, str] | None:
    value = interval.this
    unit = interval.args.get("unit")
    if not isinstance(value, exp.Literal) or not value.is_string:
        return None
    if unit is not None:
        return (value.name, unit.name) if value.name.isdigit() else None
    match = _AMOUNT_UNIT.match(value.name)
    return (match.group(1), match.group(2)) if match else None


def _frame_bound(bound: exp.Expr | None) -> exp.Expr | None:
    if not isinstance(bound, exp.Interval):
        return None
    parts = _amount_and_unit(bound)
    if parts is None:
        return None
    amount, unit = parts
    return exp.Var(this=f"'{amount}' {unit.lower()}")


def transform(expression: exp.Expr) -> exp.Expr:
    for spec in expression.find_all(exp.WindowSpec):
        if spec.args.get("kind") != "RANGE":
            continue
        for side in ("start", "end"):
            replacement = _frame_bound(spec.args.get(side))
            if replacement is not None:
                spec.set(side, replacement)
    return expression
