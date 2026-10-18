# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional, Union
from unittest.mock import Mock, patch

import pytest

from benchbox.base import BaseBenchmark
from benchbox.core.connection import DatabaseConnection, DatabaseError
from benchbox.utils.path_utils import resolve_benchmark_runs_dir

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class MockBaseBenchmark(BaseBenchmark):
    def __init__(self, scale_factor: float = 1.0, **kwargs: Any) -> None:
        super().__init__(scale_factor=scale_factor, **kwargs)
        self._queries: dict[Union[int, str], str] = {
            1: "SELECT * FROM table1",
            2: "SELECT COUNT(*) FROM table2",
            "complex": "SELECT t1.id, t2.name FROM table1 t1 JOIN table2 t2 ON t1.id = t2.id",
            "error_query": "SELECT * FROM nonexistent_table",
            "slow_query": "SELECT * FROM table1 WHERE id IN (SELECT id FROM table2)",
        }
        self._data_generated = False
        self._load_data_called = False

    def generate_data(self) -> list[Union[str, Path]]:
        self._data_generated = True
        return [Path("table1.csv"), Path("table2.csv"), Path("table3.csv")]

    def get_queries(self) -> dict[int | str, str]:
        return self._queries

    def get_query(self, query_id: Union[int, str], *, params: Optional[dict[str, Any]] = None) -> str:
        if query_id not in self._queries:
            raise ValueError(f"Invalid query ID: {query_id}")

        query = self._queries[query_id]

        if params:
            for key, value in params.items():
                query = query.replace(f"${key}", str(value))

        return query

    def _load_data(self, connection: DatabaseConnection) -> None:
        self._load_data_called = True
        connection.execute("CREATE TABLE IF NOT EXISTS table1 (id INT, name VARCHAR(50))")
        connection.execute("CREATE TABLE IF NOT EXISTS table2 (id INT, value DECIMAL(10,2))")
        connection.execute("INSERT INTO table1 VALUES (1, 'test1'), (2, 'test2')")
        connection.execute("INSERT INTO table2 VALUES (1, 10.5), (2, 20.5)")


class FailingBenchmark(BaseBenchmark):
    def __init__(self, fail_mode: str = "generate_data", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail_mode = fail_mode

    def generate_data(self) -> list[Union[str, Path]]:
        if self.fail_mode == "generate_data":
            raise RuntimeError("Data generation failed")
        return [Path("test.csv")]

    def get_queries(self) -> dict[str, str]:
        if self.fail_mode == "get_queries":
            raise RuntimeError("Query retrieval failed")
        return {"1": "SELECT 1"}

    def get_query(self, query_id: Union[int, str], *, params: Optional[dict[str, Any]] = None) -> str:
        if self.fail_mode == "get_query":
            raise RuntimeError("Query retrieval failed")
        return "SELECT 1"

    def _load_data(self, connection: DatabaseConnection) -> None:
        if self.fail_mode == "load_data":
            raise RuntimeError("Data loading failed")


class TestMockBaseBenchmarkInitialization:
    def test_basic_initialization(self):
        benchmark = MockBaseBenchmark(scale_factor=1.0)
        assert benchmark.scale_factor == 1.0
        assert benchmark.output_dir == resolve_benchmark_runs_dir() / "datagen" / "mockbase_sf1"
        assert not benchmark._data_generated
        assert not benchmark._load_data_called

    def test_custom_scale_factor(self):
        benchmark = MockBaseBenchmark(scale_factor=2)
        assert benchmark.scale_factor == 2

    def test_custom_output_dir(self):
        custom_dir = Path("/tmp/test_output")
        benchmark = MockBaseBenchmark(output_dir=custom_dir)
        assert benchmark.output_dir == custom_dir

    def test_string_output_dir(self):
        benchmark = MockBaseBenchmark(output_dir="/tmp/test_output")
        assert benchmark.output_dir == Path("/tmp/test_output")

    def test_custom_kwargs(self):
        benchmark = MockBaseBenchmark(scale_factor=1.0, custom_param="test_value", another_param=42)
        assert benchmark.custom_param == "test_value"
        assert benchmark.another_param == 42

    def test_init_rejects_non_positive_scale_factor(self):
        with pytest.raises(ValueError, match="Scale factor must be positive"):
            MockBaseBenchmark(scale_factor=0)

    def test_init_rejects_fractional_scale_factor_at_or_above_one(self):
        with pytest.raises(ValueError, match="must be whole integers"):
            MockBaseBenchmark(scale_factor=1.5)


class TestMockBaseBenchmarkAbstractMethods:
    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            BaseBenchmark()

    def test_concrete_class_implements_required_methods(self):
        benchmark = MockBaseBenchmark()

        assert hasattr(benchmark, "generate_data")
        assert hasattr(benchmark, "get_queries")
        assert hasattr(benchmark, "get_query")
        assert callable(benchmark.generate_data)
        assert callable(benchmark.get_queries)
        assert callable(benchmark.get_query)

    def test_unimplemented_load_data_raises_error(self):

        class UnimplementedBenchmark(BaseBenchmark):
            def generate_data(self) -> list[Union[str, Path]]:
                return []

            def get_queries(self) -> dict[str, str]:
                return {}

            def get_query(
                self,
                query_id: Union[int, str],
                *,
                params: Optional[dict[str, Any]] = None,
            ) -> str:
                return "SELECT 1"

        benchmark = UnimplementedBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)

        with pytest.raises(NotImplementedError, match="must implement _load_data"):
            benchmark._load_data(mock_connection)

    def test_validate_scale_factor_type_requires_numeric_values(self):
        benchmark = MockBaseBenchmark()

        with pytest.raises(TypeError, match="scale_factor must be a number"):
            benchmark._validate_scale_factor_type("1.0")

    def test_initialize_benchmark_implementation_passes_common_options(self):

        class DummyImplementation:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs

        benchmark = MockBaseBenchmark()
        output_dir = Path("/tmp/impl-output")

        benchmark._initialize_benchmark_implementation(
            DummyImplementation,
            scale_factor=2,
            output_dir=output_dir,
            verbose=3,
            force_regenerate=True,
            custom_flag="enabled",
        )

        assert isinstance(benchmark._impl, DummyImplementation)
        assert benchmark._impl.kwargs == {
            "scale_factor": 2,
            "output_dir": output_dir,
            "verbose": 3,
            "force_regenerate": True,
            "custom_flag": "enabled",
        }

    def test_benchmark_name_mapping_helpers_cover_special_and_default_cases(self):
        special_benchmark = type("TPCHBenchmark", (MockBaseBenchmark,), {})()
        generic_benchmark = type("AnalyticsBenchmark", (MockBaseBenchmark,), {})()
        base_benchmark = MockBaseBenchmark()

        assert special_benchmark._get_benchmark_name() == "tpch"
        assert generic_benchmark._get_benchmark_name() == "analytics"
        assert base_benchmark.get_data_source_benchmark() is None

    def test_run_with_platform_uses_default_benchmark_type(self):
        benchmark = MockBaseBenchmark()
        adapter = Mock()
        adapter.run_benchmark.return_value = "platform-result"

        result = benchmark.run_with_platform(adapter, query_subset=["Q1"])

        assert result == "platform-result"
        adapter.run_benchmark.assert_called_once_with(
            benchmark,
            query_subset=["Q1"],
            benchmark_type="olap",
        )

    def test_benchmark_name_prefers_impl_and_falls_back_locally(self):
        benchmark = MockBaseBenchmark()

        benchmark._impl = SimpleNamespace(_name="impl-name")
        assert benchmark.benchmark_name == "impl-name"

        benchmark._impl = SimpleNamespace(benchmark_name="impl-benchmark-name")
        assert benchmark.benchmark_name == "impl-benchmark-name"

        del benchmark._impl
        benchmark._name = "local-name"
        assert benchmark.benchmark_name == "local-name"

        del benchmark._name
        assert benchmark.benchmark_name == "MockBaseBenchmark"


