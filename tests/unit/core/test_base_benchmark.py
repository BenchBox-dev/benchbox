# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.base_benchmark import BaseBenchmark
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.result_factory import build_enhanced_benchmark_result

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class MockBenchmark(BaseBenchmark):
    def __init__(self, scale_factor=1.0, **kwargs):
        super().__init__(scale_factor=scale_factor, **kwargs)
        self._name = "Mock Benchmark"
        self._version = "1.0.0"
        self._description = "A mock benchmark for testing"
        self._resources = []

    def generate_data(self, tables=None, output_format="memory"):
        result = {}
        mock_tables = tables or ["table1", "table2"]
        for table in mock_tables:
            result[table] = [{"id": i, "value": f"test_{i}"} for i in range(10)]
        return result

    def get_query(self, query_id):
        queries = self.get_all_queries()
        if query_id not in queries:
            raise ValueError(f"Query ID {query_id} not found")
        return queries[query_id]

    def get_all_queries(self):
        return {
            1: "SELECT * FROM table1",
            2: "SELECT * FROM table2 WHERE id > 5",
            3: "SELECT t1.id, t2.value FROM table1 t1 JOIN table2 t2 ON t1.id = t2.id",
        }

    def execute_query(self, query_id, connection, params=None):
        self.get_query(query_id)
        return [{"id": i, "result": f"value_{i}"} for i in range(5)]

    def cleanup(self):
        self._resources = []


@pytest.fixture
def mock_db_connection():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = [{"id": 1, "value": "test"}]
    return conn


@pytest.fixture
def base_benchmark():
    return MockBenchmark(scale_factor=1.0)


class TestInitialization:
    def test_basic_initialization(self):

        benchmark = MockBenchmark()
        assert benchmark.scale_factor == 1.0
        assert benchmark._name == "Mock Benchmark"
        assert benchmark._version == "1.0.0"

    def test_custom_scale_factor(self):

        benchmark = MockBenchmark(scale_factor=5.0)
        assert benchmark.scale_factor == 5.0

    def test_invalid_scale_factor(self):

        with pytest.raises(ValueError, match="Scale factor must be positive"):
            MockBenchmark(scale_factor=-1.0)

        with pytest.raises(ValueError, match="Scale factor must be positive"):
            MockBenchmark(scale_factor=0)

    def test_custom_config(self):

        benchmark = MockBenchmark(scale_factor=2.0, custom_param="value")
        assert benchmark.scale_factor == 2.0
        assert benchmark.config.get("custom_param") == "value"


class TestAbstractMethods:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError, match="abstract"):
            BaseBenchmark(scale_factor=1.0)

    def test_abstract_methods_defined(self):
        abstract_methods = [
            method
            for method in dir(BaseBenchmark)
            if not method.startswith("__")
            and hasattr(getattr(BaseBenchmark, method), "__isabstractmethod__")
            and getattr(BaseBenchmark, method).__isabstractmethod__
        ]

        expected_abstract_methods = [
            "generate_data",
            "get_query",
            "get_all_queries",
            "execute_query",
        ]

        for method in expected_abstract_methods:
            assert method in abstract_methods, f"Expected abstract method {method} not found"


class TestMetadata:
    def test_name_access(self, base_benchmark):

        assert base_benchmark.name == "Mock Benchmark"

    def test_version_access(self, base_benchmark):

        assert base_benchmark.version == "1.0.0"

    def test_description_access(self, base_benchmark):

        assert base_benchmark.description == "A mock benchmark for testing"

    def test_metadata_immutability(self, base_benchmark):

        with pytest.raises(AttributeError):
            base_benchmark.name = "New Name"

        with pytest.raises(AttributeError):
            base_benchmark.version = "2.0.0"

        with pytest.raises(AttributeError):
            base_benchmark.description = "New description"


class TestResourceManagement:
    def test_cleanup(self, base_benchmark):

        base_benchmark._resources = ["resource1", "resource2"]

        base_benchmark.cleanup()

        assert base_benchmark._resources == []

    def test_context_manager(self):

        with MockBenchmark(scale_factor=1.0) as benchmark:
            benchmark._resources = ["resource1"]
            assert len(benchmark._resources) == 1

        assert benchmark._resources == []

    def test_context_manager_with_exception(self):

        benchmark = None
        try:
            with MockBenchmark(scale_factor=1.0) as benchmark:
                benchmark._resources = ["resource1"]
                raise RuntimeError("Test exception")
        except RuntimeError:
            assert benchmark._resources == []


