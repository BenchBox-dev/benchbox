from __future__ import annotations

import importlib.machinery
import importlib.util
import sys as _sys
from functools import wraps
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.utilities.owned_cli_state import owned_cli_state

__import__("benchbox.cli.commands.run")
_run_module = _sys.modules["benchbox.cli.commands.run"]


@pytest.fixture(autouse=True)
def _unit_home(_hermetic_state, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path_factory.mktemp("unit-home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("BENCHBOX_HOST_LOCK", raising=False)
    if _sys.platform == "win32":
        roaming = home / "AppData" / "Roaming"
        local = home / "AppData" / "Local"
        roaming.mkdir(parents=True)
        local.mkdir(parents=True)
        monkeypatch.setenv("APPDATA", str(roaming))
        monkeypatch.setenv("LOCALAPPDATA", str(local))
    return home


@pytest.fixture(autouse=True)
def _no_cloud_metadata_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.platforms import athena
    from benchbox.platforms.base import client_region

    def imds_unreachable(*args, **kwargs):
        raise OSError("cloud metadata service is not reachable from unit tests")

    monkeypatch.setattr(client_region, "_imds_open", imds_unreachable)
    monkeypatch.setattr(athena.AthenaAdapter, "_check_instance_metadata_available", lambda self: False)


@pytest.fixture
def chdb_probe_satisfied(monkeypatch):
    real_find_spec = importlib.util.find_spec

    def _find_spec(name, *args, **kwargs):
        if name == "chdb":
            return importlib.machinery.ModuleSpec("chdb", loader=None)
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)


@pytest.fixture(autouse=True)
def _owned_cli_invocations(_hermetic_state, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    invoke = CliRunner.invoke

    @wraps(invoke)
    def invoke_owned(*args, **kwargs):
        with owned_cli_state():
            return invoke(*args, **kwargs)

    monkeypatch.setattr(CliRunner, "invoke", invoke_owned)


@pytest.fixture
def cli_benchmark_mocks():
    with (
        patch("benchbox.cli.main.BenchmarkManager") as mock_main_manager,
        patch("benchbox.cli.main.DatabaseManager") as mock_main_db_manager,
        patch("benchbox.cli.main.SystemProfiler") as mock_main_profiler,
        patch("benchbox.cli.main.ConfigManager") as mock_config,
        patch("benchbox.cli.main.get_config_manager") as mock_get_config_manager,
        patch("benchbox.cli.orchestrator.BenchmarkOrchestrator") as mock_orchestrator,
        patch.object(_run_module, "BenchmarkManager") as mock_run_manager,
        patch.object(_run_module, "DatabaseManager") as mock_run_db_manager,
        patch.object(_run_module, "SystemProfiler") as mock_run_profiler,
        patch.object(_run_module, "BenchmarkOrchestrator") as mock_run_orchestrator,
        patch.object(_run_module, "_execute_orchestrated_run") as mock_execute_orchestrated_run,
        patch.object(_run_module, "_export_orchestrated_result") as mock_export_orchestrated_result,
        patch.object(_run_module, "_render_post_run_charts"),
        patch("benchbox.cli.preferences.save_last_run_config"),
    ):
        mock_manager_instance = type("MockBenchmarkManager", (), {})()
        mock_manager_instance.benchmarks = {
            "tpch": {"display_name": "TPC-H", "estimated_time_range": (2, 10)},
            "tpcds": {"display_name": "TPC-DS", "estimated_time_range": (5, 30)},
        }
        mock_manager_instance.set_verbosity = lambda *_args, **_kwargs: None
        mock_manager_instance.validate_scale_factor = lambda *_args, **_kwargs: None
        mock_main_manager.return_value = mock_manager_instance
        mock_run_manager.return_value = mock_manager_instance

        mock_db_manager_instance = type("MockDatabaseManager", (), {})()
        mock_db_manager_instance.set_verbosity = lambda *_args, **_kwargs: None
        mock_db_config = type(
            "MockDbConfig",
            (),
            {
                "type": "duckdb",
                "options": {},
                "driver_version_actual": None,
                "driver_version_resolved": None,
            },
        )()
        mock_db_manager_instance.create_config = lambda *_args, **_kwargs: mock_db_config
        mock_main_db_manager.return_value = mock_db_manager_instance
        mock_run_db_manager.return_value = mock_db_manager_instance

        mock_system_profile = type(
            "MockSystemProfile",
            (),
            {"cpu_cores_logical": 4, "memory_total_gb": 8},
        )()
        mock_profiler_instance = type("MockProfiler", (), {})()
        mock_profiler_instance.get_system_profile = lambda: mock_system_profile
        mock_main_profiler.return_value = mock_profiler_instance
        mock_run_profiler.return_value = mock_profiler_instance

        mock_config_instance = type("MockConfigManager", (), {})()
        mock_config_instance.config_path = Path("benchbox.yaml")
        mock_config_instance.validate_config = lambda: True
        mock_config_instance.load_unified_tuning_config = lambda *_args, **_kwargs: None
        mock_config_instance.get = lambda _key, default=None: default
        mock_config.return_value = mock_config_instance
        mock_get_config_manager.return_value = mock_config_instance

        mock_orchestrator_instance = type("MockOrchestrator", (), {})()
        mock_orchestrator_instance.set_verbosity = lambda *_args, **_kwargs: None
        mock_orchestrator_instance.set_custom_output_dir = lambda *_args, **_kwargs: None
        mock_orchestrator.return_value = mock_orchestrator_instance
        mock_run_orchestrator.return_value = mock_orchestrator_instance

        mock_result = type(
            "MockResult",
            (),
            {"validation_status": "PASSED", "execution_id": "mock-exec-id", "query_results": []},
        )()
        mock_execute_orchestrated_run.return_value = mock_result
        mock_export_orchestrated_result.return_value = {"json": "benchmark_runs/results/mock-exec-id.json"}

        yield {
            "manager": mock_run_manager,
            "db_manager": mock_run_db_manager,
            "profiler": mock_run_profiler,
            "config": mock_config,
            "orchestrator": mock_run_orchestrator,
        }


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()
