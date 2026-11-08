from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.uat import cells_io, gate_summary, infrastructure_retry, orchestrator
from tests.uat.config import ConfigError, validate_config
from tests.uat.runner import CellResult

pytestmark = pytest.mark.medium

CELL_LIMIT_S = 1200


@dataclass
class _Source:
    commit_sha: str = "a" * 40
    commit_short_sha: str = "aaaaaaa"
    dirty: bool = False


def _result_json(path: Path, errors: list[tuple[str, str]]) -> Path:
    payload = {
        "version": "2.1",
        "run": {"id": "uat", "timestamp": "2026-01-01T00:00:00", "total_duration_ms": 1, "query_time_ms": 1},
        "benchmark": {"id": "tpch", "name": "TPC-H", "scale_factor": 0.01, "test_type": "power"},
        "platform": {"name": "SingleStore"},
        "summary": {
            "queries": {"total": len(errors), "passed": 0, "failed": len(errors)},
            "validation": {"status": "failed"},
        },
        "queries": [{"id": query_id, "status": "ERROR", "ms": 1} for query_id, _ in errors],
        "errors": [
            {"phase": "query", "query_id": query_id, "type": "QueryError", "message": message}
            for query_id, message in errors
        ],
        "phases": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Attempts:
    def __init__(self, tmp_path: Path, outcomes: list[dict]) -> None:
        self.tmp_path = tmp_path
        self.outcomes = outcomes
        self.calls: list[dict] = []

    def __call__(self, platform: str, benchmark: str, scale: float, **kwargs) -> CellResult:
        index = len(self.calls)
        self.calls.append(kwargs)
        spec = self.outcomes[min(index, len(self.outcomes) - 1)]
        log_path = self.tmp_path / f"{platform}_{benchmark}_{index}.log"
        log_path.write_text(spec.get("log", ""), encoding="utf-8")
        result_path = None
        if spec.get("errors"):
            result_path = _result_json(self.tmp_path / f"result_{index}.json", spec["errors"])
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status=spec["status"],
            exit_code=spec["exit_code"],
            elapsed_s=spec.get("elapsed_s", 5.0),
            log_path=log_path,
            result_path=result_path,
            submit_terminal_state=spec.get("submit", "missing_manifest"),
        )


TIMEOUT = {"status": "timed-out", "exit_code": 124, "elapsed_s": float(CELL_LIMIT_S)}
PASSED = {"status": "passed", "exit_code": 0, "submit": "submittable"}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _run(attempts: _Attempts, *, budget_s: int = 10_000, clock: _Clock | None = None, scale: float = 0.1) -> CellResult:
    clock = clock or _Clock()
    wrapped = infrastructure_retry.with_infrastructure_retry(attempts, job_budget_s=budget_s, clock=clock)
    return wrapped("singlestore", "tpch", scale, timeout_s=CELL_LIMIT_S)


def test_timeout_retries_once_and_a_passing_retry_passes_the_cell(tmp_path):
    attempts = _Attempts(tmp_path, [TIMEOUT, PASSED])
    result = _run(attempts)

    assert len(attempts.calls) == 2
    assert result.status == "passed"
    assert result.attempt == 2
    assert result.retry_signature == "timeout"
    assert result.retry_outcome == infrastructure_retry.RETRY_PASSED
    assert result.first_attempt_status == "timed-out"
    assert result.first_attempt_log_path == tmp_path / "singlestore_tpch_0.log"
    assert result.first_attempt_elapsed_s == float(CELL_LIMIT_S)


def test_second_infrastructure_failure_fails_the_cell_without_a_third_attempt(tmp_path):
    attempts = _Attempts(tmp_path, [TIMEOUT, TIMEOUT, PASSED])
    result = _run(attempts)

    assert len(attempts.calls) == 2
    assert result.status == "timed-out"
    assert result.attempt == 2
    assert result.retry_outcome == infrastructure_retry.RETRY_FAILED


