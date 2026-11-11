from __future__ import annotations

import sys
import sys as _sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli

__import__("benchbox.cli.commands.run")
_run_module = _sys.modules["benchbox.cli.commands.run"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _mock_result() -> SimpleNamespace:
    return SimpleNamespace(
        validation_status="PASSED",
        execution_id="exec123",
        query_results=[],
        validation_details={},
    )


def test_direct_and_non_interactive_direct_use_shared_execution_and_export_helpers() -> None:
    runner = CliRunner()

    with (
        patch.object(_run_module, "DatabaseManager") as mock_db_mgr,
        patch.object(_run_module, "BenchmarkManager") as mock_bench_mgr,
        patch.object(_run_module, "SystemProfiler") as mock_profiler,
        patch.object(_run_module, "BenchmarkOrchestrator") as mock_orchestrator_cls,
        patch.object(_run_module, "_execute_orchestrated_run", return_value=_mock_result()) as mock_execute,
        patch.object(
            _run_module,
            "_export_orchestrated_result",
            return_value={"json": "/tmp/result.json"},
        ) as mock_export,
    ):
        mock_bench_mgr.return_value.benchmarks = {"tpch": {"display_name": "TPC-H", "estimated_time_range": (1, 2)}}
        mock_profiler.return_value.get_system_profile.return_value = Mock()
        mock_db_cfg = Mock()
        mock_db_cfg.type = "duckdb"
        mock_db_cfg.options = {}
        mock_db_mgr.return_value.create_config.return_value = mock_db_cfg
        mock_orchestrator_cls.return_value.directory_manager.results_dir = "/tmp/results"

        result_direct = runner.invoke(
            cli,
            ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01"],
        )
        assert result_direct.exit_code == 0

        result_non_interactive = runner.invoke(
            cli,
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--non-interactive",
            ],
        )
        assert result_non_interactive.exit_code == 0

        assert mock_execute.call_count == 2
        assert mock_export.call_count == 2


def test_load_only_uses_shared_execution_and_export_helpers() -> None:
    runner = CliRunner()

    with (
        patch.object(_run_module, "DatabaseManager") as mock_db_mgr,
        patch.object(_run_module, "BenchmarkManager") as mock_bench_mgr,
        patch.object(_run_module, "SystemProfiler") as mock_profiler,
        patch.object(_run_module, "BenchmarkOrchestrator") as mock_orchestrator_cls,
        patch.object(_run_module, "_execute_orchestrated_run", return_value=_mock_result()) as mock_execute,
        patch.object(
            _run_module,
            "_export_orchestrated_result",
            return_value={"json": "/tmp/result.json"},
        ) as mock_export,
    ):
        mock_bench_mgr.return_value.benchmarks = {"tpch": {"display_name": "TPC-H", "estimated_time_range": (1, 2)}}
        mock_profiler.return_value.get_system_profile.return_value = Mock()
        mock_db_cfg = Mock()
        mock_db_cfg.type = "duckdb"
        mock_db_cfg.options = {}
        mock_db_mgr.return_value.create_config.return_value = mock_db_cfg
        mock_orchestrator_cls.return_value.directory_manager.results_dir = "/tmp/results"

        result = runner.invoke(
            cli,
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--phases",
                "load",
            ],
        )
        assert result.exit_code == 0
        assert mock_execute.call_count == 1
        assert mock_export.call_count == 1


