from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpch.power_test import TPCHPowerTest
from benchbox.platforms.base.connection_wrappers import PlatformAdapterCursor
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


class TestTPCTestRouting:
    def test_platform_adapter_has_tpc_methods(self):

        adapter = DuckDBAdapter()

        assert hasattr(adapter, "_execute_queries_by_type")
        assert hasattr(adapter, "_execute_power_test")
        assert hasattr(adapter, "_execute_throughput_test")
        assert hasattr(adapter, "_execute_maintenance_test")
        assert hasattr(adapter, "_execute_combined_test")
        assert hasattr(adapter, "_execute_tpch_power_test")
        assert hasattr(adapter, "_execute_tpcds_power_test")
        assert hasattr(adapter, "_execute_tpcds_throughput_test")
        assert hasattr(adapter, "_execute_tpcds_maintenance_test")

    def test_benchmark_name_detection_tpch(self):

        DuckDBAdapter()

        mock_benchmark = Mock()
        mock_benchmark._name = ""
        mock_benchmark.__class__.__name__ = "TPCHBenchmark"
        mock_benchmark.display_name = "TPC-H Benchmark"

        benchmark_name = getattr(mock_benchmark, "_name", type(mock_benchmark).__name__.lower())
        if not any(x in benchmark_name.lower() for x in ["tpch", "tpcds"]):
            display_name = getattr(mock_benchmark, "display_name", "").lower()
            class_name = type(mock_benchmark).__name__.lower()
            if "tpch" in display_name or "tpch" in class_name:
                benchmark_name = "tpch"

        assert "tpch" in benchmark_name.lower()

    def test_benchmark_name_detection_tpcds(self):

        DuckDBAdapter()

        mock_benchmark = Mock()
        mock_benchmark._name = ""
        mock_benchmark.__class__.__name__ = "TPCDSBenchmark"
        mock_benchmark.display_name = "TPC-DS Benchmark"

        benchmark_name = getattr(mock_benchmark, "_name", type(mock_benchmark).__name__.lower())
        if not any(x in benchmark_name.lower() for x in ["tpch", "tpcds"]):
            display_name = getattr(mock_benchmark, "display_name", "").lower()
            class_name = type(mock_benchmark).__name__.lower()
            if "tpcds" in display_name or "tpcds" in class_name:
                benchmark_name = "tpcds"

        assert "tpcds" in benchmark_name.lower()

    def test_queries_by_type_routing(self):

        adapter = DuckDBAdapter()

        mock_benchmark = Mock()
        mock_connection = Mock()

        adapter._execute_all_queries = Mock(return_value=[])
        adapter._execute_power_test = Mock(return_value=[])
        adapter._execute_throughput_test = Mock(return_value=[])
        adapter._execute_maintenance_test = Mock(return_value=[])
        adapter._execute_combined_test = Mock(return_value=[])

        adapter._execute_queries_by_type(mock_benchmark, mock_connection, {"test_execution_type": "standard"})
        adapter._execute_all_queries.assert_called_once()

        adapter._execute_queries_by_type(mock_benchmark, mock_connection, {"test_execution_type": "power"})
        adapter._execute_power_test.assert_called_once()

        adapter._execute_queries_by_type(mock_benchmark, mock_connection, {"test_execution_type": "throughput"})
        adapter._execute_throughput_test.assert_called_once()

        adapter._execute_queries_by_type(mock_benchmark, mock_connection, {"test_execution_type": "maintenance"})
        adapter._execute_maintenance_test.assert_called_once()

        adapter._execute_queries_by_type(mock_benchmark, mock_connection, {"test_execution_type": "combined"})
        adapter._execute_combined_test.assert_called_once()

    def test_tpch_power_test_method_structure(self):

        adapter = DuckDBAdapter()

        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "TPCHBenchmark"
        mock_connection = Mock()

        adapter.execute_query = Mock(
            return_value={
                "query_id": 1,
                "execution_time": 0.1,
                "status": "SUCCESS",
                "rows_returned": 100,
                "error": None,
            }
        )

        with patch("benchbox.core.tpch.streams.TPCHStreams") as mock_streams:
            mock_streams.PERMUTATION_MATRIX = [
                [
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                    7,
                    8,
                    9,
                    10,
                    11,
                    12,
                    13,
                    14,
                    15,
                    16,
                    17,
                    18,
                    19,
                    20,
                    21,
                    22,
                ]
            ]

            mock_benchmark.get_query = Mock(return_value="SELECT 1")

            run_config = {
                "scale_factor": 1.0,
                "seed": 1,
                "stream_id": 0,
                "verbose": False,
                "iterations": 1,
                "warm_up_iterations": 0,
            }

            result = adapter._execute_tpch_power_test(mock_benchmark, mock_connection, run_config)

            assert isinstance(result, list)
            assert len(result) == 22

            for query_result in result:
                assert "query_id" in query_result
                assert "execution_time_seconds" in query_result
                assert "status" in query_result
                assert "test_type" in query_result
                assert query_result["test_type"] == "power"
                assert "stream_id" in query_result
                assert "position" in query_result

    def test_unsupported_benchmark_fallback(self):

        adapter = DuckDBAdapter()

        mock_benchmark = Mock()
        mock_connection = Mock()

        adapter._execute_all_queries = Mock(return_value=[{"test_type": "standard"}])

        run_config = {
            "benchmark_name": "unsupported",
            "test_execution_type": "power",
            "iterations": 1,
            "warm_up_iterations": 0,
        }
        result = adapter._execute_power_test(mock_benchmark, mock_connection, run_config)

        adapter._execute_all_queries.assert_called_once()
        assert result[0]["test_type"] == "standard"


