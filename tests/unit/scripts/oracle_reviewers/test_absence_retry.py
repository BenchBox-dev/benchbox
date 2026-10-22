from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import attempts, cli
from _project.scripts.oracle_reviewers.absence import Absence
from _project.scripts.oracle_reviewers.selection import (
    ABSENT,
    FAIL,
    JOB_FAILURE_DETAIL,
    PASS,
    PENDING,
    REVIEW,
    Attempt,
    SelectionInput,
    next_step,
)

from .test_selection import RETRYABLE, _input, _reviewer

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _chosen(selection: SelectionInput, history: list[Attempt]) -> str:
    step = next_step(selection, history)
    return step.reviewer.name if step.kind == REVIEW and step.reviewer else step.kind


@pytest.mark.parametrize("kind", RETRYABLE)
def test_eligible_absence_retries_the_same_reviewer_once(kind: str) -> None:
    selection = _input([_reviewer("r1")])
    first = Attempt(1, "r1", ABSENT, kind, "why")
    assert _chosen(selection, [first]) == "r1"
    assert _chosen(selection, [first, Attempt(2, "r1", ABSENT, kind, "why")]) == PENDING


@pytest.mark.parametrize("kind", ("quota", "auth", "timeout"))
def test_infrastructure_absences_never_retry(kind: str) -> None:
    assert _chosen(_input([_reviewer("r1")]), [Attempt(1, "r1", ABSENT, kind)]) == PENDING


def test_job_failure_marker_is_not_retried() -> None:
    history = [Attempt(1, "r1", ABSENT, "error", JOB_FAILURE_DETAIL)]
    assert _chosen(_input([_reviewer("r1")]), history) == PENDING
    assert _chosen(_input([_reviewer("r1"), _reviewer("r2")]), history) == "r2"


def test_second_attempt_infrastructure_failure_ends_the_retry() -> None:
    chain = [_reviewer("r1"), _reviewer("r2")]
    history = [Attempt(1, "r1", ABSENT, "invalid"), Attempt(2, "r1", ABSENT, "timeout")]
    assert _chosen(_input(chain), history) == "r2"


def test_terminal_verdicts_are_never_retried() -> None:
    chain = [_reviewer("r1")]
    assert next_step(_input(chain), [Attempt(1, "r1", PASS)]).kind == PASS
    assert next_step(_input(chain), [Attempt(1, "r1", ABSENT, "invalid"), Attempt(2, "r1", FAIL)]).kind == FAIL


def test_two_reviewer_chain_retries_first_before_advancing() -> None:
    selection = _input([_reviewer("r1"), _reviewer("r2")])
    history = [Attempt(1, "r1", ABSENT, "incomplete")]
    assert _chosen(selection, history) == "r1"
    history.append(Attempt(2, "r1", ABSENT, "invalid"))
    assert _chosen(selection, history) == "r2"
    history.append(Attempt(3, "r2", ABSENT, "empty"))
    assert _chosen(selection, history) == "r2"
    history.append(Attempt(4, "r2", ABSENT, "empty"))
    step = next_step(selection, history)
    assert step.kind == PENDING
    assert step.reasons == ("r1: absent (invalid)", "r2: absent (empty)")


def test_overall_cap_one_blocks_the_retry() -> None:
    selection = _input([_reviewer("r1")], max_attempts=1)
    step = next_step(selection, [Attempt(1, "r1", ABSENT, "invalid")])
    assert step.kind == PENDING
    assert step.reasons == ("r1: skipped: no attempt slot left in this run",)


def test_cap_stops_a_chain_mid_retry() -> None:
    selection = _input([_reviewer("r1"), _reviewer("r2")], max_attempts=2)
    history = [Attempt(1, "r1", ABSENT, "invalid"), Attempt(2, "r1", ABSENT, "invalid")]
    assert next_step(selection, history).kind == PENDING


@pytest.mark.parametrize(
    "overrides",
    (
        {"enabled": False, "disabled_reason": "off"},
        {"read_only": "soft"},
    ),
)
def test_ineligible_reviewer_is_not_retried(overrides: dict[str, Any]) -> None:
    selection = _input([_reviewer("r1", **overrides)], brief_mode="file-list")
    assert _chosen(selection, [Attempt(1, "r1", ABSENT, "invalid")]) == PENDING


def test_oversize_brief_blocks_the_retry() -> None:
    assert _chosen(_input([_reviewer("r1")], brief_mode="oversize"), [Attempt(1, "r1", ABSENT, "invalid")]) == PENDING


def test_pool_quota_in_the_run_blocks_the_retry() -> None:
    chain = [_reviewer("r1", pool="shared"), _reviewer("r2", pool="shared")]
    history = [Attempt(1, "r1", ABSENT, "invalid"), Attempt(2, "r2", ABSENT, "quota")]
    assert next_step(_input(chain), history).kind == PENDING


