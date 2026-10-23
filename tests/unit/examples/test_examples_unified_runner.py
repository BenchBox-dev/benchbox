# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


pytest.importorskip("pandas")


examples_dir = Path(__file__).parent.parent.parent.parent / "examples"
sys.path.insert(0, str(examples_dir))
import unified_runner

from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig


class TestUnifiedRunner:
    def test_benchmark_classes_available(self):

        available = unified_runner._get_benchmark_name_map()

        assert "tpch" in available
        assert "tpcds" in available

        optional = {
            "tpcdi",
            "ssb",
            "clickbench",
            "h2odb",
            "amplab",
            "joinorder",
            "read_primitives",
            "merge",
            "tpchavoc",
            "coffeeshop",
        }

        missing_optional = [name for name in optional if name not in available]
        assert set(missing_optional).issubset(optional)

    def test_extract_platform_handles_equals_style(self):

        with patch("unified_runner.sys.argv", ["unified_runner.py", "--platform=duckdb"]):
            assert unified_runner.extract_platform_from_argv() == "duckdb"

    def test_parser_does_not_require_platform_for_list_commands(self):

        with patch("unified_runner.sys.argv", ["unified_runner.py"]):
            parser = unified_runner.create_base_parser()

        args = parser.parse_args(["--list-platforms"])
        assert args.list_platforms is True
        assert args.platform is None
        assert args.benchmark is None

    @patch("benchbox.core.platform_registry.PlatformRegistry.get_platform_availability")
    def test_list_platforms_functionality(self, mock_get_availability):

        mock_get_availability.return_value = {
            "duckdb": True,
            "databricks": False,
            "clickhouse": True,
        }

        with patch("sys.argv", ["unified_runner.py", "--list-platforms"]):
            with patch("benchbox.core.results.display.emit") as mock_emit:
                result = unified_runner.main()
                assert result == 0
                assert any("duckdb" in str(call) for call in mock_emit.call_args_list)

    def test_list_benchmarks_functionality(self):

        with patch("sys.argv", ["unified_runner.py", "--list-benchmarks"]):
            with patch("benchbox.core.results.display.emit") as mock_emit:
                result = unified_runner.main()
                assert result == 0
                calls = [str(call) for call in mock_emit.call_args_list]
                assert any("tpch" in call for call in calls)
                assert any("tpcds" in call for call in calls)
                assert any("Available benchmarks" in call for call in calls)

    def test_auto_detect_databricks_config_no_sdk(self):

        from benchbox.platforms.databricks import DatabricksAdapter

        with patch("benchbox.platforms.databricks.DatabricksAdapter._auto_detect_databricks_config") as mock_detect:
            mock_detect.return_value = None
            result = DatabricksAdapter._auto_detect_databricks_config()
            assert result is None

    def test_auto_detect_databricks_config_with_sdk(self):

        from benchbox.platforms.databricks import DatabricksAdapter

        with patch("benchbox.platforms.databricks.DatabricksAdapter._auto_detect_databricks_config") as mock_detect:
            mock_detect.return_value = {
                "server_hostname": "test.cloud.databricks.com",
                "http_path": "/sql/1.0/warehouses/test",
                "access_token": "test_token",
            }
            result = DatabricksAdapter._auto_detect_databricks_config()
            assert result is not None
            assert "server_hostname" in result

    def test_get_platform_adapter_config_duckdb(self):

        args = Namespace(
            benchmark="tpch",
            scale=0.01,
            output=None,
            force=False,
            duckdb_database_path=None,
            memory_limit="4GB",
        )

        with patch("benchbox.utils.scale_factor.format_benchmark_name") as mock_format:
            with patch("benchbox.utils.database_naming.generate_database_filename") as mock_generate:
                mock_format.return_value = "tpch_0.01"
                mock_generate.return_value = "tpch_0.01.duckdb"

                config = unified_runner.get_platform_adapter_config("duckdb", args)

                assert "database_path" in config
                assert "memory_limit" in config
                assert "force_recreate" in config
                assert config["memory_limit"] == "4GB"
                assert not config["force_recreate"]

    def test_get_platform_adapter_config_databricks(self):

        args = Namespace(
            server_hostname="test.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
            catalog="workspace",
            schema="benchbox",
            verbose=0,
            platform_config=None,
        )

        config = unified_runner.get_platform_adapter_config(
            "databricks", args, None, benchmark_name="tpch", scale_factor=1.0
        )

        assert config["server_hostname"] == "test.databricks.com"
        assert config["http_path"] == "/sql/1.0/warehouses/test"
        assert config["access_token"] == "test_token"
        assert config["catalog"] == "workspace"
        assert "tpch" in config["schema"]

    def test_get_platform_adapter_config_clickhouse(self):

        args = Namespace(
            mode="local",
            data_path="/custom/path",
            verbose=0,
            platform_config=None,
        )

        config = unified_runner.get_platform_adapter_config("clickhouse", args, None)

        assert config["mode"] == "local"
        assert config["data_path"] == "/custom/path"

    def test_get_benchmark_config(self):

        args = Namespace(
            scale=1.0,
            verbose=2,
            force=True,
            compress=True,
            benchmark="tpch",
            output=None,
        )

        with patch("benchbox.utils.scale_factor.format_benchmark_name") as mock_format:
            mock_format.return_value = "tpch_1.0"

            config = unified_runner.get_benchmark_config(args, "duckdb")

            assert config["scale_factor"] == 1.0
            assert config["verbose"]
            assert config["force_regenerate"]
            assert config["compress_data"]
            assert config["compression_type"] == "zstd"
            assert "output_dir" in config

    def test_console_summary_data(self):

        results = MagicMock()
        results.successful_queries = 22
        results.total_queries = 22
        results.duration_seconds = 120.5
        results.total_execution_time = 100.0
        results.average_query_time = 5.5

        cfg = {"benchmark": "tpch", "scale_factor": 0.1, "platform": "duckdb"}

        result_data = unified_runner._make_console_summary(results, cfg)

        assert result_data["benchmark"] == "tpch"
        assert result_data["scale_factor"] == 0.1
        assert result_data["platform"] == "Duckdb"
        assert result_data["success"] is True
        assert result_data["successful_queries"] == 22
        assert result_data["total_queries"] == 22
        assert result_data["total_duration"] == 120.5
        assert result_data["average_query_time"] == 5.5

    def test_display_results(self, capsys):

        result_data = {
            "benchmark": "tpch",
            "scale_factor": 0.1,
            "platform": "DuckDB",
            "success": True,
            "successful_queries": 22,
            "total_queries": 22,
            "total_duration": 120.5,
            "total_execution_time": 100.0,
            "average_query_time": 4.5,
        }

        from benchbox.core.results.display import display_results

        display_results(result_data, verbosity=1)

        captured = capsys.readouterr()
        assert "Benchmark: TPCH" in captured.out
        assert "Scale Factor: 0.1" in captured.out
        assert "Platform: DuckDB" in captured.out
        assert "PASSED" in captured.out
        assert "22/22 successful" in captured.out
        assert "✅ TPCH benchmark completed!" in captured.out

    def test_create_platform_specific_args_duckdb(self):

        import argparse

        from benchbox.core.platform_registry import PlatformRegistry

        parser = argparse.ArgumentParser()
        PlatformRegistry.add_platform_arguments(parser, "duckdb")

        args = parser.parse_args(["--duckdb-database-path", "/test/path", "--memory-limit", "8GB"])
        assert args.duckdb_database_path == "/test/path"
        assert args.memory_limit == "8GB"

    @patch("unified_runner.PlatformRegistry.create_adapter")
    @patch("unified_runner.list_available_platforms")
    def test_main_dry_run_mode(self, mock_list_platforms, mock_create_adapter, tmp_path):

        mock_list_platforms.return_value = {"duckdb": True}

        dry_run_dir = tmp_path / "dry"

        test_args = [
            "unified_runner.py",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--scale",
            "0.001",
            "--dry-run",
            str(dry_run_dir),
        ]

        with patch("sys.argv", test_args), patch("unified_runner.execute_example_dry_run") as mock_execute:
            with patch("unified_runner.SystemProfiler") as mock_profiler:
                mock_profiler.return_value.get_system_profile.return_value = MagicMock()

                mock_execute.return_value = (MagicMock(), {})

                result = unified_runner.main()

        assert result == 0
        mock_execute.assert_called_once()
        args, kwargs = mock_execute.call_args
        assert kwargs["output_dir"] == dry_run_dir
        assert kwargs["benchmark_config"].name == "tpch"
        assert kwargs["database_config"].type == "duckdb"

    @patch("unified_runner.list_available_platforms")
    def test_main_unavailable_platform(self, mock_list_platforms):

        mock_list_platforms.return_value = {"duckdb": False}

        test_args = ["unified_runner.py", "--platform", "duckdb", "--benchmark", "tpch"]

        with patch("sys.argv", test_args), patch("builtins.print"):
            result = unified_runner.main()
            assert result == 1

    @patch("unified_runner.list_available_platforms")
    def test_quiet_mode_restores_state(self, mock_list_platforms, tmp_path):

        from benchbox.utils.printing import is_quiet, set_quiet

        mock_list_platforms.return_value = {"duckdb": True}
        preview_dir = tmp_path / "preview"

        previous_state = is_quiet()
        set_quiet(False)
        try:
            with (
                patch(
                    "unified_runner.sys.argv",
                    [
                        "unified_runner.py",
                        "--platform",
                        "duckdb",
                        "--benchmark",
                        "tpch",
                        "--dry-run",
                        str(preview_dir),
                        "--quiet",
                    ],
                ),
                patch("unified_runner.execute_example_dry_run") as mock_execute,
            ):
                mock_execute.return_value = (MagicMock(warnings=[]), {})
                with patch("unified_runner.SystemProfiler") as mock_profiler:
                    mock_profiler.return_value.get_system_profile.return_value = MagicMock()
                    result = unified_runner.main()
        finally:
            set_quiet(previous_state)

        assert result == 0
        assert is_quiet() == previous_state

    def test_phase_validation(self):

        valid_phases = [
            "generate",
            "load",
            "warmup",
            "power",
            "throughput",
            "maintenance",
        ]

        for phase in valid_phases:
            user_phases = [p.strip() for p in phase.split(",") if p.strip()]
            assert all(p in valid_phases for p in user_phases)

        invalid_phases = ["invalid", "test", "wrong"]
        for phase in invalid_phases:
            user_phases = [p.strip() for p in phase.split(",") if p.strip()]
            assert any(p not in valid_phases for p in user_phases)

    def test_determine_test_execution_type_handles_data_only(self):

        assert unified_runner._determine_test_execution_type(["generate"]) == "data_only"
        assert unified_runner._determine_test_execution_type(["load"]) == "load_only"
        assert unified_runner._determine_test_execution_type(["generate", "load"]) == "load_only"

    @patch("unified_runner.ResultExporter.export_result", return_value={})
    @patch("unified_runner.SystemProfiler")
    @patch("unified_runner.run_benchmark_lifecycle")
    @patch("unified_runner.PlatformRegistry.create_adapter")
    @patch("unified_runner.get_platform_adapter_config")
    @patch("unified_runner.merge_all_configs")
    @patch("unified_runner.list_available_platforms")
    def test_main_invokes_lifecycle(
        self,
        mock_list_platforms,
        mock_merge_configs,
        mock_platform_config,
        mock_create_adapter,
        mock_run_lifecycle,
        mock_profiler,
        mock_export_result,
    ):

        mock_list_platforms.return_value = {"duckdb": True}
        mock_merge_configs.return_value = {
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale_factor": 0.1,
            "streams": 2,
            "compress": False,
            "force": False,
            "display_name": "TPC-H",
            "compression_type": "zstd",
        }
        mock_platform_config.return_value = {"database_path": "/tmp/test.duckdb"}

        adapter = MagicMock()
        adapter.platform_config = {"database_path": "/tmp/test.duckdb"}
        mock_create_adapter.return_value = adapter

        lifecycle_result = MagicMock()
        lifecycle_result.total_queries = 5
        lifecycle_result.successful_queries = 5
        lifecycle_result.failed_queries = 0
        lifecycle_result.validation_status = "PASSED"
        lifecycle_result.total_duration = 1.0
        lifecycle_result.total_execution_time = 1.0
        lifecycle_result.average_query_time = 0.2
        lifecycle_result.data_loading_time = 0.4
        lifecycle_result.schema_creation_time = 0.1
        lifecycle_result.data_size_mb = 64.0
        mock_run_lifecycle.return_value = lifecycle_result

        profiler_instance = MagicMock()
        profiler_instance.get_system_profile.return_value = MagicMock()
        mock_profiler.return_value = profiler_instance

        with patch(
            "sys.argv",
            [
                "unified_runner.py",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--phases",
                "power",
            ],
        ):
            exit_code = unified_runner.main()

        assert exit_code == 0
        mock_run_lifecycle.assert_called_once()
        mock_export_result.assert_called_once()

        call_kwargs = mock_run_lifecycle.call_args.kwargs
        assert isinstance(call_kwargs["benchmark_config"], BenchmarkConfig)
        assert isinstance(call_kwargs["database_config"], DatabaseConfig)
        assert call_kwargs["platform_adapter"] is adapter
        assert call_kwargs["platform_config"] == mock_platform_config.return_value
        assert call_kwargs["phases"].execute is True
        assert call_kwargs["phases"].load is True
        assert call_kwargs["phases"].generate is False
        assert call_kwargs["validation_opts"].enable_postload_validation is False
        assert call_kwargs["output_root"] is not None

    @patch("unified_runner.ResultExporter.export_result", return_value={})
    @patch("unified_runner.SystemProfiler")
    @patch("unified_runner.run_benchmark_lifecycle")
    @patch("unified_runner.PlatformRegistry.create_adapter")
    @patch("unified_runner.get_platform_adapter_config")
    @patch("unified_runner.merge_all_configs")
    @patch("unified_runner.list_available_platforms")
    def test_multi_phase_runs_all_query_phases(
        self,
        mock_list_platforms,
        mock_merge_configs,
        mock_platform_config,
        mock_create_adapter,
        mock_run_lifecycle,
        mock_profiler,
        mock_export_result,
    ):

        mock_list_platforms.return_value = {"duckdb": True}
        mock_merge_configs.return_value = {
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale_factor": 0.1,
            "streams": 3,
            "compress": False,
            "force": False,
            "display_name": "TPC-H",
            "compression_type": "zstd",
        }
        mock_platform_config.return_value = {"database_path": "/tmp/test.duckdb"}

        adapter = MagicMock()
        adapter.platform_config = {"database_path": "/tmp/test.duckdb"}
        mock_create_adapter.return_value = adapter

        profiler_instance = MagicMock()
        profiler_instance.get_system_profile.return_value = MagicMock()
        mock_profiler.return_value = profiler_instance

        power_result = MagicMock()
        power_result.total_queries = 5
        power_result.successful_queries = 5
        power_result.failed_queries = 0
        power_result.validation_status = "PASSED"
        power_result.total_duration = 1.0
        power_result.total_execution_time = 1.0
        power_result.average_query_time = 0.2

        throughput_result = MagicMock()
        throughput_result.total_queries = 10
        throughput_result.successful_queries = 10
        throughput_result.failed_queries = 0
        throughput_result.validation_status = "PASSED"
        throughput_result.total_duration = 2.0
        throughput_result.total_execution_time = 2.0
        throughput_result.average_query_time = 0.2

        mock_run_lifecycle.side_effect = [power_result, throughput_result]

        with patch(
            "sys.argv",
            [
                "unified_runner.py",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--phases",
                "power,throughput",
            ],
        ):
            exit_code = unified_runner.main()

        assert exit_code == 0
        assert mock_run_lifecycle.call_count == 2
        first_call, second_call = mock_run_lifecycle.call_args_list

        assert first_call.kwargs["benchmark_config"].test_execution_type == "power"
        assert second_call.kwargs["benchmark_config"].test_execution_type == "throughput"
        assert first_call.kwargs["phases"].execute is True
        assert second_call.kwargs["phases"].execute is True
        assert mock_export_result.call_count == 2

    @patch("unified_runner.ResultExporter.export_result", return_value={})
    @patch("unified_runner.SystemProfiler")
    @patch("unified_runner.run_benchmark_lifecycle")
    @patch("unified_runner.PlatformRegistry.create_adapter")
    @patch("unified_runner.get_platform_adapter_config", return_value={})
    @patch("unified_runner.merge_all_configs")
    @patch("unified_runner.list_available_platforms", return_value={"duckdb": True})
    def test_data_only_mode_runs_generate_phase(
        self,
        mock_list_platforms,
        mock_merge_configs,
        mock_platform_config,
        mock_create_adapter,
        mock_run_lifecycle,
        mock_profiler,
        mock_export_result,
    ):

        mock_merge_configs.return_value = {
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "streams": 1,
            "display_name": "TPC-H",
        }

        profiler_instance = MagicMock()
        profiler_instance.get_system_profile.return_value = MagicMock()
        mock_profiler.return_value = profiler_instance

        data_only_result = MagicMock()
        data_only_result.total_queries = 0
        data_only_result.failed_queries = 0
        data_only_result.successful_queries = 0
        data_only_result.validation_status = "NOT_VALIDATED"
        data_only_result.total_duration = 0.0
        data_only_result.total_execution_time = 0.0
        data_only_result.average_query_time = 0.0
        mock_run_lifecycle.return_value = data_only_result

        with patch(
            "sys.argv",
            [
                "unified_runner.py",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--phases",
                "generate",
            ],
        ):
            exit_code = unified_runner.main()

        assert exit_code == 0
        mock_create_adapter.assert_not_called()
        mock_run_lifecycle.assert_called_once()
        assert mock_run_lifecycle.call_args.kwargs["benchmark_config"].test_execution_type == "data_only"
        assert mock_run_lifecycle.call_args.kwargs["database_config"] is None
        assert mock_export_result.call_count == 1

    @patch("unified_runner.ResultExporter.export_result", return_value={})
    @patch("unified_runner.SystemProfiler")
    @patch("unified_runner.run_benchmark_lifecycle")
    @patch("unified_runner.PlatformRegistry.create_adapter")
    @patch("unified_runner.get_platform_adapter_config")
    @patch("unified_runner.merge_all_configs")
    @patch("unified_runner.list_available_platforms", return_value={"duckdb": True})
    def test_load_only_mode_runs_phase(
        self,
        mock_list_platforms,
        mock_merge_configs,
        mock_platform_config,
        mock_create_adapter,
        mock_run_lifecycle,
        mock_profiler,
        mock_export_result,
    ):

        mock_merge_configs.return_value = {
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "streams": 1,
            "display_name": "TPC-H",
        }
        mock_platform_config.return_value = {"database_path": "/tmp/test.duckdb"}

        adapter = MagicMock()
        adapter.platform_config = {"database_path": "/tmp/test.duckdb"}
        mock_create_adapter.return_value = adapter

        profiler_instance = MagicMock()
        profiler_instance.get_system_profile.return_value = MagicMock()
        mock_profiler.return_value = profiler_instance

        load_result = MagicMock()
        load_result.total_queries = 0
        load_result.failed_queries = 0
        load_result.successful_queries = 0
        load_result.validation_status = "PASSED"
        load_result.total_duration = 0.0
        load_result.total_execution_time = 0.0
        load_result.average_query_time = 0.0
        mock_run_lifecycle.return_value = load_result

        with patch(
            "sys.argv",
            [
                "unified_runner.py",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--phases",
                "load",
            ],
        ):
            exit_code = unified_runner.main()

        assert exit_code == 0
        mock_create_adapter.assert_called_once()
        mock_run_lifecycle.assert_called_once()
        assert mock_run_lifecycle.call_args.kwargs["benchmark_config"].test_execution_type == "load_only"
        assert mock_run_lifecycle.call_args.kwargs["database_config"] is not None
        assert mock_export_result.call_count == 1

    @patch("unified_runner.ResultExporter.export_result", return_value={})
    @patch("unified_runner.SystemProfiler")
    @patch("unified_runner.run_benchmark_lifecycle")
    @patch("unified_runner.PlatformRegistry.create_adapter")
    @patch("unified_runner.get_platform_adapter_config")
    @patch("unified_runner.merge_all_configs")
    @patch("unified_runner.list_available_platforms", return_value={"duckdb": True})
    def test_validation_failure_sets_nonzero_exit(
        self,
        mock_list_platforms,
        mock_merge_configs,
        mock_platform_config,
        mock_create_adapter,
        mock_run_lifecycle,
        mock_profiler,
        mock_export_result,
    ):

        mock_merge_configs.return_value = {
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale_factor": 0.1,
            "streams": 1,
            "display_name": "TPC-H",
        }
        mock_platform_config.return_value = {"database_path": "/tmp/test.duckdb"}
        adapter = MagicMock()
        adapter.platform_config = {"database_path": "/tmp/test.duckdb"}
        mock_create_adapter.return_value = adapter

        profiler_instance = MagicMock()
        profiler_instance.get_system_profile.return_value = MagicMock()
        mock_profiler.return_value = profiler_instance

        failing_result = MagicMock()
        failing_result.total_queries = 5
        failing_result.successful_queries = 5
        failing_result.failed_queries = 0
        failing_result.validation_status = "VALIDATION_FAILED"
        failing_result.total_duration = 1.0
        failing_result.total_execution_time = 1.0
        failing_result.average_query_time = 0.2
        mock_run_lifecycle.return_value = failing_result

        with patch(
            "sys.argv",
            [
                "unified_runner.py",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--phases",
                "power",
            ],
        ):
            exit_code = unified_runner.main()

        assert exit_code == 1
        mock_run_lifecycle.assert_called_once()
        assert mock_run_lifecycle.call_args.kwargs["benchmark_config"].test_execution_type == "power"
        assert mock_export_result.call_count == 1

    def test_empty_phases_produces_error(self):

        with (
            patch("unified_runner.list_available_platforms", return_value={"duckdb": True}),
            patch(
                "sys.argv",
                [
                    "unified_runner.py",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--phases",
                    "",
                ],
            ),
            pytest.raises(SystemExit) as excinfo,
        ):
            unified_runner.main()

        assert excinfo.value.code != 0


@pytest.mark.integration
class TestUnifiedRunnerIntegration:
    def test_unified_runner_help(self):

        script_path = examples_dir / "unified_runner.py"
        result = subprocess.run([sys.executable, str(script_path), "--help"], capture_output=True, text=True)

        assert result.returncode == 0
        assert "Unified Multi-Platform Benchmark Runner" in result.stdout
        assert "--platform" in result.stdout
        assert "--benchmark" in result.stdout

    def test_unified_runner_list_platforms(self):

        script_path = examples_dir / "unified_runner.py"
        result = subprocess.run(
            [sys.executable, str(script_path), "--list-platforms"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Available platforms:" in result.stdout
        assert "duckdb:" in result.stdout