class TestMockBaseBenchmarkDatabaseSetup:
    def test_setup_database_success(self):
        benchmark = MockBaseBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)

        with patch("benchbox.base.elapsed_seconds", return_value=2.0):
            benchmark.setup_database(mock_connection)

        assert benchmark._data_generated
        assert benchmark._load_data_called
        assert mock_connection.execute.call_count >= 2

    def test_setup_database_skips_data_generation_if_already_generated(self):
        benchmark = MockBaseBenchmark()
        benchmark._data_generated = True
        mock_connection = Mock(spec=DatabaseConnection)

        with patch.object(benchmark, "generate_data") as mock_generate:
            benchmark.setup_database(mock_connection)
            mock_generate.assert_not_called()

        assert benchmark._load_data_called

    def test_setup_database_handles_data_generation_failure(self):
        benchmark = FailingBenchmark(fail_mode="generate_data")
        mock_connection = Mock(spec=DatabaseConnection)

        with pytest.raises(RuntimeError, match="Data generation failed"):
            benchmark.setup_database(mock_connection)

    def test_setup_database_handles_data_loading_failure(self):
        benchmark = FailingBenchmark(fail_mode="load_data")
        mock_connection = Mock(spec=DatabaseConnection)

        with pytest.raises(RuntimeError, match="Data loading failed"):
            benchmark.setup_database(mock_connection)

    def test_setup_database_logs_timing_info(self, caplog):
        benchmark = MockBaseBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)

        with caplog.at_level(logging.INFO, logger="benchbox.base"):
            benchmark.setup_database(mock_connection)

        assert "Setting up database schema and loading data" in caplog.text
        assert "Database setup completed" in caplog.text


