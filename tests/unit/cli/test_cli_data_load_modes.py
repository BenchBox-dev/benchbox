# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
import sys as _sys
from contextlib import ExitStack, contextmanager
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.benchmarks import BenchmarkConfig
from benchbox.cli.main import cli

__import__("benchbox.cli.commands.run")
_run_module = _sys.modules["benchbox.cli.commands.run"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@contextmanager
def _mock_run_command_components(*, include_db_manager: bool = False):
    with ExitStack() as stack:
        mocks = {
            "bench_mgr": stack.enter_context(patch.object(_run_module, "BenchmarkManager")),
            "profiler": stack.enter_context(patch.object(_run_module, "SystemProfiler")),
            "orchestrator": stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator")),
            "exporter": stack.enter_context(patch.object(_run_module, "ResultExporter")),
            "db_mgr": None,
        }
        if include_db_manager:
            mocks["db_mgr"] = stack.enter_context(patch.object(_run_module, "DatabaseManager"))

        mocks["bench_mgr"].return_value.benchmarks = {
            "tpch": {"display_name": "TPC-H", "estimated_time_range": (2, 10)}
        }
        mocks["profiler"].return_value.get_system_profile.return_value = Mock()

        mock_result = Mock()
        mock_result.validation_status = "PASSED"
        mock_result.validation_details = {"stages": []}
        mocks["orchestrator"].return_value.execute_benchmark.return_value = mock_result
        mocks["exporter"].return_value.export_result.return_value = {"json": "/tmp/result.json"}

        yield mocks


@contextmanager
def _mock_quick_restart(saved_config):
    mock_sys = Mock()
    mock_sys.stdin.isatty.return_value = True
    mock_sys.stdout.isatty.return_value = True

    with (
        _mock_run_command_components(include_db_manager=True) as mocks,
        patch.object(_run_module, "sys", mock_sys),
        patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False),
        patch("benchbox.cli.preferences.load_last_run_config", return_value=saved_config),
        patch("benchbox.cli.preferences.format_last_run_summary", return_value="saved run"),
        patch.object(_run_module.Confirm, "ask", side_effect=lambda prompt, default=False: True),
        patch("benchbox.cli.preferences.save_last_run_config") as save_last_run,
    ):
        profile = Mock(
            cpu_cores_logical=8,
            cpu_cores_physical=4,
            memory_total_gb=16,
            is_apple_silicon=False,
            architecture="x86_64",
            os_type="darwin",
            numa_nodes=None,
        )
        mocks["profiler"].return_value.get_system_profile.return_value = profile
        database_config = Mock(
            type="duckdb",
            options={},
            driver_version_actual="1.4.3",
            driver_version_resolved="1.4.3",
            execution_mode="sql",
        )
        assert mocks["db_mgr"] is not None
        mocks["db_mgr"].return_value.create_config.return_value = database_config
        mocks["save_last_run"] = save_last_run
        yield mocks


