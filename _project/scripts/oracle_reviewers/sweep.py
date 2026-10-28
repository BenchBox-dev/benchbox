from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from .github import RUN_TITLE_PULL, SWEEP_FAILURE_SURFACE_PREFIX

COMPLETED = "completed"
FAILED = frozenset({"failure", "timed_out"})
NEUTRAL = frozenset({"skipped", "cancelled"})
NOT_STARTED = frozenset({"queued", "waiting", "pending", "requested"})
RUN_WINDOW = timedelta(hours=48)
DISPATCH_GRACE = timedelta(minutes=2)
DISPATCH_WINDOW = timedelta(hours=24)
DISPATCH_EVENT = "workflow_dispatch"
STATELESS_RETRY_REASON = "stateless-retry"
STATUS_DESCRIPTION_LIMIT = 140


@dataclass(frozen=True)
class Run:
    id: int
    pr: int
    event: str
    status: str
    conclusion: str | None
    created_at: datetime
    head: str | None


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
            match.group(2),
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


def with_timeouts(
    runs: Iterable[Run], timed_out: Callable[[int], bool], since: datetime | None = None
) -> tuple[Run, ...]:
    resolved: list[Run] = []
    settled = False
    for run in runs:
        if (
            not settled
            and run.conclusion == "cancelled"
            and (since is None or run.created_at >= since)
            and timed_out(run.id)
        ):
            run = replace(run, conclusion="timed_out")
        settled = settled or run.conclusion == "success"
        resolved.append(run)
    return tuple(resolved)


def for_head(runs: Iterable[Run], head: str) -> tuple[Run, ...]:
    return tuple(run for run in runs if run.head == head)


def stateless_failures(runs: Iterable[Run], since: datetime | None = None) -> int:
    count = 0
    for run in runs:
        if since is not None and run.created_at < since:
            break
        if run.status != COMPLETED or run.conclusion in NEUTRAL:
            continue
        if run.conclusion not in FAILED:
            break
        count += 1
    return count


def recent_dispatches(runs: Iterable[Run], now: datetime) -> int:
    return sum(1 for run in runs if run.event == DISPATCH_EVENT and now - run.created_at < DISPATCH_WINDOW)


def surface_description(steps: Iterable[str]) -> str:
    named = "; ".join(steps) if steps else "an earlier step"
    description = f"{SWEEP_FAILURE_SURFACE_PREFIX}{named}. Rerun with /oracle-review or push a fix"
    if len(description) > STATUS_DESCRIPTION_LIMIT:
        description = description[: STATUS_DESCRIPTION_LIMIT - 1] + "…"
    return description


def reset_spent(runs: Iterable[Run], reset_at: datetime, head: str, exclude: int | None = None) -> bool:
    return any(
        run.id != exclude
        and run.created_at >= reset_at
        and run.head in (None, head)
        and run.status not in NOT_STARTED
        and run.conclusion not in NEUTRAL
        for run in runs
    )


def newest_failed(runs: tuple[Run, ...]) -> bool:
    finished = next((run for run in runs if run.status == COMPLETED and run.conclusion not in NEUTRAL), None)
    return finished is not None and finished.conclusion in FAILED
