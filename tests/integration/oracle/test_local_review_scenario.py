from __future__ import annotations

import json

import pytest

from .harness import CHECKER, GATEWAY_TOKEN, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

HIGH = {
    "severity": "High",
    "file": CHECKER,
    "line": 2,
    "title": "Equality ignores the operand types",
    "detail": "checker treats 1 and True as equal, so a boolean column matches an integer column.",
}


def test_a_clean_local_review_proceeds_and_keeps_a_redacted_capture(harness: Harness) -> None:
    harness.branch("feat/local", {CHECKER: "def checker(a, b):\n    return a == b and a is not None\n"}, "reject None")
    harness.script_omp(
        [{"decision": "SHIP", "summary": "No defects.", "files_examined": [CHECKER]}], "OMP_GATEWAY_TOKEN"
    )

    review = harness.local_review("feat/local")

    assert review.code == 0, review.stdout + review.stderr
    assert "Local oracle review: no Critical or High findings; pr-open may proceed." in review.stdout
    [capture] = harness.local_captures()
    assert GATEWAY_TOKEN not in json.dumps(capture) and harness.github.url not in json.dumps(capture)
    assert capture["exit_code"] == 0 and capture["absence"] is None
    assert "[redacted]" in capture["stderr_excerpt"]


def test_a_local_review_with_a_high_finding_refuses_the_pull_request(harness: Harness) -> None:
    harness.branch("feat/local", {CHECKER: "def checker(a, b):\n    return a == b and a is not None\n"}, "reject None")
    harness.script_omp(
        [{"decision": "SHIP_WITH_FIXES", "summary": "One defect.", "files_examined": [CHECKER], "defects": [HIGH]}]
    )

    review = harness.local_review("feat/local")

    assert review.code == 1, review.stdout + review.stderr
    assert f"[High] {CHECKER}:2 Equality ignores the operand types" in review.stdout
    assert "Refusing to open the PR" in review.stderr