class TestCLIDataLoadModes:
    def setup_method(self):
        self.runner = CliRunner()

    def test_data_only_no_database_required(self):

        with _mock_run_command_components() as mocks:
            result = self.runner.invoke(cli, ["run", "--benchmark", "tpch", "--scale", "0.01", "--phases", "generate"])
            assert result.exit_code == 0
            assert "Data generation completed" in result.output or "Benchmark completed" in result.output

            mocks["orchestrator"].return_value.execute_benchmark.assert_called_once()
            call_args = mocks["orchestrator"].return_value.execute_benchmark.call_args[0]
            assert call_args[2] is None

    def test_data_only_ignores_database_parameter(self):

        with _mock_run_command_components():
            result = self.runner.invoke(
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
                    "generate",
                ],
            )
            assert result.exit_code == 0
            assert "Data generation completed" in result.output or "Benchmark completed" in result.output

    def test_load_only_requires_database(self):

        result = self.runner.invoke(cli, ["run", "--benchmark", "tpch", "--scale", "0.01", "--phases", "load"])
        assert result.exit_code != 0
        assert "Error: --platform parameter is required for --phases load" in result.output

    def test_load_only_with_database(self):

        with _mock_run_command_components(include_db_manager=True) as mocks:
            mock_db_config = Mock()
            mock_db_config.type = "duckdb"
            mock_db_config.options = {}
            assert mocks["db_mgr"] is not None
            mocks["db_mgr"].return_value.create_config.return_value = mock_db_config

            result = self.runner.invoke(
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
            assert "Data loading completed" in result.output or "Benchmark completed" in result.output

            mocks["db_mgr"].return_value.create_config.assert_called_once()

            mocks["orchestrator"].return_value.execute_benchmark.assert_called_once()
            call_args = mocks["orchestrator"].return_value.execute_benchmark.call_args[0]
            assert call_args[2] is not None

    def test_load_only_failure_exits_non_zero(self):
        with _mock_run_command_components(include_db_manager=True) as mocks:
            mock_db_config = Mock()
            mock_db_config.type = "duckdb"
            mock_db_config.options = {}
            assert mocks["db_mgr"] is not None
            mocks["db_mgr"].return_value.create_config.return_value = mock_db_config
            failed_result = mocks["orchestrator"].return_value.execute_benchmark.return_value
            failed_result.validation_status = "FAILED"
            failed_result.validation_details = {"error": "load failed"}

            result = self.runner.invoke(
                cli,
                ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--phases", "generate,load"],
            )

            assert result.exit_code == 1
            assert "Data loading failed: FAILED" in result.output
            mocks["exporter"].return_value.export_result.assert_not_called()

    def test_benchmark_config_test_execution_type(self):

        with _mock_run_command_components() as mocks:
            result = self.runner.invoke(cli, ["run", "--benchmark", "tpch", "--scale", "0.01", "--phases", "generate"])
            assert result.exit_code == 0
            assert "Data generation completed: COMPLETED" in result.output

            mocks["orchestrator"].return_value.execute_benchmark.assert_called_once()
            phases = mocks["orchestrator"].return_value.execute_benchmark.call_args[0][3]
            assert phases == ["generate"]

    def test_force_flag(self):
        with _mock_run_command_components(include_db_manager=True) as mocks:
            mock_db_cfg = Mock()
            mock_db_cfg.type = "duckdb"
            mock_db_cfg.options = {}
            assert mocks["db_mgr"] is not None
            mocks["db_mgr"].return_value.create_config.return_value = mock_db_cfg

            result = self.runner.invoke(
                cli,
                [
                    "run",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--scale",
                    "0.01",
                    "--force",
                    "datagen",
                ],
            )
            assert result.exit_code == 0

            mocks["orchestrator"].return_value.execute_benchmark.assert_called_once()
            config = mocks["orchestrator"].return_value.execute_benchmark.call_args[0][0]
            assert isinstance(config, BenchmarkConfig)
            assert config.options.get("force_regenerate") is True

    def test_table_mode_option_forwarded(self):
        with _mock_run_command_components(include_db_manager=True) as mocks:
            mock_db_cfg = Mock()
            mock_db_cfg.type = "duckdb"
            mock_db_cfg.options = {}
            assert mocks["db_mgr"] is not None
            mocks["db_mgr"].return_value.create_config.return_value = mock_db_cfg

            result = self.runner.invoke(
                cli,
                [
                    "run",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--scale",
                    "0.01",
                    "--table-mode",
                    "external",
                ],
            )
            assert result.exit_code == 0

            mocks["orchestrator"].return_value.execute_benchmark.assert_called_once()
            config = mocks["orchestrator"].return_value.execute_benchmark.call_args[0][0]
            assert isinstance(config, BenchmarkConfig)
            assert config.options.get("table_mode") == "external"

    def test_table_mode_cli_value_saved_for_quick_restart(self):
        with (
            _mock_run_command_components(include_db_manager=True) as mocks,
            patch("benchbox.cli.preferences.save_last_run_config") as mock_save_last_run_config,
        ):
            mock_db_cfg = Mock()
            mock_db_cfg.type = "duckdb"
            mock_db_cfg.options = {}
            mock_db_cfg.driver_version_actual = "1.4.3"
            mock_db_cfg.driver_version_resolved = "1.4.3"
            assert mocks["db_mgr"] is not None
            mocks["db_mgr"].return_value.create_config.return_value = mock_db_cfg

            result = self.runner.invoke(
                cli,
                [
                    "run",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--scale",
                    "0.01",
                    "--table-mode",
                    "external",
                ],
            )
            assert result.exit_code == 0
            assert mock_save_last_run_config.called
            _, kwargs = mock_save_last_run_config.call_args
            assert kwargs.get("additional_options", {}).get("table_mode") == "external"

    def test_table_mode_cli_precedence_over_quick_restart_config(self):
        mock_sys = Mock()
        mock_sys.stdin = Mock()
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout = Mock()
        mock_sys.stdout.isatty.return_value = True

        with (
            _mock_run_command_components(include_db_manager=True) as mocks,
            patch.object(_run_module, "sys", mock_sys),
            patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False),
            patch(
                "benchbox.cli.preferences.load_last_run_config",
                return_value={
                    "database": "duckdb",
                    "benchmark": "tpch",
                    "scale": 0.01,
                    "phases": ["load", "power"],
                    "tuning_mode": "notuning",
                    "table_mode": "native",
                },
            ),
            patch("benchbox.cli.preferences.format_last_run_summary", return_value="saved run"),
            patch.object(_run_module.Confirm, "ask") as mock_confirm_ask,
            patch("benchbox.cli.preferences.save_last_run_config"),
        ):

            def _confirm_side_effect(prompt, default=False):
                prompt_text = str(prompt)
                if "Reuse this configuration?" in prompt_text:
                    return True
                if "configure tuning options" in prompt_text:
                    return False
                if "Proceed with execution?" in prompt_text:
                    return True
                return default

            mock_confirm_ask.side_effect = _confirm_side_effect

            mock_profile = Mock()
            mock_profile.cpu_cores_logical = 8
            mock_profile.cpu_cores_physical = 4
            mock_profile.memory_total_gb = 16
            mock_profile.is_apple_silicon = False
            mock_profile.architecture = "x86_64"
            mock_profile.os_type = "darwin"
            mock_profile.numa_nodes = None
            mocks["profiler"].return_value.get_system_profile.return_value = mock_profile

            mock_db_cfg = Mock()
            mock_db_cfg.type = "duckdb"
            mock_db_cfg.options = {}
            mock_db_cfg.driver_version_actual = "1.4.3"
            mock_db_cfg.driver_version_resolved = "1.4.3"
            assert mocks["db_mgr"] is not None
            mocks["db_mgr"].return_value.create_config.return_value = mock_db_cfg

            result = self.runner.invoke(cli, ["run", "--table-mode", "external"])
            assert result.exit_code == 0, result.output

            mocks["orchestrator"].return_value.execute_benchmark.assert_called_once()
            config = mocks["orchestrator"].return_value.execute_benchmark.call_args[0][0]
            assert isinstance(config, BenchmarkConfig)
            assert config.options.get("table_mode") == "external"

    def test_quick_restart_atomically_refreshes_execution_significant_state(self):
        saved = {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 0.01,
            "phases": ["load", "power"],
            "tuning_mode": "notuning",
            "table_mode": "native",
            "seed": 41,
            "compress_data": False,
            "compression_type": "none",
            "concurrency": 3,
            "test_execution_type": "maintenance",
        }
        with _mock_quick_restart(saved) as mocks:
            result = self.runner.invoke(
                cli,
                [
                    "run",
                    "--phases",
                    "throughput",
                    "--seed",
                    "99",
                    "--compression",
                    "gzip:6",
                    "--presort",
                    "parquet-sorted",
                ],
            )

        assert result.exit_code == 0, result.output
        execute = mocks["orchestrator"].return_value.execute_benchmark
        execute.assert_called_once()
        config, _profile, database_config, phases = execute.call_args.args[:4]
        execution_context = execute.call_args.kwargs["execution_context"]

        assert config.name == "tpch"
        assert config.scale_factor == 0.01
        assert config.concurrency == 3
        assert config.test_execution_type == "throughput"
        assert config.compress_data is True
        assert config.compression_type == "gzip"
        assert config.compression_level == 6
        assert config.options["seed"] == 99
        assert config.options["tuning_enabled"] is False
        assert config.options["unified_tuning_configuration"] is not None
        assert config.options["data_organization"]["table_configs"]
        assert "df_tuning_config" not in config.options
        assert database_config is mocks["db_mgr"].return_value.create_config.return_value
        assert mocks["db_mgr"].return_value.create_config.call_args.args[0] == "duckdb"
        database_overrides = mocks["db_mgr"].return_value.create_config.call_args.args[2]
        assert database_overrides["tuning_enabled"] is False
        assert database_overrides["execution_mode"] == "sql"
        assert phases == ["throughput"]
        assert execution_context.phases == ["throughput"]
        assert execution_context.seed == 99
        assert execution_context.mode == "sql"
        assert execution_context.tuning_mode == "notuning"

    @pytest.mark.parametrize(
        ("saved_compression", "expected"),
        [
            (
                {"compress_data": False, "compression_type": "none", "compression_level": None},
                (False, "none", None),
            ),
            (
                {"compress_data": True, "compression_type": "gzip", "compression_level": 6},
                (True, "gzip", 6),
            ),
        ],
    )
    def test_quick_restart_saved_compression_reaches_config_and_result_metadata(
        self,
        saved_compression,
        expected,
    ):
        saved = {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 0.01,
            "phases": ["load", "power"],
            "queries": None,
            "tuning_mode": "notuning",
            "table_mode": "native",
            "mode": "sql",
            "seed": 41,
            "concurrency": None,
            **saved_compression,
        }

        with _mock_quick_restart(saved) as mocks:
            result = self.runner.invoke(cli, ["run"])

        assert result.exit_code == 0, result.output
        execute = mocks["orchestrator"].return_value.execute_benchmark
        config = execute.call_args.args[0]
        execution_context = execute.call_args.kwargs["execution_context"]
        enabled, compression_type, level = expected
        assert (config.compress_data, config.compression_type, config.compression_level) == expected
        assert (
            execution_context.compression_enabled,
            execution_context.compression_type,
            execution_context.compression_level,
        ) == (enabled, compression_type, level)

    def test_quick_restart_saved_power_iterations_reach_execution_config(self):
        saved = {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 0.01,
            "phases": ["power"],
            "queries": None,
            "tuning_mode": "notuning",
            "table_mode": "native",
            "mode": "sql",
            "seed": 41,
            "concurrency": None,
            "compress_data": True,
            "compression_type": "zstd",
            "compression_level": None,
            "iterations": 5,
            "replay_schema_version": 1,
            "non_replayable_options": [],
        }

        with _mock_quick_restart(saved) as mocks:
            result = self.runner.invoke(cli, ["run"])

        assert result.exit_code == 0, result.output
        config = mocks["orchestrator"].return_value.execute_benchmark.call_args.args[0]
        assert config.options["power_iterations"] == 5
        saved_call = mocks["save_last_run"].call_args.kwargs
        assert saved_call["iterations"] == 5
        assert saved_call["non_replayable_options"] == []
        assert "not an exact replay" not in result.output

    def test_quick_restart_without_saved_seed_is_labeled_non_exact(self):
        saved = {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 0.01,
            "phases": ["power"],
            "tuning_mode": "notuning",
        }
        with _mock_quick_restart(saved) as mocks:
            result = self.runner.invoke(cli, ["run"])

        assert result.exit_code == 0, result.output
        assert "not an exact" in result.output
        assert "replay." in result.output
        execution_context = mocks["orchestrator"].return_value.execute_benchmark.call_args.kwargs["execution_context"]
        assert execution_context.seed is None

    def test_quick_restart_invalid_tuning_fails_closed_without_execution(self):
        saved = {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 0.01,
            "phases": ["power"],
            "tuning_mode": "missing-quick-restart-tuning.yaml",
            "seed": 7,
        }
        with _mock_quick_restart(saved) as mocks:
            result = self.runner.invoke(cli, ["run"])

        assert result.exit_code != 0
        assert "Tuning file not found" in result.output
        mocks["orchestrator"].return_value.execute_benchmark.assert_not_called()
        mocks["db_mgr"].return_value.create_config.assert_not_called()

    def test_table_mode_external_rejects_tuned_in_quick_restart(self):
        mock_sys = Mock()
        mock_sys.stdin = Mock()
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout = Mock()
        mock_sys.stdout.isatty.return_value = True

        with (
            _mock_run_command_components(include_db_manager=True) as mocks,
            patch.object(_run_module, "sys", mock_sys),
            patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False),
            patch(
                "benchbox.cli.preferences.load_last_run_config",
                return_value={
                    "database": "duckdb",
                    "benchmark": "tpch",
                    "scale": 0.01,
                    "phases": ["load", "power"],
                    "tuning_mode": "tuned",
                    "table_mode": "native",
                },
            ),
            patch("benchbox.cli.preferences.format_last_run_summary", return_value="saved run"),
            patch.object(_run_module.Confirm, "ask", side_effect=[True]),
            patch("benchbox.cli.preferences.save_last_run_config"),
        ):
            mock_profile = Mock()
            mock_profile.cpu_cores_logical = 8
            mock_profile.cpu_cores_physical = 4
            mock_profile.memory_total_gb = 16
            mock_profile.is_apple_silicon = False
            mock_profile.architecture = "x86_64"
            mock_profile.os_type = "darwin"
            mock_profile.numa_nodes = None
            mocks["profiler"].return_value.get_system_profile.return_value = mock_profile

            result = self.runner.invoke(cli, ["run", "--table-mode", "external"])
            assert result.exit_code != 0
            assert "--table-mode external is incompatible with tuning enabled" in result.output
            mocks["orchestrator"].return_value.execute_benchmark.assert_not_called()

    def test_table_mode_external_shows_tag_in_output(self):
        with _mock_run_command_components(include_db_manager=True) as mocks:
            mock_db_cfg = Mock()
            mock_db_cfg.type = "duckdb"
            mock_db_cfg.options = {}
            mock_db_cfg.driver_version_actual = "1.4.3"
            mock_db_cfg.driver_version_resolved = "1.4.3"
            assert mocks["db_mgr"] is not None
            mocks["db_mgr"].return_value.create_config.return_value = mock_db_cfg

            result = self.runner.invoke(
                cli,
                [
                    "run",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--scale",
                    "0.01",
                    "--table-mode",
                    "external",
                ],
            )
            assert result.exit_code == 0
            assert "[external]" in result.output, f"Expected '[external]' tag in CLI output, got:\n{result.output}"

    def test_table_mode_native_omits_tag_in_output(self):
        with _mock_run_command_components(include_db_manager=True) as mocks:
            mock_db_cfg = Mock()
            mock_db_cfg.type = "duckdb"
            mock_db_cfg.options = {}
            mock_db_cfg.driver_version_actual = "1.4.3"
            mock_db_cfg.driver_version_resolved = "1.4.3"
            assert mocks["db_mgr"] is not None
            mocks["db_mgr"].return_value.create_config.return_value = mock_db_cfg

            result = self.runner.invoke(
                cli,
                [
                    "run",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--scale",
                    "0.01",
                ],
            )
            assert result.exit_code == 0
            assert "[external]" not in result.output

    def test_table_mode_external_rejects_tuned(self):
        result = self.runner.invoke(
            cli,
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--table-mode",
                "external",
                "--tuning",
                "tuned",
            ],
        )
        assert result.exit_code != 0
        assert "--table-mode external is incompatible with tuning enabled" in result.output

    def test_table_mode_external_rejects_auto(self):
        result = self.runner.invoke(
            cli,
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--table-mode",
                "external",
                "--tuning",
                "auto",
            ],
        )
        assert result.exit_code != 0
        assert "--table-mode external is incompatible with tuning enabled" in result.output

    def test_table_mode_external_rejects_custom_file(self, tmp_path):
        tuning_file = tmp_path / "custom.yaml"
        tuning_file.write_text("constraints: {}\n")
        result = self.runner.invoke(
            cli,
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--table-mode",
                "external",
                "--tuning",
                str(tuning_file),
            ],
        )
        assert result.exit_code != 0
        assert "--table-mode external is incompatible with tuning enabled" in result.output

    def test_table_mode_external_allows_notuning(self):
        with _mock_run_command_components(include_db_manager=True) as mocks:
            mock_db_cfg = Mock()
            mock_db_cfg.type = "duckdb"
            mock_db_cfg.options = {}
            mock_db_cfg.driver_version_actual = "1.4.3"
            mock_db_cfg.driver_version_resolved = "1.4.3"
            assert mocks["db_mgr"] is not None
            mocks["db_mgr"].return_value.create_config.return_value = mock_db_cfg

            result = self.runner.invoke(
                cli,
                [
                    "run",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--scale",
                    "0.01",
                    "--table-mode",
                    "external",
                    "--tuning",
                    "notuning",
                ],
            )
            assert result.exit_code == 0
            assert "incompatible with tuning enabled" not in result.output

    def test_enable_postgen_manifest_flag_forwarded(self):
        with _mock_run_command_components() as mocks:
            result = self.runner.invoke(
                cli,
                [
                    "run",
                    "--benchmark",
                    "tpch",
                    "--scale",
                    "0.01",
                    "--phases",
                    "generate",
                    "--validation",
                    "postgen",
                ],
            )

            assert result.exit_code == 0
            call_args = mocks["orchestrator"].return_value.execute_benchmark.call_args[0]
            benchmark_config = call_args[0]
            assert benchmark_config.options["enable_postgen_manifest_validation"] is True