def _plan(chain: list[Any], max_attempts: int = 5) -> dict[str, Any]:
    return {
        "pr": 7,
        "head_sha": "a" * 40,
        "max_attempts": max_attempts,
        "brief_mode": "inline",
        "chain": [reviewer.to_json() for reviewer in chain],
    }


def _write(directory: Path, slot: int, reviewer: str, plan: dict[str, Any], kind: str, detail: str = "") -> None:
    artifact = attempts.build_artifact(
        run_id="9",
        pr=plan["pr"],
        head_sha=plan["head_sha"],
        slot=slot,
        reviewer=reviewer,
        verdict=None,
        missing=Absence(kind, detail),
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / attempts.artifact_name(slot)).write_text(json.dumps(artifact), encoding="utf-8")


def _decide(directory: Path, plan: dict[str, Any]) -> tuple[str, list[str]]:
    loaded = attempts.load(directory, plan, "9")
    step, errors = attempts.decide(SelectionInput.from_plan(plan, NOW), loaded)
    return (step.reviewer.name if step.reviewer else step.kind), errors


def test_replay_accepts_the_retry_and_chain_advance(tmp_path: Path) -> None:
    plan = _plan([_reviewer("r1"), _reviewer("r2")])
    _write(tmp_path, 1, "r1", plan, "invalid")
    _write(tmp_path, 2, "r1", plan, "empty")
    _write(tmp_path, 3, "r2", plan, "invalid")
    assert _decide(tmp_path, plan) == ("r2", [])


def test_replay_rejects_a_third_attempt_of_one_reviewer(tmp_path: Path) -> None:
    plan = _plan([_reviewer("r1"), _reviewer("r2")])
    for slot in (1, 2, 3):
        _write(tmp_path, slot, "r1", plan, "invalid")
    _, errors = _decide(tmp_path, plan)
    assert errors == ["slot 3 ran r1, but the policy selects r2"]


def test_replay_rejects_a_retry_after_infrastructure_absence(tmp_path: Path) -> None:
    plan = _plan([_reviewer("r1")])
    _write(tmp_path, 1, "r1", plan, "timeout")
    _write(tmp_path, 2, "r1", plan, "invalid")
    _, errors = _decide(tmp_path, plan)
    assert errors == ["slot 2 ran r1, but the policy selects pending"]


def test_replay_rejects_a_retry_after_a_job_failure(tmp_path: Path) -> None:
    plan = _plan([_reviewer("r1")])
    _write(tmp_path, 1, "r1", plan, "error", JOB_FAILURE_DETAIL)
    _write(tmp_path, 2, "r1", plan, "invalid")
    _, errors = _decide(tmp_path, plan)
    assert errors == ["slot 2 ran r1, but the policy selects pending"]


def _args(tmp_path: Path, slot: int, reviewer: str) -> argparse.Namespace:
    return argparse.Namespace(
        plan=str(tmp_path / "plan.json"), out_dir=str(tmp_path / "out"), slot=slot, reviewer=reviewer
    )


def test_ensure_attempt_records_the_shared_job_failure_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "9")
    plan = _plan([_reviewer("r1")])
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    assert cli.command_ensure_attempt(_args(tmp_path, 1, "r1")) == 0
    data = json.loads((tmp_path / "out" / attempts.artifact_name(1)).read_text(encoding="utf-8"))
    assert data["absence"]["kind"] == "error"
    assert data["absence"]["detail"] == JOB_FAILURE_DETAIL
    assert _decide(tmp_path / "out", plan) == (PENDING, [])


@pytest.mark.parametrize("decision, terminal", [("SHIP", PASS), ("DO_NOT_SHIP", FAIL)])
def test_replay_rejects_resampling_after_a_valid_decision(tmp_path: Path, decision: str, terminal: str) -> None:
    plan = {**_plan([_reviewer("r1")]), "max_defects": 10}
    verdict = {
        "status": "complete",
        "incomplete_reason": "",
        "decision": decision,
        "summary": "Review complete.",
        "files_examined": [],
        "defects": [],
        "prior_defects": [],
    }
    artifact = attempts.build_artifact(
        run_id="9", pr=7, head_sha=plan["head_sha"], slot=1, reviewer="r1", verdict=verdict, missing=None
    )
    (tmp_path / "attempt-1.json").write_text(json.dumps(artifact), encoding="utf-8")
    _write(tmp_path, 2, "r1", plan, "invalid")
    loaded = attempts.load(tmp_path, plan, "9")
    step, errors = attempts.decide(SelectionInput.from_plan(plan, NOW), loaded)
    assert step.kind == terminal
    assert step.reviewer is not None and step.reviewer.name == "r1"
    assert loaded.attempts[1].outcome == ABSENT and errors