class TestErrorHandling:
    def test_invalid_query_id(self, base_benchmark):

        with pytest.raises(ValueError, match="Query ID .* not found"):
            base_benchmark.get_query(999)

    def test_query_execution_error(self, base_benchmark, mock_db_connection):

        mock_db_connection.cursor.return_value.execute.side_effect = Exception("DB Error")

        with patch.object(base_benchmark, "execute_query", side_effect=Exception("DB Error")):
            with pytest.raises(Exception, match="DB Error"):
                base_benchmark.execute_query(1, mock_db_connection)


class TestQueryManagement:
    def test_get_query(self, base_benchmark):

        query = base_benchmark.get_query(1)
        assert query == "SELECT * FROM table1"

    def test_get_all_queries(self, base_benchmark):

        queries = base_benchmark.get_all_queries()
        assert len(queries) == 3
        assert 1 in queries
        assert 2 in queries
        assert 3 in queries

    def test_nonexistent_query(self, base_benchmark):

        with pytest.raises(ValueError):
            base_benchmark.get_query(999)


class TestDataGeneration:
    def test_generate_data_default(self, base_benchmark):

        data = base_benchmark.generate_data()
        assert "table1" in data
        assert "table2" in data
        assert len(data["table1"]) == 10
        assert len(data["table2"]) == 10

    def test_generate_specific_tables(self, base_benchmark):

        data = base_benchmark.generate_data(tables=["table1"])
        assert "table1" in data
        assert "table2" not in data

    def test_scale_factor_effect(self):

        class ScaledMockBenchmark(MockBenchmark):
            def generate_data(self, tables=None, output_format="memory"):
                result = {}
                mock_tables = tables or ["table1", "table2"]
                row_count = int(10 * self.scale_factor)
                for table in mock_tables:
                    result[table] = [{"id": i, "value": f"test_{i}"} for i in range(row_count)]
                return result

        benchmark1 = ScaledMockBenchmark(scale_factor=1.0)
        data1 = benchmark1.generate_data()
        assert len(data1["table1"]) == 10

        benchmark2 = ScaledMockBenchmark(scale_factor=2.0)
        data2 = benchmark2.generate_data()
        assert len(data2["table1"]) == 20


class TestMinimalResultHelper:
    def test_create_minimal_benchmark_result_populates_core_fields(self, base_benchmark):

        result = base_benchmark.create_minimal_benchmark_result(
            validation_status="FAILED",
            validation_details={"error": "boom"},
            duration_seconds=2.5,
            platform="duckdb",
            execution_metadata={"reason": "test"},
            system_profile={"cpu": "M2"},
            phases={"setup": {"status": "FAILED"}},
            execution_id="custom-id",
        )

        assert isinstance(result, BenchmarkResults)
        assert result.platform == "duckdb"
        assert result.validation_status == "FAILED"
        assert result.validation_details == {"error": "boom"}
        assert result.duration_seconds == 2.5
        assert result.execution_metadata["result_type"] == "minimal"
        assert result.execution_metadata["status"] == "FAILED"
        assert result.execution_metadata["reason"] == "test"
        assert result.execution_metadata["benchmark_id"] == "mock_benchmark"
        assert result.system_profile == {"cpu": "M2"}
        assert result.execution_phases is None
        assert result.execution_id == "custom-id"

    def test_create_minimal_benchmark_result_defaults(self, base_benchmark):

        result = base_benchmark.create_minimal_benchmark_result(validation_status="INTERRUPTED")

        assert isinstance(result, BenchmarkResults)
        assert result.platform == "unknown"
        assert result.validation_status == "INTERRUPTED"
        assert result.validation_details == {}
        assert result.duration_seconds == 0.0
        assert result.query_results == []
        assert result.execution_metadata["result_type"] == "minimal"
        assert result.execution_metadata["status"] == "INTERRUPTED"
        assert result.execution_metadata["benchmark_id"] == "mock_benchmark"


def test_create_minimal_benchmark_result_propagates_duration_seconds(base_benchmark):

    result = base_benchmark.create_minimal_benchmark_result(
        validation_status="FAILED",
        duration_seconds=3.25,
    )

    assert isinstance(result, BenchmarkResults)
    assert result.duration_seconds == 3.25