class TestCLIDataLoadModesDryRun:
    def setup_method(self):
        self.runner = CliRunner()

    def test_dry_run_data_only_no_database_required(self):

        with (
            patch("benchbox.cli.dryrun.DryRunExecutor") as mock_executor,
            patch("benchbox.cli.main.BenchmarkManager") as mock_bench_mgr,
            patch("benchbox.cli.main.SystemProfiler") as mock_profiler,
        ):
            mock_bench_mgr.return_value.benchmarks = {
                "tpch": {"display_name": "TPC-H", "estimated_time_range": (2, 10)}
            }
            mock_profiler.return_value.get_system_profile.return_value = Mock()
            mock_executor.return_value.execute_dry_run.return_value = Mock()
            mock_executor.return_value.display_dry_run_results.return_value = None
            mock_executor.return_value.save_dry_run_results.return_value = {"json": "/tmp/result.json"}

            result = self.runner.invoke(
                cli,
                [
                    "run",
                    "--benchmark",
                    "tpch",
                    "--scale",
                    "0.01",
                    "--phases",
                    "generate",
                    "--dry-run",
                    "/tmp",
                ],
            )
            assert result.exit_code == 0

            mock_executor.return_value.execute_dry_run.assert_called_once()
            call_args = mock_executor.return_value.execute_dry_run.call_args[0]
            assert call_args[2] is None

    def test_dry_run_data_only_ignores_database(self):

        with (
            patch("benchbox.cli.dryrun.DryRunExecutor") as mock_executor,
            patch("benchbox.cli.main.BenchmarkManager") as mock_bench_mgr,
            patch("benchbox.cli.main.SystemProfiler") as mock_profiler,
        ):
            mock_bench_mgr.return_value.benchmarks = {
                "tpch": {"display_name": "TPC-H", "estimated_time_range": (2, 10)}
            }
            mock_profiler.return_value.get_system_profile.return_value = Mock()
            mock_executor.return_value.execute_dry_run.return_value = Mock()
            mock_executor.return_value.display_dry_run_results.return_value = None
            mock_executor.return_value.save_dry_run_results.return_value = {"json": "/tmp/result.json"}

            result = self.runner.invoke(
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
                    "generate",
                    "--dry-run",
                    "/tmp",
                ],
            )
            assert result.exit_code == 0
            assert "Platform parameter ignored in data-only dry run" in result.output

    def test_dry_run_load_only_requires_database(self):

        result = self.runner.invoke(
            cli,
            [
                "run",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--phases",
                "load",
                "--dry-run",
                "/tmp",
            ],
        )
        assert result.exit_code != 0
        assert "Dry run mode requires --platform and --benchmark parameters" in result.output

    def test_dry_run_missing_benchmark_data_only(self):

        result = self.runner.invoke(cli, ["run", "--phases", "generate", "--dry-run", "/tmp"])
        assert result.exit_code != 0
        assert "Dry run with --phases generate requires --benchmark parameter" in result.output


