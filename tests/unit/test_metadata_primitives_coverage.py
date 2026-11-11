# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

from benchbox.metadata_primitives import MetadataPrimitives

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_IMPL_PATH = "benchbox.metadata_primitives.MetadataPrimitivesBenchmark"


def _make_facade(**kwargs):

    with patch(_IMPL_PATH) as MockImpl:
        facade = MetadataPrimitives(scale_factor=1.0, **kwargs)
        mock_impl = MockImpl.return_value
    return facade, mock_impl


class TestInstantiation:
    def test_creates_inner_impl(self):
        with patch(_IMPL_PATH) as MockImpl:
            MetadataPrimitives(scale_factor=2.0)

        MockImpl.assert_called_once()
        call_kwargs = MockImpl.call_args
        assert call_kwargs.kwargs.get("scale_factor") == 2.0 or call_kwargs.args[0] == 2.0

    def test_impl_stored_as_attribute(self):
        with patch(_IMPL_PATH) as MockImpl:
            facade = MetadataPrimitives()
            assert facade._impl is MockImpl.return_value


class TestGenerateData:
    def test_delegates_to_impl(self):
        facade, mock_impl = _make_facade()
        mock_impl.generate_data.return_value = {}

        result = facade.generate_data()

        mock_impl.generate_data.assert_called_once_with(None)
        assert result == {}

    def test_passes_tables_argument(self):
        facade, mock_impl = _make_facade()
        mock_impl.generate_data.return_value = {}

        facade.generate_data(tables=["lineitem"])

        mock_impl.generate_data.assert_called_once_with(["lineitem"])


class TestGetSchema:
    def test_delegates_to_impl(self):
        facade, mock_impl = _make_facade()
        expected = {"lineitem": {"columns": []}}
        mock_impl.get_schema.return_value = expected

        result = facade.get_schema()

        mock_impl.get_schema.assert_called_once_with()
        assert result is expected


class TestGetCreateTablesSql:
    def test_delegates_with_defaults(self):
        facade, mock_impl = _make_facade()
        mock_impl.get_create_tables_sql.return_value = "CREATE TABLE t1 ()"

        result = facade.get_create_tables_sql()

        mock_impl.get_create_tables_sql.assert_called_once_with(dialect="standard", tuning_config=None)
        assert result == "CREATE TABLE t1 ()"

    def test_delegates_with_dialect(self):
        facade, mock_impl = _make_facade()

        facade.get_create_tables_sql(dialect="snowflake")

        mock_impl.get_create_tables_sql.assert_called_once_with(dialect="snowflake", tuning_config=None)

    def test_delegates_with_tuning_config(self):
        facade, mock_impl = _make_facade()
        tuning = MagicMock()

        facade.get_create_tables_sql(dialect="duckdb", tuning_config=tuning)

        mock_impl.get_create_tables_sql.assert_called_once_with(dialect="duckdb", tuning_config=tuning)


class TestGetTableNames:
    def test_delegates_to_impl(self):
        facade, mock_impl = _make_facade()
        expected = ["lineitem", "orders", "customer"]
        mock_impl.get_table_names.return_value = expected

        result = facade.get_table_names()

        mock_impl.get_table_names.assert_called_once_with()
        assert result is expected


class TestExecuteQuery:
    def test_delegates_with_required_args(self):
        facade, mock_impl = _make_facade()
        conn = MagicMock()
        mock_result = MagicMock()
        mock_impl.execute_query.return_value = mock_result

        result = facade.execute_query("Q1", conn)

        mock_impl.execute_query.assert_called_once_with("Q1", conn, dialect=None)
        assert result is mock_result

    def test_delegates_with_dialect(self):
        facade, mock_impl = _make_facade()
        conn = MagicMock()

        facade.execute_query("SHOW_TABLES", conn, dialect="duckdb")

        mock_impl.execute_query.assert_called_once_with("SHOW_TABLES", conn, dialect="duckdb")