class TestMockBaseBenchmarkQueryExecution:
    def setup_method(self):
        self.benchmark = MockBaseBenchmark()
        self.mock_connection = Mock(spec=DatabaseConnection)
        self.mock_cursor = Mock()
        self.mock_connection.execute.return_value = self.mock_cursor
        self.mock_connection.fetchall.return_value = [
            {"id": 1, "name": "test1"},
            {"id": 2, "name": "test2"},
        ]

    def test_run_query_success_without_results(self):
        with patch("benchbox.base.elapsed_seconds", return_value=1.5):
            result = self.benchmark.run_query(1, self.mock_connection)

        assert result["query_id"] == 1
        assert result["execution_time_seconds"] == 1.5
        assert result["query_text"] == "SELECT * FROM table1"
        assert result["results"] is None
        assert result["row_count"] == 0

        self.mock_connection.execute.assert_called_once_with("SELECT * FROM table1")
        self.mock_connection.fetchall.assert_not_called()

    def test_run_query_success_with_results(self):
        with patch("benchbox.base.elapsed_seconds", return_value=1.5):
            result = self.benchmark.run_query(1, self.mock_connection, fetch_results=True)

        assert result["query_id"] == 1
        assert result["execution_time_seconds"] == 1.5
        assert result["query_text"] == "SELECT * FROM table1"
        assert result["results"] == [
            {"id": 1, "name": "test1"},
            {"id": 2, "name": "test2"},
        ]
        assert result["row_count"] == 2

        self.mock_connection.execute.assert_called_once_with("SELECT * FROM table1")
        self.mock_connection.fetchall.assert_called_once()

    def test_run_query_with_parameters(self):
        params = {"limit": "10", "filter": "name"}
        query_with_params = "SELECT * FROM table1 WHERE $filter IS NOT NULL LIMIT $limit"

        self.benchmark._queries[1] = query_with_params

        result = self.benchmark.run_query(1, self.mock_connection, params=params)

        expected_query = "SELECT * FROM table1 WHERE name IS NOT NULL LIMIT 10"
        assert result["query_text"] == expected_query
        self.mock_connection.execute.assert_called_once_with(expected_query)

    def test_run_query_invalid_query_id(self):
        with pytest.raises(ValueError, match="Invalid query ID"):
            self.benchmark.run_query(999, self.mock_connection)

    def test_run_query_database_error(self):
        self.mock_connection.execute.side_effect = DatabaseError("Database connection failed")

        with pytest.raises(DatabaseError, match="Database connection failed"):
            self.benchmark.run_query(1, self.mock_connection)

    def test_run_query_logs_execution_info(self, caplog):
        with caplog.at_level(logging.INFO, logger="benchbox.base"):
            self.benchmark.run_query(1, self.mock_connection)

        assert "Query 1 completed" in caplog.text

    def test_run_query_logs_results_info_when_fetched(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="benchbox.base"):
            self.benchmark.run_query(1, self.mock_connection, fetch_results=True)

        assert "Query 1 returned 2 rows" in caplog.text

    def test_run_query_handles_empty_results(self):
        self.mock_connection.fetchall.return_value = []

        result = self.benchmark.run_query(1, self.mock_connection, fetch_results=True)

        assert result["results"] == []
        assert result["row_count"] == 0

    def test_run_query_handles_none_results(self):
        self.mock_connection.fetchall.return_value = None

        result = self.benchmark.run_query(1, self.mock_connection, fetch_results=True)

        assert result["results"] is None
        assert result["row_count"] == 0


