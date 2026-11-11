# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.benchmarks import BenchmarkConfig
from benchbox.cli.main import cli
from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.cli.system import SystemProfile
from benchbox.core.schemas import DatabaseConfig
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
]


@pytest.mark.xdist_group("cli_phase_validation")
class TestCLIPowerThroughputExecution:
    def setup_method(self):
        self.runner = CliRunner()

    def test_power_phase_validation(self):

        result = self.runner.invoke(cli, ["run", "--benchmark", "tpch", "--phases", "power"])

        assert result.exit_code != 0
        assert "database" in result.output.lower() or "error" in result.output.lower()

    def test_throughput_phase_validation(self):

        result = self.runner.invoke(cli, ["run", "--benchmark", "tpch", "--phases", "throughput"])

        assert result.exit_code != 0
        assert "database" in result.output.lower() or "error" in result.output.lower()

    def test_maintenance_phase_validation(self):

        result = self.runner.invoke(cli, ["run", "--benchmark", "tpch", "--phases", "maintenance"])

        assert result.exit_code != 0
        assert "database" in result.output.lower() or "error" in result.output.lower()

    def test_combined_phases_validation(self):

        result = self.runner.invoke(cli, ["run", "--benchmark", "tpch", "--phases", "power,throughput"])

        assert result.exit_code != 0
        assert "database" in result.output.lower() or "error" in result.output.lower()

    def test_multiple_phases_allowed(self, cli_benchmark_mocks):
        result = self.runner.invoke(
            cli,
            ["run", "--platform", "duckdb", "--benchmark", "tpch", "--phases", "power,throughput", "--non-interactive"],
        )
        assert result.exit_code == 0

    def test_invalid_tuning_mode_value(self):

        result = self.runner.invoke(
            cli,
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--tuning",
                "invalid-mode",
            ],
        )
        assert result.exit_code != 0
        assert "invalid tuning value" in result.output.lower()

    def test_tuning_mode_accepts_config_file(self):

        with self.runner.isolated_filesystem():
            config_path = "test_tuning.yaml"
            with open(config_path, "w", encoding="utf-8") as f:
                f.write("primary_keys:\n  enabled: true\n")

            with (
                patch("benchbox.cli.main.BenchmarkManager") as mock_bench_mgr,
                patch("benchbox.cli.main.SystemProfiler") as mock_profiler,
                patch("benchbox.cli.main.DatabaseManager") as mock_db_mgr,
                patch("benchbox.cli.orchestrator.BenchmarkOrchestrator") as mock_orchestrator,
            ):
                mock_bench_mgr.return_value.benchmarks = {
                    "tpch": {"display_name": "TPC-H", "estimated_time_range": (2, 10)}
                }
                mock_profiler.return_value.get_system_profile.return_value = Mock()
                mock_db_mgr.return_value.create_config.return_value = Mock()
                mock_orchestrator.return_value.execute_benchmark.return_value = Mock(validation_status="PASSED")

                result = self.runner.invoke(
                    cli,
                    ["run", "--platform", "duckdb", "--benchmark", "tpch", "--tuning", config_path],
                )

        assert result.exit_code == 0