class TestOrchestratorDataLoadModes:
    @patch("benchbox.cli.orchestrator.get_platform_adapter")
    @patch("benchbox.cli.orchestrator.console")
    def test_execute_benchmark_data_only_skips_platform_adapter(self, mock_console, mock_get_adapter):

        from benchbox.cli.orchestrator import BenchmarkOrchestrator
        from benchbox.cli.system import SystemProfile

        config = BenchmarkConfig(
            name="test",
            display_name="Test",
            scale_factor=0.01,
            test_execution_type="data_only",
        )
        system_profile = Mock(spec=SystemProfile)
        system_profile.cpu_cores_logical = 4
        system_profile.memory_total_gb = 8

        orchestrator = BenchmarkOrchestrator()

        mock_benchmark = Mock()
        mock_benchmark._name = "test"
        mock_benchmark.scale_factor = 0.01
        mock_benchmark.output_dir = "/tmp/test"
        mock_benchmark.tables = None
        mock_benchmark.generate_data.return_value = {"table1": "file1.csv"}

        mock_benchmark.parallel = 1

        if hasattr(mock_benchmark, "data_generator"):
            mock_benchmark.data_generator.parallel = 1
            mock_benchmark.data_generator.validator = Mock()
            mock_benchmark.data_generator.validator.should_regenerate_data.return_value = (
                True,
                "No existing data",
            )

        def mock_create_enhanced_result(platform, query_results, **kwargs):
            from datetime import datetime

            from benchbox.core.results.models import BenchmarkResults

            return BenchmarkResults(
                benchmark_name="test",
                platform=platform,
                scale_factor=0.01,
                execution_id="test123",
                timestamp=datetime.now(),
                duration_seconds=kwargs.get("duration_seconds", 1.0),
                query_definitions={},
                execution_phases=kwargs.get("phases", {}),
                total_queries=0,
                successful_queries=0,
                failed_queries=0,
                total_execution_time=1.0,
                average_query_time=0.0,
                validation_status="PASSED",
                validation_details={},
            )

        mock_benchmark.create_enhanced_benchmark_result = mock_create_enhanced_result

        with patch.object(orchestrator, "_get_benchmark_instance", return_value=mock_benchmark):
            result = orchestrator.execute_benchmark(config, system_profile, None, ["generate"])

        mock_get_adapter.assert_not_called()

        assert result.validation_status == "NOT_RUN"


