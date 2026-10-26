from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

RUN_TITLE_PULL = re.compile(r"\bpr=(\d+)\)$")
COMPLETED = "completed"
FAILED = frozenset({"failure", "timed_out", "cancelled"})
RUN_WINDOW = timedelta(days=7)
DISPATCH_GRACE = timedelta(minutes=2)
DISPATCH_WINDOW = timedelta(hours=24)
DISPATCH_EVENT = "workflow_dispatch"


@dataclass(frozen=True)
class Run:
    id: int
    pr: int
    event: str
    status: str
    conclusion: str | None
    created_at: datetime


def _run(item: Mapping[str, Any]) -> Run | None:
    match = RUN_TITLE_PULL.search(item.get("display_title") or "")
    if match is None:
        return None
    try:
        return Run(
            int(item["id"]),
            int(match.group(1)),
            str(item.get("event", "")),
            str(item.get("status", "")),
            item.get("conclusion"),
            datetime.fromisoformat(str(item["created_at"])),
        )
    except (KeyError, TypeError, ValueError):
        return None


def parse_runs(items: Iterable[Mapping[str, Any]]) -> dict[int, tuple[Run, ...]]:
    grouped: dict[int, list[Run]] = defaultdict(list)
    for item in items:
        run = _run(item)
        if run is not None:
            grouped[run.pr].append(run)
    return {pr: tuple(sorted(runs, key=lambda run: run.created_at, reverse=True)) for pr, runs in grouped.items()}


def in_flight(runs_by_pr: Mapping[int, tuple[Run, ...]], now: datetime) -> frozenset[int]:
    return frozenset(
        pr
        for pr, runs in runs_by_pr.items()
        if any(run.status != COMPLETED or now - run.created_at < DISPATCH_GRACE for run in runs)
    )


def stateless_failures(runs: Iterable[Run], head_committed_at: datetime, exclude: int | None = None) -> int:
    count = 0
    for run in runs:
        if run.created_at < head_committed_at:
            break
        if run.id == exclude or run.status != COMPLETED:
            continue
        if run.conclusion not in FAILED:
            break
        count += 1
    return count


def recent_dispatches(runs: Iterable[Run], now: datetime) -> int:
    return sum(1 for run in runs if run.event == DISPATCH_EVENT and now - run.created_at < DISPATCH_WINDOW)


def newest_failed(runs: tuple[Run, ...]) -> bool:
    finished = next((run for run in runs if run.status == COMPLETED), None)
    return finished is not None and finished.conclusion in FAILED
