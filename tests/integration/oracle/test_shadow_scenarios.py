from __future__ import annotations

import json
from pathlib import Path

import pytest

from .harness import CHECKER, Harness, ReviewRun
from .runs import STEPS

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIXED_CHECKER = "def checker(a, b):\n    if a is None or b is None:\n        return False\n    return a == b\n"
SHIP = {"decision": "SHIP", "summary": "The change keeps checker semantics; no defects.", "files_examined": [CHECKER]}


@pytest.fixture(autouse=True)
def shadowed(harness: Harness) -> None:
    harness.default_mode = STEPS
    harness.default_shadow = True


def _result(run: ReviewRun) -> dict:
    return json.loads((run.temp / "oracle-shadow" / "result.json").read_text(encoding="utf-8"))


def _open(harness: Harness, pr: int) -> str:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(pr, "feat/checker")
    harness.script_omp([SHIP])
    return head


def test_the_shadow_never_starts_a_reviewer_and_posts_exactly_what_the_posting_steps_posted(harness: Harness) -> None:
    head = _open(harness, 41)

    run = harness.review(41)

    assert _result(run) == {"differences": {}, "equal": True}
    assert harness.omp_calls() == 1
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]
    assert len(harness.github.reviews[41]) == 1 and not harness.github.comments[41]
    assert len(harness.github.dispatches) == 1
    uploaded = [item["name"] for item in harness.github.artifacts if item["workflow_run"]["id"] == run.run["id"]]
    assert "oracle-verdict-shadow-41" in uploaded


def test_a_shadow_that_disagrees_with_the_posted_status_is_recorded_not_hidden(harness: Harness) -> None:
    _open(harness, 42)

    def tamper(shadow: Path) -> None:
        path = shadow / "final" / "status.json"
        status = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**status, "state": "failure"}), encoding="utf-8")

    run = harness.review(42, before_compare=tamper)

    result = _result(run)
    assert result["equal"] is False
    assert sorted(result["differences"]) == ["final/status.json", "publish"]
    assert result["differences"]["final/status.json"]["shadow"]["state"] == "failure"
    assert result["differences"]["final/status.json"]["posted"]["state"] == "success"
    assert "differ from the posted outputs in final/status.json, publish" in run.step_summary()


def test_a_shadow_that_planned_something_else_is_recorded(harness: Harness) -> None:
    _open(harness, 43)

    def tamper(shadow: Path) -> None:
        path = shadow / "plan" / "plan.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**plan, "max_attempts": 1}), encoding="utf-8")

    run = harness.review(43, before_compare=tamper)

    assert list(_result(run)["differences"]) == ["plan"]


def test_a_plan_that_posts_nothing_is_still_compared(harness: Harness) -> None:
    harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(44, "feat/checker", draft=True)

    run = harness.review(44)

    assert run.plan["decision"] == "skip"
    assert _result(run) == {"differences": {}, "equal": True}
    assert harness.github.oracle_statuses(run.plan["head_sha"]) == []