class TestMockBaseBenchmarkBenchmarkExecution:
    def setup_method(self):
        self.benchmark = MockBaseBenchmark()
        self.mock_connection = Mock(spec=DatabaseConnection)
        self.mock_cursor = Mock()
        self.mock_connection.execute.return_value = self.mock_cursor
        self.mock_connection.fetchall.return_value = [{"result": "success"}]

    def test_run_benchmark_success_with_setup(self):
        with (
            patch.object(
                self.benchmark,
                "run_query",
                side_effect=[
                    {
                        "query_id": 1,
                        "execution_time_seconds": 1.0,
                        "query_text": "SELECT 1",
                        "results": None,
                        "row_count": 0,
                    },
                    {
                        "query_id": 2,
                        "execution_time_seconds": 2.0,
                        "query_text": "SELECT 2",
                        "results": None,
                        "row_count": 0,
                    },
                ],
            ),
            patch("benchbox.base.elapsed_seconds", side_effect=[1.0, 3.0, 8.0]),
        ):
            result = self.benchmark.run_benchmark(self.mock_connection, query_ids=[1, 2], setup_database=True)

        assert result["benchmark_name"] == "MockBaseBenchmark"
        assert result["total_queries"] == 2
        assert result["successful_queries"] == 2
        assert result["failed_queries"] == 0
        assert result["setup_time"] == 3.0
        assert len(result["query_results"]) == 2
        assert "average_query_time" in result
        assert "min_query_time" in result
        assert "max_query_time" in result

    def test_run_benchmark_success_without_setup(self):
        with (
            patch.object(
                self.benchmark,
                "run_query",
                return_value={
                    "query_id": 1,
                    "execution_time_seconds": 1.0,
                    "query_text": "SELECT 1",
                    "results": None,
                    "row_count": 0,
                },
            ),
            patch("benchbox.base.elapsed_seconds", return_value=1.0),
        ):
            result = self.benchmark.run_benchmark(self.mock_connection, query_ids=[1], setup_database=False)

        assert result["setup_time"] == 0.0
        assert result["successful_queries"] == 1

    def test_run_benchmark_all_queries_default(self):
        result = self.benchmark.run_benchmark(self.mock_connection, setup_database=False)

        assert result["total_queries"] == len(self.benchmark.get_queries())
        assert result["successful_queries"] == len(self.benchmark.get_queries())

    def test_run_benchmark_partial_failure(self):

        def mock_execute(query):
            if "nonexistent_table" in query:
                raise DatabaseError("Table does not exist")
            return self.mock_cursor

        self.mock_connection.execute.side_effect = mock_execute

        result = self.benchmark.run_benchmark(self.mock_connection, query_ids=[1, "error_query"], setup_database=False)

        assert result["total_queries"] == 2
        assert result["successful_queries"] == 1
        assert result["failed_queries"] == 1

        failed_query = next(r for r in result["query_results"] if "error" in r)
        assert failed_query["query_id"] == "error_query"
        assert "Table does not exist" in failed_query["error"]

    def test_run_benchmark_setup_failure(self):
        benchmark = FailingBenchmark(fail_mode="load_data")

        with pytest.raises(RuntimeError, match="Data loading failed"):
            benchmark.run_benchmark(self.mock_connection, query_ids=[1], setup_database=True)

    def test_run_benchmark_with_fetch_results(self):
        result = self.benchmark.run_benchmark(
            self.mock_connection,
            query_ids=[1],
            fetch_results=True,
            setup_database=False,
        )

        query_result = result["query_results"][0]
        assert query_result["results"] == [{"result": "success"}]
        assert query_result["row_count"] == 1

    def test_run_benchmark_logs_progress(self, caplog):
        with caplog.at_level(logging.INFO, logger="benchbox.base"):
            self.benchmark.run_benchmark(self.mock_connection, query_ids=[1], setup_database=False)

        assert "Running benchmark with 1 queries" in caplog.text
        assert "Benchmark completed" in caplog.text

    def test_run_benchmark_calculates_timing_statistics(self):
        with (
            patch.object(
                self.benchmark,
                "run_query",
                side_effect=[
                    {
                        "query_id": 1,
                        "execution_time_seconds": 1.0,
                        "query_text": "SELECT 1",
                        "results": None,
                        "row_count": 0,
                    },
                    {
                        "query_id": 2,
                        "execution_time_seconds": 1.0,
                        "query_text": "SELECT 2",
                        "results": None,
                        "row_count": 0,
                    },
                    {
                        "query_id": "complex",
                        "execution_time_seconds": 1.0,
                        "query_text": "SELECT 3",
                        "results": None,
                        "row_count": 0,
                    },
                ],
            ),
            patch("benchbox.base.elapsed_seconds", return_value=3.0),
        ):
            result = self.benchmark.run_benchmark(
                self.mock_connection, query_ids=[1, 2, "complex"], setup_database=False
            )

        assert result["average_query_time"] == 1.0
        assert result["min_query_time"] == 1.0
        assert result["max_query_time"] == 1.0

    def test_run_benchmark_handles_empty_query_list(self):
        with (
            patch("benchbox.base.mono_time", return_value=0.0),
            patch("benchbox.base.elapsed_seconds", return_value=1.0),
        ):
            result = self.benchmark.run_benchmark(self.mock_connection, query_ids=[], setup_database=False)

        assert result["total_queries"] == 0
        assert result["successful_queries"] == 0
        assert result["failed_queries"] == 0
        assert result["query_results"] == []
        assert result["average_query_time"] == 0.0


class TestMockBaseBenchmarkResultFormatting:
    def setup_method(self):
        self.benchmark = MockBaseBenchmark()
        self.sample_result = {
            "benchmark_name": "TestBenchmark",
            "total_queries": 3,
            "successful_queries": 2,
            "failed_queries": 1,
            "setup_time": 1.5,
            "total_execution_time": 5.0,
            "average_query_time": 2.0,
            "min_query_time": 1.0,
            "max_query_time": 3.0,
            "query_results": [
                {"query_id": 1, "execution_time_seconds": 1.0, "row_count": 10},
                {"query_id": 2, "execution_time_seconds": 3.0, "row_count": 5},
                {"query_id": 3, "error": "Database connection failed"},
            ],
        }

    def test_format_results_basic_structure(self):
        formatted = self.benchmark.format_results(self.sample_result)

        assert "Benchmark: TestBenchmark" in formatted
        assert "Total Queries: 3" in formatted
        assert "Successful: 2" in formatted
        assert "Failed: 1" in formatted
        assert "Setup Time: 1.50s" in formatted
        assert "Total Execution Time: 5.00s" in formatted
        assert "Average Query Time: 2.000s" in formatted
        assert "Min Query Time: 1.000s" in formatted
        assert "Max Query Time: 3.000s" in formatted

    def test_format_results_query_details(self):
        formatted = self.benchmark.format_results(self.sample_result)

        assert "Query Details:" in formatted
        assert "Query 1: 1.000s (10 rows)" in formatted
        assert "Query 2: 3.000s (5 rows)" in formatted
        assert "Query 3: FAILED - Database connection failed" in formatted

    def test_format_results_no_setup_time(self):
        result = self.sample_result.copy()
        result["setup_time"] = 0.0

        formatted = self.benchmark.format_results(result)

        assert "Setup Time:" not in formatted

    def test_format_results_no_failures(self):
        result = {
            "benchmark_name": "TestBenchmark",
            "total_queries": 2,
            "successful_queries": 2,
            "failed_queries": 0,
            "setup_time": 0.0,
            "total_execution_time": 3.0,
            "average_query_time": 1.5,
            "min_query_time": 1.0,
            "max_query_time": 2.0,
            "query_results": [
                {"query_id": 1, "execution_time_seconds": 1.0, "row_count": 10},
                {"query_id": 2, "execution_time_seconds": 2.0, "row_count": 5},
            ],
        }

        formatted = self.benchmark.format_results(result)

        assert "Failed: 0" in formatted
        assert "FAILED" not in formatted

    def test_format_time_utility(self):
        assert self.benchmark._format_time(0.05) == "50.0ms"
        assert self.benchmark._format_time(0.5) == "500.0ms"

        assert self.benchmark._format_time(1.0) == "1.00s"
        assert self.benchmark._format_time(30.5) == "30.50s"

        assert self.benchmark._format_time(60.0) == "1m 0.0s"
        assert self.benchmark._format_time(125.5) == "2m 5.5s"


