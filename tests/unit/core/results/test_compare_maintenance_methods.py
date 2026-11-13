from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.cli.exit_codes import ExitCode
from benchbox.core.analysis.comparison import PlatformComparison
from benchbox.core.results.analytics import compare_results
from benchbox.core.results.models import (
    BenchmarkResults,
    ExecutionPhases,
    MaintenanceOperation,
    MaintenanceTestPhase,
    SetupPhase,
)
from tests.fixtures.result_dict_fixtures import make_v2_result_dict

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NEW = "dsdgen_refresh_sets"
LEGACY = "synthetic_rows"


def _write(path: Path, method: str | None) -> str:
    queries = [
        {"id": "1", "ms": 10.0, "rows": 1, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"}
    ]
    data = make_v2_result_dict(queries=queries, total_ms=1000)
    if method is not None:
        data["run"]["maintenance_method"] = method
        data["phases"] = {"maintenance_test": {"status": "COMPLETED", "duration_ms": 5, "operations": []}}
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _result(platform: str, method: str | None, with_phase: bool = True) -> BenchmarkResults:
    phases = None
    if with_phase:
        phase = MaintenanceTestPhase(
            start_time="",
            end_time="",
            duration_ms=5,
            maintenance_operations=[MaintenanceOperation("LF_SS", "INSERT", "store_sales", 1, 1, "SUCCESS")],
            query_executions=[],
            maintenance_method=method,
        )
        phases = ExecutionPhases(setup=SetupPhase(), maintenance_test=phase)
    return BenchmarkResults(
        benchmark_name="TPC-DS",
        platform=platform,
        scale_factor=1.0,
        execution_id=f"{platform}-1",
        timestamp=datetime.now(),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        query_results=[{"query_id": "Q1", "execution_time_ms": 100.0, "status": "success"}],
        total_execution_time=100.0,
        average_query_time=100.0,
        execution_phases=phases,
    )


def test_compare_results_refuses_different_methods(tmp_path: Path):
    outcome = compare_results(Path(_write(tmp_path / "a.json", NEW)), Path(_write(tmp_path / "b.json", LEGACY)))
    assert outcome["error_code"] == "VALIDATION_ERROR"
    assert len(outcome["details"]["differences"]) == 1
    assert "maintenance method differs" in outcome["details"]["differences"][0]


def test_compare_results_accepts_equal_methods(tmp_path: Path):
    outcome = compare_results(Path(_write(tmp_path / "a.json", NEW)), Path(_write(tmp_path / "b.json", NEW)))
    assert outcome.get("error_code") != "VALIDATION_ERROR"
    assert "error" not in outcome


def test_compare_results_accepts_one_side_without_maintenance(tmp_path: Path):
    outcome = compare_results(Path(_write(tmp_path / "a.json", NEW)), Path(_write(tmp_path / "b.json", None)))
    assert "error" not in outcome


def test_validate_rejects_mixed_methods():
    validation = PlatformComparison([_result("duckdb", NEW), _result("sqlite", LEGACY)]).validate()
    assert validation.is_valid is False
    assert any(
        error.startswith("Results use different measurement modes: maintenance method differs (")
        for error in validation.errors
    )


def test_validate_treats_unrecorded_method_as_legacy():
    validation = PlatformComparison([_result("duckdb", NEW), _result("sqlite", None)]).validate()
    assert validation.is_valid is False


def test_validate_accepts_equal_methods():
    validation = PlatformComparison([_result("duckdb", NEW), _result("sqlite", NEW)]).validate()
    assert not any("measurement modes" in error for error in validation.errors)


def test_validate_accepts_result_without_maintenance():
    validation = PlatformComparison([_result("duckdb", NEW), _result("sqlite", None, with_phase=False)]).validate()
    assert not any("measurement modes" in error for error in validation.errors)


def test_cli_file_comparison_refuses_different_methods(tmp_path: Path):
    result = CliRunner().invoke(cli, ["compare", _write(tmp_path / "a.json", NEW), _write(tmp_path / "b.json", LEGACY)])
    assert result.exit_code == ExitCode.NOT_COMPARABLE
    assert "maintenance method differs" in result.output


def test_cli_refusal_is_not_bypassed_by_heterogeneous_flag(tmp_path: Path):
    result = CliRunner().invoke(
        cli,
        [
            "compare",
            _write(tmp_path / "a.json", NEW),
            _write(tmp_path / "b.json", LEGACY),
            "--allow-heterogeneous-comparison",
        ],
    )
    assert result.exit_code == ExitCode.NOT_COMPARABLE
    assert "maintenance method differs" in result.output


def test_cli_file_comparison_accepts_equal_methods(tmp_path: Path):
    result = CliRunner().invoke(cli, ["compare", _write(tmp_path / "a.json", NEW), _write(tmp_path / "b.json", NEW)])
    assert result.exit_code == 0, result.output
