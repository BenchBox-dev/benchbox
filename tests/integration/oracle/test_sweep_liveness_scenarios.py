from __future__ import annotations

import pytest

from _project.scripts.oracle_reviewers.github import SWEEP_FAILURE_SURFACE_PREFIX

from .fake_github import ACCOUNTS, APP_TOKEN, now_text
from .harness import SLUG, Harness, ago
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


def test_a_runs_listing_capped_at_every_window_fails_the_sweep_and_holds_the_due_retry(harness: Harness) -> None:
    head, run = _pending_review(harness, 75, "feat/capped")
    harness.github.age(run, 600)
    for minutes in (10, 20, 30):
        harness.github.add_run(VERDICT, event="schedule", pr=None, head_sha=head, created_at=ago(minutes))
    harness.github.run_listing_cap = 1

    sweep = harness.sweep()

    assert sweep.code == 1
    assert _verdict_dispatches(harness) == []
    assert harness.github.oracle_statuses(head)[-1]["state"] == "pending"


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


def _shared_head(harness: Harness, first: int, second: int, branch: str) -> str:
    head = harness.branch(branch, {"benchbox/core/equivalence/checker.py": "x = 1\n"}, "change")
    harness.github.open_pull(first, branch)
    harness.github.open_pull(second, branch)
    return head


def _app_status(harness: Harness, head: str, state: str, description: str, run: dict) -> None:
    harness.github.statuses[head].append(
        {
            "id": run["id"] + 5,
            "state": state,
            "context": "oracle-verdict",
            "description": description,
            "target_url": f"https://github.com/{SLUG}/actions/runs/{run['id']}",
            "creator": ACCOUNTS[APP_TOKEN],
            "created_at": now_text(),
            "updated_at": now_text(),
        }
    )


def test_another_pull_requests_decisive_status_on_a_shared_head_does_not_suppress_this_pull_requests_recovery(
    harness: Harness,
) -> None:
    head = _shared_head(harness, 81, 82, "feat/shared-status")
    published = harness.github.add_run(VERDICT, event="pull_request_target", pr=81, head_sha=head, title_head=head)
    harness.github.complete_run(published, "success")
    harness.github.age(published, 120)
    _app_status(harness, head, "success", "oracle-verdict: SHIP", published)
    _failed_run(harness, 82, head, "pull_request_target", 40, "Run the attempt loop")

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert [item["inputs"] for item in _verdict_dispatches(harness)] == [
        {"pr": "82", "head": head, "reason": "stateless-retry"}
    ]


def test_another_pull_requests_surfaced_hold_on_a_shared_head_does_not_suppress_this_pull_requests_recovery(
    harness: Harness,
) -> None:
    head = _shared_head(harness, 83, 84, "feat/shared-hold")
    failed = _failed_run(harness, 83, head, "pull_request_target", 90, "Run the attempt loop")
    hold = f"{SWEEP_FAILURE_SURFACE_PREFIX}Run the attempt loop. Rerun with /oracle-review or push a fix"
    _app_status(harness, head, "pending", hold, failed)
    _failed_run(harness, 84, head, "pull_request_target", 40, "Run the attempt loop")

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert 84 in [int(item["inputs"]["pr"]) for item in _verdict_dispatches(harness)]


def test_this_pull_requests_own_surfaced_hold_from_a_run_older_than_the_listing_still_stops_the_retry(
    harness: Harness,
) -> None:
    head = harness.branch("feat/own-hold", {"benchbox/core/equivalence/checker.py": "x = 1\n"}, "change")
    harness.github.open_pull(85, "feat/own-hold")
    old = _failed_run(harness, 85, head, "pull_request_target", 60 * 72, "Run the attempt loop")
    hold = f"{SWEEP_FAILURE_SURFACE_PREFIX}Run the attempt loop. Rerun with /oracle-review or push a fix"
    _app_status(harness, head, "pending", hold, old)
    _failed_run(harness, 85, head, "pull_request_target", 40, "Run the attempt loop")

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert _verdict_dispatches(harness) == []


def _retarget(harness: Harness, pr: int, minutes: int) -> None:
    harness.github.timeline[pr].append({"event": "base_ref_changed", "created_at": ago(minutes)})


def _aged_app_status(harness: Harness, head: str, state: str, description: str, run: dict, minutes: int) -> None:
    _app_status(harness, head, state, description, run)
    harness.github.statuses[head][-1]["created_at"] = ago(minutes)
    harness.github.statuses[head][-1]["updated_at"] = ago(minutes)


def test_a_decisive_status_from_before_a_base_retarget_does_not_suppress_recovery_of_the_fresh_review(
    harness: Harness,
) -> None:
    head = harness.branch("feat/retarget", {"benchbox/core/equivalence/checker.py": "x = 1\n"}, "change")
    harness.github.open_pull(86, "feat/retarget")
    published = harness.github.add_run(VERDICT, event="pull_request_target", pr=86, head_sha=head, title_head=head)
    harness.github.complete_run(published, "success")
    harness.github.age(published, 180)
    _aged_app_status(harness, head, "success", "oracle-verdict: SHIP", published, 180)
    _retarget(harness, 86, 120)
    _failed_run(harness, 86, head, "pull_request_target", 40, "Run the attempt loop")

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert [item["inputs"] for item in _verdict_dispatches(harness)] == [
        {"pr": "86", "head": head, "reason": "stateless-retry"}
    ]


def test_a_decisive_status_from_after_a_base_retarget_still_suppresses_recovery(harness: Harness) -> None:
    head = harness.branch("feat/retarget-kept", {"benchbox/core/equivalence/checker.py": "x = 1\n"}, "change")
    harness.github.open_pull(87, "feat/retarget-kept")
    _retarget(harness, 87, 180)
    published = harness.github.add_run(VERDICT, event="pull_request_target", pr=87, head_sha=head, title_head=head)
    harness.github.complete_run(published, "success")
    _aged_app_status(harness, head, "success", "oracle-verdict: SHIP", published, 120)
    _failed_run(harness, 87, head, "pull_request_target", 40, "Run the attempt loop")

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert _verdict_dispatches(harness) == []


def test_a_surfaced_hold_from_before_a_base_retarget_does_not_suppress_recovery_of_the_fresh_review(
    harness: Harness,
) -> None:
    head = harness.branch("feat/retarget-hold", {"benchbox/core/equivalence/checker.py": "x = 1\n"}, "change")
    harness.github.open_pull(88, "feat/retarget-hold")
    old = harness.github.add_run(VERDICT, event="pull_request_target", pr=88, head_sha=head, title_head=head)
    harness.github.complete_run(old, "success")
    harness.github.age(old, 200)
    hold = f"{SWEEP_FAILURE_SURFACE_PREFIX}Run the attempt loop. Rerun with /oracle-review or push a fix"
    _aged_app_status(harness, head, "pending", hold, old, 180)
    _retarget(harness, 88, 120)
    _failed_run(harness, 88, head, "pull_request_target", 40, "Run the attempt loop")

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert [item["inputs"] for item in _verdict_dispatches(harness)] == [
        {"pr": "88", "head": head, "reason": "stateless-retry"}
    ]
