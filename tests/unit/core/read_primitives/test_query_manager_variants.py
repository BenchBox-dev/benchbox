# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.query_catalog_base import QuerySkippedError
from benchbox.core.read_primitives.queries import ReadPrimitivesQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryManagerVariantRetrieval:
    @pytest.fixture
    def mock_catalog_with_variants(self, monkeypatch, tmp_path):
        catalog_yaml = """
version: 1
queries:
  - id: query_base_only
    category: test
    sql: SELECT * FROM orders

  - id: query_with_duckdb_variant
    category: test
    sql: SELECT * FROM orders
    variants:
      duckdb: SELECT * FROM orders USING SAMPLE 10%

  - id: query_with_multiple_variants
    category: test
    sql: SELECT * FROM orders
    variants:
      duckdb: SELECT * FROM orders USING SAMPLE 10%
      bigquery: SELECT * FROM `orders` TABLESAMPLE SYSTEM (10 PERCENT)
      snowflake: SELECT * FROM orders SAMPLE (10)

  - id: query_skip_on_duckdb
    category: test
    sql: SELECT JSON_EXTRACT(data, '$.field') FROM table
    skip_on: [duckdb, sqlite]

  - id: query_with_variant_and_skip
    category: test
    sql: SELECT * FROM orders
    variants:
      bigquery: SELECT * FROM `orders`
    skip_on: [sqlite]
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

    def test_get_query_without_dialect_returns_base(self, mock_catalog_with_variants):
        manager = ReadPrimitivesQueryManager()

        query = manager.get_query("query_with_duckdb_variant")
        assert query == "SELECT * FROM orders"
        assert "USING SAMPLE" not in query

    def test_get_query_with_matching_variant_returns_variant(self, mock_catalog_with_variants):
        manager = ReadPrimitivesQueryManager()

        query = manager.get_query("query_with_duckdb_variant", dialect="duckdb")
        assert "USING SAMPLE 10%" in query

    def test_get_query_with_non_matching_variant_returns_base(self, mock_catalog_with_variants):
        manager = ReadPrimitivesQueryManager()

        query = manager.get_query("query_with_duckdb_variant", dialect="bigquery")
        assert query == "SELECT * FROM orders"
        assert "USING SAMPLE" not in query

    def test_get_query_with_multiple_variants_returns_correct_one(self, mock_catalog_with_variants):
        manager = ReadPrimitivesQueryManager()

        duckdb_query = manager.get_query("query_with_multiple_variants", dialect="duckdb")
        assert "USING SAMPLE" in duckdb_query

        bigquery_query = manager.get_query("query_with_multiple_variants", dialect="bigquery")
        assert "TABLESAMPLE SYSTEM" in bigquery_query

        snowflake_query = manager.get_query("query_with_multiple_variants", dialect="snowflake")
        assert "SAMPLE (10)" in snowflake_query

    def test_get_query_skip_on_raises_query_skipped_error(self, mock_catalog_with_variants):
        manager = ReadPrimitivesQueryManager()

        with pytest.raises(QuerySkippedError) as exc_info:
            manager.get_query("query_skip_on_duckdb", dialect="duckdb")

        assert "not supported on dialect" in str(exc_info.value)
        assert "duckdb" in str(exc_info.value).lower()

    def test_get_query_skip_on_with_different_dialect_works(self, mock_catalog_with_variants):
        manager = ReadPrimitivesQueryManager()

        query = manager.get_query("query_skip_on_duckdb", dialect="bigquery")
        assert "JSON_EXTRACT" in query

    def test_get_query_dialect_case_insensitive(self, mock_catalog_with_variants):
        manager = ReadPrimitivesQueryManager()

        query1 = manager.get_query("query_with_duckdb_variant", dialect="duckdb")
        query2 = manager.get_query("query_with_duckdb_variant", dialect="DuckDB")
        query3 = manager.get_query("query_with_duckdb_variant", dialect="DUCKDB")

        assert query1 == query2 == query3
        assert "USING SAMPLE" in query1

    def test_get_query_skip_on_case_insensitive(self, mock_catalog_with_variants):

        manager = ReadPrimitivesQueryManager()

        with pytest.raises(ValueError):
            manager.get_query("query_skip_on_duckdb", dialect="duckdb")

        with pytest.raises(ValueError):
            manager.get_query("query_skip_on_duckdb", dialect="DuckDB")

        with pytest.raises(ValueError):
            manager.get_query("query_skip_on_duckdb", dialect="DUCKDB")

    def test_get_query_base_only_query_with_dialect(self, mock_catalog_with_variants):
        manager = ReadPrimitivesQueryManager()

        query = manager.get_query("query_base_only", dialect="duckdb")
        assert query == "SELECT * FROM orders"

    def test_get_query_invalid_query_id_raises_valueerror(self, mock_catalog_with_variants):
        manager = ReadPrimitivesQueryManager()

        with pytest.raises(ValueError) as exc_info:
            manager.get_query("nonexistent_query_id")

        assert "Invalid query ID" in str(exc_info.value)

    def test_get_query_variant_with_skip_both_present(self, mock_catalog_with_variants):

        manager = ReadPrimitivesQueryManager()

        with pytest.raises(ValueError):
            manager.get_query("query_with_variant_and_skip", dialect="sqlite")

        query = manager.get_query("query_with_variant_and_skip", dialect="bigquery")
        assert "`orders`" in query

        query = manager.get_query("query_with_variant_and_skip", dialect="duckdb")
        assert query == "SELECT * FROM orders"


class TestQueryManagerVariantIntegration:
    def test_actual_catalog_variant_lookup_works(self):

        manager = ReadPrimitivesQueryManager()

        for query_id in ["map_construction", "map_access", "map_keys_values"]:
            query = manager.get_query(query_id, dialect="datafusion")
            assert "MAP(" in query.upper()
            assert "MAP_FROM_ENTRIES" not in query.upper()

        with pytest.raises(ValueError, match="not supported on dialect"):
            manager.get_query("array_distinct", dialect="datafusion")

    def test_actual_catalog_datafusion_rewrites_for_known_failures(self):
        manager = ReadPrimitivesQueryManager()

        intrinsic = manager.get_query("intrinsic_to_date", dialect="datafusion")
        assert "TO_DATE('1995-03-15')" in intrinsic.upper()

        max_by = manager.get_query("max_by_simple", dialect="datafusion")
        assert "ROW_NUMBER()" in max_by.upper()
        assert "MAX_BY" not in max_by.upper()

    def test_actual_catalog_datafusion_skips_unsupported_json_queries(self):
        manager = ReadPrimitivesQueryManager()

        for query_id in ["json_extract_simple", "json_extract_nested", "json_aggregates"]:
            with pytest.raises(ValueError, match="not supported on dialect"):
                manager.get_query(query_id, dialect="datafusion")

    def test_actual_catalog_databricks_skips_unsupported_queries(self):
        manager = ReadPrimitivesQueryManager()

        for query_id in [
            "json_extract_nested",
            "json_aggregates",
            "timeseries_trend_analysis",
            "list_reduce",
        ]:
            with pytest.raises(QuerySkippedError, match="not supported on dialect"):
                manager.get_query(query_id, dialect="databricks")

    def test_actual_catalog_clickhouse_uses_lowercase_window_functions(self):
        manager = ReadPrimitivesQueryManager()

        query = manager.get_query("window_lead_lag_same_frame", dialect="clickhouse")

        assert "lag(o_totalprice, 1)" in query
        assert "lead(o_totalprice, 1)" in query
        assert "LAG(o_totalprice, 1)" not in query
        assert "LEAD(o_totalprice, 1)" not in query
        assert "toDate('1995-01-01')" in query

    def test_actual_catalog_clickhouse_lowercases_timeseries_lag(self):
        manager = ReadPrimitivesQueryManager()

        query = manager.get_query("timeseries_trend_analysis", dialect="clickhouse")

        assert "lag(monthly_revenue, 1)" in query
        assert "LAG(monthly_revenue, 1)" not in query

    def test_actual_catalog_clickhouse_skips_missing_cume_dist(self):
        manager = ReadPrimitivesQueryManager()

        for query_id in ("window_multiple_orderings", "qualify_cume_dist"):
            with pytest.raises(QuerySkippedError, match="not supported on dialect"):
                manager.get_query(query_id, dialect="clickhouse")

    def test_query_manager_initialized_successfully(self):

        manager = ReadPrimitivesQueryManager()

        assert manager.catalog_version >= 1

        all_queries = manager.get_all_queries()
        assert len(all_queries) > 100

    def test_get_query_with_none_dialect_returns_base(self):
        manager = ReadPrimitivesQueryManager()

        all_queries = manager.get_all_queries()
        first_query_id = list(all_queries.keys())[0]

        query1 = manager.get_query(first_query_id, dialect=None)
        query2 = manager.get_query(first_query_id)

        assert query1 == query2


