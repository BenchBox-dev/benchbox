from __future__ import annotations

import pytest

from .harness import CHECKER, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

PATCH_A = "def checker(a, b):\n    return a == b and a is not None\n"
PATCH_B = "def checker(a, b):\n    return a == b or a is b\n"
SHIP = {"decision": "SHIP", "summary": "No defects.", "files_examined": [CHECKER]}
CACHED = "the recorded review stands"


def test_local_reviews_of_different_patches_run_at_the_same_time(harness: Harness) -> None:
    harness.branch("feat/first", {CHECKER: PATCH_A}, "reject None")
    harness.branch("feat/second", {CHECKER: PATCH_B}, "short-circuit on identity")
    harness.script_omp([{**SHIP, "rendezvous": 2}])

    first = harness.start_local_review("feat/first")
    second = harness.start_local_review("feat/second")
    results = [harness.finish_local_review(first), harness.finish_local_review(second)]

    assert [item.code for item in results] == [0, 0], [item.stderr for item in results]
    assert [call["met"] for call in harness.omp_log()] == [True, True]
    assert len(harness.local_captures()) == 2


def test_two_local_reviews_of_the_same_patch_review_once_and_share_the_result(harness: Harness) -> None:
    harness.branch("feat/original", {CHECKER: PATCH_A}, "reject None")
    harness.branch("feat/duplicate", {CHECKER: PATCH_A}, "reject None again")
    harness.script_omp([SHIP])

    first = harness.start_local_review("feat/original")
    second = harness.start_local_review("feat/duplicate")
    results = [harness.finish_local_review(first), harness.finish_local_review(second)]

    assert [item.code for item in results] == [0, 0], [item.stderr for item in results]
    assert harness.omp_calls() == 1
    assert sum(CACHED in item.stdout + item.stderr for item in results) == 1
    assert len(harness.local_captures()) == 1
