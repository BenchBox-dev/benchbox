from __future__ import annotations

import json
from pathlib import Path

import pytest
from pr_refresh_certification import DECISION_FULL, DECISION_SHADOW
from pr_refresh_replay import (
    _percentile,
    completeness_errors,
    load_records,
    main,
    replay_record,
    summarize,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
ELIGIBLE = REPO_ROOT / "tests" / "fixtures" / "ci" / "pr-refresh" / "eligible.json"
REPLAY_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ci" / "pr-refresh-replay"


def _eligible_record(**overrides: object) -> dict:
    request = json.loads(ELIGIBLE.read_text(encoding="utf-8"))
    record = {
        "id": "eligible-refresh",
        "pr_number": 1717,
        "request": request,
        "actual_lane_conclusions": {
            "code-lint": "success",
            "code-test": "success",
            "correctness-gate": "success",
            "plan-capture-gate": "success",
            "medium-test": "success",
        },
        "required_gate_seconds": 1800,
        "all_workflow_seconds": 2400,
        "runner_minutes": 90,
    }
    record.update(overrides)
    return record


def test_eligible_refresh_replays_as_shadow_without_full_only_failure() -> None:
    result = replay_record(_eligible_record())
    assert result.decision == DECISION_SHADOW
    assert result.full_only_failure is False
    assert result.actual_full_lanes_failed == []


def test_shadow_eligible_with_failed_medium_is_full_only_failure() -> None:
    record = _eligible_record()
    record["actual_lane_conclusions"]["medium-test"] = "failure"
    result = replay_record(record)
    assert result.decision == DECISION_SHADOW
    assert result.full_only_failure is True
    assert result.actual_full_lanes_failed == ["medium-test"]


def test_plan_capture_failure_inside_the_correctness_partition_is_full_only_failure() -> None:
    record = _eligible_record()
    lanes = record["actual_lane_conclusions"]
    del lanes["plan-capture-gate"]
    lanes["correctness-gate"] = "failure"
    result = replay_record(record)
    assert result.decision == DECISION_SHADOW
    assert result.full_only_failure is True
    assert result.actual_full_lanes_failed == ["correctness-gate"]


def test_chained_refresh_is_full_required() -> None:
    record = _eligible_record()
    request = record["request"]
    parent1 = request["commits"][request["before"]]
    parent1["parents"] = [parent1["parents"][0], "f" * 40]
    result = replay_record(record)
    assert result.decision == DECISION_FULL
    assert "chained_refresh" in result.reasons


def test_fork_and_self_change_and_malformed_are_full_required() -> None:
    fork = _eligible_record()
    fork["request"]["is_fork"] = True
    assert replay_record(fork).reasons == ["fork_head"]

    self_change = _eligible_record()
    self_change["request"]["authored_paths"] = ["scripts/pr_refresh_certification.py"]
    assert "self_change" in replay_record(self_change).reasons

    malformed = {"id": "bad", "request": {"pr_number": "nope"}}
    result = replay_record(malformed)
    assert result.decision == DECISION_FULL
    assert result.reasons == ["malformed_payload"]


def test_summary_reports_yield_and_reason_distribution() -> None:
    results = [
        replay_record(_eligible_record()),
        replay_record(_eligible_record(id="docs")),
    ]
    fork = _eligible_record(id="fork")
    fork["request"]["is_fork"] = True
    results.append(replay_record(fork))
    summary = summarize(results)
    assert summary.records == 3
    assert summary.shadow_eligible == 2
    assert summary.full_required == 1
    assert summary.eligibility_yield == pytest.approx(2 / 3)
    assert summary.reason_counts["fork_head"] == 1
    assert summary.required_gate_p50 == 1800


def test_cli_reads_fixture_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    if REPLAY_FIXTURES.is_dir() and any(REPLAY_FIXTURES.glob("*.json")):
        source = REPLAY_FIXTURES
    else:
        source = tmp_path / "records"
        source.mkdir()
        (source / "one.json").write_text(json.dumps(_eligible_record()), encoding="utf-8")
    out = tmp_path / "summary.json"
    assert main(["--fixtures", str(source), "--json-out", str(out), "--check-completeness"]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["records"] >= 1
    assert payload["completeness_errors"] == []
    printed = json.loads(capsys.readouterr().out)
    assert printed["summary"]["records"] == payload["summary"]["records"]
    assert load_records(source)


def test_check_completeness_fails_when_lane_outcomes_missing(tmp_path: Path) -> None:
    source = tmp_path / "records"
    source.mkdir()
    record = _eligible_record()
    del record["actual_lane_conclusions"]
    (source / "bad.json").write_text(json.dumps(record), encoding="utf-8")
    assert main(["--fixtures", str(source), "--check-completeness"]) == 1


def test_percentile_uses_nearest_rank_not_an_off_by_one_index() -> None:
    assert _percentile([float(n) for n in range(1, 21)], 95) == 19.0
    assert _percentile([float(n) for n in range(1, 101)], 95) == 95.0
    assert _percentile([1.0, 2.0, 3.0, 4.0, 100.0], 50) == 3.0
    assert _percentile([], 95) is None
    assert _percentile([7.0], 95) == 7.0


def test_synthetic_controls_are_excluded_from_performance_aggregates() -> None:
    observation = _eligible_record(id="obs")
    observation["required_gate_seconds"] = 900
    observation["runner_minutes"] = 40
    control = _eligible_record(id="ctl")
    control["kind"] = "control"
    control["required_gate_seconds"] = 100
    control["runner_minutes"] = 20

    summary = summarize([replay_record(observation), replay_record(control)])

    assert summary.records == 2
    assert summary.observations == 1
    assert summary.controls == 1
    assert summary.required_gate_p50 == 900
    assert summary.runner_minutes_total == 40


def test_completeness_rejects_non_terminal_lane_conclusions() -> None:
    record = _eligible_record(id="pending")
    record["actual_lane_conclusions"]["medium-test"] = None
    errors = completeness_errors([record], [replay_record(record)])
    assert any("non-terminal lane conclusions" in error for error in errors)
