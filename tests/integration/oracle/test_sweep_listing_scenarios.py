from __future__ import annotations

import pytest

from .harness import Harness
from .test_sweep_scenarios import VERDICT, _pending_review

pytestmark = [pytest.mark.integration, pytest.mark.medium]

LISTING = r"/actions/workflows/oracle-verdict\.yml/runs\?.*created="
ACTIVE = r"/actions/workflows/oracle-verdict\.yml/runs\?.*status="


def test_a_failing_runs_listing_still_dispatches_a_due_retry_and_fails_the_sweep(harness: Harness) -> None:
    head, run = _pending_review(harness, 65, "feat/listing")
    harness.github.age(run, 180)
    harness.github.fail("GET", LISTING, 502, 100)

    sweep = harness.sweep()

    assert sweep.code == 1
    assert harness.github.dispatches[-1:] == [
        {"workflow": VERDICT, "ref": "develop", "inputs": {"pr": "65", "head": head}}
    ]


def test_a_failing_listing_and_active_run_lookup_dispatches_nothing_and_fails_the_sweep(harness: Harness) -> None:
    _, run = _pending_review(harness, 66, "feat/blind")
    harness.github.age(run, 180)
    harness.github.fail("GET", LISTING, 502, 100)
    harness.github.fail("GET", ACTIVE, 502, 100)
    before = list(harness.github.dispatches)

    sweep = harness.sweep()

    assert sweep.code == 1
    assert harness.github.dispatches == before
