# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import datetime

import pytest

from benchbox.core.results.models import BenchmarkResults
from benchbox.core.schemas import BenchmarkConfig, QueryResult
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.unit
class TestQueryResult:
    def test_query_result_creation(self):

        result = QueryResult(
            query_id="q1",
            query_name="Simple Select",
            sql_text="SELECT 1",
            execution_time_ms=10.5,
            rows_returned=1,
            status="SUCCESS",
        )

        assert result.query_id == "q1"
        assert result.query_name == "Simple Select"
        assert result.sql_text == "SELECT 1"
        assert result.execution_time_ms == 10.5
        assert result.rows_returned == 1
        assert result.status == "SUCCESS"
        assert result.error_message is None
        assert result.resource_usage is None

    def test_query_result_with_error(self):

        result = QueryResult(
            query_id="q2",
            query_name="Failed Query",
            sql_text="SELECT * FROM non_existent_table",
            execution_time_ms=0.0,
            rows_returned=0,
            status="ERROR",
            error_message="Table not found",
        )

        assert result.status == "ERROR"
        assert result.error_message == "Table not found"
        assert result.execution_time_ms == 0.0

    def test_query_result_with_resource_usage(self):

        resource_usage = {
            "memory_peak_mb": 256.0,
            "cpu_time_ms": 50.0,
            "disk_io_mb": 10.5,
        }

        result = QueryResult(
            query_id="q3",
            query_name="Resource Heavy Query",
            sql_text="SELECT * FROM large_table",
            execution_time_ms=1500.0,
            rows_returned=100000,
            status="SUCCESS",
            resource_usage=resource_usage,
        )

        assert result.resource_usage == resource_usage
        assert result.resource_usage["memory_peak_mb"] == 256.0


@pytest.mark.unit
class TestBenchmarkResults:
    def test_benchmark_results_creation(self):

        result = make_benchmark_results(benchmark_id="tpch_001", benchmark_name="TPC-H", scale_factor=1.0)

        assert isinstance(result, BenchmarkResults)
        assert result.benchmark_id == "tpch_001"
        assert result.benchmark_name == "TPC-H"
        assert result.execution_id == "test-exec"
        assert isinstance(result.timestamp, datetime)
        assert result.duration_seconds == 0.0
        assert result.scale_factor == 1.0
        assert result.total_queries == 0
        assert result.query_results == []
        assert result.validation_status == "UNKNOWN"
        assert result.validation_details == {}
        assert hasattr(result, "platform") and result.platform == "cli"
        assert result.benchmark_version is None

    def test_benchmark_results_with_custom_values(self):

        query_results = [
            QueryResult(
                query_id="q1",
                query_name="Query 1",
                sql_text="SELECT 1",
                execution_time_ms=10.0,
                rows_returned=1,
                status="SUCCESS",
            ),
            QueryResult(
                query_id="q2",
                query_name="Query 2",
                sql_text="SELECT 2",
                execution_time_ms=15.0,
                rows_returned=1,
                status="SUCCESS",
            ),
        ]

        summary_metrics = {"total_time": 25.0, "avg_time": 12.5, "throughput": 2.0}

        result = make_benchmark_results(
            benchmark_id="tpch_002",
            benchmark_name="TPC-H",
            benchmark_version="1.0.0",
            execution_id="exec_001",
            duration_seconds=30.0,
            scale_factor=1.0,
            query_subset=["q1", "q2"],
            concurrency_level=4,
            query_results=query_results,
            summary_metrics=summary_metrics,
            validation_status="PASSED",
            total_queries=len(query_results),
            successful_queries=2,
        )

        assert isinstance(result, BenchmarkResults)
        assert result.benchmark_version == "1.0.0"
        assert result.execution_id == "exec_001"
        assert result.duration_seconds == 30.0
        assert result.scale_factor == 1.0
        assert result.query_subset == ["q1", "q2"]
        assert result.concurrency_level == 4
        assert len(result.query_results) == 2
        assert result.summary_metrics["total_time"] == 25.0
        assert result.validation_status == "PASSED"
        assert result.total_queries == 2
        assert result.successful_queries == 2

    def test_benchmark_results_add_query_result(self):

        result = make_benchmark_results(benchmark_id="test_001", benchmark_name="Test Benchmark")

        query_result = QueryResult(
            query_id="q1",
            query_name="Test Query",
            sql_text="SELECT 1",
            execution_time_ms=10.0,
            rows_returned=1,
            status="SUCCESS",
        )

        result.query_results.append(query_result)

        assert isinstance(result, BenchmarkResults)
        assert len(result.query_results) == 1
        stored = result.query_results[0]
        assert getattr(stored, "query_id", None) == "q1"


