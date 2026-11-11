# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
import sys as _sys
from unittest.mock import MagicMock, patch

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


class TestCompressionCLI:
    def setup_method(self):
        self.runner = CliRunner()

    @patch("benchbox.cli.main.get_config_manager")
    @patch.object(_run_module, "BenchmarkOrchestrator")
    @patch.object(_run_module, "DatabaseManager")
    @patch.object(_run_module, "BenchmarkManager")
    @patch.object(_run_module, "SystemProfiler")
    def test_cli_compression_options(
        self, mock_profiler, mock_bench_manager, mock_db_manager, mock_orchestrator, mock_get_cfg
    ):

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {"export_formats": ["json"]}.get(key, default)
        cfg.config_path = "test.toml"
        cfg.validate_config.return_value = True
        mock_get_cfg.return_value = cfg

        mock_db_instance = MagicMock()
        mock_db_manager.return_value = mock_db_instance
        mock_db_instance.create_config.return_value = MagicMock()

        mock_bench_instance = MagicMock()
        mock_bench_manager.return_value = mock_bench_instance
        mock_bench_instance.benchmarks = {
            "ssb": {
                "display_name": "SSB",
                "estimated_time_range": (1, 5),
                "queries": 13,
                "complexity": "Low",
            }
        }

        mock_profiler_instance = MagicMock()
        mock_profiler.return_value = mock_profiler_instance
        mock_profiler_instance.get_system_profile.return_value = MagicMock()

        mock_orchestrator_instance = MagicMock()
        mock_orchestrator.return_value = mock_orchestrator_instance
        mock_result = MagicMock()
        mock_result.validation_status = "PASSED"
        mock_result.query_results = []
        mock_result.execution_id = "test-123"
        mock_orchestrator_instance.execute_benchmark.return_value = mock_result

        with patch.object(_run_module, "ResultExporter") as mock_exporter:
            mock_exporter.return_value.export_result.return_value = {"json": "test.json"}
            result = self.runner.invoke(
                cli,
                [
                    "run",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "ssb",
                    "--scale",
                    "0.01",
                    "--compression",
                    "zstd:5",
                ],
            )

        assert result.exit_code == 0

        mock_orchestrator_instance.execute_benchmark.assert_called_once()

        call_args = mock_orchestrator_instance.execute_benchmark.call_args
        benchmark_config = call_args[0][0]

        assert isinstance(benchmark_config, BenchmarkConfig)
        assert benchmark_config.compress_data is True
        assert benchmark_config.compression_type == "zstd"
        assert benchmark_config.compression_level == 5

    @patch("benchbox.cli.main.get_config_manager")
    @patch.object(_run_module, "BenchmarkOrchestrator")
    @patch.object(_run_module, "DatabaseManager")
    @patch.object(_run_module, "BenchmarkManager")
    @patch.object(_run_module, "SystemProfiler")
    def test_cli_compression_defaults(
        self, mock_profiler, mock_bench_manager, mock_db_manager, mock_orchestrator, mock_get_cfg
    ):

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {"export_formats": ["json"]}.get(key, default)
        cfg.config_path = "test.toml"
        cfg.validate_config.return_value = True
        mock_get_cfg.return_value = cfg

        mock_db_instance = MagicMock()
        mock_db_manager.return_value = mock_db_instance
        mock_db_instance.create_config.return_value = MagicMock()

        mock_bench_instance = MagicMock()
        mock_bench_manager.return_value = mock_bench_instance
        mock_bench_instance.benchmarks = {
            "ssb": {
                "display_name": "SSB",
                "estimated_time_range": (1, 5),
                "queries": 13,
                "complexity": "Low",
            }
        }

        mock_profiler_instance = MagicMock()
        mock_profiler.return_value = mock_profiler_instance
        mock_profiler_instance.get_system_profile.return_value = MagicMock()

        mock_orchestrator_instance = MagicMock()
        mock_orchestrator.return_value = mock_orchestrator_instance
        mock_result = MagicMock()
        mock_result.validation_status = "PASSED"
        mock_result.query_results = []
        mock_result.execution_id = "test-123"
        mock_orchestrator_instance.execute_benchmark.return_value = mock_result

        with patch.object(_run_module, "ResultExporter") as mock_exporter:
            mock_exporter.return_value.export_result.return_value = {"json": "test.json"}
            result = self.runner.invoke(
                cli,
                ["run", "--platform", "duckdb", "--benchmark", "ssb", "--scale", "0.01"],
            )

        assert result.exit_code == 0

        call_args = mock_orchestrator_instance.execute_benchmark.call_args
        benchmark_config = call_args[0][0]

        assert benchmark_config.compress_data is False
        assert benchmark_config.compression_type == "none"
        assert benchmark_config.compression_level is None

    def test_cli_help_includes_compression_options(self):

        result = self.runner.invoke(cli, ["run", "--help-topic", "all"])

        assert result.exit_code == 0
        assert "--compression" in result.output

    def test_cli_compression_examples_in_help(self):

        result = self.runner.invoke(cli, ["run", "--help-topic", "all"])

        assert result.exit_code == 0
        assert "--compression" in result.output

    def test_invalid_compression_type_validation(self):

        result = self.runner.invoke(
            cli,
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "ssb",
                "--compression",
                "invalid",
            ],
        )

        assert result.exit_code != 0
        assert "Invalid compression" in result.output or "error" in result.output.lower()

    @patch("benchbox.cli.main.get_config_manager")
    @patch("benchbox.cli.dryrun.DryRunExecutor")
    @patch.object(_run_module, "DatabaseManager")
    @patch.object(_run_module, "BenchmarkManager")
    @patch.object(_run_module, "SystemProfiler")
    def test_dry_run_with_compression_options(
        self, mock_profiler, mock_bench_manager, mock_db_manager, mock_dry_run, mock_get_cfg
    ):

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {"export_formats": ["json"]}.get(key, default)
        cfg.config_path = "test.toml"
        cfg.validate_config.return_value = True
        mock_get_cfg.return_value = cfg

        mock_db_instance = MagicMock()
        mock_db_manager.return_value = mock_db_instance
        mock_db_instance.create_config.return_value = MagicMock()

        mock_bench_instance = MagicMock()
        mock_bench_manager.return_value = mock_bench_instance
        mock_bench_instance.benchmarks = {
            "ssb": {
                "display_name": "SSB",
                "estimated_time_range": (1, 5),
                "queries": 13,
                "complexity": "Low",
            }
        }

        mock_profiler_instance = MagicMock()
        mock_profiler.return_value = mock_profiler_instance
        mock_profiler_instance.get_system_profile.return_value = MagicMock()

        mock_dry_run_instance = MagicMock()
        mock_dry_run.return_value = mock_dry_run_instance
        mock_dry_run_instance.execute_dry_run.return_value = MagicMock()

        result = self.runner.invoke(
            cli,
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "ssb",
                "--scale",
                "0.01",
                "--compression",
                "gzip:9",
                "--dry-run",
                "/tmp/test",
            ],
        )

        assert result.exit_code == 0

        mock_dry_run_instance.execute_dry_run.assert_called_once()

        call_args = mock_dry_run_instance.execute_dry_run.call_args
        benchmark_config = call_args[0][0]

        assert isinstance(benchmark_config, BenchmarkConfig)
        assert benchmark_config.compress_data is True
        assert benchmark_config.compression_type == "gzip"
        assert benchmark_config.compression_level == 9


class TestBenchmarkConfig:
    def test_benchmark_config_defaults(self):

        config = BenchmarkConfig(name="test", display_name="Test")

        assert hasattr(config, "compress_data")
        assert hasattr(config, "compression_type")
        assert hasattr(config, "compression_level")

    def test_benchmark_config_compression_settings(self):

        config = BenchmarkConfig(
            name="test",
            display_name="Test",
            compress_data=True,
            compression_type="gzip",
            compression_level=5,
        )

        assert config.compress_data is True
        assert config.compression_type == "gzip"
        assert config.compression_level == 5

    def test_benchmark_config_post_init(self):

        config = BenchmarkConfig(name="test", display_name="Test")

        assert config.options == {}


