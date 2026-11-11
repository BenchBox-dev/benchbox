# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.xdist_group("cli_phase_validation")
class TestCLITestExecution:
    def setup_method(self):
        self.runner = CliRunner()

    def test_power_phase_validation(self, cli_benchmark_mocks):

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
                "power",
                "--non-interactive",
            ],
        )

        assert result.exit_code == 0
        assert "running tpch on duckdb" in result.output.lower() or "power test execution" in result.output.lower()

    def test_throughput_phase_validation(self, cli_benchmark_mocks):

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
                "throughput",
                "--non-interactive",
            ],
        )

        assert result.exit_code == 0
        assert "running tpch on duckdb" in result.output.lower() or "throughput test execution" in result.output.lower()

    def test_maintenance_phase_validation(self, cli_benchmark_mocks):

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
                "maintenance",
                "--non-interactive",
            ],
        )

        assert result.exit_code == 0
        assert (
            "running tpch on duckdb" in result.output.lower() or "maintenance test execution" in result.output.lower()
        )

    def test_combined_phases_validation(self, cli_benchmark_mocks):

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
                "power,throughput",
                "--non-interactive",
            ],
        )

        assert result.exit_code == 0
        assert "running tpch on duckdb" in result.output.lower() or "combined test execution" in result.output.lower()

    def test_phase_requires_benchmark(self):
        result = self.runner.invoke(cli, ["run", "--phases", "power,throughput"], catch_exceptions=False)
        assert result.exit_code != 0
        assert "benchmark" in result.output.lower() or "required" in result.output.lower()

    def test_benchmark_config_test_execution_type_setting(self):

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="power")
        assert config.test_execution_type == "power"

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="throughput")
        assert config.test_execution_type == "throughput"

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="maintenance")
        assert config.test_execution_type == "maintenance"

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="combined")
        assert config.test_execution_type == "combined"

        config = BenchmarkConfig(name="tpch", display_name="TPC-H")
        assert config.test_execution_type == "standard"

    def test_no_tuning_disables_constraints(self, cli_benchmark_mocks):

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
                "--tuning",
                "notuning",
                "--non-interactive",
            ],
        )

        assert result.exit_code == 0
        assert "benchmark completed" in result.output.lower() or "passed" in result.output.lower()