class TestBenchmarkOrchestratorPowerThroughput:
    def setup_method(self):
        self.orchestrator = BenchmarkOrchestrator()
        self.orchestrator.directory_manager.get_datagen_path = Mock(return_value="/tmp/test_datagen")
        self.orchestrator.directory_manager.get_database_path = Mock(return_value="/tmp/test_db.duckdb")

    @patch("benchbox.core.run_service.run_benchmark_lifecycle")
    @patch("benchbox.cli.orchestrator.get_platform_adapter")
    @patch.object(BenchmarkOrchestrator, "_get_benchmark_instance")
    def test_power_test_execution_type(self, mock_get_benchmark, mock_get_platform, mock_lifecycle):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="power",
        )

        system_profile = Mock(spec=SystemProfile)
        system_profile.cpu_cores_logical = 4
        system_profile.memory_total_gb = 8

        database_config = DatabaseConfig(
            type="duckdb",
            name="test_db",
            options={},
        )

        mock_benchmark = Mock()
        mock_benchmark._name = "tpch"
        mock_benchmark.scale_factor = 0.01
        mock_benchmark.output_dir = "/tmp/test"
        mock_benchmark.generate_data.return_value = ["customer.tbl", "orders.tbl"]
        mock_benchmark.get_data_source_benchmark.return_value = None
        mock_get_benchmark.return_value = mock_benchmark

        mock_platform = Mock()
        mock_platform.platform_name = "DuckDB"
        mock_platform.create_connection.return_value = Mock()
        mock_platform.create_schema.return_value = 0.5
        mock_platform.load_data.return_value = (
            {"customer": 150000, "orders": 1500000},
            2.0,
            None,
        )
        mock_platform.configure_for_benchmark.return_value = None
        mock_platform._calculate_data_size.return_value = 100.0
        mock_platform._get_platform_metadata.return_value = {}

        from benchbox.platforms.base import (
            ExecutionPhases,
            SetupPhase,
        )

        mock_enhanced_result = make_benchmark_results(
            benchmark_name="TPC-H",
            platform="DuckDB",
            execution_id="test-power-123",
            duration_seconds=0.8,
            query_definitions={},
            execution_phases=ExecutionPhases(setup=SetupPhase()),
            total_queries=2,
            successful_queries=2,
            total_execution_time=0.8,
            average_query_time=0.4,
            test_execution_type="power",
            power_at_size=123.45,
        )

        mock_platform.run_benchmark.return_value = mock_enhanced_result
        mock_get_platform.return_value = mock_platform

        mock_lifecycle.return_value = mock_enhanced_result

        result = self.orchestrator.execute_benchmark(config, system_profile, database_config)

        mock_lifecycle.assert_called_once()
        call_kwargs = mock_lifecycle.call_args.kwargs
        assert call_kwargs.get("benchmark_config").test_execution_type == "power"
        assert result is not None
        assert getattr(result, "power_at_size", None) == 123.45

    @patch("benchbox.core.run_service.run_benchmark_lifecycle")
    @patch("benchbox.cli.orchestrator.get_platform_adapter")
    @patch.object(BenchmarkOrchestrator, "_get_benchmark_instance")
    def test_throughput_test_execution_type(self, mock_get_benchmark, mock_get_platform, mock_lifecycle):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="throughput",
        )

        system_profile = Mock(spec=SystemProfile)
        system_profile.cpu_cores_logical = 4
        system_profile.memory_total_gb = 8

        database_config = DatabaseConfig(
            type="duckdb",
            name="test_db",
            options={},
        )

        mock_benchmark = Mock()
        mock_benchmark._name = "tpch"
        mock_benchmark.scale_factor = 0.01
        mock_benchmark.output_dir = "/tmp/test"
        mock_benchmark.generate_data.return_value = ["customer.tbl", "orders.tbl"]
        mock_benchmark.get_data_source_benchmark.return_value = None
        mock_get_benchmark.return_value = mock_benchmark

        mock_platform = Mock()
        mock_platform.platform_name = "DuckDB"
        mock_platform.create_connection.return_value = Mock()
        mock_platform.create_schema.return_value = 0.5
        mock_platform.load_data.return_value = (
            {"customer": 150000, "orders": 1500000},
            2.0,
            None,
        )
        mock_platform.configure_for_benchmark.return_value = None
        mock_platform._calculate_data_size.return_value = 100.0
        mock_platform._get_platform_metadata.return_value = {}

        from benchbox.platforms.base import (
            ExecutionPhases,
            SetupPhase,
        )

        mock_enhanced_result = make_benchmark_results(
            benchmark_name="TPC-H",
            platform="DuckDB",
            execution_id="test-throughput-123",
            duration_seconds=1.3,
            query_definitions={},
            execution_phases=ExecutionPhases(setup=SetupPhase()),
            total_queries=2,
            successful_queries=2,
            total_execution_time=1.3,
            average_query_time=0.65,
            test_execution_type="throughput",
            throughput_at_size=987.65,
        )

        mock_platform.run_benchmark.return_value = mock_enhanced_result
        mock_get_platform.return_value = mock_platform

        mock_lifecycle.return_value = mock_enhanced_result

        result = self.orchestrator.execute_benchmark(config, system_profile, database_config)

        mock_lifecycle.assert_called_once()
        call_kwargs = mock_lifecycle.call_args.kwargs
        assert call_kwargs.get("benchmark_config").test_execution_type == "throughput"
        assert result is not None
        assert getattr(result, "throughput_at_size", None) == 987.65

    @patch("benchbox.core.run_service.run_benchmark_lifecycle")
    @patch("benchbox.cli.orchestrator.get_platform_adapter")
    @patch.object(BenchmarkOrchestrator, "_get_benchmark_instance")
    def test_combined_test_execution_type(self, mock_get_benchmark, mock_get_platform, mock_lifecycle):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="combined",
        )

        system_profile = Mock(spec=SystemProfile)
        system_profile.cpu_cores_logical = 4
        system_profile.memory_total_gb = 8

        database_config = DatabaseConfig(
            type="duckdb",
            name="test_db",
            options={},
        )

        mock_benchmark = Mock()
        mock_benchmark._name = "tpch"
        mock_benchmark.scale_factor = 0.01
        mock_benchmark.output_dir = "/tmp/test"
        mock_benchmark.generate_data.return_value = ["customer.tbl", "orders.tbl"]
        mock_benchmark.get_data_source_benchmark.return_value = None
        mock_get_benchmark.return_value = mock_benchmark

        mock_platform = Mock()
        mock_platform.platform_name = "DuckDB"
        mock_platform.create_connection.return_value = Mock()
        mock_platform.create_schema.return_value = 0.5
        mock_platform.load_data.return_value = (
            {"customer": 150000, "orders": 1500000},
            2.0,
            None,
        )
        mock_platform.configure_for_benchmark.return_value = None
        mock_platform._calculate_data_size.return_value = 100.0
        mock_platform._get_platform_metadata.return_value = {}

        from benchbox.platforms.base import (
            ExecutionPhases,
            SetupPhase,
        )

        mock_enhanced_result = make_benchmark_results(
            benchmark_name="TPC-H",
            platform="DuckDB",
            execution_id="test-combined-123",
            duration_seconds=1.8,
            query_definitions={},
            execution_phases=ExecutionPhases(setup=SetupPhase()),
            total_queries=3,
            successful_queries=3,
            total_execution_time=1.8,
            average_query_time=0.6,
            test_execution_type="combined",
            power_at_size=456.78,
            throughput_at_size=321.45,
            qph_at_size=654.32,
        )

        mock_platform.run_benchmark.return_value = mock_enhanced_result
        mock_get_platform.return_value = mock_platform

        mock_lifecycle.return_value = mock_enhanced_result

        result = self.orchestrator.execute_benchmark(config, system_profile, database_config)

        mock_lifecycle.assert_called_once()
        call_kwargs = mock_lifecycle.call_args.kwargs
        assert call_kwargs.get("benchmark_config").test_execution_type == "combined"
        assert result is not None
        assert getattr(result, "power_at_size", None) == 456.78
        assert getattr(result, "throughput_at_size", None) == 321.45


