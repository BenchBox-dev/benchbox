from __future__ import annotations

import datetime
import math
import re
from decimal import Decimal

_MIDNIGHT = re.compile(r"^(\d{4}-\d\d-\d\d)[ T]00:00:00(?:\.0+)?$")
LOOSE = {"rel_tol": 1e-6, "abs_tol": 0.0101}
STRICT = {"rel_tol": 1e-9, "abs_tol": 1e-9}


def _cell(value, strict: bool):
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, datetime.datetime):
        text = value.isoformat(sep=" ")
    elif isinstance(value, datetime.date):
        text = value.isoformat()
    elif isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode("utf-8", "replace")
    else:
        text = str(value)
    if strict:
        return text
    text = text.rstrip(" ")
    match = _MIDNIGHT.match(text)
    return match.group(1) if match else text


def _sort_key(row: tuple) -> tuple:
    key = []
    for cell in row:
        if cell is None:
            key.append(("0", ""))
        elif isinstance(cell, float):
            key.append(("1", round(cell, 2) if math.isfinite(cell) else str(cell)))
        else:
            key.append(("2", cell))
    return tuple(key)


def _normalised(rows, strict: bool) -> list[tuple]:
    return sorted((tuple(_cell(cell, strict) for cell in row) for row in rows), key=_sort_key)


def _same_cell(left, right, tolerance: dict[str, float]) -> bool:
    if isinstance(left, float) and isinstance(right, float):
        return (math.isnan(left) and math.isnan(right)) or math.isclose(left, right, **tolerance)
    return left == right


def first_difference(left, right, strict: bool = False) -> str | None:
    tolerance = STRICT if strict else LOOSE
    left_rows, right_rows = _normalised(left, strict), _normalised(right, strict)
    if len(left_rows) != len(right_rows):
        return f"row count {len(left_rows)} != {len(right_rows)}"
    for index, (left_row, right_row) in enumerate(zip(left_rows, right_rows, strict=True)):
        if len(left_row) != len(right_row) or not all(
            _same_cell(a, b, tolerance) for a, b in zip(left_row, right_row, strict=True)
        ):
            return f"sorted row {index}: {left_row!r} != {right_row!r}"
    return None
