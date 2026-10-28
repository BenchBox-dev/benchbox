from __future__ import annotations

import json
from typing import Any

import pytest

from .harness import CHECKER, REPO_ROOT, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIXED_CHECKER = "def checker(a, b):\n    if a is None or b is None:\n        return False\n    return a == b\n"
ANSWERS = REPO_ROOT / "tests" / "fixtures" / "oracle_reviewers" / "jev_recovery_answers.json"
RAW_REPLY = "RAW-REPLY-SENTINEL looks fine to me, nothing to report"
INVALID_REPLY = {"kind": "text", "text": RAW_REPLY}
SHIP = {"decision": "SHIP", "summary": "The change keeps checker semantics; no defects.", "files_examined": [CHECKER]}


def _recorded(case: str) -> dict[str, Any]:
    cases = json.loads(ANSWERS.read_text(encoding="utf-8"))["cases"]
    return next(item["answer"] for item in cases if item["case"] == case)


def _open(harness: Harness, pr: int) -> str:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(pr, "feat/checker")
    return head


def _reviewers(run: Any) -> list[str]:
    return [item["reviewer"] for item in run.attempts()]


def test_a_stop_answer_ends_the_run_pending_without_another_reviewer(harness: Harness) -> None:
    head = _open(harness, 61)
    harness.script_omp([INVALID_REPLY, SHIP])
    harness.jev.script([_recorded("3453 contract, medium attempt 1, two earlier on small")])

    run = harness.review(61)

    assert harness.omp_calls() == 1
    assert _reviewers(run) == ["medium"]
    recorded = run.attempts()[0]["recovery"]
    assert (recorded["choice"], recorded["source"], recorded["probability"]) == ("stop", "jev", 1.0)
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["pending"]
    status = json.loads((run.temp / "oracle-final" / "status.json").read_text(encoding="utf-8"))
    assert "stopped: the process is blocked (the reviewer output is not JSON" in status["description"]
    assert run.steps["resume-1"].outputs["attempts"] == "1"


def test_the_jev_request_carries_no_raw_reply_text(harness: Harness) -> None:
    _open(harness, 62)
    harness.script_omp([INVALID_REPLY, SHIP])
    harness.jev.script([_recorded("3453 contract, medium attempt 1, two earlier on small")])

    run = harness.review(62)

    [request] = harness.jev.requests
    assert "RAW-REPLY-SENTINEL" not in json.dumps(request)
    assert "RAW-REPLY-SENTINEL" in json.dumps([item["diagnostic"] for item in run.attempts()])
    assert set(request["questions"]) == {"recovery"}
    assert set(request["questions"]["recovery"]["criteria"]) == {"retry", "stop", "escalate"}


def test_an_escalate_answer_moves_up_to_the_next_reviewer(harness: Harness) -> None:
    head = _open(harness, 63)
    harness.script_omp([INVALID_REPLY, SHIP])
    harness.jev.script([_recorded("format: prose instead of JSON, small attempt 2")])

    run = harness.review(63)

    assert harness.omp_calls() == 2
    assert len(set(_reviewers(run))) == 2 and _reviewers(run)[0] == "medium"
    assert run.attempts()[0]["recovery"]["choice"] == "escalate"
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]


def test_a_retry_answer_runs_the_same_reviewer_again(harness: Harness) -> None:
    head = _open(harness, 64)
    harness.script_omp([INVALID_REPLY, SHIP])
    harness.jev.script([_recorded("format: prose instead of JSON, small attempt 1")])

    run = harness.review(64)

    assert harness.omp_calls() == 2
    assert _reviewers(run) == ["medium", "medium"]
    assert run.attempts()[0]["recovery"]["choice"] == "retry"
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]


def test_an_unavailable_jev_retries_once_then_stops_and_never_escalates(harness: Harness) -> None:
    head = _open(harness, 65)
    harness.script_omp([INVALID_REPLY, INVALID_REPLY, SHIP])
    harness.jev.script([])

    run = harness.review(65)

    assert harness.omp_calls() == 2
    assert _reviewers(run) == ["medium", "medium"]
    first, second = (item["recovery"] for item in run.attempts())
    assert (first["choice"], first["source"], first["reason"]) == ("retry", "fallback", "unavailable")
    assert (second["choice"], second["source"]) == ("stop", "fallback")
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["pending"]
