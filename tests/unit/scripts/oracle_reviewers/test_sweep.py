from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from _project.scripts.oracle_reviewers import sweep
from _project.scripts.oracle_reviewers.github import SWEEP_FAILURE_SURFACE_PREFIX

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _run(
    run_id: int,
    age: timedelta,
    *,
    status: str = sweep.COMPLETED,
    conclusion: str | None = "failure",
    event: str = sweep.DISPATCH_EVENT,
) -> sweep.Run:
    return sweep.Run(run_id, 7, event, status, conclusion, NOW - age, None)


def test_a_completed_run_exactly_one_grace_period_old_is_no_longer_in_flight() -> None:
    runs = {7: (_run(1, sweep.DISPATCH_GRACE),), 8: (_run(2, sweep.DISPATCH_GRACE - timedelta(seconds=1)),)}
    assert sweep.in_flight(runs, NOW) == frozenset({8})


def test_a_failure_created_at_the_head_commit_time_counts() -> None:
    committed = NOW - timedelta(hours=1)
    runs = [_run(2, timedelta(minutes=30)), _run(1, timedelta(hours=1)), _run(0, timedelta(hours=2))]
    assert sweep.stateless_failures(runs, committed) == 2


def test_recent_dispatches_count_each_dispatch_inside_the_window_once() -> None:
    runs = [
        _run(1, timedelta(hours=1)),
        _run(2, timedelta(hours=2)),
        _run(3, timedelta(hours=3), event="pull_request_target"),
        _run(4, sweep.DISPATCH_WINDOW),
        _run(5, sweep.DISPATCH_WINDOW + timedelta(hours=1)),
    ]
    assert sweep.recent_dispatches(runs, NOW) == 2


def test_a_run_created_at_the_reset_moment_spends_the_reset() -> None:
    assert sweep.reset_spent([_run(1, timedelta(hours=1))], NOW - timedelta(hours=1), "abc") is True
    assert sweep.reset_spent([_run(1, timedelta(hours=1))], NOW - timedelta(minutes=59), "abc") is False


def test_the_surfaced_description_names_every_failed_step_or_an_earlier_step() -> None:
    assert sweep.surface_description(["Scan the disk", "Redact"]).startswith(
        f"{SWEEP_FAILURE_SURFACE_PREFIX}Scan the disk; Redact. "
    )
    assert sweep.surface_description([]).startswith(f"{SWEEP_FAILURE_SURFACE_PREFIX}an earlier step. ")


def test_the_surfaced_description_is_kept_at_the_limit_and_clipped_beyond_it() -> None:
    bare = sweep.surface_description([""])
    suffix = bare.removeprefix(SWEEP_FAILURE_SURFACE_PREFIX)
    room = sweep.STATUS_DESCRIPTION_LIMIT - len(bare)
    assert sweep.surface_description(["x" * room]) == f"{SWEEP_FAILURE_SURFACE_PREFIX}{'x' * room}{suffix}"
    clipped = sweep.surface_description(["x" * (room + 1)])
    full = f"{SWEEP_FAILURE_SURFACE_PREFIX}{'x' * (room + 1)}{suffix}"
    assert clipped == full[: sweep.STATUS_DESCRIPTION_LIMIT - 1] + "…"
