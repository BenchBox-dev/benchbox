from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from benchbox.cli import database as cli_database, orchestrator as cli_orchestrator
from benchbox.cli.app import cli
from benchbox.cli.exit_codes import EXIT_CODE_MEANINGS, RESERVED_BY_HOST_LOCK, ExitCode
from tests.fixtures.result_dict_fixtures import make_v2_result_dict

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _bundle(path: Path, query_ms: float, *, gateway: str | None = None, fetch_mode: str | None = None) -> str:
    platform: dict = {"name": "snowflake"}
    if gateway:
        platform["gateway"] = {"name": gateway, "routed": True}
    run: dict = {"timestamp": "2026-10-10T10:00:00Z", "id": path.stem, "total_duration_ms": 1000}
    if fetch_mode:
        run["fetch_mode"] = fetch_mode
    payload = {
        "result_schema_version": "2.3",
        "benchmark": {"name": "TPC-H", "id": "tpch", "scale_factor": 0.01, "test_type": "power"},
        "platform": platform,
        "run": run,
        "queries": [{"id": "Q1", "ms": query_ms, "status": "SUCCESS", "run_type": "measurement"}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _timed(path: Path, query_ms: dict[str, float]) -> str:
    queries = [
        {"id": query_id, "ms": ms, "rows": 1, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"}
        for query_id, ms in query_ms.items()
    ]
    path.write_text(json.dumps(make_v2_result_dict(queries=queries, total_ms=1000)), encoding="utf-8")
    return str(path)


def _compare(*args: str):
    return CliRunner().invoke(cli, ["compare", *args])


class TestResultFileMode:
    def test_success_exits_zero(self, tmp_path: Path):
        result = _compare(_bundle(tmp_path / "a.json", 100.0), _bundle(tmp_path / "b.json", 100.0))

        assert result.exit_code == ExitCode.SUCCESS, result.output

    def test_regression_over_threshold_exits_three(self, tmp_path: Path):
        result = _compare(
            _bundle(tmp_path / "a.json", 100.0), _bundle(tmp_path / "b.json", 200.0), "--fail-on-regression", "10%"
        )

        assert result.exit_code == 3
        assert "Performance regression detected" in result.output

    def test_slowdown_under_threshold_exits_zero(self, tmp_path: Path):
        result = _compare(
            _bundle(tmp_path / "a.json", 100.0), _bundle(tmp_path / "b.json", 105.0), "--fail-on-regression", "10%"
        )

        assert result.exit_code == 0, result.output

    def test_slowdown_under_minimum_delta_exits_zero(self, tmp_path: Path):
        result = _compare(
            _timed(tmp_path / "a.json", {"8": 9.0}),
            _timed(tmp_path / "b.json", {"8": 11.6}),
            "--fail-on-regression",
            "10%",
            "--min-regression-delta",
            "5ms",
        )

        assert result.exit_code == 0, result.output

    def test_incomparable_variants_exit_four(self, tmp_path: Path):
        result = _compare(_bundle(tmp_path / "a.json", 100.0), _bundle(tmp_path / "b.json", 100.0, gateway="espresso"))

        assert result.exit_code == 4
        assert "--allow-heterogeneous-comparison" in result.output

    def test_heterogeneous_flag_bypasses_the_variant_refusal_then_normal_codes_apply(self, tmp_path: Path):
        baseline = _bundle(tmp_path / "a.json", 100.0)
        gateway = _bundle(tmp_path / "b.json", 100.0, gateway="espresso")
        slower = _bundle(tmp_path / "c.json", 300.0, gateway="espresso")

        assert _compare(baseline, gateway, "--allow-heterogeneous-comparison").exit_code == 0
        regressed = _compare(baseline, slower, "--allow-heterogeneous-comparison", "--fail-on-regression", "10%")
        assert regressed.exit_code == 3

    def test_incomparable_timing_modes_exit_four(self, tmp_path: Path):
        result = _compare(
            _bundle(tmp_path / "a.json", 100.0, fetch_mode="arrow"),
            _bundle(tmp_path / "b.json", 100.0, fetch_mode="rows"),
        )

        assert result.exit_code == 4
        assert "fetch mode differs" in " ".join(result.output.split())

    def test_missing_result_file_exits_one(self, tmp_path: Path):
        result = _compare(_bundle(tmp_path / "a.json", 100.0), str(tmp_path / "absent.json"))

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_corrupt_result_file_exits_one(self, tmp_path: Path):
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{not json", encoding="utf-8")

        assert _compare(_bundle(tmp_path / "a.json", 100.0), str(corrupt)).exit_code == 1

    def test_usage_error_exits_two(self, tmp_path: Path):
        result = _compare(
            _bundle(tmp_path / "a.json", 100.0), _bundle(tmp_path / "b.json", 100.0), "--min-regression-delta", "5ms"
        )

        assert result.exit_code == 2


class TestPlatformRunMode:
    @pytest.fixture
    def runner_doubles(self, monkeypatch):
        manager = MagicMock()
        monkeypatch.setattr(cli_database, "DatabaseManager", lambda: manager)

        def execute_benchmark(*_args, **_kwargs):
            outcome = MagicMock()
            outcome.query_results = [
                {"query_id": "Q1", "status": "SUCCESS", "execution_time_ms": 10.0, "rows_returned": 1}
            ]
            return outcome

        orchestrator = MagicMock()
        orchestrator.execute_benchmark.side_effect = execute_benchmark
        monkeypatch.setattr(cli_orchestrator, "BenchmarkOrchestrator", lambda: orchestrator)
        monkeypatch.setattr("benchbox.cli.system.SystemProfiler", MagicMock)
        return manager

    def test_incomparable_timing_modes_exit_four(self, runner_doubles, monkeypatch):
        calls = []

        def differences(results):
            calls.append(len(results))
            return ["timing method differs (arrow vs rows)"] if len(results) > 1 else []

        monkeypatch.setattr("benchbox.core.results.comparison_modes.results_mode_differences", differences)

        result = _compare("-p", "duckdb", "-p", "sqlite", "--non-interactive")

        assert result.exit_code == 4, result.output
        assert "timing method differs" in " ".join(result.output.split())
        assert calls == [1, 2]

    def test_comparable_modes_exit_zero(self, runner_doubles, monkeypatch):
        monkeypatch.setattr("benchbox.core.results.comparison_modes.results_mode_differences", lambda results: [])

        result = _compare("-p", "duckdb", "-p", "sqlite", "--non-interactive")

        assert result.exit_code == 0, result.output

    def test_a_platform_that_fails_for_another_reason_exits_one(self, runner_doubles, monkeypatch):
        monkeypatch.setattr("benchbox.core.results.comparison_modes.results_mode_differences", lambda results: [])
        orchestrator = cli_orchestrator.BenchmarkOrchestrator()
        orchestrator.execute_benchmark.side_effect = RuntimeError("driver missing")

        result = _compare("-p", "duckdb", "-p", "sqlite", "--non-interactive")

        assert result.exit_code == 1


class TestExitCodeTable:
    def test_help_lists_every_compare_code(self):
        output = CliRunner().invoke(cli, ["compare", "--help"]).output

        for code in (ExitCode.SUCCESS, ExitCode.ERROR, ExitCode.USAGE, ExitCode.REGRESSION, ExitCode.NOT_COMPARABLE):
            assert EXIT_CODE_MEANINGS[code] in " ".join(output.split())

    def test_codes_are_distinct_and_clear_of_the_host_lock_range(self):
        values = [int(code) for code in ExitCode]

        assert len(values) == len(set(values))
        assert not any(value in RESERVED_BY_HOST_LOCK for value in values)
