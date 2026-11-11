# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
import unittest.mock as mock
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from benchbox.platforms.base import (
    PlatformAdapterConnection,
    PlatformAdapterCursor,
)


class MockBenchmark:
    def __init__(self, benchmark_name: str):
        self._name = benchmark_name
        self.display_name = benchmark_name

    def get_query(self, query_id: int, **kwargs):
        return f"SELECT * FROM test_table WHERE id = {query_id}"

    def get_queries(self):
        queries = {}
        if "tpch" in self._name.lower():
            for i in range(1, 23):
                queries[str(i)] = f"Query {i}"
        elif "tpcds" in self._name.lower():
            for i in range(1, 100):
                queries[str(i)] = f"Query {i}"
        return queries


class MockPlatformAdapter:
    def __init__(self):
        self.platform_name = "mock_platform"
        self.logger = mock.Mock()

    def connect(self, **config):
        return mock.Mock()

    def create_database(self, database_name: str, **config) -> dict[str, Any]:
        return {"status": "success", "database": database_name}

    def execute_query(
        self, connection: Any, query: str, query_name: str | None = None, benchmark_type: str | None = None, **kwargs
    ) -> dict[str, Any]:
        import random

        execution_time = random.uniform(0.1, 2.0)
        rows_returned = random.randint(1, 1000)

        return {
            "query_name": query_name or "test_query",
            "execution_time": execution_time,
            "status": "SUCCESS",
            "rows_returned": rows_returned,
            "query_text": query[:100] + "..." if len(query) > 100 else query,
        }

    def get_target_dialect(self) -> str:
        return "standard"

    def _format_execution_time(self, execution_time: float) -> str:
        if execution_time < 1.0:
            return f"{execution_time * 1000:.0f}ms"
        else:
            return f"{execution_time:.2f}s"

    def _execute_tpch_power_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        results = []
        for i in range(1, 23):
            try:
                sql = benchmark.get_query(i)
                _ = self.execute_query(connection, sql, str(i))
                status = "SUCCESS"
            except Exception:
                status = "FAILED"
            results.append(
                {
                    "query_id": i,
                    "execution_time": 1.5,
                    "status": status,
                    "rows_returned": 100 if status == "SUCCESS" else 0,
                    "test_type": "power",
                    "stream_id": run_config.get("stream_id", 0),
                    "position": i,
                }
            )
        return results

    def _execute_tpcds_power_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        results = []
        for i in range(1, 11):
            result = {
                "query_id": i,
                "execution_time": 2.0,
                "status": "SUCCESS",
                "rows_returned": 200,
                "test_type": "power",
                "stream_id": run_config.get("stream_id", 0),
                "position": i,
            }
            results.append(result)
        return results

    def _execute_tpcds_throughput_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        results = []
        num_streams = run_config.get("num_streams", 2)
        for stream_id in range(num_streams):
            for i in range(1, 6):
                result = {
                    "query_id": i,
                    "execution_time": 1.0,
                    "status": "SUCCESS",
                    "rows_returned": 150,
                    "test_type": "throughput",
                    "stream_id": stream_id,
                }
                results.append(result)
        return results

    def _execute_tpcds_maintenance_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        operations = ["insert", "update", "delete", "validate"]
        results = []
        for i, op_type in enumerate(operations):
            result = {
                "query_id": f"{op_type}_operation_{i + 1}",
                "execution_time": 0.5,
                "status": "SUCCESS",
                "rows_returned": 50,
                "test_type": "maintenance",
                "operation_type": op_type.upper(),
                "table_name": f"test_table_{i + 1}",
            }
            results.append(result)
        return results

    def _execute_queries_by_type(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        test_execution_type = run_config.get("test_execution_type", "standard")

        if test_execution_type == "power":
            if "tpch" in benchmark._name.lower():
                return self._execute_tpch_power_test(benchmark, connection, run_config)
            elif "tpcds" in benchmark._name.lower():
                return self._execute_tpcds_power_test(benchmark, connection, run_config)
            else:
                return self._execute_all_queries(benchmark, connection, run_config)
        elif test_execution_type == "throughput":
            return self._execute_tpcds_throughput_test(benchmark, connection, run_config)
        elif test_execution_type == "maintenance":
            return self._execute_tpcds_maintenance_test(benchmark, connection, run_config)
        else:
            return self._execute_all_queries(benchmark, connection, run_config)

    def _execute_all_queries(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        return [{"query_id": 1, "status": "SUCCESS", "execution_time": 1.0}]


class TestPlatformAdapterConnection:
    def test_connection_creation(self):
        mock_connection = mock.Mock()
        mock_platform = MockPlatformAdapter()

        adapter_conn = PlatformAdapterConnection(mock_connection, mock_platform)

        assert adapter_conn.connection == mock_connection
        assert adapter_conn.platform_adapter == mock_platform
        assert adapter_conn.dialect == "standard"

    def test_query_execution(self):
        mock_connection = mock.Mock()
        mock_platform = MockPlatformAdapter()

        adapter_conn = PlatformAdapterConnection(mock_connection, mock_platform)
        cursor = adapter_conn.execute("SELECT * FROM test_table")

        assert isinstance(cursor, PlatformAdapterCursor)
        assert len(cursor.rows) > 0

    def test_cursor_methods(self):
        mock_platform_result = {
            "rows_returned": 5,
            "status": "SUCCESS",
            "execution_time": 1.5,
        }

        cursor = PlatformAdapterCursor(mock_platform_result)

        rows = cursor.fetchall()
        assert len(rows) == 5
        assert all(row == (None,) for row in rows)

        first_row = cursor.fetchone()
        assert first_row == (None,)


class TestTPCHPowerTestIntegration:
    def test_tpch_power_test_execution(self):
        mock_platform = MockPlatformAdapter()
        mock_connection = mock.Mock()
        tpch_benchmark = MockBenchmark("tpch")

        run_config = {"scale_factor": 0.01, "seed": 1, "stream_id": 0, "verbose": False}

        results = mock_platform._execute_tpch_power_test(tpch_benchmark, mock_connection, run_config)

        assert isinstance(results, list)
        assert len(results) > 0

        for result in results:
            if result.get("query_id") != "power_test_error":
                assert "query_id" in result
                assert "execution_time" in result
                assert "status" in result
                assert "test_type" in result
                assert result["test_type"] == "power"
                assert "stream_id" in result

    def test_tpch_power_test_error_handling(self):
        mock_platform = MockPlatformAdapter()

        mock_platform.execute_query = mock.Mock(side_effect=Exception("Database error"))

        mock_connection = mock.Mock()
        tpch_benchmark = MockBenchmark("tpch")

        run_config = {"scale_factor": 0.01, "seed": 1, "stream_id": 0, "verbose": False}

        results = mock_platform._execute_tpch_power_test(tpch_benchmark, mock_connection, run_config)

        assert isinstance(results, list)
        assert len(results) > 0

        error_results = [r for r in results if r.get("status") == "FAILED"]
        assert len(error_results) > 0


class TestTPCDSPowerTestIntegration:
    def test_tpcds_power_test_execution(self):
        mock_platform = MockPlatformAdapter()
        mock_connection = mock.Mock()
        tpcds_benchmark = MockBenchmark("tpcds")

        run_config = {"scale_factor": 0.01, "seed": 1, "stream_id": 0, "verbose": False}

        results = mock_platform._execute_tpcds_power_test(tpcds_benchmark, mock_connection, run_config)

        assert isinstance(results, list)
        assert len(results) > 0

        for result in results:
            if result.get("query_id") != "power_test_error":
                assert "query_id" in result
                assert "execution_time" in result
                assert "status" in result
                assert "test_type" in result
                assert result["test_type"] == "power"


class TestTPCDSThroughputTestIntegration:
    def test_tpcds_throughput_test_execution(self):
        mock_platform = MockPlatformAdapter()
        mock_connection = mock.Mock()
        tpcds_benchmark = MockBenchmark("tpcds")

        run_config = {"scale_factor": 0.01, "num_streams": 2, "verbose": False}

        results = mock_platform._execute_tpcds_throughput_test(tpcds_benchmark, mock_connection, run_config)

        assert isinstance(results, list)
        assert len(results) > 0

        for result in results:
            if result.get("query_id") != "throughput_test_error":
                assert "query_id" in result
                assert "execution_time" in result
                assert "status" in result
                assert "test_type" in result
                assert result["test_type"] == "throughput"
                assert "stream_id" in result


class TestTPCDSMaintenanceTestIntegration:
    def test_tpcds_maintenance_test_execution(self):
        mock_platform = MockPlatformAdapter()
        mock_connection = mock.Mock()
        tpcds_benchmark = MockBenchmark("tpcds")

        run_config = {
            "scale_factor": 0.01,
            "verbose": False,
            "output_dir": "/tmp/test_maintenance",
        }

        results = mock_platform._execute_tpcds_maintenance_test(tpcds_benchmark, mock_connection, run_config)

        assert isinstance(results, list)
        assert len(results) > 0

        for result in results:
            if result.get("query_id") != "maintenance_test_error":
                assert "query_id" in result
                assert "execution_time" in result
                assert "status" in result
                assert "test_type" in result
                assert result["test_type"] == "maintenance"
                assert "operation_type" in result
                assert "table_name" in result


class TestTpcTestRouting:
    def test_power_test_routing(self):
        mock_platform = MockPlatformAdapter()
        mock_connection = mock.Mock()

        tpch_benchmark = MockBenchmark("tpch")
        run_config = {"test_execution_type": "power", "scale_factor": 0.01}

        results = mock_platform._execute_queries_by_type(tpch_benchmark, mock_connection, run_config)
        assert isinstance(results, list)

        tpcds_benchmark = MockBenchmark("tpcds")
        run_config = {"test_execution_type": "power", "scale_factor": 0.01}

        results = mock_platform._execute_queries_by_type(tpcds_benchmark, mock_connection, run_config)
        assert isinstance(results, list)

    def test_throughput_test_routing(self):
        mock_platform = MockPlatformAdapter()
        mock_connection = mock.Mock()
        tpcds_benchmark = MockBenchmark("tpcds")

        run_config = {
            "test_execution_type": "throughput",
            "scale_factor": 0.01,
            "num_streams": 2,
        }

        results = mock_platform._execute_queries_by_type(tpcds_benchmark, mock_connection, run_config)
        assert isinstance(results, list)

    def test_maintenance_test_routing(self):
        mock_platform = MockPlatformAdapter()
        mock_connection = mock.Mock()
        tpcds_benchmark = MockBenchmark("tpcds")

        run_config = {"test_execution_type": "maintenance", "scale_factor": 0.01}

        results = mock_platform._execute_queries_by_type(tpcds_benchmark, mock_connection, run_config)
        assert isinstance(results, list)

    def test_standard_test_fallback(self):
        mock_platform = MockPlatformAdapter()
        mock_connection = mock.Mock()

        mock_platform._execute_all_queries = mock.Mock(
            return_value=[{"query_id": 1, "status": "SUCCESS", "execution_time": 1.0}]
        )

        tpch_benchmark = MockBenchmark("tpch")
        run_config = {"test_execution_type": "standard", "scale_factor": 0.01}

        results = mock_platform._execute_queries_by_type(tpch_benchmark, mock_connection, run_config)
        assert isinstance(results, list)
        mock_platform._execute_all_queries.assert_called_once()


if __name__ == "__main__":
    import unittest

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestPlatformAdapterConnection,
        TestTPCHPowerTestIntegration,
        TestTPCDSPowerTestIntegration,
        TestTPCDSThroughputTestIntegration,
        TestTPCDSMaintenanceTestIntegration,
        TestTpcTestRouting,
    ]

    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)


