from __future__ import annotations

from bisect import bisect_right
from datetime import date
from typing import Any, Final

RELEASE_DATES: Final[tuple[tuple[date, str], ...]] = (
    (date(2026, 1, 10), "0.1.0"),
    (date(2026, 1, 24), "0.1.1"),
    (date(2026, 2, 9), "0.1.2"),
    (date(2026, 2, 23), "0.1.3"),
    (date(2026, 3, 3), "0.1.4"),
    (date(2026, 3, 10), "0.1.5"),
    (date(2026, 4, 1), "0.2.0"),
    (date(2026, 4, 26), "0.2.1"),
    (date(2026, 5, 16), "0.3.0"),
    (date(2026, 7, 9), "0.3.1"),
    (date(2026, 8, 27), "0.4.0"),
    (date(2026, 9, 24), "0.4.1"),
    (date(2026, 10, 5), "0.4.2"),
    (date(2026, 11, 4), "0.5.0"),
    (date(2026, 11, 11), "0.5.1"),
)
_DATES: Final = [released for released, _ in RELEASE_DATES]

VERSION_BASIS_RECORDED: Final = "recorded"
VERSION_BASIS_INFERRED: Final = "inferred_from_run_date"


def release_on_or_before(run_date: date) -> str | None:
    index = bisect_right(_DATES, run_date)
    return RELEASE_DATES[index - 1][1] if index else None


def resolve_benchbox_version(recorded: Any, run_timestamp: Any) -> tuple[str | None, str | None]:
    if isinstance(recorded, str) and recorded.strip():
        return recorded.strip(), VERSION_BASIS_RECORDED
    if isinstance(run_timestamp, str) and len(run_timestamp) >= 10:
        try:
            inferred = release_on_or_before(date.fromisoformat(run_timestamp[:10]))
        except ValueError:
            return None, None
        if inferred:
            return inferred, VERSION_BASIS_INFERRED
    return None, None