@pytest.mark.parametrize(
    "first",
    [
        {"status": "failed", "exit_code": 137, "log": "peak rss"},
        {"status": "failed", "exit_code": 1, "log": "benchbox.core.data_fetch.errors.DownloadError: download failed"},
        {
            "status": "failed",
            "exit_code": 1,
            "submit": "query_failure",
            "errors": [("Q1", "ERROR 1712 (HY000): Leaf Error: out of memory"), ("Q2", "(1712, 'Leaf Error')")],
        },
    ],
    ids=["memory", "download", "singlestore-1712"],
)
def test_each_infrastructure_signature_retries(tmp_path, first):
    attempts = _Attempts(tmp_path, [first, PASSED])
    result = _run(attempts)

    assert len(attempts.calls) == 2
    assert result.status == "passed"
    assert result.attempt == 2


@pytest.mark.parametrize(
    "first",
    [
        {
            "status": "failed",
            "exit_code": 1,
            "submit": "query_failure",
            "errors": [("Q1", "Result mismatch: expected 4 rows, got 5")],
        },
        {
            "status": "failed",
            "exit_code": 1,
            "submit": "query_failure",
            "errors": [("Q1", "ERROR 1712 (HY000): Leaf Error"), ("Q2", "Result mismatch: expected 4 rows, got 5")],
        },
        {"status": "failed", "exit_code": 1, "submit": "schema_violation", "log": "validation failed"},
        {"status": "failed", "exit_code": 1, "log": "Traceback (most recent call last):\nKeyError: 'x'"},
    ],
    ids=["wrong-results", "1712-mixed-with-wrong-results", "validation-failure", "crash"],
)
def test_product_signature_failures_are_never_retried(tmp_path, first):
    attempts = _Attempts(tmp_path, [first, PASSED])
    result = _run(attempts)

    assert len(attempts.calls) == 1
    assert result.status == "failed"
    assert result.attempt == 1
    assert result.retry_outcome is None


def test_passing_cell_is_not_retried(tmp_path):
    attempts = _Attempts(tmp_path, [PASSED])
    result = _run(attempts)

    assert len(attempts.calls) == 1
    assert result.attempt == 1


def test_no_retry_starts_when_remaining_job_time_is_below_the_cell_limit(tmp_path):
    clock = _Clock()
    attempts = _Attempts(tmp_path, [TIMEOUT, PASSED])
    wrapped = infrastructure_retry.with_infrastructure_retry(attempts, job_budget_s=3000, clock=clock)
    clock.now += 1801.0
    result = wrapped("singlestore", "tpch", 0.1, timeout_s=CELL_LIMIT_S)

    assert len(attempts.calls) == 1
    assert result.status == "timed-out"
    assert result.attempt == 1
    assert result.retry_signature == "timeout"
    assert result.retry_outcome == infrastructure_retry.RETRY_SKIPPED_INSUFFICIENT_TIME


def test_retry_starts_when_remaining_job_time_equals_the_cell_limit(tmp_path):
    clock = _Clock()
    attempts = _Attempts(tmp_path, [TIMEOUT, PASSED])
    wrapped = infrastructure_retry.with_infrastructure_retry(attempts, job_budget_s=3000, clock=clock)
    clock.now += 1800.0
    result = wrapped("singlestore", "tpch", 0.1, timeout_s=CELL_LIMIT_S)

    assert len(attempts.calls) == 2
    assert result.retry_outcome == infrastructure_retry.RETRY_PASSED


def _attempts_in(tmp_path: Path, name: str, outcomes: list[dict]) -> _Attempts:
    directory = tmp_path / name
    directory.mkdir()
    return _Attempts(directory, outcomes)


def test_retry_accounting_counts_outcomes_and_signatures(tmp_path):
    download = {"status": "failed", "exit_code": 1, "log": "DownloadError: boom"}
    results = [
        _run(_attempts_in(tmp_path, "retry-passes", [TIMEOUT, PASSED])),
        _run(_attempts_in(tmp_path, "retry-fails", [TIMEOUT, TIMEOUT])),
        _run(_attempts_in(tmp_path, "download", [download, PASSED])),
        _run(_attempts_in(tmp_path, "no-time", [TIMEOUT]), budget_s=10),
        _run(_attempts_in(tmp_path, "clean", [PASSED])),
    ]

    accounting = infrastructure_retry.retry_accounting(results, job_budget_s=10_000)

    assert accounting.triggered == 4
    assert accounting.retried == 3
    assert accounting.retry_passed == 2
    assert accounting.retry_failed == 1
    assert accounting.skipped_insufficient_time == 1
    assert accounting.retried_by_signature == {"download_error": 1, "timeout": 2}
    assert accounting.job_budget_s == 10_000


