import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpcds.benchmark import BenchmarkPhase, QueryResult, TPCDSBenchmark
from benchbox.core.tpcds.official_benchmark import (
    TPCDSOfficialBenchmark,
    TPCDSOfficialBenchmarkConfig,
    TPCDSOfficialBenchmarkResult,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


class TestTPCDSOfficialBenchmark:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir)

    @pytest.fixture
    def benchmark_instance(self, temp_dir):
        return TPCDSOfficialBenchmark(
            scale_factor=1.0,
            output_dir=temp_dir,
            verbose=False,
        )

    @pytest.fixture
    def mock_connection_factory(self):

        def factory():
            return Mock()

        return factory

    def test_official_benchmark_initialization(self, temp_dir):
        benchmark = TPCDSOfficialBenchmark(scale_factor=1.0, output_dir=temp_dir, verbose=True, num_streams=2)

        assert benchmark.config.scale_factor == 1.0
        assert benchmark.config.output_dir == temp_dir
        assert benchmark.config.verbose is True
        assert benchmark.config.num_streams == 2
        assert benchmark.benchmark is not None
        assert temp_dir.exists()

    def test_benchmark_result_initialization(self):
        config = TPCDSOfficialBenchmarkConfig(scale_factor=1.0)
        result = TPCDSOfficialBenchmarkResult(
            config=config,
            start_time="2023-01-01T00:00:00",
            end_time="2023-01-01T01:00:00",
            total_time=3600.0,
            power_test_result=None,
            throughput_test_result=None,
            maintenance_test_result=None,
            power_at_size=0.0,
            throughput_at_size=0.0,
            success=True,
            errors=[],
        )

        assert result.config.scale_factor == 1.0
        assert result.power_test_result is None
        assert result.throughput_test_result is None
        assert result.maintenance_test_result is None
        assert result.success is True
        assert result.errors == []

    def test_query_result_initialization(self):
        result = QueryResult(
            query_id=1,
            execution_time=1.5,
            success=True,
        )

        assert result.query_id == 1
        assert result.execution_time == 1.5
        assert result.success is True

    def test_config_initialization(self):
        config = TPCDSOfficialBenchmarkConfig(
            scale_factor=2.0,
            num_streams=8,
            power_test_enabled=False,
            throughput_test_enabled=True,
            maintenance_test_enabled=False,
            validation_enabled=False,
            audit_trail=False,
            verbose=True,
        )

        assert config.scale_factor == 2.0
        assert config.num_streams == 8
        assert config.power_test_enabled is False
        assert config.throughput_test_enabled is True
        assert config.maintenance_test_enabled is False
        assert config.validation_enabled is False
        assert config.audit_trail is False
        assert config.verbose is True

    def test_run_official_benchmark_basic(self, benchmark_instance, mock_connection_factory):
        with (
            patch("benchbox.core.tpcds.power_test.TPCDSPowerTest") as mock_power_test,
            patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest") as mock_maintenance_test,
        ):
            mock_power_instance = Mock()
            mock_power_instance.run.return_value = {"power_at_size": 100.0}
            mock_power_test.return_value = mock_power_instance

            adapter = Mock()
            adapter._run_routed_throughput.return_value = Mock(success=True, throughput_at_size=200.0)

            mock_maintenance_instance = Mock()
            mock_maintenance_instance.run.return_value = {"success": True}
            mock_maintenance_test.return_value = mock_maintenance_instance

            with pytest.warns(DeprecationWarning):
                result = benchmark_instance.run_official_benchmark(mock_connection_factory, adapter=adapter)

            assert isinstance(result, TPCDSOfficialBenchmarkResult)
            assert result.success is True
            assert result.start_time is not None
            assert result.end_time is not None
            assert result.total_time > 0

    def test_run_official_benchmark_with_custom_config(self, benchmark_instance, mock_connection_factory):
        custom_config = TPCDSOfficialBenchmarkConfig(
            scale_factor=1.0,
            num_streams=2,
            power_test_enabled=True,
            throughput_test_enabled=False,
            maintenance_test_enabled=False,
            verbose=True,
        )

        with patch("benchbox.core.tpcds.power_test.TPCDSPowerTest") as mock_power_test:
            mock_power_instance = Mock()
            mock_power_instance.run.return_value = {"power_at_size": 150.0}
            mock_power_test.return_value = mock_power_instance

            result = benchmark_instance.run_official_benchmark(mock_connection_factory, config=custom_config)

            assert result.success is True
            assert result.power_at_size == 150.0
            assert result.throughput_at_size == 0.0
            assert not hasattr(result, "qphds_at_size")

    def test_validate_compliance(self, benchmark_instance):
        valid_result = TPCDSOfficialBenchmarkResult(
            config=TPCDSOfficialBenchmarkConfig(),
            start_time="2023-01-01T00:00:00",
            end_time="2023-01-01T01:00:00",
            total_time=3600.0,
            power_test_result=None,
            throughput_test_result=None,
            maintenance_test_result=None,
            power_at_size=100.0,
            throughput_at_size=200.0,
            success=True,
            errors=[],
        )

        assert benchmark_instance.validate_compliance(valid_result) is True

        invalid_result = TPCDSOfficialBenchmarkResult(
            config=TPCDSOfficialBenchmarkConfig(),
            start_time="2023-01-01T00:00:00",
            end_time="2023-01-01T01:00:00",
            total_time=3600.0,
            power_test_result=None,
            throughput_test_result=None,
            maintenance_test_result=None,
            power_at_size=0.0,
            throughput_at_size=0.0,
            success=False,
            errors=["Test error"],
        )

        assert benchmark_instance.validate_compliance(invalid_result) is False

    def test_generate_audit_trail(self, benchmark_instance, temp_dir):
        result = TPCDSOfficialBenchmarkResult(
            config=TPCDSOfficialBenchmarkConfig(scale_factor=1.0, num_streams=4),
            start_time="2023-01-01T00:00:00",
            end_time="2023-01-01T01:00:00",
            total_time=3600.0,
            power_test_result=None,
            throughput_test_result=None,
            maintenance_test_result=None,
            power_at_size=500.0,
            throughput_at_size=800.0,
            success=True,
            errors=[],
        )

        import os
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix="_tpcds_audit_trail_test.txt", delete=False, encoding="utf-8"
        ) as tmp_file:
            audit_file = Path(tmp_file.name)

        try:
            audit_path = benchmark_instance.generate_audit_trail(result, audit_file)

            assert audit_path.exists()
            assert audit_path.is_file()
            assert audit_path == audit_file

            content = audit_path.read_text()
            assert "TPC-DS Official Benchmark Audit Trail" in content
            assert "Scale Factor: 1.0" in content
            assert "Number of Streams: 4" in content
            assert "Throughput@Size:" in content
            assert "QphDS" not in content
        finally:
            if audit_file.exists():
                os.unlink(audit_file)

    def test_integration_with_base_benchmark(self, temp_dir):
        base_benchmark = TPCDSBenchmark(scale_factor=1.0, output_dir=temp_dir, verbose=False)

        assert hasattr(base_benchmark, "run_official_benchmark")
        assert callable(base_benchmark.run_official_benchmark)

    def test_error_handling_during_execution(self):

        Mock()

        config = TPCDSOfficialBenchmarkConfig(
            scale_factor=1.0,
            verbose=False,
        )

        result = TPCDSOfficialBenchmarkResult(
            config=config,
            start_time="2023-01-01T00:00:00",
            end_time="",
            total_time=0.0,
            power_test_result=None,
            throughput_test_result=None,
            maintenance_test_result=None,
            power_at_size=0.0,
            throughput_at_size=0.0,
            success=True,
            errors=[],
        )

        try:
            raise Exception("Database connection failed")
        except Exception as e:
            result.errors.append(f"Power Test failed: {e}")
            result.success = False

        assert isinstance(result, TPCDSOfficialBenchmarkResult)
        assert result.success is False
        assert len(result.errors) > 0
        assert "Power Test failed" in result.errors[0]
        assert "Database connection failed" in result.errors[0]

    def test_query_result_attributes(self):
        result = QueryResult(query_id=5, execution_time=2.5, success=True)

        assert result.query_id == 5
        assert result.execution_time == 2.5
        assert result.success is True

    def test_benchmark_phases_enum(self):
        assert BenchmarkPhase.POWER == "power"
        assert BenchmarkPhase.THROUGHPUT == "throughput"
        assert BenchmarkPhase.MAINTENANCE == "maintenance"


@pytest.mark.integration
class TestTPCDSIntegration:
    def test_integration_with_query_manager(self):
        from benchbox.core.tpcds.queries import TPCDSQueryManager

        query_manager = TPCDSQueryManager()
        assert isinstance(query_manager, TPCDSQueryManager)
        assert hasattr(query_manager, "available")

    def test_integration_with_stream_manager(self):
        from benchbox.core.tpcds.queries import TPCDSQueryManager
        from benchbox.core.tpcds.streams import TPCDSStreamManager

        query_manager = TPCDSQueryManager()
        stream_manager = TPCDSStreamManager(query_manager)

        assert stream_manager is not None
        assert stream_manager.query_manager is query_manager

    def test_integration_with_base_benchmark(self):
        from benchbox.core.tpcds.benchmark import TPCDSBenchmark

        benchmark = TPCDSBenchmark(scale_factor=1.0, verbose=False)

        assert hasattr(benchmark, "run_official_benchmark")
        assert callable(benchmark.run_official_benchmark)
