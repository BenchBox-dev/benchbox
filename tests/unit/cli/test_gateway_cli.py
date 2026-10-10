import sys
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner, Result

from benchbox.cli.database import DatabaseManager
from benchbox.cli.main import cli
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.platform_config import get_platform_config
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.schemas import DatabaseConfig

__import__("benchbox.cli.commands.run")
_run_module = sys.modules["benchbox.cli.commands.run"]

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _run_cli(args: list[str], database_manager: MagicMock) -> Result:
    benchmark_manager = MagicMock()
    benchmark_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "class": Mock(),
            "description": "TPC-H",
            "estimated_time_range": (2, 10),
        }
    }
    benchmark_manager.validate_scale_factor = Mock()

    runner = CliRunner()
    with (
        patch.object(_run_module, "DatabaseManager", return_value=database_manager),
        patch.object(_run_module, "BenchmarkManager", return_value=benchmark_manager),
        patch.object(_run_module, "SystemProfiler") as profiler,
        patch("benchbox.cli.dryrun.DryRunExecutor") as dry_run_executor,
        patch("benchbox.cli.main.get_config_manager") as config_manager,
    ):
        profiler.return_value.get_system_profile.return_value = Mock()
        dry_run_executor.return_value.save_dry_run_results.return_value = {}
        config_manager.return_value.get.side_effect = lambda *a, **k: a[1] if len(a) > 1 else k.get("default")
        return runner.invoke(cli, [*args, "--dry-run", "/tmp/benchbox-gateway-cli-test"])


def test_gateway_cli_value_reaches_adapter_config() -> None:
    database_manager = MagicMock()
    database_manager.create_config.return_value = DatabaseConfig(type="snowflake", name="Snowflake")

    result = _run_cli(
        [
            "run",
            "--platform",
            "snowflake",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--phases",
            "power",
            "--non-interactive",
            "--gateway",
            "espresso",
        ],
        database_manager,
    )

    assert result.exit_code == 0, result.output
    assert database_manager.create_config.call_args.kwargs["gateway_selector"] == "espresso"


def test_gateway_cli_validation_fails_before_database_work() -> None:
    database_manager = MagicMock()

    result = _run_cli(
        [
            "run",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--phases",
            "power",
            "--non-interactive",
            "--gateway",
            "espresso",
        ],
        database_manager,
    )

    assert result.exit_code == 1
    assert "Allowed: native" in result.output
    database_manager.create_config.assert_not_called()


def test_custom_gateway_without_host_fails_before_database_work() -> None:
    database_manager = MagicMock()

    result = _run_cli(
        [
            "run",
            "--platform",
            "snowflake",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--phases",
            "power",
            "--non-interactive",
            "--gateway",
            "custom",
        ],
        database_manager,
    )

    assert result.exit_code == 1
    assert "requires gateway_host" in result.output
    database_manager.create_config.assert_not_called()


def test_database_config_projects_gateway_to_adapter_config() -> None:
    config = DatabaseConfig(type="snowflake", name="Snowflake")
    with (
        patch.object(PlatformHookRegistry, "get_default_options", return_value={}),
        patch.object(PlatformHookRegistry, "build_database_config", return_value=config),
        patch.object(PlatformRegistry, "get_platform_info", return_value=None),
    ):
        configured = DatabaseManager().create_config("snowflake", gateway_selector="espresso")

    assert configured.gateway == "espresso"
    assert get_platform_config(configured, None)["gateway"] == "espresso"