class TestMockBaseBenchmarkQueryTranslation:
    def setup_method(self):
        self.benchmark = MockBaseBenchmark()

    def test_translate_query_success(self):
        with patch("benchbox.base.sqlglot") as mock_sqlglot:
            with patch("benchbox.utils.dialect_utils.normalize_dialect_for_sqlglot") as mock_normalize:
                mock_normalize.return_value = "mysql"
                mock_sqlglot.transpile.return_value = ["SELECT * FROM table1"]

                result = self.benchmark.translate_query(1, "mysql")

                assert result == "SELECT * FROM table1"
                mock_normalize.assert_called_once_with("mysql")
                mock_sqlglot.transpile.assert_called_once_with(
                    "SELECT * FROM table1", read="postgres", write="mysql", identify=True
                )

    def test_translate_query_invalid_query_id(self):
        with pytest.raises(ValueError, match="Invalid query ID"):
            self.benchmark.translate_query(999, "mysql")

    def test_translate_query_no_sqlglot(self):
        with patch("benchbox.base.sqlglot", None), pytest.raises(ImportError, match="Bundled benchbox._vendor.sqlglot"):
            self.benchmark.translate_query(1, "mysql")

    def test_translate_query_unsupported_dialect(self):
        with patch("benchbox.base.sqlglot") as mock_sqlglot:
            mock_sqlglot.transpile.side_effect = ValueError("Unsupported dialect")

            with pytest.raises(ValueError, match="Error translating to dialect"):
                self.benchmark.translate_query(1, "unsupported")

    def test_translate_query_with_parameters(self):
        with patch("benchbox.base.sqlglot") as mock_sqlglot:
            mock_sqlglot.transpile.return_value = ["SELECT * FROM table1 WHERE name = 'test'"]

            result = self.benchmark.translate_query(1, "mysql")

            mock_sqlglot.transpile.assert_called_once()
            assert result == "SELECT * FROM table1 WHERE name = 'test'"


class TestMockBaseBenchmarkBackwardCompatibility:
    def test_existing_benchmark_pattern_still_works(self):
        benchmark = MockBaseBenchmark()

        assert benchmark.scale_factor == 1.0
        assert benchmark.output_dir == resolve_benchmark_runs_dir() / "datagen" / "mockbase_sf1"

        queries = benchmark.get_queries()
        assert isinstance(queries, dict)
        assert len(queries) > 0

        query = benchmark.get_query(1)
        assert isinstance(query, str)
        assert len(query) > 0

    def test_custom_attributes_preserved(self):
        benchmark = MockBaseBenchmark(custom_param="test_value", another_param=42)

        assert benchmark.custom_param == "test_value"
        assert benchmark.another_param == 42

    def test_subclass_overrides_work(self):

        class CustomBenchmark(MockBaseBenchmark):
            def get_queries(self) -> dict[str, str]:
                return {"custom": "SELECT 'custom' as result"}

        benchmark = CustomBenchmark()
        queries = benchmark.get_queries()

        assert queries == {"custom": "SELECT 'custom' as result"}

    def test_run_with_platform_keeps_structural_third_party_adapter_compatibility(self):

        class ThirdPartyAdapter:
            def run_benchmark(self, benchmark, **run_config):
                return {"benchmark": benchmark, "run_config": run_config}

        benchmark = MockBaseBenchmark()
        result = benchmark.run_with_platform(ThirdPartyAdapter(), query_subset=["q1"])

        assert result["benchmark"] is benchmark
        assert result["run_config"] == {"query_subset": ["q1"], "benchmark_type": "olap"}

    def test_load_data_override_works(self):

        class CustomLoadBenchmark(MockBaseBenchmark):
            def _load_data(self, connection: DatabaseConnection) -> None:
                connection.execute("CREATE TABLE custom_table (id INT)")
                self.custom_load_called = True

        benchmark = CustomLoadBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)

        benchmark._load_data(mock_connection)

        assert benchmark.custom_load_called
        mock_connection.execute.assert_called_with("CREATE TABLE custom_table (id INT)")


class TestMockBaseBenchmarkErrorHandling:
    def test_database_connection_error(self):
        benchmark = MockBaseBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)
        mock_connection.execute.side_effect = DatabaseError("Connection failed")

        with pytest.raises(DatabaseError, match="Connection failed"):
            benchmark.run_query(1, mock_connection)

    def test_query_execution_timeout(self):
        benchmark = MockBaseBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)
        mock_connection.execute.side_effect = TimeoutError("Query timed out")

        with pytest.raises(TimeoutError, match="Query timed out"):
            benchmark.run_query(1, mock_connection)

    def test_result_fetching_error(self):
        benchmark = MockBaseBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)
        mock_cursor = Mock()
        mock_connection.execute.return_value = mock_cursor
        mock_connection.fetchall.side_effect = DatabaseError("Failed to fetch results")

        with pytest.raises(DatabaseError, match="Failed to fetch results"):
            benchmark.run_query(1, mock_connection, fetch_results=True)

    def test_setup_logging_error_handling(self, caplog):
        benchmark = FailingBenchmark(fail_mode="generate_data")
        mock_connection = Mock(spec=DatabaseConnection)

        with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
            benchmark.setup_database(mock_connection)

        assert "Database setup failed" in caplog.text

    def test_query_execution_logging_error_handling(self, caplog):
        benchmark = MockBaseBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)
        mock_connection.execute.side_effect = DatabaseError("Query failed")

        with caplog.at_level(logging.ERROR), pytest.raises(DatabaseError):
            benchmark.run_query(1, mock_connection)

        assert "Query 1 execution failed" in caplog.text


