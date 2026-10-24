#!/usr/bin/env python3

from __future__ import annotations

import datetime as dt
from typing import Any

import check_suites

REQUIRED_CHECK_NAMES: tuple[str, ...] = (
    "core",
    "explorer",
    "results-data",
    "docs",
    "tooling",
)


def _parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _started_at_key(run: dict[str, Any]) -> dt.datetime | None:
    raw = run.get("started_at")
    if not raw:
        return None
    try:
        return _parse_iso(str(raw))
    except (ValueError, TypeError):
        return None


def latest_check_run(check_runs: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    matches = [run for run in check_runs if run.get("name") == name]
    if not matches:
        return None

    def _key(run: dict[str, Any]) -> tuple[int, dt.datetime]:
        parsed = _started_at_key(run)
        if parsed is None:
            return (0, dt.datetime.min.replace(tzinfo=dt.timezone.utc))
        return (1, parsed)

    return max(matches, key=_key)


def is_check_run_success(run: dict[str, Any] | None) -> bool:
    if run is None:
        return False
    return run.get("status") == "completed" and run.get("conclusion") == "success"


def is_required_lane_green(check_runs: list[dict[str, Any]]) -> bool:
    return check_suites.all_green(check_runs, REQUIRED_CHECK_NAMES)
