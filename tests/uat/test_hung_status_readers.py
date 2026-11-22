from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.uat import _cli, cells_io, clickhouse_memory, infrastructure_retry, orchestrator, rubric
from tests.uat.phases import report as report_phase
from tests.uat.runner import CellResult

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _cell(status: str, exit_code: int, log_path: Path | None = None) -> CellResult:
    return CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status=status,
        exit_code=exit_code,
        elapsed_s=1.0,
        log_path=log_path or Path("/tmp/duckdb_tpch_0.01.log"),
        result_path=None,
    )


HUNG = _cell("hung", -9)
TIMED_OUT = _cell("timed-out", 124)


def test_rubric_counts_a_hung_row_as_an_attempted_cell():
    row = {"platform": "duckdb", "benchmark": "tpch", "scale": 0.01, "status": "hung", "exit_code": -9}
    observation = rubric.observation_from_row(row, run_id="run", completed_at="2026-11-21T00:00:00Z")
    assert observation is not None
    assert observation.cell == ("duckdb", "tpch", 0.01)


def test_clickhouse_memory_trace_accepts_hung_as_an_outcome():
    trace = clickhouse_memory.ClickHouseMemoryTrace(
        platform="clickhouse-server",
        rung=clickhouse_memory.DEFAULT_MEMORY_RUNGS[0],
        started_at_utc=clickhouse_memory.utc_now(),
        driver_timeout_s=300,
        driver_timeout_source="test fixture",
    )
    trace.finish(outcome="hung")
    assert trace.outcome == "hung"


def test_execute_summary_counts_a_hung_cell_as_timed_out(monkeypatch, capsys, tmp_path):
    outcome = SimpleNamespace(
        results=[HUNG, TIMED_OUT, _cell("passed", 0)],
        pruned=(),
        skipped_unreachable=(),
        docker_events=(),
        aborted=False,
        abort_reason=None,
        exit_code=lambda: 1,
    )
    result = SimpleNamespace(
        aborted_phase=None, abort_reason=None, log_dir=tmp_path, execute_outcome=outcome, exit_code=lambda: 1
    )
    monkeypatch.setattr("tests.uat.orchestrator.run_sweep", lambda *args, **kwargs: result)
    args = SimpleNamespace(
        config=str(REPO_ROOT / "tests" / "uat" / "configs" / "stress-default.yaml"),
        databases_root=str(tmp_path / "databases"),
        no_cleanup=True,
    )

    _cli._handle_execute(args)

    summary = json.loads(capsys.readouterr().out)
    assert summary["timed_out"] == 2
    assert summary["passed"] == 1


def test_gate_accounting_counts_a_hung_cell_as_timed_out():
    accounting = orchestrator._accounting_for_gate_summary(None, SimpleNamespace(results=(HUNG, TIMED_OUT)))
    assert accounting.timed_out == 2
    assert accounting.attempted == 2


def test_cells_jsonl_row_of_a_hung_cell_is_marked_timed_out_with_the_timeout_terminal_state(tmp_path):
    source_info = SimpleNamespace(commit_sha="a" * 40, commit_short_sha="aaaaaaa", dirty=False)
    row = json.loads(cells_io._render_cell_row(_cell("hung", -9, tmp_path / "missing.log"), source_info=source_info))
    assert row["status"] == "hung"
    assert row["timed_out"] is True
    assert row["terminal_state"] == "timeout"


def test_terminal_state_of_a_hung_cell_is_timeout_whatever_its_exit_code():
    assert report_phase.terminal_state(_cell("hung", 1)) == "timeout"
    assert report_phase.terminal_state(_cell("hung", -9)) == "timeout"


def test_report_summary_counts_a_hung_cell_as_timed_out(tmp_path):
    summary = report_phase.write_report(
        [HUNG, TIMED_OUT, _cell("passed", 0)], output_path=tmp_path / "matrix_summary.tsv"
    )
    assert summary.timeout_count == 2


def test_infrastructure_retry_treats_a_hung_cell_like_a_timed_out_cell(tmp_path):
    log = tmp_path / "cell.log"
    log.write_text("", encoding="utf-8")
    hung = infrastructure_retry.infrastructure_signature(_cell("hung", -9, log))
    timed_out = infrastructure_retry.infrastructure_signature(_cell("timed-out", 124, log))
    assert hung == timed_out == "timeout"