class TestTPCTestIntegration:
    @pytest.fixture
    def tpch_mock_benchmark(self):
        mock = Mock()
        mock.get_query = Mock(return_value="SELECT 1 as test_query")
        return mock

    @pytest.fixture
    def tpcds_mock_benchmark(self):
        mock = Mock()
        mock.get_query = Mock(return_value="SELECT 1 as test_query")
        mock.get_queries = Mock(return_value={"1": "SELECT 1", "2": "SELECT 2"})
        return mock

    def test_tpch_power_test_uses_adapter_reported_row_count(self, tpch_mock_benchmark):
        cursor = PlatformAdapterCursor(
            {
                "status": "SUCCESS",
                "rows_returned": 42,
                "first_row": ("sentinel",),
            }
        )
        assert len(cursor.fetchall()) == 42

        mock_connection = Mock()
        mock_connection.execute.return_value = cursor

        power_test = TPCHPowerTest(
            benchmark=tpch_mock_benchmark,
            connection=mock_connection,
            scale_factor=1.0,
            seed=1,
            validation=False,
            warm_up=False,
            query_subset=["6"],
        )

        result = power_test.run()

        assert result.success
        assert result.query_results[0]["result_count"] == 42

    def test_tpch_power_test_count_only_path_never_warns(self, tpch_mock_benchmark, caplog):
        import logging

        cursor = PlatformAdapterCursor(
            {
                "status": "SUCCESS",
                "rows_returned": 42,
                "first_row": ("sentinel",),
                "query_id": "6",
            }
        )
        assert cursor._rows is None

        mock_connection = Mock()
        mock_connection.execute.return_value = cursor

        power_test = TPCHPowerTest(
            benchmark=tpch_mock_benchmark,
            connection=mock_connection,
            scale_factor=1.0,
            seed=1,
            validation=False,
            warm_up=False,
            query_subset=["6"],
        )

        with caplog.at_level(logging.WARNING, logger="benchbox.platforms.base.connection_wrappers"):
            result = power_test.run()

        assert result.success
        assert result.query_results[0]["result_count"] == 42
        assert cursor._rows is None
        connection_wrapper_warnings = [
            r
            for r in caplog.records
            if r.name == "benchbox.platforms.base.connection_wrappers" and r.levelno == logging.WARNING
        ]
        assert connection_wrapper_warnings == []

    def test_tpch_power_test_drains_raw_cursor_before_commit(self, tpch_mock_benchmark):

        class _CommitBeforeDrainSensitiveCursor:
            def __init__(self, rows: list) -> None:
                self._rows = rows
                self.drained = False

            def fetchall(self) -> list:
                self.drained = True
                return self._rows

        cursor = _CommitBeforeDrainSensitiveCursor([("a",), ("b",), ("c",)])
        mock_connection = Mock(spec=["execute", "commit"])
        mock_connection.execute.return_value = cursor

        def _commit_requires_drained_cursor() -> None:
            assert cursor.drained, "commit() was called before the cursor was drained"

        mock_connection.commit.side_effect = _commit_requires_drained_cursor

        power_test = TPCHPowerTest(
            benchmark=tpch_mock_benchmark,
            connection=mock_connection,
            scale_factor=1.0,
            seed=1,
            validation=False,
            warm_up=False,
            query_subset=["6"],
        )

        result = power_test.run()

        assert result.success
        assert result.query_results[0]["result_count"] == 3
        mock_connection.commit.assert_called_once()

    @patch("rich.console.Console")
    def test_tpch_power_test_execution_flow(self, mock_console, tpch_mock_benchmark):

        adapter = DuckDBAdapter()

        mock_connection = Mock()
        mock_connection.execute = Mock()
        mock_connection.fetchall = Mock(return_value=[])

        with patch("benchbox.core.tpch.streams.TPCHStreams") as mock_streams:
            mock_streams.PERMUTATION_MATRIX = [
                [
                    14,
                    2,
                    9,
                    20,
                    6,
                    17,
                    18,
                    8,
                    21,
                    13,
                    3,
                    22,
                    16,
                    4,
                    11,
                    15,
                    1,
                    10,
                    19,
                    5,
                    7,
                    12,
                ]
            ]

            run_config = {
                "benchmark_name": "tpch",
                "scale_factor": 1.0,
                "seed": 1,
                "stream_id": 0,
                "verbose": False,
                "iterations": 1,
                "warm_up_iterations": 0,
            }

            result = adapter._execute_tpch_power_test(tpch_mock_benchmark, mock_connection, run_config)

            assert len(result) == 22

            first_query_result = result[0]
            expected_first_query = mock_streams.PERMUTATION_MATRIX[0][0]
            assert first_query_result["query_id"] == expected_first_query
            assert first_query_result["position"] == 1
            assert first_query_result["test_type"] == "power"

    @patch("rich.console.Console")
    def test_tpcds_power_test_with_limited_queries(self, mock_console, tpcds_mock_benchmark):

        adapter = DuckDBAdapter()

        mock_connection = Mock()
        mock_connection.execute = Mock()
        mock_connection.fetchall = Mock(return_value=[])

        run_config = {
            "benchmark_name": "tpcds",
            "scale_factor": 1.0,
            "seed": 1,
            "stream_id": 0,
            "verbose": False,
            "iterations": 1,
            "warm_up_iterations": 0,
        }

        result = adapter._execute_tpcds_power_test(tpcds_mock_benchmark, mock_connection, run_config)

        assert len(result) == 2

        query_ids = [r["query_id"] for r in result]
        assert set(query_ids) == {"1", "2"}

        for query_result in result:
            assert query_result["query_id"] in ["1", "2"]
            assert query_result["test_type"] == "power"
            assert "position" in query_result

    @patch("rich.console.Console")
    def test_error_handling_in_power_test(self, mock_console, tpch_mock_benchmark):

        adapter = DuckDBAdapter()

        mock_connection = Mock()

        tpch_mock_benchmark.get_query.side_effect = Exception("Query generation failed")

        with patch("benchbox.core.tpch.streams.TPCHStreams") as mock_streams:
            mock_streams.PERMUTATION_MATRIX = [[1, 2]]

            run_config = {
                "scale_factor": 1.0,
                "seed": 1,
                "stream_id": 0,
                "verbose": False,
            }

            result = adapter._execute_tpch_power_test(tpch_mock_benchmark, mock_connection, run_config)

            assert len(result) >= 1
            assert any("error" in str(r).lower() or r.get("status") == "FAILED" for r in result)

    @patch("rich.console.Console")
    def test_maintenance_test_basic_operations(self, mock_console):

        adapter = DuckDBAdapter()

        mock_connection = Mock()
        mock_connection.execute = Mock()
        mock_connection.fetchall = Mock(return_value=[])

        with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest") as mock_maintenance_class:
            mock_ops = []
            for op_type, table in [
                ("COUNT_VALIDATION", "customer"),
                ("INDEX_CHECK", "store_sales"),
                ("DATA_INTEGRITY", "item"),
                ("REFERENTIAL_CHECK", "date_dim"),
            ]:
                mock_op = Mock()
                mock_op.operation_type = op_type
                mock_op.table_name = table
                mock_op.duration = 0.1
                mock_op.success = True
                mock_op.rows_affected = 100
                mock_ops.append(mock_op)

            mock_maintenance_instance = Mock()
            mock_maintenance_instance.run.return_value = {
                "success": True,
                "operations": mock_ops,
                "insert_operations": 1,
                "update_operations": 1,
                "delete_operations": 1,
                "total_operations": 4,
                "successful_operations": 4,
                "total_time": 0.4,
                "overall_throughput": 10.0,
                "errors": [],
            }
            mock_maintenance_class.return_value = mock_maintenance_instance

            run_config = {"scale_factor": 1.0, "verbose": False}
            result = adapter._execute_tpcds_maintenance_test(None, mock_connection, run_config)

            assert len(result) == 4

            for op_result in result:
                assert op_result["test_type"] == "maintenance"
                assert "table_name" in op_result
                assert "operation_type" in op_result

            expected_operations = [
                "COUNT_VALIDATION",
                "INDEX_CHECK",
                "DATA_INTEGRITY",
                "REFERENTIAL_CHECK",
            ]
            actual_operations = [r["operation_type"] for r in result]
            assert set(actual_operations) == set(expected_operations)