def test_create_enhanced_benchmark_result_per_table_timings(base_benchmark):

    result = base_benchmark.create_enhanced_benchmark_result(
        platform="duckdb",
        query_results=[],
        duration_seconds=2.0,
        data_loading_time=2.0,
        table_statistics={"lineitem": 6000000, "orders": 1500000},
        per_table_timings={
            "lineitem": {"total_ms": 1800},
            "orders": {"total_ms": 200},
        },
    )

    assert isinstance(result, BenchmarkResults)
    assert result.table_statistics["lineitem"] == {"rows": 6000000, "load_time_ms": 1800}
    assert result.table_statistics["orders"] == {"rows": 1500000, "load_time_ms": 200}
    assert result.data_loading_time == 2.0


def test_create_enhanced_benchmark_result_per_table_timings_missing_table(base_benchmark):

    result = base_benchmark.create_enhanced_benchmark_result(
        platform="duckdb",
        query_results=[],
        duration_seconds=1.0,
        data_loading_time=1.0,
        table_statistics={"lineitem": 6000000, "nation": 25},
        per_table_timings={"lineitem": {"total_ms": 950}},
    )

    assert result.table_statistics["lineitem"] == {"rows": 6000000, "load_time_ms": 950}
    assert result.table_statistics["nation"] == {"rows": 25}


def test_create_enhanced_benchmark_result_per_table_timings_float_format(base_benchmark):

    result = base_benchmark.create_enhanced_benchmark_result(
        platform="fabric_spark",
        query_results=[],
        duration_seconds=1.0,
        data_loading_time=1.0,
        table_statistics={"sales": 100000},
        per_table_timings={"sales": 1.5},
    )

    assert result.table_statistics["sales"] == {"rows": 100000}


def test_result_factory_build_enhanced_benchmark_result_matches_base_behavior(base_benchmark):

    kwargs = {
        "platform": "duckdb",
        "query_results": [],
        "duration_seconds": 2.5,
        "execution_metadata": {"mode": "load_only", "benchmark_id": "mock"},
        "table_statistics": {"lineitem": 6000000, "orders": 1500000},
        "per_table_timings": {"lineitem": {"total_ms": 2000}, "orders": {"total_ms": 500}},
        "data_loading_time": 2.5,
    }

    result_from_method = base_benchmark.create_enhanced_benchmark_result(**kwargs)
    result_from_factory = build_enhanced_benchmark_result(benchmark=base_benchmark, **kwargs)

    assert isinstance(result_from_factory, BenchmarkResults)
    assert result_from_factory.duration_seconds == result_from_method.duration_seconds
    assert result_from_factory.data_loading_time == result_from_method.data_loading_time
    assert result_from_factory.table_statistics == result_from_method.table_statistics
    assert result_from_factory.execution_metadata.get("mode") == "load_only"
    assert result_from_factory.execution_metadata.get("benchmark_id") == "mock"


def test_result_factory_build_enhanced_handles_non_dict_per_table_timings(base_benchmark):

    result = build_enhanced_benchmark_result(
        benchmark=base_benchmark,
        platform="fabric_spark",
        query_results=[],
        duration_seconds=1.0,
        data_loading_time=1.0,
        table_statistics={"sales": 100000},
        per_table_timings={"sales": 1.5},
    )

    assert isinstance(result, BenchmarkResults)
    assert result.table_statistics["sales"] == {"rows": 100000}


def test_result_factory_malformed_total_ms_degrades_to_zero(base_benchmark):

    result = build_enhanced_benchmark_result(
        benchmark=base_benchmark,
        platform="duckdb",
        query_results=[],
        duration_seconds=1.0,
        data_loading_time=1.0,
        table_statistics={"a": 10, "b": 20, "c": 30},
        per_table_timings={
            "a": {"total_ms": None},
            "b": {"total_ms": "n/a"},
            "c": {"total_ms": 42.9},
        },
    )

    assert result.table_statistics["a"] == {"rows": 10}
    assert result.table_statistics["b"] == {"rows": 20}
    assert result.table_statistics["c"] == {"rows": 30, "load_time_ms": 42}


def test_result_factory_non_dict_timings_container_degrades_to_zero(base_benchmark):

    result = build_enhanced_benchmark_result(
        benchmark=base_benchmark,
        platform="duckdb",
        query_results=[],
        duration_seconds=1.0,
        data_loading_time=1.0,
        table_statistics={"a": 10},
        per_table_timings="error",
    )

    assert result.table_statistics["a"] == {"rows": 10}


if __name__ == "__main__":
    pytest.main(["-v"])