class TestTPCMetricsCalculation:
    def setup_method(self):
        self.orchestrator = BenchmarkOrchestrator()

    def test_power_at_size_passthrough(self):
        result = make_benchmark_results(
            benchmark_name="TPC-H",
            platform="duckdb",
            execution_id="p1",
            duration_seconds=1.0,
            query_definitions={},
            execution_phases={},
            total_queries=22,
            successful_queries=22,
            total_execution_time=1.0,
            average_query_time=0.1,
            power_at_size=4321.0,
        )
        assert result.power_at_size == 4321.0

    def test_throughput_at_size_passthrough(self):
        result = make_benchmark_results(
            benchmark_name="TPC-DS",
            platform="duckdb",
            scale_factor=0.1,
            execution_id="t1",
            duration_seconds=2.0,
            query_definitions={},
            execution_phases={},
            total_queries=99,
            successful_queries=99,
            total_execution_time=2.0,
            average_query_time=0.02,
            throughput_at_size=9876.5,
        )
        assert result.throughput_at_size == 9876.5

    def test_combined_test_metrics_passthrough(self):
        result = make_benchmark_results(
            benchmark_name="TPC-H",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="c1",
            duration_seconds=3.0,
            query_definitions={},
            execution_phases={},
            total_queries=22,
            successful_queries=22,
            total_execution_time=3.0,
            average_query_time=0.2,
            test_execution_type="combined",
            power_at_size=111.1,
            throughput_at_size=222.2,
            qph_at_size=333.3,
        )
        assert result.power_at_size == 111.1
        assert result.throughput_at_size == 222.2
        assert getattr(result, "qph_at_size", None) == 333.3


class TestNoTuningValidation:
    def setup_method(self):
        self.orchestrator = BenchmarkOrchestrator()

    def test_no_tuning_config_creation(self):

        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        config = UnifiedTuningConfiguration()
        config.primary_keys.enabled = False
        config.foreign_keys.enabled = False

        assert config.primary_keys.enabled is False
        assert config.foreign_keys.enabled is False

    def test_cli_no_tuning_flag_processing(self, cli_benchmark_mocks, cli_runner):

        result = cli_runner.invoke(
            cli,
            ["run", "--platform", "duckdb", "--benchmark", "tpch", "--tuning", "notuning", "--non-interactive"],
        )

        assert result.exit_code == 0


if __name__ == "__main__":
    pytest.main([__file__])