class TestMockBaseBenchmarkIntegration:
    def test_full_benchmark_workflow(self):
        benchmark = MockBaseBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)
        mock_cursor = Mock()
        mock_connection.execute.return_value = mock_cursor
        mock_connection.fetchall.return_value = [{"id": 1, "value": "test"}]

        result = benchmark.run_benchmark(
            mock_connection,
            query_ids=[1, 2],
            fetch_results=True,
            setup_database=True,
        )

        assert benchmark._data_generated
        assert benchmark._load_data_called
        assert result["successful_queries"] == 2
        assert result["failed_queries"] == 0
        assert len(result["query_results"]) == 2

        formatted = benchmark.format_results(result)
        assert "Benchmark: MockBaseBenchmark" in formatted
        assert "Successful: 2" in formatted
        assert "Failed: 0" in formatted

    def test_benchmark_with_mixed_success_failure(self):
        benchmark = MockBaseBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)
        mock_cursor = Mock()

        def mock_execute(query):
            if "nonexistent_table" in query:
                raise DatabaseError("Table does not exist")
            return mock_cursor

        mock_connection.execute.side_effect = mock_execute
        mock_connection.fetchall.return_value = [{"result": "success"}]

        result = benchmark.run_benchmark(
            mock_connection,
            query_ids=[1, "error_query", 2],
            fetch_results=True,
            setup_database=True,
        )

        assert result["total_queries"] == 3
        assert result["successful_queries"] == 2
        assert result["failed_queries"] == 1

        error_result = next(r for r in result["query_results"] if "error" in r)
        assert error_result["query_id"] == "error_query"
        assert "Table does not exist" in error_result["error"]

    def test_benchmark_performance_metrics(self):
        benchmark = MockBaseBenchmark()
        mock_connection = Mock(spec=DatabaseConnection)
        mock_cursor = Mock()
        mock_connection.execute.return_value = mock_cursor
        mock_connection.fetchall.return_value = []

        with (
            patch.object(
                benchmark,
                "run_query",
                side_effect=[
                    {
                        "query_id": 1,
                        "execution_time_seconds": 1.0,
                        "query_text": "SELECT 1",
                        "results": None,
                        "row_count": 0,
                    },
                    {
                        "query_id": 2,
                        "execution_time_seconds": 1.0,
                        "query_text": "SELECT 2",
                        "results": None,
                        "row_count": 0,
                    },
                    {
                        "query_id": "complex",
                        "execution_time_seconds": 1.0,
                        "query_text": "SELECT 3",
                        "results": None,
                        "row_count": 0,
                    },
                ],
            ),
            patch("benchbox.base.elapsed_seconds", return_value=7.0),
        ):
            result = benchmark.run_benchmark(mock_connection, query_ids=[1, 2, "complex"], setup_database=False)

        assert result["average_query_time"] == 1.0
        assert result["min_query_time"] == 1.0
        assert result["max_query_time"] == 1.0
        assert result["total_execution_time"] == 7.0