def test_cells_rows_record_the_attempt_and_round_trip(tmp_path):
    attempts = _Attempts(tmp_path, [TIMEOUT, PASSED])
    retried = _run(attempts)
    cells_path = tmp_path / "cells.jsonl"
    cells_io.write_cells_jsonl(cells_path, [retried], source_info=_Source())

    row = json.loads(cells_path.read_text(encoding="utf-8"))
    assert row["attempt"] == 2
    assert row["retry_signature"] == "timeout"
    assert row["retry_outcome"] == "retry_passed"
    assert row["first_attempt_status"] == "timed-out"
    assert row["first_attempt_log_path"].endswith("singlestore_tpch_0.log")
    assert row["failure_kind"] is None

    restored = cells_io.read_cells_jsonl(cells_path)[0]
    assert (restored.attempt, restored.retry_signature, restored.retry_outcome) == (2, "timeout", "retry_passed")
    assert restored.first_attempt_log_path == retried.first_attempt_log_path


def test_failed_cell_row_carries_failure_kind_and_signature(tmp_path):
    attempts = _Attempts(tmp_path, [TIMEOUT, TIMEOUT])
    cells_path = tmp_path / "cells.jsonl"
    cells_io.write_cells_jsonl(cells_path, [_run(attempts)], source_info=_Source())

    row = json.loads(cells_path.read_text(encoding="utf-8"))
    assert (row["failure_kind"], row["failure_signature"]) == ("infrastructure", "timeout")


def test_gate_summary_round_trips_retry_counters(tmp_path):
    accounting = gate_summary.InfrastructureRetryAccounting(
        job_budget_s=19800,
        triggered=2,
        retried=1,
        retry_passed=1,
        skipped_insufficient_time=1,
        retried_by_signature={"singlestore_1712": 1},
    )
    summary = gate_summary.GateSummary(
        config_name="x",
        source_commit_sha="a" * 40,
        source_dirty=False,
        container_engine=None,
        completed_at="2026-01-01T00:00:00",
        dry_run=False,
        aborted=False,
        abort_phase=None,
        abort_reason=None,
        phase_exit_codes={"execute": 0},
        accounting=gate_summary.PhaseAccounting(),
        unreachable_is_estimated=False,
        validator_clean_rate=None,
        validator_clean_rate_floor=None,
        validator_floor_breached=None,
        cross_scale_clean_pairs=None,
        cross_scale_floor=None,
        cross_scale_floor_breached=None,
        verdict="green",
        infrastructure_retry=accounting,
    )
    path = gate_summary.write_gate_summary(tmp_path, summary)

    assert json.loads(path.read_text(encoding="utf-8"))["infrastructure_retry"]["retried_by_signature"] == {
        "singlestore_1712": 1
    }
    assert gate_summary.read_gate_summary(path).infrastructure_retry == accounting


def _sweep_config(tmp_path: Path, *, budget_s: int | None):
    execute = {"per_cell_timeout_s": CELL_LIMIT_S}
    if budget_s is not None:
        execute["infrastructure_retry_job_budget_s"] = budget_s
    return validate_config(
        {
            "name": "retry-e2e",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "execute": execute,
            "output": {"benchmark_runs_dir_template": str(tmp_path / "runs")},
        }
    )


