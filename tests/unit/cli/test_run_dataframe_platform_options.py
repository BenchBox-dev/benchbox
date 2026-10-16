# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys as _sys
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli
from benchbox.cli.platform import normalize_platform_name
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.schemas import DatabaseConfig

__import__("benchbox.cli.commands.run")
_run_module = _sys.modules["benchbox.cli.commands.run"]

import benchbox.platforms

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_DATAFRAME_OPTION_CASES = [
    ("datafusion-df", "target_partitions", "4", 4),
    ("polars-df", "streaming", "true", True),
    ("pandas-df", "dtype_backend", "pyarrow", "pyarrow"),
    ("cudf-df", "device_id", "1", 1),
    ("dask-df", "n_workers", "8", 8),
    ("pyspark-df", "driver_memory", "8g", "8g"),
    ("pyspark-df", "shuffle_partitions", "8", 8),
]


class TestDataFrameOptionSpecKeying:
    @pytest.mark.parametrize("df_alias,option,raw,expected", _DATAFRAME_OPTION_CASES)
    def test_option_reachable_under_normalized_key(self, df_alias, option, raw, expected):
        base_key = normalize_platform_name(df_alias)
        assert base_key != df_alias

        parsed = PlatformHookRegistry.parse_options(base_key, [(option, raw)])
        assert parsed[option] == expected

        assert option in PlatformHookRegistry.list_option_specs(base_key)

    @pytest.mark.parametrize("df_alias,option,raw,expected", _DATAFRAME_OPTION_CASES)
    def test_option_not_orphaned_under_df_alias(self, df_alias, option, raw, expected):
        assert option not in PlatformHookRegistry.list_option_specs(df_alias)


def _datafusion_df_available() -> bool:
    try:
        from benchbox.platforms import list_available_dataframe_platforms

        avail = list_available_dataframe_platforms()
    except Exception:
        return False
    return bool(avail.get("datafusion-df") or avail.get("datafusion"))


@pytest.mark.skipif(not _datafusion_df_available(), reason="DataFusion DataFrame backend not installed")
class TestDataFrameOptionReachesConfig:
    def _invoke_run(self, platform, option_pairs):
        mock_db_manager = MagicMock()
        mock_db_manager.create_config.return_value = DatabaseConfig(type="datafusion", name="DataFusion")

        mock_result = Mock()
        mock_result.validation_status = "PASSED"
        mock_result.execution_id = "test-exec-id"

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute_benchmark.return_value = mock_result
        mock_orchestrator.directory_manager.get_result_path.return_value = "/tmp/test.json"
        mock_orchestrator.directory_manager.results_dir = "/tmp"

        mock_bench_manager = MagicMock()
        mock_bench_manager.benchmarks = {
            "tpch": {
                "display_name": "TPC-H",
                "class": Mock(),
                "description": "TPC-H",
                "estimated_time_range": (2, 10),
            }
        }
        mock_bench_manager.validate_scale_factor = Mock()

        args = [
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
        ]
        for name, value in option_pairs:
            args += ["--platform-option", f"{name}={value}"]

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
            mock_cfg.return_value.get.side_effect = lambda *a, **k: a[1] if len(a) > 1 else k.get("default")
            result = runner.invoke(cli, args)
        return result, mock_db_manager

    def test_datafusion_df_target_partitions_reaches_create_config(self):
        result, mock_db_manager = self._invoke_run("datafusion-df", [("target_partitions", "4")])

        assert "Unknown platform option" not in result.output
        assert result.exit_code == 0, result.output

        assert mock_db_manager.create_config.called
        call = mock_db_manager.create_config.call_args
        platform_key = call.args[0]
        options = call.args[1]
        assert platform_key == "datafusion"
        assert options.get("target_partitions") == 4