class TestMockBaseBenchmarkResultHelpers:
    def test_create_enhanced_benchmark_result_delegates_to_impl(self):
        benchmark = MockBaseBenchmark()
        expected = object()
        benchmark._impl = Mock()
        benchmark._impl.create_enhanced_benchmark_result.return_value = expected

        result = benchmark.create_enhanced_benchmark_result(
            platform="duckdb",
            query_results=[{"query_id": "Q1"}],
            execution_metadata={"mode": "sql"},
            duration_seconds=1.0,
            validation_status="FAILED",
        )

        assert result is expected
        benchmark._impl.create_enhanced_benchmark_result.assert_called_once_with(
            platform="duckdb",
            query_results=[{"query_id": "Q1"}],
            execution_metadata={"mode": "sql"},
            phases=None,
            resource_utilization=None,
            performance_characteristics=None,
            duration_seconds=1.0,
            validation_status="FAILED",
        )

    def test_create_enhanced_benchmark_result_builds_result_with_snapshot(self):
        benchmark = MockBaseBenchmark()
        result = benchmark.create_enhanced_benchmark_result(
            platform="duckdb",
            query_results=[
                {
                    "query_id": "Q1",
                    "execution_time_seconds": 1.25,
                    "row_count": 3,
                    "query_text": "select 1",
                    "status": "SUCCESS",
                }
            ],
            execution_metadata={
                "mode": "sql",
                "run_config": {
                    "compression": {"type": "zstd", "level": 3},
                    "seed": 7,
                    "phases": ["power"],
                    "query_subset": ["Q1"],
                    "tuning_mode": "auto",
                    "tuning_config": {"threads": 4},
                    "platform_options": {"memory_limit": "1GB"},
                    "table_mode": "iceberg",
                },
            },
            duration_seconds=2.5,
            validation_status="PASSED",
            validation_details={"ok": True},
            system_profile={"cpu": "test"},
            table_statistics={"lineitem": 100},
            data_loading_time=1.5,
            platform_info={"version": "1.0", "client_version": "2.0"},
            performance_snapshot={"peak_memory_mb": 10},
        )

        assert result.platform == "duckdb"
        assert result.duration_seconds == 2.5
        assert result.validation_status == "PASSED"
        assert result.validation_details == {"ok": True}
        assert result.execution_metadata == {
            "mode": "sql",
            "run_config": {
                "compression": {"type": "zstd", "level": 3},
                "seed": 7,
                "phases": ["power"],
                "query_subset": ["Q1"],
                "tuning_mode": "auto",
                "tuning_config": {"threads": 4},
                "platform_options": {"memory_limit": "1GB"},
                "table_mode": "iceberg",
            },
            "execution_mode": "sql",
        }
        assert result.table_statistics == {"lineitem": {"rows": 100}}
        assert result.data_loading_time == 1.5
        assert result.platform_info == {
            "platform_name": "duckdb",
            "execution_mode": "sql",
            "platform_version": "1.0",
            "client_library_version": "2.0",
            "configuration": {"version": "1.0", "client_version": "2.0"},
        }
        assert result.performance_summary == {"peak_memory_mb": 10}
        assert result.performance_characteristics == {"peak_memory_mb": 10}
        assert result.query_results[0]["query_id"] == "Q1"
        assert result.query_results[0]["execution_time_seconds"] == 1.25

    def test_create_minimal_benchmark_result_sets_metadata_override(self):
        benchmark = MockBaseBenchmark()
        benchmark.name = "TPC-H Wrapper"

        result = benchmark.create_minimal_benchmark_result(
            validation_status="FAILED",
            validation_details={"reason": "cancelled"},
            duration_seconds=0.75,
            platform="duckdb",
            execution_metadata={"mode": "sql", "reason": "manual-stop"},
            system_profile={"cpu": "m-series"},
            phases={"setup": {"status": "SKIPPED", "duration_ms": 0}},
        )

        assert result.validation_status == "FAILED"
        assert result.validation_details == {"reason": "cancelled"}
        assert result.duration_seconds == 0.75
        assert result.system_profile == {"cpu": "m-series"}
        assert result.execution_metadata["result_type"] == "minimal"
        assert result.execution_metadata["status"] == "FAILED"
        assert result.execution_metadata["benchmark_id"] == "tpc_h_wrapper"
        assert result.execution_metadata["mode"] == "sql"
        assert result.execution_metadata["reason"] == "manual-stop"
        assert result.execution_metadata["execution_mode"] == "sql"
        assert result.execution_metadata["phase_status"] == {"setup": {"status": "SKIPPED", "duration_ms": 0}}
        assert result.benchmark_id == "tpc_h_wrapper"
        assert result.query_results == []

    def test_resolve_output_dir_requires_configured_path(self):
        benchmark = MockBaseBenchmark()
        benchmark.output_dir = None

        with pytest.raises(RuntimeError, match="output directory is not configured"):
            benchmark._resolve_output_dir()

    def test_validate_preflight_creates_and_validates_output_dir(self, tmp_path):
        benchmark = MockBaseBenchmark()
        output_dir = tmp_path / "tpch-datagen"

        result = benchmark.validate_preflight(output_dir=output_dir, benchmark_name="tpch")

        assert result.is_valid is True
        assert result.errors == []
        assert result.details["benchmark_type"] == "tpch"
        assert Path(result.details["output_dir"]) == output_dir
        assert output_dir.exists()

    def test_validate_manifest_passes_string_paths_to_validation_engine(self, tmp_path):
        benchmark = MockBaseBenchmark()
        expected = SimpleNamespace(is_valid=True, errors=[], warnings=[], details={})

        with patch(
            "benchbox.core.validation.DataValidationEngine.validate_generated_data",
            return_value=expected,
        ) as mock_validate:
            result = benchmark.validate_manifest(manifest_path=str(tmp_path / "manifest.json"))

        assert result is expected
        manifest_arg = mock_validate.call_args.args[0]
        assert isinstance(manifest_arg, Path)
        assert manifest_arg == tmp_path / "manifest.json"

    def test_validate_manifest_handles_handlers_without_joinpath(self):
        benchmark = MockBaseBenchmark()

        with patch.object(benchmark, "_resolve_output_dir", return_value=object()):
            result = benchmark.validate_manifest()

        assert result.is_valid is False
        assert result.errors == ["Manifest path is not available"]
        assert result.details == {"benchmark": "mockbase"}

    def test_validate_loaded_data_delegates_to_database_validation_engine(self):
        benchmark = MockBaseBenchmark()
        connection = object()
        expected = SimpleNamespace(is_valid=True, errors=[], warnings=[], details={})

        with patch(
            "benchbox.core.validation.DatabaseValidationEngine.validate_loaded_data",
            return_value=expected,
        ) as mock_validate:
            result = benchmark.validate_loaded_data(connection, benchmark_name="tpch")

        assert result is expected
        mock_validate.assert_called_once_with(connection, "tpch", benchmark.scale_factor)

    def test_create_result_builder_populates_platform_and_run_config(self):
        benchmark = MockBaseBenchmark()

        builder = benchmark._create_result_builder(
            "duckdb",
            {
                "mode": "dataframe",
                "benchmark_id": "custom_id",
                "run_config": {
                    "compression": {"type": "gzip", "level": 6},
                    "seed": 42,
                    "phases": ["power", "throughput"],
                    "query_subset": ["Q1"],
                    "tuning_mode": "manual",
                    "tuning_config": {"threads": 2},
                    "platform_options": {"temp_directory": "/tmp"},
                    "table_mode": "external",
                },
            },
            lambda name: f"normalized-{name}",
            platform_info={"platform_version": "1.5", "client_library_version": "2.0"},
            test_execution_type="power",
            execution_id="exec123",
        )

        assert builder._benchmark.name == "MockBaseBenchmark"
        assert builder._benchmark.benchmark_id == "custom_id"
        assert builder._benchmark.test_type == "power"
        assert builder._platform.name == "duckdb"
        assert builder._platform.execution_mode == "dataframe"
        assert builder._platform.config == {
            "platform_version": "1.5",
            "client_library_version": "2.0",
        }
        assert builder._execution_id == "exec123"
        assert builder._run_config.compression_type == "gzip"
        assert builder._run_config.compression_level == 6
        assert builder._run_config.seed == 42
        assert builder._run_config.phases == ["power", "throughput"]
        assert builder._run_config.query_subset == ["Q1"]
        assert builder._run_config.tuning_mode == "manual"
        assert builder._run_config.tuning_config == {"threads": 2}
        assert builder._run_config.platform_options == {"temp_directory": "/tmp"}
        assert builder._run_config.table_mode == "external"

    def test_populate_builder_adds_query_results_and_metadata(self):
        benchmark = MockBaseBenchmark()
        builder = Mock()

        benchmark._populate_builder(
            builder,
            execution_metadata={"mode": "sql"},
            query_results=[{"query_id": "Q1"}, {"query_id": "Q2"}],
            normalize_query_result=lambda item: {"normalized": item["query_id"]},
            duration_seconds=2.5,
            table_statistics={"lineitem": 100},
            data_loading_time=1.5,
            validation_status="FAILED",
            validation_details={"reason": "boom"},
            system_profile={"cpu": "test"},
            tunings_applied={"threads": 8},
            tuning_config_hash="abc123",
            tuning_source_file="tuning.yaml",
            tuning_source="auto_discovered",
            query_plans_captured=2,
            plan_capture_failures=1,
            plan_capture_errors=[{"query_id": "Q2"}],
        )

        assert builder.add_query_result.call_args_list == [
            (({"normalized": "Q1"},),),
            (({"normalized": "Q2"},),),
        ]
        builder.add_table_stats.assert_called_once_with("lineitem", 100)
        builder.set_loading_time.assert_called_once_with(1500.0)
        builder.set_total_duration.assert_called_once_with(2.5)
        builder.set_execution_metadata.assert_called_once_with({"mode": "sql"})
        builder.set_validation_status.assert_called_once_with("FAILED", {"reason": "boom"})
        builder.set_system_profile.assert_called_once_with({"cpu": "test"})
        builder.set_tuning_info.assert_called_once_with(
            tunings_applied={"threads": 8},
            config_hash="abc123",
            source_file="tuning.yaml",
            source="auto_discovered",
        )
        builder.add_plan_capture_stats.assert_called_once_with(2, 1, [{"query_id": "Q2"}])

    def test_apply_phases_to_builder_handles_phase_objects_and_dicts(self):
        benchmark = MockBaseBenchmark()
        builder = Mock()

        from benchbox.core.results.models import ExecutionPhases, SetupPhase

        phases_obj = ExecutionPhases(setup=SetupPhase())
        benchmark._apply_phases_to_builder(builder, phases_obj, ExecutionPhases)
        benchmark._apply_phases_to_builder(
            builder,
            {"power": {"status": "COMPLETED", "duration_ms": 12, "stream_id": 3}},
            ExecutionPhases,
        )

        builder.set_execution_phases.assert_called_once_with(phases_obj)
        builder.set_phase_status.assert_called_once_with(
            "power",
            "COMPLETED",
            12,
            stream_id=3,
        )

    def test_attach_performance_snapshot_supports_snapshot_objects_and_fallbacks(self):
        benchmark = MockBaseBenchmark()
        result = SimpleNamespace(performance_summary={}, performance_characteristics={})

        from benchbox.monitoring.performance import PerformanceSnapshot

        snapshot = PerformanceSnapshot(
            timestamp="2026-03-25T00:00:00Z",
            counters={"queries": 2},
            gauges={"memory_mb": 64.0},
            timings={},
            metadata={"platform": "duckdb"},
        )
        benchmark._attach_performance_snapshot(result, None, performance_snapshot=snapshot)

        assert result.performance_summary["counters"] == {"queries": 2}
        assert result.performance_characteristics["counters"] == {"queries": 2}

        fallback_result = SimpleNamespace(performance_summary={}, performance_characteristics={})
        benchmark._attach_performance_snapshot(fallback_result, {"throughput": 7.5})

        assert fallback_result.performance_summary == {"throughput": 7.5}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
