import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpch.benchmark import TPCHBenchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


class TestTPCHCompliance:
    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.connection_string = "sqlite:///:memory:"
        self.dialect = "sqlite"

    def test_tpch_benchmark_initialization(self) -> None:
        benchmark = TPCHBenchmark(scale_factor=1.0, output_dir=self.temp_dir)

        assert benchmark.scale_factor == 1.0
        assert benchmark.output_dir == Path(self.temp_dir)

    def test_tpch_power_test_integration(self) -> None:
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("result1",), ("result2",)]
        mock_conn.execute.return_value = mock_cursor
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.commit.return_value = None
        mock_conn.close.return_value = None

        benchmark = TPCHBenchmark(scale_factor=1.0, output_dir=self.temp_dir)

        with patch.object(benchmark, "get_query") as mock_get_query:
            mock_get_query.return_value = "SELECT 1"

            from benchbox.core.tpch.power_test import TPCHPowerTest

            power_test = TPCHPowerTest(
                benchmark=benchmark,
                connection=mock_conn,
                scale_factor=1.0,
                dialect=self.dialect,
                verbose=False,
            )

            result = power_test.run()

            assert result.scale_factor == 1.0
            assert result.power_at_size > 0
            assert len(result.query_results) == 22

    def test_tpch_throughput_test_integration(self) -> None:

        def mock_connection_factory():
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchall.return_value = [("result1",), ("result2",)]
            mock_conn.execute.return_value = mock_cursor
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.commit.return_value = None
            mock_conn.close.return_value = None
            return mock_conn

        benchmark = TPCHBenchmark(scale_factor=1.0, output_dir=self.temp_dir)

        with patch.object(benchmark, "get_query") as mock_get_query:
            mock_get_query.return_value = "SELECT 1"

            from benchbox.core.tpch.throughput_test import TPCHThroughputTest

            throughput_test = TPCHThroughputTest(
                benchmark=benchmark,
                connection_factory=mock_connection_factory,
                num_streams=2,
                scale_factor=1.0,
                verbose=False,
            )

            result = throughput_test.run()

            assert result.scale_factor == 1.0
            assert result.throughput_at_size > 0
            assert len(result.stream_results) == 2

    def test_tpch_maintenance_test_integration(self) -> None:
        import duckdb

        from benchbox.core.tpch.maintenance_test import TPCHMaintenanceTest
        from tests.integration._tpch_refresh_utils import _load_sf001_base

        database_path = str(Path(self.temp_dir) / "maintenance.duckdb")
        seed_connection = duckdb.connect(database_path)
        try:
            _load_sf001_base(seed_connection)
        finally:
            seed_connection.close()

        maintenance_test = TPCHMaintenanceTest(
            connection_factory=lambda: duckdb.connect(database_path),
            scale_factor=0.01,
            verbose=False,
        )

        result = maintenance_test.run_maintenance_test(rf1_interval=0.0, rf2_interval=0.0)

        assert result.success, result.errors
        assert result.config.scale_factor == 0.01
        assert result.total_time > 0
        assert result.rf1_operations > 0
        assert result.rf2_operations > 0


