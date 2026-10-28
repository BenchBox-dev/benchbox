from __future__ import annotations

import pytest

from .harness import Harness
from .test_sweep_scenarios import VERDICT, _failed_run, _pending_review

pytestmark = [pytest.mark.integration, pytest.mark.medium]

LISTING = r"/actions/workflows/oracle-verdict\.yml/runs\?.*created="
ACTIVE = r"/actions/workflows/oracle-verdict\.yml/runs\?.*status="


def test_a_failing_runs_listing_still_dispatches_a_due_retry_and_fails_the_sweep(harness: Harness) -> None:
    head, run = _pending_review(harness, 65, "feat/listing")
    harness.github.age(run, 180)
    harness.github.fail("GET", LISTING, 502, 2)

    sweep = harness.sweep()

    assert sweep.code == 1
    assert harness.github.dispatches[-1:] == [
        {"workflow": VERDICT, "ref": "develop", "inputs": {"pr": "65", "head": head}}
    ]


def test_a_failing_listing_holds_a_due_retry_whose_run_history_cannot_be_listed(harness: Harness) -> None:
    _, run = _pending_review(harness, 67, "feat/no-history")
    harness.github.age(run, 180)
    harness.github.fail("GET", LISTING, 502, 100)
    before = list(harness.github.dispatches)

    sweep = harness.sweep()

    assert sweep.code == 1
    assert harness.github.dispatches == before


def test_a_degraded_sweep_keeps_the_two_failure_stop_on_a_due_head(harness: Harness) -> None:
    head, run = _pending_review(harness, 68, "feat/degraded-hold")
    harness.github.age(run, 600)
    _failed_run(harness, 68, head, "workflow_dispatch", 90, "Run the attempt loop")
    last = _failed_run(harness, 68, head, "workflow_dispatch", 30, "Run the attempt loop")
    posted = len(harness.github.oracle_statuses(head))
    before = list(harness.github.dispatches)
    harness.github.fail("GET", LISTING, 502, 2)

    sweep = harness.sweep()

    assert sweep.code == 1
    assert harness.github.dispatches == before
    surfaced = harness.github.oracle_statuses(head)[posted:]
    assert [item["state"] for item in surfaced] == ["pending"]
    assert surfaced[0]["target_url"].endswith(f"/actions/runs/{last['id']}")


def test_a_failing_listing_and_active_run_lookup_dispatches_nothing_and_fails_the_sweep(harness: Harness) -> None:
    _, run = _pending_review(harness, 66, "feat/blind")
    harness.github.age(run, 180)
    harness.github.fail("GET", LISTING, 502, 100)
    harness.github.fail("GET", ACTIVE, 502, 100)
    before = list(harness.github.dispatches)

    sweep = harness.sweep()

    assert sweep.code == 1
    assert harness.github.dispatches == before


def test_a_truncated_changed_file_listing_holds_the_head_for_owner_review(harness: Harness) -> None:
    harness.branch("feat/truncated", {"NOTES.md": "A note.\n"}, "add a note")
    harness.github.reported_changed_files[90] = 3001
    harness.github.open_pull(90, "feat/truncated")

    run = harness.review(90)

    assert run.plan["decision_reason"] == "the changed-file listing is incomplete (1 of 3001 files): owner review"
    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["pending"]
    assert harness.omp_calls() == 0
