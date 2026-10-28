from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import attempts, cli, recovery, selection
from _project.scripts.oracle_reviewers.absence import Absence
from _project.scripts.oracle_reviewers.jev import JevError, JevSession
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.recovery import ESCALATE, FALLBACK, JEV, RETRY, STOP, Recovery
from _project.scripts.oracle_reviewers.selection import ABSENT, PENDING, REVIEW, Attempt, SelectionInput, next_step

from .test_attempts import HEAD, RUN_ID, _plan, _v, _write
from .test_selection import _input, _reviewer

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
ANSWERS = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "oracle_reviewers" / "jev_recovery_answers.json"
CASES = json.loads(ANSWERS.read_text(encoding="utf-8"))["cases"]
RAW = "RAW-REPLY-SENTINEL-" + "k3Jd9sLq" * 3
JEV_STOP = Recovery(STOP, JEV, STOP, 0.95, 0.9)
JEV_ESCALATE = Recovery(ESCALATE, JEV, ESCALATE, 0.9, 0.8)
JEV_RETRY = Recovery(RETRY, JEV, RETRY, 0.9, 0.8)
MESSAGE = "disputed finding D1 has no concedes boolean, so the verdict is invalid"


def _invalid(slot: int, reviewer: str, held: Recovery | None, detail: str = MESSAGE) -> Attempt:
    return Attempt(slot, reviewer, ABSENT, "invalid", detail, recovery=held)


def _chosen(chain: list[str], history: list[Attempt], max_attempts: int = 6) -> tuple[str, tuple[str, ...]]:
    step = next_step(_input([_reviewer(name) for name in chain], max_attempts=max_attempts), history)
    return (step.reviewer.name if step.kind == REVIEW and step.reviewer else step.kind), step.reasons


def test_a_stop_answer_ends_the_run_pending_without_trying_another_reviewer() -> None:
    chosen, reasons = _chosen(["r1", "r2"], [_invalid(1, "r1", JEV_STOP)])
    assert chosen == PENDING
    assert reasons == (f"stopped: the process is blocked ({MESSAGE})",)


def test_an_escalate_answer_moves_to_the_next_reviewer() -> None:
    assert _chosen(["r1", "r2"], [_invalid(1, "r1", JEV_ESCALATE)])[0] == "r2"


def test_a_retry_answer_repeats_the_same_reviewer_and_becomes_escalate_after_the_cap() -> None:
    first = _invalid(1, "r1", JEV_RETRY)
    assert _chosen(["r1", "r2"], [first])[0] == "r1"
    assert _chosen(["r1", "r2"], [first, _invalid(2, "r1", JEV_RETRY)])[0] == "r2"
    assert _chosen(["r1"], [first, _invalid(2, "r1", JEV_RETRY)])[0] == PENDING


def test_an_invalid_attempt_with_no_recovery_waits_instead_of_escalating() -> None:
    chosen, reasons = _chosen(["r1", "r2"], [_invalid(1, "r1", None)])
    assert chosen == PENDING
    assert reasons == ("r1: absent (invalid): the recovery is not recorded",)
    assert _chosen(["r1", "r2"], [_invalid(1, "r1", None)], max_attempts=1)[0] == PENDING


def test_a_stop_after_an_escalation_still_ends_the_run() -> None:
    history = [_invalid(1, "r1", JEV_ESCALATE), _invalid(2, "r2", JEV_STOP, "the brief is empty or truncated")]
    chosen, reasons = _chosen(["r1", "r2", "r3"], history)
    assert chosen == PENDING and reasons == ("stopped: the process is blocked (the brief is empty or truncated)",)


@pytest.mark.parametrize("kind", ("incomplete", "empty", "error"))
def test_other_absences_keep_the_single_retry_before_the_chain_advances(kind: str) -> None:
    first = Attempt(1, "r1", ABSENT, kind, "why")
    assert _chosen(["r1", "r2"], [first])[0] == "r1"
    assert _chosen(["r1", "r2"], [first, Attempt(2, "r1", ABSENT, kind, "why")])[0] == "r2"


def _plan_chain(policy: Policy) -> dict[str, Any]:
    return _plan(policy, ("small", "medium", "large"))