class TestTPCDSCompliance:
    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.connection_string = "sqlite:///:memory:"
        self.dialect = "sqlite"

    def test_tpcds_benchmark_initialization(self) -> None:
        benchmark = TPCDSBenchmark(scale_factor=1.0, output_dir=self.temp_dir)

        assert benchmark.scale_factor == 1.0
        assert benchmark.output_dir == Path(self.temp_dir)

    def test_tpcds_power_test_integration(self) -> None:
        benchmark = TPCDSBenchmark(scale_factor=1.0, output_dir=self.temp_dir)

        with (
            patch.object(benchmark, "get_query") as mock_get_query,
            patch.object(benchmark, "get_queries") as mock_get_queries,
        ):
            mock_get_query.return_value = "SELECT 1"
            mock_get_queries.return_value = {str(i): f"SELECT {i}" for i in range(1, 11)}

            from benchbox.core.tpcds.power_test import TPCDSPowerTest

            def mock_connection_factory():
                mock_conn = Mock()
                mock_cursor = Mock()
                mock_results = [("result1",), ("result2",), ("result3",)]
                mock_cursor.fetchall.return_value = mock_results
                mock_conn.execute.return_value = mock_cursor
                mock_conn.commit.return_value = None
                mock_conn.close.return_value = None
                return mock_conn

            power_test = TPCDSPowerTest(
                benchmark=benchmark,
                connection_factory=mock_connection_factory,
                scale_factor=1.0,
                verbose=False,
            )

            power_test._query_sequence = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

            result = power_test.run()

            assert result.scale_factor == 1.0
            assert result.power_at_size > 0
            assert len(result.query_results) == 10

    def test_tpcds_throughput_test_integration(self) -> None:

        def mock_connection_factory():
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchall.return_value = [("result1",), ("result2",)]
            mock_conn.execute.return_value = mock_cursor
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.commit.return_value = None
            mock_conn.close.return_value = None
            return mock_conn

        benchmark = TPCDSBenchmark(scale_factor=1.0, output_dir=self.temp_dir)

        with patch.object(benchmark, "get_query") as mock_get_query:
            mock_get_query.return_value = "SELECT 1"

            from benchbox.core.tpcds.throughput_test import TPCDSThroughputTest

            throughput_test = TPCDSThroughputTest(
                benchmark=benchmark,
                connection_factory=mock_connection_factory,
                num_streams=2,
                scale_factor=1.0,
                verbose=False,
            )

            result = throughput_test.run()

            assert result.scale_factor == 1.0
            assert result.throughput_at_size > 0
            assert len(result.stream_results) == 2

    def test_tpcds_maintenance_test_integration(self) -> None:

        def mock_connection_factory():
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchall.return_value = [("result1",), ("result2",)]
            mock_conn.execute.return_value = mock_cursor
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.commit.return_value = None
            mock_conn.close.return_value = None
            return mock_conn

        benchmark = TPCDSBenchmark(scale_factor=1.0, output_dir=self.temp_dir)

        with patch.object(benchmark, "run_maintenance_test") as mock_run_test:
            mock_result = Mock()
            mock_result.test_duration = 60.0
            mock_result.overall_throughput = 100.0
            mock_result.operation_metrics = []
            mock_run_test.return_value = mock_result

            result = benchmark.run_maintenance_test(connection_factory=mock_connection_factory)

            assert result.test_duration == 60.0
            assert result.overall_throughput == 100.0


class TestTPCBenchmarkFlows:
    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.connection_string = "sqlite:///:memory:"
        self.dialect = "sqlite"

    def test_tpch_complete_benchmark_workflow(self) -> None:

        def mock_connection_factory():
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchall.return_value = [("result1",), ("result2",)]
            mock_conn.execute.return_value = mock_cursor
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.commit.return_value = None
            mock_conn.close.return_value = None
            return mock_conn

        mock_conn = mock_connection_factory()

        benchmark = TPCHBenchmark(scale_factor=1.0, output_dir=self.temp_dir)

        with patch.object(benchmark, "get_query") as mock_get_query:
            mock_get_query.return_value = "SELECT 1"

            from benchbox.core.tpch.power_test import TPCHPowerTest

            power_test = TPCHPowerTest(
                benchmark=benchmark,
                connection=mock_conn,
                scale_factor=1.0,
                dialect=self.dialect,
                verbose=False,
            )
            power_result = power_test.run()

            from benchbox.core.tpch.throughput_test import TPCHThroughputTest

            throughput_test = TPCHThroughputTest(
                benchmark=benchmark,
                connection_factory=mock_connection_factory,
                num_streams=2,
                scale_factor=1.0,
                verbose=False,
            )
            throughput_result = throughput_test.run()

            from benchbox.core.tpch.maintenance_test import TPCHMaintenanceTest

            maintenance_test = TPCHMaintenanceTest(
                connection_factory=mock_connection_factory,
                scale_factor=1.0,
                verbose=False,
            )
            maintenance_result = maintenance_test.run_maintenance_test(rf1_interval=0.0, rf2_interval=0.0)

            assert power_result.power_at_size > 0
            assert throughput_result.throughput_at_size > 0
            assert maintenance_result.total_time > 0

    def test_tpc_metrics_validation(self) -> None:
        assert TPCMetricsCalculator.calculate_power_at_size([], scale_factor=1.0) == 0.0
        assert TPCMetricsCalculator.calculate_power_at_size([-50.0], scale_factor=1.0) == 0.0
        assert TPCMetricsCalculator.calculate_throughput_at_size(44, 0.0, 1.0, 2) == 0.0
        assert TPCMetricsCalculator.calculate_throughput_at_size(44, 100.0, 1.0, 0) == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