class TestRunBenchmark:
    def test_delegates_with_defaults(self):
        facade, mock_impl = _make_facade()
        conn = MagicMock()
        mock_result = MagicMock()
        mock_impl.run_benchmark.return_value = mock_result

        result = facade.run_benchmark(conn)

        mock_impl.run_benchmark.assert_called_once_with(
            conn, dialect=None, categories=None, query_ids=None, iterations=1
        )
        assert result is mock_result

    def test_delegates_with_all_args(self):
        facade, mock_impl = _make_facade()
        conn = MagicMock()

        facade.run_benchmark(conn, dialect="snowflake", categories=["schema"], query_ids=["Q1"], iterations=3)

        mock_impl.run_benchmark.assert_called_once_with(
            conn, dialect="snowflake", categories=["schema"], query_ids=["Q1"], iterations=3
        )


class TestSetupComplexity:
    def test_delegates(self):
        facade, mock_impl = _make_facade()
        conn = MagicMock()
        config = MagicMock()
        expected = MagicMock()
        mock_impl.setup_complexity.return_value = expected

        result = facade.setup_complexity(conn, "duckdb", config)

        mock_impl.setup_complexity.assert_called_once_with(conn, "duckdb", config)
        assert result is expected


class TestTeardownComplexity:
    def test_delegates(self):
        facade, mock_impl = _make_facade()
        conn = MagicMock()
        generated = MagicMock()

        facade.teardown_complexity(conn, "duckdb", generated)

        mock_impl.teardown_complexity.assert_called_once_with(conn, "duckdb", generated)


class TestRunComplexityBenchmark:
    def test_delegates_with_defaults(self):
        facade, mock_impl = _make_facade()
        conn = MagicMock()
        config = MagicMock()
        expected = MagicMock()
        mock_impl.run_complexity_benchmark.return_value = expected

        result = facade.run_complexity_benchmark(conn, "duckdb", config)

        mock_impl.run_complexity_benchmark.assert_called_once_with(
            conn, "duckdb", config, iterations=1, categories=None
        )
        assert result is expected

    def test_delegates_with_extra_args(self):
        facade, mock_impl = _make_facade()
        conn = MagicMock()
        config = MagicMock()

        facade.run_complexity_benchmark(conn, "snowflake", config, iterations=5, categories=["wide_tables"])

        mock_impl.run_complexity_benchmark.assert_called_once_with(
            conn, "snowflake", config, iterations=5, categories=["wide_tables"]
        )


class TestGetComplexityCategories:
    def test_delegates(self):
        facade, mock_impl = _make_facade()
        expected = ["wide_tables", "nested_views", "large_catalog"]
        mock_impl.get_complexity_categories.return_value = expected

        result = facade.get_complexity_categories()

        mock_impl.get_complexity_categories.assert_called_once_with()
        assert result is expected


class TestGetBenchmarkInfo:
    def test_returns_dict_with_required_keys(self):
        facade, mock_impl = _make_facade()
        mock_impl.get_queries.return_value = ["Q1", "Q2", "Q3"]
        mock_impl.get_query_categories.return_value = ["schema_discovery", "column_introspection"]
        mock_impl.get_complexity_categories.return_value = ["wide_tables"]

        info = facade.get_benchmark_info()

        assert info["name"] == "Metadata Primitives Benchmark"
        assert info["version"] == "1.0"
        assert info["query_count"] == 3
        assert info["categories"] == ["schema_discovery", "column_introspection"]
        assert info["complexity_categories"] == ["wide_tables"]
        assert "description" in info

    def test_delegates_get_queries_for_count(self):
        facade, mock_impl = _make_facade()
        mock_impl.get_queries.return_value = list(range(10))
        mock_impl.get_query_categories.return_value = []
        mock_impl.get_complexity_categories.return_value = []

        info = facade.get_benchmark_info()

        mock_impl.get_queries.assert_called_once_with()
        assert info["query_count"] == 10