def test_replay_rejects_an_attempt_that_contradicts_the_recorded_recovery(policy: Policy, tmp_path: Path) -> None:
    plan = _plan_chain(policy)
    chosen = SelectionInput.from_plan(plan, NOW)

    def decide(*rows: tuple[str, Recovery]) -> list[str]:
        for slot, (reviewer, held) in enumerate(rows, start=1):
            _write(tmp_path, slot, reviewer, missing=Absence("invalid", MESSAGE), recovered=held)
        for stale in tmp_path.glob("attempt-*.json"):
            if int(stale.stem.split("-")[1]) > len(rows):
                stale.unlink()
        return attempts.decide(chosen, attempts.load(tmp_path, plan, RUN_ID))[1]

    assert decide(("small", JEV_STOP)) == []
    after_stop = decide(("small", JEV_STOP), ("medium", JEV_STOP))
    assert after_stop == ["slot 2 ran medium, but the policy selects pending"]
    after_escalate = decide(("small", JEV_ESCALATE), ("small", JEV_STOP))
    assert after_escalate == ["slot 2 ran small, but the policy selects medium"]
    assert decide(("small", JEV_ESCALATE), ("medium", JEV_RETRY)) == []


def test_replay_rejects_a_fallback_recovery_the_policy_would_not_give(policy: Policy, tmp_path: Path) -> None:
    plan = _plan_chain(policy)
    chosen = SelectionInput.from_plan(plan, NOW)
    retry_then_retry = Recovery(RETRY, FALLBACK, reason=recovery.UNAVAILABLE)
    _write(tmp_path, 1, "small", missing=Absence("invalid", MESSAGE), recovered=retry_then_retry)
    _write(tmp_path, 2, "small", missing=Absence("invalid", MESSAGE), recovered=retry_then_retry)
    errors = attempts.decide(chosen, attempts.load(tmp_path, plan, RUN_ID))[1]
    assert errors == ["slot 2 records a fallback recovery that the policy does not give"]


def test_a_recovery_that_is_not_coherent_is_an_artifact_error(policy: Policy, tmp_path: Path) -> None:
    plan = _plan_chain(policy)
    for held in (
        {
            "choice": "escalate",
            "source": "fallback",
            "answer": "",
            "probability": None,
            "confidence": None,
            "reason": "unavailable",
        },
        {"choice": "stop", "source": "jev", "answer": "stop", "probability": 0.9, "confidence": 0.4, "reason": ""},
        {"choice": "retry", "source": "jev", "answer": "stop", "probability": 0.9, "confidence": 0.9, "reason": ""},
        {"choice": "retry", "source": "somewhere", "answer": "", "probability": None, "confidence": None, "reason": ""},
    ):
        _write(tmp_path, 1, "small", missing=Absence("invalid", MESSAGE), recovery=held)
        loaded = attempts.load(tmp_path, plan, RUN_ID)
        assert loaded.errors == ["attempt-1.json: unreadable recovery record"], held


def test_a_recovery_on_an_attempt_that_was_not_invalid_is_an_artifact_error(policy: Policy, tmp_path: Path) -> None:
    plan = _plan_chain(policy)
    _write(tmp_path, 1, "small", verdict=_v("Reviewed."), recovered=JEV_RETRY)
    loaded = attempts.load(tmp_path, plan, RUN_ID)
    assert loaded.errors == ["attempt-1.json: a recovery is recorded on an attempt that was not invalid"]


@pytest.mark.parametrize("case", CASES, ids=[item["case"] for item in CASES])
def test_recorded_jev_answers_resolve_to_the_expected_recovery(case: dict[str, Any]) -> None:
    number = 2 if "attempt 2" in case["case"] else 1
    held = recovery.judge(case["answer"], number, selection.MAX_ATTEMPTS_PER_REVIEWER)
    answer = case["answer"]
    if answer["confidence"] >= 0.60:
        assert (held.source, held.choice) == (JEV, answer["choice"])
        assert held.probability == answer["probabilities"][answer["choice"]]
    else:
        assert (held.source, held.reason, held.choice) == (FALLBACK, recovery.LOW_CONFIDENCE, STOP)


def test_recorded_contract_failures_are_never_escalated_and_format_failures_never_stop() -> None:
    contract = [item["answer"]["choice"] for item in CASES if item["case"].startswith("3453 contract")]
    formats = [item["answer"]["choice"] for item in CASES if not item["case"].startswith("3453 contract")]
    assert ESCALATE not in contract
    assert STOP not in formats