class TestDryRunDataLoadModes:
    def test_execute_dry_run_with_none_database_config(self):

        from datetime import datetime

        from benchbox.cli.dryrun import DryRunExecutor
        from benchbox.core.schemas import SystemProfile

        executor = DryRunExecutor()
        config = BenchmarkConfig(name="test", display_name="Test", scale_factor=0.01)
        system_profile = SystemProfile(
            os_name="Linux",
            os_version="6.6",
            architecture="x86_64",
            cpu_model="Test CPU",
            cpu_cores_physical=4,
            cpu_cores_logical=8,
            memory_total_gb=16.0,
            memory_available_gb=12.0,
            python_version="3.11",
            disk_space_gb=256.0,
            timestamp=datetime.now(),
        )

        mock_benchmark = Mock()
        mock_benchmark._name = "test"
        mock_benchmark.get_query_list.return_value = ["Q1", "Q2"]
        mock_benchmark.get_query.return_value = "SELECT 1"

        with patch.object(executor, "_get_benchmark_instance", return_value=mock_benchmark):
            result = executor.execute_dry_run(config, system_profile, None)

        assert result.database_config["type"] == "data_only"
        assert result.platform_config == {"data_only": True}

    def test_get_platform_config_with_none(self):

        from benchbox.cli.dryrun import DryRunExecutor
        from benchbox.cli.system import SystemProfile

        executor = DryRunExecutor()
        system_profile = Mock(spec=SystemProfile)

        result = executor._get_platform_config(None, system_profile)

        assert result == {"data_only": True}