def _run_sweep(tmp_path: Path, attempts: _Attempts, *, budget_s: int | None):
    logs = tmp_path / "logs"
    with patch.object(orchestrator.exec_phase, "run_cell", attempts):
        result = orchestrator.run_sweep(_sweep_config(tmp_path, budget_s=budget_s), log_dir_override=logs)
    rows = [json.loads(line) for line in (logs / "cells.jsonl").read_text(encoding="utf-8").splitlines()]
    summary = json.loads((logs / gate_summary.GATE_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    return result, rows, summary


def test_sweep_retries_with_a_configured_budget_and_records_one_row_and_the_counters(tmp_path):
    attempts = _attempts_in(tmp_path, "cells", [TIMEOUT, PASSED])

    result, rows, summary = _run_sweep(tmp_path, attempts, budget_s=19800)

    assert len(attempts.calls) == 2
    assert result.phase_exit_codes["execute"] == 0
    assert [(row["status"], row["attempt"], row["retry_outcome"]) for row in rows] == [("passed", 2, "retry_passed")]
    assert summary["infrastructure_retry"] == {
        "job_budget_s": 19800,
        "triggered": 1,
        "retried": 1,
        "retry_passed": 1,
        "retry_failed": 0,
        "skipped_insufficient_time": 0,
        "retried_by_signature": {"timeout": 1},
    }


def test_sweep_gate_summary_reports_zero_counters_when_nothing_failed(tmp_path):
    attempts = _attempts_in(tmp_path, "cells", [PASSED])

    _, rows, summary = _run_sweep(tmp_path, attempts, budget_s=19800)

    assert len(attempts.calls) == 1
    assert rows[0]["attempt"] == 1
    assert summary["infrastructure_retry"]["triggered"] == 0
    assert summary["infrastructure_retry"]["retried_by_signature"] == {}


def test_sweep_failed_retry_fails_the_cell_and_the_gate(tmp_path):
    attempts = _attempts_in(tmp_path, "cells", [TIMEOUT, TIMEOUT])

    result, rows, summary = _run_sweep(tmp_path, attempts, budget_s=19800)

    assert len(attempts.calls) == 2
    assert result.phase_exit_codes["execute"] == 1
    assert [(row["status"], row["attempt"], row["retry_outcome"]) for row in rows] == [("timed-out", 2, "retry_failed")]
    assert summary["verdict"] == "red"
    assert summary["infrastructure_retry"]["retry_failed"] == 1


def test_sweep_does_not_retry_without_a_configured_budget(tmp_path):
    attempts = _attempts_in(tmp_path, "cells", [TIMEOUT, PASSED])

    _, rows, summary = _run_sweep(tmp_path, attempts, budget_s=None)

    assert len(attempts.calls) == 1
    assert [(row["status"], row["attempt"]) for row in rows] == [("timed-out", 1)]
    assert summary["infrastructure_retry"] is None


def test_retry_budget_must_be_a_positive_integer(tmp_path):
    with pytest.raises(ConfigError):
        _sweep_config(tmp_path, budget_s=0)


@pytest.mark.parametrize("terminal_state", ["timeout", "killed"])
@pytest.mark.parametrize(
    "evidence",
    [
        {"failed_query_errors": ("Result mismatch: expected 4 rows, got 5",)},
        {"failed_query_errors": ("ERROR 1712 (HY000)", "Result mismatch: expected 4 rows, got 5")},
        {"submit_terminal_state": "schema_violation"},
    ],
    ids=["wrong-results", "1712-mixed-with-wrong-results", "validation-failure"],
)
def test_product_evidence_wins_over_a_timeout_or_kill(terminal_state, evidence):
    from tests.uat import failure_signature

    result = failure_signature.classify_failure(
        failure_signature.FailureEvidence(
            status="failed", terminal_state=terminal_state, **{"submit_terminal_state": "", **evidence}
        )
    )
    assert result is not None and result.kind == failure_signature.KIND_PRODUCT


@pytest.mark.parametrize(("terminal_state", "signature"), [("timeout", "timeout"), ("killed", "memory")])
def test_a_timeout_or_kill_without_product_evidence_stays_infrastructure(terminal_state, signature):
    from tests.uat import failure_signature

    result = failure_signature.classify_failure(
        failure_signature.FailureEvidence(status="failed", terminal_state=terminal_state, submit_terminal_state="")
    )
    assert result is not None
    assert (result.kind, result.signature) == (failure_signature.KIND_INFRASTRUCTURE, signature)