class _Session(JevSession):
    def __init__(self, answer: Mapping[str, Any] | Exception, key: str = "k-test") -> None:
        super().__init__(api_key=key)
        self.answer = answer
        self.states: list[Any] = []

    def ask(self, state: object, questions: Mapping[str, object]) -> dict[str, Any]:
        self.states.append((state, questions))
        if isinstance(self.answer, Exception):
            raise self.answer
        return {"recovery": self.answer}


def _artifact_dir(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    plan = {**_plan(policy, ("small", "medium")), "workspace": str(tmp_path)}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setenv("GITHUB_RUN_ID", RUN_ID)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return plan_path, tmp_path / "attempts"


def _recover(plan_path: Path, directory: Path, tmp_path: Path) -> int:
    return cli.command_recover(
        argparse.Namespace(plan=str(plan_path), attempts_dir=str(directory), workspace=str(tmp_path))
    )


def _recorded(directory: Path, slot: int) -> dict[str, Any] | None:
    return json.loads((directory / f"attempt-{slot}.json").read_text(encoding="utf-8"))["recovery"]


def test_a_jev_answer_is_recorded_on_the_attempt_with_its_probability_and_confidence(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path, directory = _artifact_dir(policy, tmp_path, monkeypatch)
    _write(directory, 1, "small", missing=Absence("invalid", MESSAGE))
    answer = {"choice": "stop", "probabilities": {"retry": 0.0, "stop": 0.98, "escalate": 0.02}, "confidence": 0.97}
    session = _Session(answer)
    monkeypatch.setattr(cli, "JevSession", lambda: session)
    assert _recover(plan_path, directory, tmp_path) == 0
    assert _recorded(directory, 1) == {
        "choice": "stop",
        "source": "jev",
        "answer": "stop",
        "probability": 0.98,
        "confidence": 0.97,
        "reason": "",
    }
    [(state, questions)] = session.states
    assert "attempt 1 of at most 2" in state["text"] and MESSAGE in state["text"]
    assert set(questions["recovery"]["criteria"]) == {"retry", "stop", "escalate"}


def test_a_recorded_attempt_is_not_asked_about_twice(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path, directory = _artifact_dir(policy, tmp_path, monkeypatch)
    _write(directory, 1, "small", missing=Absence("invalid", MESSAGE), recovered=JEV_RETRY)
    session = _Session(JevError(None, "must not be called"))
    monkeypatch.setattr(cli, "JevSession", lambda: session)
    assert _recover(plan_path, directory, tmp_path) == 0
    assert session.states == []
    assert _recorded(directory, 1) == JEV_RETRY.to_json()


def test_the_final_slot_is_not_classified(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_path, directory = _artifact_dir(policy, tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["max_attempts"] = 1
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    _write(directory, 1, "small", missing=Absence("invalid", MESSAGE))
    session = _Session(JevError(None, "must not be called"))
    monkeypatch.setattr(cli, "JevSession", lambda: session)
    assert _recover(plan_path, directory, tmp_path) == 0
    assert session.states == [] and _recorded(directory, 1) is None


FALLBACKS = {
    "unavailable": lambda: _Session(JevError(503, "down")),
    "out-of-label": lambda: _Session({"choice": "maybe", "probabilities": {"maybe": 1.0}, "confidence": 0.99}),
    "low-confidence": lambda: _Session(
        {"choice": "escalate", "probabilities": {"retry": 0.2, "stop": 0.2, "escalate": 0.6}, "confidence": 0.3}
    ),
    "unkeyed": lambda: JevSession(api_key=""),
}


@pytest.mark.parametrize("reason", sorted(FALLBACKS))
def test_an_unusable_jev_answer_retries_once_then_stops_and_never_escalates(
    reason: str, policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path, directory = _artifact_dir(policy, tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(cli, "JevSession", FALLBACKS[reason])
    chosen = SelectionInput.from_plan(plan, NOW)

    _write(directory, 1, "small", missing=Absence("invalid", MESSAGE))
    assert _recover(plan_path, directory, tmp_path) == 0
    first = _recorded(directory, 1)
    assert first is not None and (first["choice"], first["source"], first["reason"]) == (RETRY, FALLBACK, reason)
    step, errors = attempts.decide(chosen, attempts.load(directory, plan, RUN_ID))
    assert errors == [] and step.kind == REVIEW and step.reviewer is not None and step.reviewer.name == "small"

    _write(directory, 2, "small", missing=Absence("invalid", MESSAGE))
    assert _recover(plan_path, directory, tmp_path) == 0
    second = _recorded(directory, 2)
    assert second is not None and (second["choice"], second["source"], second["reason"]) == (STOP, FALLBACK, reason)
    step, errors = attempts.decide(chosen, attempts.load(directory, plan, RUN_ID))
    assert errors == [] and step.kind == PENDING and step.reviewer is None
    assert step.reasons == (f"stopped: the process is blocked ({MESSAGE})",)


def _ask_about(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Any]:
    plan_path, directory = _artifact_dir(policy, tmp_path, monkeypatch)
    session = _Session(
        {"choice": "retry", "probabilities": {"retry": 0.9, "stop": 0.05, "escalate": 0.05}, "confidence": 0.9}
    )
    monkeypatch.setattr(cli, "JevSession", lambda: session)
    assert _recover(plan_path, directory, tmp_path) == 0
    [(state, questions)] = session.states
    return state["text"], questions


def test_the_jev_request_carries_no_raw_reply_text_from_an_unparsed_reply(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "attempts"
    prose = f'rewrites=wrapped\nshape={{"summary":"str"}}\n{RAW} excerpt of the reply'
    _write(directory, 1, "small", missing=Absence("invalid", MESSAGE), diagnostic=prose)
    text, questions = _ask_about(policy, tmp_path, monkeypatch)
    assert RAW not in json.dumps([text, questions])
    assert 'Reply shape (types and counts only): {"summary":"str"}' in text


def test_the_jev_request_carries_no_raw_reply_text_from_a_parsed_reply(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "attempts"
    reply = {
        **_v(RAW, [{"severity": "High", "file": RAW, "line": 1, "title": RAW, "detail": RAW}]),
        "decision": "BOGUS",
    }
    _write(directory, 1, "small", verdict=reply, missing=None)
    text, questions = _ask_about(policy, tmp_path, monkeypatch)
    assert RAW not in json.dumps([text, questions])
    assert "decision must be one of" in text and "list_items" in text


def test_earlier_invalid_messages_of_the_run_reach_jev_with_their_tier_and_attempt(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "attempts"
    _write(directory, 1, "small", missing=Absence("invalid", MESSAGE), recovered=JEV_ESCALATE)
    _write(directory, 2, "medium", missing=Absence("invalid", "the brief is empty or truncated"))
    text, _ = _ask_about(policy, tmp_path, monkeypatch)
    assert f"tier small, attempt 1: {MESSAGE}" in text
    assert "tier medium, model role" in text and "attempt 1 of at most 2" in text
    assert "Validation message: the brief is empty or truncated" in text


def test_the_shape_is_taken_only_from_the_leading_marker_and_only_when_it_holds_types_and_counts() -> None:
    clean = 'shape={"status":"str","defects":{"list_items":2}}'
    assert recovery.shape_of({"verdict": None, "diagnostic": clean + "\nraw excerpt"}) == (
        '{"status":"str","defects":{"list_items":2}}'
    )
    assert recovery.shape_of({"verdict": None, "diagnostic": f"rewrites=a\n{clean}"}) != recovery.UNKNOWN_SHAPE
    assert recovery.shape_of({"verdict": None, "diagnostic": f"excerpt\n{clean}"}) == recovery.UNKNOWN_SHAPE
    assert recovery.shape_of({"verdict": None, "diagnostic": 'shape={"status":"leaked value here"}'}) == (
        recovery.UNKNOWN_SHAPE
    )
    assert recovery.shape_of({"verdict": None, "diagnostic": 'shape={"bad key!":"str"}'}) == recovery.UNKNOWN_SHAPE
    shaped = recovery.shape_of({"verdict": {"summary": RAW, "defects": [RAW, RAW]}, "diagnostic": ""})
    assert RAW not in shaped and "list_items" in shaped


def test_the_state_names_the_tier_the_model_role_the_attempt_and_the_earlier_messages() -> None:
    state = recovery.request_state(
        tier="medium",
        harness="omp",
        role="@default",
        model="anthropic/claude-sonnet-5-5",
        effort="high",
        number=1,
        limit=2,
        message=MESSAGE,
        shape='{"status":"str"}',
        earlier=[("small", 1, "first"), ("small", 2, "second")],
    )["text"]
    assert "tier medium, model role @default (omp anthropic/claude-sonnet-5-5, effort high)" in state
    assert "attempt 1 of at most 2" in state
    assert "tier small, attempt 1: first; tier small, attempt 2: second" in state
