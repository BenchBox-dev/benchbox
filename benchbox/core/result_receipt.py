from __future__ import annotations

from typing import Any

ARROW_TABLE_METHODS = ("to_arrow_table", "fetch_arrow_table")


def _count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _reported_count(cursor: Any) -> int | None:
    platform_result = getattr(cursor, "platform_result", None)
    if isinstance(platform_result, dict):
        return _count(platform_result.get("rows_returned"))
    return None


def _arrow_count(cursor: Any) -> int | None:
    for name in ARROW_TABLE_METHODS:
        method = getattr(cursor, name, None)
        if callable(method):
            return _count(getattr(method(), "num_rows", None))
    return None


def cursor_result_count(cursor: Any) -> int:
    reported = _reported_count(cursor)
    if reported is not None:
        return reported
    counter = getattr(cursor, "row_count", None)
    if callable(counter):
        counted = _count(counter())
        if counted is not None:
            return counted
    for known in (getattr(cursor, "num_rows", None), getattr(cursor, "rowcount", None)):
        counted = _count(known)
        if counted is not None:
            return counted
    arrow_counted = _arrow_count(cursor)
    if arrow_counted is not None:
        return arrow_counted
    if hasattr(cursor, "fetchall"):
        return len(cursor.fetchall())
    return 0
