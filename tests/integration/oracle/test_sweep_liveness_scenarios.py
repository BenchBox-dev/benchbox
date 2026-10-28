from __future__ import annotations

import pytest

from .harness import Harness, ago
from .test_sweep_scenarios import VERDICT, _failed_run, _pending_review

pytestmark = [pytest.mark.integration, pytest.mark.medium]


def _verdict_dispatches(harness: Harness) -> list[dict]:
    return [item for item in harness.github.dispatches if item["workflow"] == VERDICT]


def _failed_dispatch(harness: Harness, pr: int, head: str, minutes: int, step: str) -> dict:
    return _failed_run(harness, pr, head, "workflow_dispatch", minutes, step)


def test_a_due_pending_head_whose_dispatches_keep_failing_is_surfaced_instead_of_redispatched_forever(
    harness: Harness,
) -> None:
    head, run = _pending_review(harness, 71, "feat/forever")
    harness.github.age(run, 600)
    _failed_dispatch(harness, 71, head, 90, "Run the attempt loop")
    last = _failed_dispatch(harness, 71, head, 30, "Run the attempt loop")
    posted = len(harness.github.oracle_statuses(head))

    first = harness.sweep()
    second = harness.sweep()

    assert (first.code, second.code) == (0, 0)
    assert _verdict_dispatches(harness) == []
    surfaced = harness.github.oracle_statuses(head)[posted:]
    assert [(item["state"], item["creator"]["login"]) for item in surfaced] == [("pending", "benchbox-oracle[bot]")]
    assert surfaced[0]["target_url"].endswith(f"/actions/runs/{last['id']}")


def test_a_due_pending_head_with_one_failed_dispatch_is_dispatched_once_more(harness: Harness) -> None:
    head, run = _pending_review(harness, 72, "feat/once-more")
    harness.github.age(run, 600)
    _failed_dispatch(harness, 72, head, 30, "Run the attempt loop")
    posted = len(harness.github.oracle_statuses(head))

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert _verdict_dispatches(harness) == [
        {"workflow": VERDICT, "ref": "develop", "inputs": {"pr": "72", "head": head}}
    ]
    assert harness.github.oracle_statuses(head)[posted:] == []


def test_a_cancelled_run_is_not_counted_as_a_failure_without_a_result(harness: Harness) -> None:
    head = harness.branch("feat/cancelled", {"benchbox/core/equivalence/checker.py": "x = 1\n"}, "change")
    harness.github.open_pull(73, "feat/cancelled")
    _failed_run(harness, 73, head, "pull_request_target", 60, "Run the attempt loop")
    cancelled = harness.github.add_run(VERDICT, event="workflow_dispatch", pr=73, head_sha=head, title_head=head)
    harness.github.complete_run(cancelled, "cancelled")
    harness.github.age(cancelled, 30)

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert _verdict_dispatches(harness) == [
        {"workflow": VERDICT, "ref": "develop", "inputs": {"pr": "73", "head": head, "reason": "stateless-retry"}}
    ]
    assert harness.github.oracle_statuses(head) == []


def test_a_failure_recorded_for_another_head_does_not_redispatch_the_current_head(harness: Harness) -> None:
    head = harness.branch("feat/attributed", {"benchbox/core/equivalence/checker.py": "x = 1\n"}, "change")
    harness.github.open_pull(74, "feat/attributed")
    _failed_run(harness, 74, "f" * 40, "pull_request_target", 40, "Run the attempt loop")

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert _verdict_dispatches(harness) == []
    assert harness.github.oracle_statuses(head) == []


def test_a_capped_runs_listing_fails_the_sweep_and_dispatches_only_the_due_retry(harness: Harness) -> None:
    head, run = _pending_review(harness, 75, "feat/capped")
    harness.github.age(run, 600)
    for minutes in (10, 20, 30):
        harness.github.add_run(VERDICT, event="schedule", pr=None, head_sha=head, created_at=ago(minutes))
    harness.github.run_listing_cap = 1

    sweep = harness.sweep()

    assert sweep.code == 1
    assert _verdict_dispatches(harness) == [
        {"workflow": VERDICT, "ref": "develop", "inputs": {"pr": "75", "head": head}}
    ]


def test_a_marked_retry_the_plan_refuses_fails_its_run(harness: Harness) -> None:
    _pending_review(harness, 76, "feat/refused")

    run = harness.review(76, event_name="workflow_dispatch", inputs={"reason": "stateless-retry"})

    assert run.steps["plan"].code == 1
    assert run.steps["plan"].outputs["decision"] == "skip"


def test_a_dispatch_for_a_superseded_head_reviews_nothing_and_succeeds(harness: Harness) -> None:
    _, pending = _pending_review(harness, 77, "feat/superseded")
    harness.github.age(pending, 600)

    run = harness.review(77, event_name="workflow_dispatch", inputs={"head": "e" * 40})

    assert run.steps["plan"].code == 0
    assert run.steps["plan"].outputs["decision"] == "skip"


TIMEOUT = "The job has exceeded the maximum execution time of 255m0s"
CONCURRENCY = "Canceling since a higher priority waiting request for oracle-verdict-pr-78 exists"


def _cancelled(harness: Harness, pr: int, head: str, minutes: int, annotation: str) -> dict:
    run = harness.github.add_run(VERDICT, event="workflow_dispatch", pr=pr, head_sha=head, title_head=head)
    harness.github.complete_run(run, "cancelled", job_annotation=annotation)
    harness.github.age(run, minutes)
    return run


def test_a_run_cancelled_by_the_job_timeout_counts_as_a_failure_and_is_surfaced(harness: Harness) -> None:
    head = harness.branch("feat/timeout", {"benchbox/core/equivalence/checker.py": "x = 1\n"}, "change")
    harness.github.open_pull(78, "feat/timeout")
    _failed_run(harness, 78, head, "pull_request_target", 90, "Run the attempt loop")
    _cancelled(harness, 78, head, 30, TIMEOUT)

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert _verdict_dispatches(harness) == []
    surfaced = harness.github.oracle_statuses(head)
    assert [item["state"] for item in surfaced] == ["pending"]


def test_a_run_cancelled_by_concurrency_is_not_a_failure(harness: Harness) -> None:
    head = harness.branch("feat/superseded-run", {"benchbox/core/equivalence/checker.py": "x = 1\n"}, "change")
    harness.github.open_pull(79, "feat/superseded-run")
    _failed_run(harness, 79, head, "pull_request_target", 90, "Run the attempt loop")
    _cancelled(harness, 79, head, 30, CONCURRENCY)

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert [item["inputs"].get("reason") for item in _verdict_dispatches(harness)] == ["stateless-retry"]
    assert harness.github.oracle_statuses(head) == []
