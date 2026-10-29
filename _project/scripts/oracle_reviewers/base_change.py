from __future__ import annotations

from datetime import datetime


def survives(moment: datetime, base_changed_at: datetime | None) -> bool:
    return base_changed_at is None or moment > base_changed_at