def test_cli_types_module_removed() -> None:

    with pytest.raises(ModuleNotFoundError):
        __import__("benchbox.cli.types")


@pytest.mark.unit
class TestBenchmarkConfig:
    def test_benchmark_config_creation(self):

        config = BenchmarkConfig(name="tpch", display_name="TPC-H")

        assert config.name == "tpch"
        assert config.display_name == "TPC-H"
        assert config.scale_factor == 0.01
        assert config.queries is None
        assert config.concurrency is None
        assert config.options == {}

    def test_benchmark_config_with_custom_values(self):

        config = BenchmarkConfig(
            name="tpcds",
            display_name="TPC-DS",
            scale_factor=10.0,
            queries=["q1", "q2", "q3"],
            concurrency=8,
            options={"database_type": "postgres", "output_dir": "/tmp/results"},
        )

        assert config.name == "tpcds"
        assert config.display_name == "TPC-DS"
        assert config.scale_factor == 10.0
        assert config.queries == ["q1", "q2", "q3"]
        assert config.concurrency == 8
        assert config.options == {
            "database_type": "postgres",
            "output_dir": "/tmp/results",
        }

    def test_benchmark_config_validation(self):

        configs = [
            BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.001),
            BenchmarkConfig(name="tpcds", display_name="TPC-DS", scale_factor=100.0),
            BenchmarkConfig(name="ssb", display_name="SSB", concurrency=16),
        ]

        for config in configs:
            assert isinstance(config.name, str)
            assert isinstance(config.display_name, str)
            assert isinstance(config.scale_factor, (int, float))
            assert config.scale_factor > 0


@pytest.mark.unit
class TestDataStructureInteractions:
    def test_complete_benchmark_workflow(self):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.1,
            concurrency=2,
            options={"database_type": "duckdb"},
        )

        result = make_benchmark_results(benchmark_id="test_workflow", benchmark_name=config.display_name)

        for i in range(3):
            query_result = QueryResult(
                query_id=f"q{i + 1}",
                query_name=f"Query {i + 1}",
                sql_text=f"SELECT {i + 1}",
                execution_time_ms=10.0 * (i + 1),
                rows_returned=1,
                status="SUCCESS",
            )
            result.query_results.append(query_result)

        total_time = sum(qr.execution_time_ms for qr in result.query_results)
        result.summary_metrics = {
            "total_time_ms": total_time,
            "avg_time_ms": total_time / len(result.query_results),
            "query_count": len(result.query_results),
        }

        result.validation_status = "PASSED"

        assert result.benchmark_name == config.display_name
        assert len(result.query_results) == 3
        assert result.summary_metrics["total_time_ms"] == 60.0
        assert result.summary_metrics["avg_time_ms"] == 20.0
        assert result.summary_metrics["query_count"] == 3
        assert result.validation_status == "PASSED"

    def test_error_handling_workflow(self):

        result = make_benchmark_results(benchmark_id="error_test", benchmark_name="Error Test Benchmark")

        success_query = QueryResult(
            query_id="q1",
            query_name="Success Query",
            sql_text="SELECT 1",
            execution_time_ms=10.0,
            rows_returned=1,
            status="SUCCESS",
        )

        error_query = QueryResult(
            query_id="q2",
            query_name="Error Query",
            sql_text="SELECT * FROM invalid_table",
            execution_time_ms=0.0,
            rows_returned=0,
            status="ERROR",
            error_message="Table 'invalid_table' doesn't exist",
        )

        timeout_query = QueryResult(
            query_id="q3",
            query_name="Timeout Query",
            sql_text="SELECT COUNT(*) FROM huge_table",
            execution_time_ms=30000.0,
            rows_returned=0,
            status="TIMEOUT",
        )

        result.query_results.extend([success_query, error_query, timeout_query])

        successful_queries = [qr for qr in result.query_results if qr.status == "SUCCESS"]
        failed_queries = [qr for qr in result.query_results if qr.status == "ERROR"]
        timeout_queries = [qr for qr in result.query_results if qr.status == "TIMEOUT"]

        result.summary_metrics = {
            "total_queries": len(result.query_results),
            "successful_queries": len(successful_queries),
            "failed_queries": len(failed_queries),
            "timeout_queries": len(timeout_queries),
            "success_rate": len(successful_queries) / len(result.query_results),
        }

        result.validation_status = "PARTIAL"

        assert result.summary_metrics["total_queries"] == 3
        assert result.summary_metrics["successful_queries"] == 1
        assert result.summary_metrics["failed_queries"] == 1
        assert result.summary_metrics["timeout_queries"] == 1
        assert result.summary_metrics["success_rate"] == 1 / 3
        assert result.validation_status == "PARTIAL"
        assert error_query.error_message == "Table 'invalid_table' doesn't exist"