class TestCLIOrchestrator:
    def setup_method(self):
        from benchbox.cli.orchestrator import BenchmarkOrchestrator

        self.orchestrator = BenchmarkOrchestrator()

    @patch("benchbox.cli.orchestrator.get_platform_adapter")
    def test_orchestrator_power_test_delegation(self, mock_get_adapter):

        from benchbox.cli.benchmarks import BenchmarkConfig
        from benchbox.cli.orchestrator import BenchmarkOrchestrator

        orchestrator = BenchmarkOrchestrator()
        cfg = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="power",
        )
        sys_prof = Mock()
        db_cfg = DatabaseConfig(
            type="duckdb",
            name="test",
            options={},
        )

        with patch.object(BenchmarkOrchestrator, "_get_benchmark_instance") as mock_bench:
            from pathlib import Path

            mock_bench.return_value = Mock(_name="tpch", scale_factor=0.01)
            mock_bench.return_value.tables = {"customer": Path("customer.tbl")}
            mock_bench.return_value.generate_data = Mock(return_value={"customer": Path("customer.tbl")})
            mock_bench.return_value.get_data_source_benchmark.return_value = None

            orchestrator.directory_manager.get_datagen_path = Mock(return_value=Path("/tmp/test_datagen"))
            orchestrator.directory_manager.get_database_path = Mock(return_value=Path("/tmp/test_db.duckdb"))

            mock_adapter = Mock()
            mock_adapter.run_benchmark.return_value = make_benchmark_results(
                benchmark_name="TPC-H",
                platform="duckdb",
                execution_id="x",
                duration_seconds=1.0,
                query_definitions={},
                execution_phases={},
                total_queries=1,
                successful_queries=1,
                total_execution_time=1.0,
                average_query_time=1.0,
                test_execution_type="power",
                power_at_size=111.0,
            )
            mock_get_adapter.return_value = mock_adapter
            result = orchestrator.execute_benchmark(cfg, sys_prof, db_cfg)
        assert mock_adapter.run_benchmark.called
        assert result.power_at_size == 111.0

    @patch("benchbox.cli.orchestrator.get_platform_adapter")
    def test_orchestrator_throughput_test_delegation(self, mock_get_adapter):

        from benchbox.cli.benchmarks import BenchmarkConfig
        from benchbox.cli.orchestrator import BenchmarkOrchestrator

        orchestrator = BenchmarkOrchestrator()
        cfg = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="throughput",
        )
        sys_prof = Mock()
        db_cfg = DatabaseConfig(
            type="duckdb",
            name="test",
            options={},
        )
        with patch.object(BenchmarkOrchestrator, "_get_benchmark_instance") as mock_bench:
            from pathlib import Path

            mock_bench.return_value = Mock(_name="tpch", scale_factor=0.01)
            mock_bench.return_value.tables = {"orders": Path("orders.tbl")}
            mock_bench.return_value.generate_data = Mock(return_value={"orders": Path("orders.tbl")})
            mock_bench.return_value.get_data_source_benchmark.return_value = None

            orchestrator.directory_manager.get_datagen_path = Mock(return_value=Path("/tmp/test_datagen"))
            orchestrator.directory_manager.get_database_path = Mock(return_value=Path("/tmp/test_db.duckdb"))
            mock_adapter = Mock()
            mock_adapter.run_benchmark.return_value = make_benchmark_results(
                benchmark_name="TPC-H",
                platform="duckdb",
                execution_id="y",
                duration_seconds=1.0,
                query_definitions={},
                execution_phases={},
                total_queries=1,
                successful_queries=1,
                total_execution_time=1.0,
                average_query_time=1.0,
                test_execution_type="throughput",
                throughput_at_size=222.0,
            )
            mock_get_adapter.return_value = mock_adapter
            result = orchestrator.execute_benchmark(cfg, sys_prof, db_cfg)
        assert mock_adapter.run_benchmark.called
        assert result.throughput_at_size == 222.0

    @patch("benchbox.cli.orchestrator.get_platform_adapter")
    def test_orchestrator_maintenance_test_delegation(self, mock_get_adapter):

        from benchbox.cli.benchmarks import BenchmarkConfig
        from benchbox.cli.orchestrator import BenchmarkOrchestrator

        orchestrator = BenchmarkOrchestrator()
        cfg = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="maintenance",
        )
        sys_prof = Mock()
        db_cfg = DatabaseConfig(
            type="duckdb",
            name="test",
            options={},
        )
        with patch.object(BenchmarkOrchestrator, "_get_benchmark_instance") as mock_bench:
            from pathlib import Path

            mock_bench.return_value = Mock(_name="tpch", scale_factor=0.01)
            mock_bench.return_value.tables = {"lineitem": Path("lineitem.tbl")}
            mock_bench.return_value.generate_data = Mock(return_value={"lineitem": Path("lineitem.tbl")})
            mock_bench.return_value.get_data_source_benchmark.return_value = None

            orchestrator.directory_manager.get_datagen_path = Mock(return_value=Path("/tmp/test_datagen"))
            orchestrator.directory_manager.get_database_path = Mock(return_value=Path("/tmp/test_db.duckdb"))
            mock_adapter = Mock()
            mock_adapter.run_benchmark.return_value = make_benchmark_results(
                benchmark_name="TPC-H",
                platform="duckdb",
                execution_id="z",
                duration_seconds=1.0,
                query_definitions={},
                execution_phases={},
                total_queries=1,
                successful_queries=1,
                total_execution_time=1.0,
                average_query_time=1.0,
                test_execution_type="maintenance",
            )
            mock_get_adapter.return_value = mock_adapter
            result = orchestrator.execute_benchmark(cfg, sys_prof, db_cfg)
        assert mock_adapter.run_benchmark.called
        assert result.test_execution_type == "maintenance"

    def test_tpc_metrics_passthrough(self):

        res = make_benchmark_results(
            benchmark_name="TPC-H",
            platform="duckdb",
            execution_id="m1",
            duration_seconds=1.0,
            query_definitions={},
            execution_phases={},
            total_queries=22,
            successful_queries=22,
            total_execution_time=1.0,
            average_query_time=0.1,
            test_execution_type="power",
            power_at_size=123.0,
        )
        assert res.test_execution_type == "power"
        assert res.power_at_size == 123.0

    def test_tpc_metrics_none_when_not_provided(self):
        res = make_benchmark_results(
            benchmark_name="TPC-DS",
            platform="duckdb",
            scale_factor=0.1,
            execution_id="m2",
            duration_seconds=2.0,
            query_definitions={},
            execution_phases={},
            total_queries=10,
            successful_queries=8,
            failed_queries=2,
            total_execution_time=2.0,
            average_query_time=0.2,
        )
        assert getattr(res, "power_at_size", None) is None
        assert getattr(res, "throughput_at_size", None) is None


class TestResultHandling:
    def test_benchmark_results_tpc_fields(self):

        result = make_benchmark_results(
            benchmark_name="TPC-H",
            platform="duckdb",
            scale_factor=0.001,
            execution_id="test-id",
            duration_seconds=120.0,
            total_queries=5,
            successful_queries=5,
            total_execution_time=100.0,
            average_query_time=20.0,
            data_loading_time=10.0,
            schema_creation_time=5.0,
            total_rows_loaded=1000,
            data_size_mb=50.0,
            table_statistics={"orders": 100},
            test_execution_type="power",
            power_at_size=1500.0,
            geometric_mean_execution_time=18.5,
        )

        assert result.test_execution_type == "power"
        assert result.power_at_size == 1500.0
        assert result.throughput_at_size is None
        assert result.geometric_mean_execution_time == 18.5

    def test_cli_benchmark_results_tpc_fields(self):

        from benchbox.core.results.models import BenchmarkResults

        result = make_benchmark_results(
            benchmark_id="tpch-001",
            benchmark_name="TPC-H",
            scale_factor=0.001,
            test_execution_type="throughput",
            throughput_at_size=2500.0,
            geometric_mean_execution_time=15.2,
        )

        assert isinstance(result, BenchmarkResults)
        assert result.test_execution_type == "throughput"
        assert result.power_at_size is None
        assert result.throughput_at_size == 2500.0
        assert result.geometric_mean_execution_time == 15.2


if __name__ == "__main__":
    pytest.main([__file__])


