# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
import sys as _sys
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli
from benchbox.core.schemas import DatabaseConfig

__import__("benchbox.cli.commands.run")
_run_module = _sys.modules["benchbox.cli.commands.run"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_database_config(driver_version_actual=None, driver_version_resolved=None):
    return DatabaseConfig(
        type="duckdb",
        name="DuckDB",
        driver_package="duckdb" if driver_version_actual or driver_version_resolved else None,
        driver_version_actual=driver_version_actual,
        driver_version_resolved=driver_version_resolved,
    )


def _make_mock_result():
    result = Mock()
    result.validation_status = "PASSED"
    result.execution_id = "test-exec-id"
    return result


def _invoke_run(platform="duckdb", database_config=None):
    if database_config is None:
        database_config = _make_database_config(driver_version_actual="1.4.3")

    mock_result = _make_mock_result()
    mock_orchestrator = MagicMock()
    mock_orchestrator.execute_benchmark.return_value = mock_result
    mock_orchestrator.directory_manager.get_result_path.return_value = "/tmp/test.json"
    mock_orchestrator.directory_manager.results_dir = "/tmp"

    mock_db_manager = MagicMock()
    mock_db_manager.create_config.return_value = database_config

    mock_bench_manager = MagicMock()
    mock_bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "class": Mock(),
            "description": "TPC-H",
        }
    }
    mock_bench_manager.validate_scale_factor = Mock()

    runner = CliRunner()
    with (
        patch.object(_run_module, "DatabaseManager", return_value=mock_db_manager),
        patch.object(_run_module, "BenchmarkManager", return_value=mock_bench_manager),
        patch.object(_run_module, "BenchmarkOrchestrator", return_value=mock_orchestrator),
        patch.object(_run_module, "SystemProfiler") as mock_profiler_cls,
        patch("benchbox.cli.main.get_config_manager") as mock_cfg,
        patch.object(_run_module, "_execute_orchestrated_run", return_value=mock_result),
        patch.object(_run_module, "_export_orchestrated_result", return_value={"json": "/tmp/t.json"}),
        patch.object(_run_module, "_render_post_run_charts"),
        patch("benchbox.cli.preferences.save_last_run_config"),
    ):
        mock_profiler_cls.return_value.get_system_profile.return_value = Mock()
        mock_cfg.return_value.get.return_value = None

        result = runner.invoke(
            cli,
            [
                "run",
                "--platform",
                platform,
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--non-interactive",
                "--phases",
                "power",
            ],
        )
    return result


@pytest.mark.unit
class TestRunDriverVersionAnnouncement:
    def test_driver_version_included_when_resolved(self):
        cfg = _make_database_config(driver_version_actual="1.4.3")
        result = _invoke_run(database_config=cfg)

        assert "Running tpch on duckdb at scale 0.01 [driver 1.4.3]" in result.output

    def test_driver_version_falls_back_to_resolved(self):
        cfg = _make_database_config(driver_version_resolved="1.3.0")
        result = _invoke_run(database_config=cfg)

        assert "[driver 1.3.0]" in result.output

    def test_no_driver_clause_when_version_unavailable(self):
        cfg = DatabaseConfig(type="duckdb", name="DuckDB", driver_package=None)
        result = _invoke_run(database_config=cfg)

        assert "[driver" not in result.output
        assert "Running tpch on duckdb at scale 0.01" in result.output

    def test_driver_version_actual_takes_precedence(self):
        cfg = _make_database_config(driver_version_actual="1.4.3", driver_version_resolved="1.3.0")
        result = _invoke_run(database_config=cfg)

        assert "[driver 1.4.3]" in result.output
        assert "[driver 1.3.0]" not in result.output

    def test_polars_platform_includes_driver_version(self):
        cfg = DatabaseConfig(type="polars", name="Polars", driver_package="polars", driver_version_actual="1.36.1")
        result = _invoke_run(platform="polars", database_config=cfg)

        assert "Running tpch on polars at scale 0.01 [driver 1.36.1]" in result.output


