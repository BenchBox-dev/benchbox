# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestBenchmarkVariantIntegration:
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
      duckdb: SELECT order_id FROM orders USING SAMPLE 10%

  - id: query_with_clickhouse_variant
    category: test
    sql: SELECT CARDINALITY(parts) AS part_count FROM supplier_parts
    variants:
      clickhouse: SELECT length(parts) AS part_count FROM supplier_parts

  - id: query_skip_on_duckdb
    category: test
    sql: SELECT JSON_EXTRACT(data, '$.field') FROM table
    skip_on: [duckdb]

  - id: query_with_multiple_variants
    category: test
    sql: SELECT * FROM orders
    variants:
      duckdb: SELECT * FROM orders USING SAMPLE 10%
      bigquery: SELECT * FROM `orders` TABLESAMPLE SYSTEM (10 PERCENT)
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

    def test_get_queries_without_dialect_returns_all_base_queries(self, mock_catalog_with_variants):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries()

        assert len(queries) == 5
        assert "query_base_only" in queries
        assert "query_with_duckdb_variant" in queries
        assert "query_with_clickhouse_variant" in queries
        assert "query_skip_on_duckdb" in queries
        assert "query_with_multiple_variants" in queries

        assert "USING SAMPLE" not in queries["query_with_duckdb_variant"]
        assert "length(parts)" not in queries["query_with_clickhouse_variant"]
        assert "TABLESAMPLE" not in queries["query_with_multiple_variants"]

    def test_get_queries_with_dialect_skips_skip_on_queries(self, mock_catalog_with_variants):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="duckdb")

        assert len(queries) == 4
        assert "query_base_only" in queries
        assert "query_with_duckdb_variant" in queries
        assert "query_with_clickhouse_variant" in queries
        assert "query_with_multiple_variants" in queries
        assert "query_skip_on_duckdb" not in queries

    def test_get_queries_with_dialect_uses_variants(self, mock_catalog_with_variants):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="duckdb")

        assert "USING SAMPLE" in queries["query_with_duckdb_variant"]
        assert '"order_id"' not in queries["query_with_duckdb_variant"]

        assert "USING SAMPLE" in queries["query_with_multiple_variants"]

        assert "orders" in queries["query_base_only"].lower()

    def test_get_queries_with_non_matching_dialect(self, mock_catalog_with_variants):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="snowflake")

        assert len(queries) == 5

        assert "orders" in queries["query_with_duckdb_variant"].lower()
        assert "USING SAMPLE" not in queries["query_with_duckdb_variant"].upper()

    def test_get_queries_with_bigquery_uses_correct_variant(self, mock_catalog_with_variants):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="bigquery")

        assert "TABLESAMPLE SYSTEM" in queries["query_with_multiple_variants"]
        assert "USING SAMPLE" not in queries["query_with_multiple_variants"]

    def test_get_queries_skip_on_case_insensitive(self, mock_catalog_with_variants):

        benchmark = ReadPrimitivesBenchmark()

        queries_lower = benchmark.get_queries(dialect="duckdb")
        queries_upper = benchmark.get_queries(dialect="DUCKDB")
        queries_mixed = benchmark.get_queries(dialect="DuckDB")

        assert "query_skip_on_duckdb" not in queries_lower
        assert "query_skip_on_duckdb" not in queries_upper
        assert "query_skip_on_duckdb" not in queries_mixed

        assert len(queries_lower) == len(queries_upper) == len(queries_mixed) == 4

    def test_get_queries_keeps_clickhouse_variant_verbatim(self, mock_catalog_with_variants):

        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="clickhouse")

        assert queries["query_with_clickhouse_variant"] == "SELECT length(parts) AS part_count FROM supplier_parts"
        assert "CHAR_LENGTH" not in queries["query_with_clickhouse_variant"]


class TestBenchmarkVariantEdgeCases:
    def test_get_queries_handles_empty_variants_dict(self, monkeypatch, tmp_path):
        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    variants: {}
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

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries(dialect="duckdb")

        assert "test_query" in queries
        assert queries["test_query"] == "SELECT 1"

    def test_get_queries_handles_empty_skip_on_list(self, monkeypatch, tmp_path):
        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    skip_on: []
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

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries(dialect="duckdb")

        assert "test_query" in queries


class TestBenchmarkWithActualCatalog:
    def test_get_queries_returns_all_queries_without_dialect(self):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries()

        assert len(queries) >= 157

    def test_get_queries_with_duckdb_skips_known_non_comparable_queries(self):
        benchmark = ReadPrimitivesBenchmark()

        queries_duckdb = benchmark.get_queries(dialect="duckdb")

        duckdb_skipped = {
            "fulltext_simple_search",
            "fulltext_boolean_search",
            "fulltext_phrase_search",
            "json_extract_simple",
        }
        assert duckdb_skipped.isdisjoint(queries_duckdb)

    def test_duckdb_array_of_struct_variant_preserves_inner_order(self):
        benchmark = ReadPrimitivesBenchmark()

        queries_duckdb = benchmark.get_queries(dialect="duckdb")
        array_sql = queries_duckdb["array_of_struct"].lower()

        assert "struct_pack" in array_sql
        assert "order by l_linenumber" in array_sql

    @pytest.mark.parametrize("dialect", ["bigquery", "databricks", "snowflake"])
    def test_cloud_dialects_skip_mysql_fulltext_queries(self, dialect):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect=dialect)

        assert "fulltext_simple_search" not in queries
        assert "fulltext_boolean_search" not in queries
        assert "fulltext_phrase_search" not in queries

    def test_lakesail_platform_skips_uat_unsupported_queries(self):
        benchmark = ReadPrimitivesBenchmark()

        skipped = set(benchmark.get_platform_skip_queries("LakeSail"))

        assert {
            "approx_top_k_lineitem",
            "window_moving_frame",
            "json_extract_nested",
            "json_aggregates",
            "fulltext_simple_search",
            "fulltext_boolean_search",
            "fulltext_phrase_search",
            "approx_quantiles_array",
            "optimizer_scalar_subquery_flattening",
            "groupby_all_simple",
            "groupby_all_complex",
            "orderby_all_simple",
            "orderby_all_desc",
            "list_transform",
            "list_filter",
            "list_reduce",
            "asof_join_basic",
            "pivot_basic",
        }.issubset(skipped)

    def test_clickhouse_keeps_timeout_only_query_available(self):
        benchmark = ReadPrimitivesBenchmark()

        queries_clickhouse = benchmark.get_queries(dialect="clickhouse")

        assert "aggregation_groupby_large" in queries_clickhouse

    def test_clickhouse_uses_native_array_length_variant(self):
        benchmark = ReadPrimitivesBenchmark()

        queries_clickhouse = benchmark.get_queries(dialect="clickhouse")

        assert "array_length" in queries_clickhouse
        assert "length(parts)" in queries_clickhouse["array_length"]
        assert "CHAR_LENGTH" not in queries_clickhouse["array_length"]

    def test_snowflake_variant_bypasses_translation(self, monkeypatch):
        benchmark = ReadPrimitivesBenchmark()
        snowflake_variant = benchmark.query_manager.get_query("asof_join_basic", dialect="snowflake")
        translated_inputs: list[tuple[str, str]] = []
        original_translate = benchmark.translate_query_text

        def tracking_translate(query_text: str, target_dialect: str) -> str:
            translated_inputs.append((query_text, target_dialect))
            return original_translate(query_text, target_dialect)

        monkeypatch.setattr(benchmark, "translate_query_text", tracking_translate)

        queries_snowflake = benchmark.get_queries(dialect="snowflake")

        assert translated_inputs
        assert queries_snowflake["asof_join_basic"] == snowflake_variant
        assert (snowflake_variant, "snowflake") not in translated_inputs
        assert "MATCH_CONDITION" in queries_snowflake["asof_join_basic"].upper()

    def test_get_queries_with_duckdb_translates_correctly(self):

        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="duckdb")

        assert len(queries) > 0

        for query_id, query_sql in queries.items():
            assert query_sql
            assert len(query_sql) > 0
            assert "SELECT" in query_sql.upper() or "WITH" in query_sql.upper()


class TestModernSQLFeatures:
    def test_catalog_has_any_value_queries(self):

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries()

        assert "any_value_simple" in queries
        assert "any_value_with_filter" in queries
        assert "ANY_VALUE" in queries["any_value_simple"].upper()

    def test_catalog_has_group_by_all_queries(self):

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries()

        assert "groupby_all_simple" in queries
        assert "groupby_all_complex" in queries
        assert "GROUP BY ALL" in queries["groupby_all_simple"].upper()

    def test_catalog_has_order_by_all_queries(self):

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries()

        assert "orderby_all_simple" in queries
        assert "orderby_all_desc" in queries
        assert "ORDER BY ALL" in queries["orderby_all_simple"].upper()

    def test_duckdb_translation_preserves_group_order_by_all_keyword(self):

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries(dialect="duckdb")

        assert "groupby_all_simple" in queries
        assert "orderby_all_simple" in queries

        groupby_sql = queries["groupby_all_simple"].upper()
        orderby_sql = queries["orderby_all_simple"].upper()

        assert "GROUP BY ALL" in groupby_sql
        assert 'GROUP BY "ALL"' not in groupby_sql
        assert "ORDER BY ALL" in orderby_sql
        assert 'ORDER BY "ALL"' not in orderby_sql

    def test_catalog_has_array_queries(self):

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries()

        array_queries = [
            "array_agg_simple",
            "array_agg_distinct",
            "array_unnest",
            "array_contains",
            "array_length",
            "array_slice",
            "array_min_max",
            "array_sort",
            "array_distinct",
        ]
        for qid in array_queries:
            assert qid in queries, f"Missing array query: {qid}"

    def test_catalog_has_struct_queries(self):

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries()

        struct_queries = ["struct_construction", "struct_access", "array_of_struct"]
        for qid in struct_queries:
            assert qid in queries, f"Missing struct query: {qid}"

    def test_catalog_has_map_queries(self):

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries()

        map_queries = ["map_construction", "map_access", "map_keys_values"]
        for qid in map_queries:
            assert qid in queries, f"Missing map query: {qid}"

    def test_catalog_has_lambda_queries(self):
        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries()

        lambda_queries = ["list_transform", "list_filter", "list_reduce"]
        for qid in lambda_queries:
            assert qid in queries, f"Missing lambda query: {qid}"

    def test_catalog_has_asof_join_query(self):

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries()

        assert "asof_join_basic" in queries
        assert "ASOF JOIN" in queries["asof_join_basic"].upper()

    def test_catalog_has_pivot_queries(self):

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries()

        assert "pivot_basic" in queries
        assert "unpivot_basic" in queries
        assert "PIVOT" in queries["pivot_basic"].upper()
        assert "UNPIVOT" in queries["unpivot_basic"].upper()

    def test_bigquery_skips_unsupported_queries(self):

        benchmark = ReadPrimitivesBenchmark()
        queries_bigquery = benchmark.get_queries(dialect="bigquery")

        bigquery_skipped = [
            "fulltext_simple_search",
            "fulltext_boolean_search",
            "fulltext_phrase_search",
            "map_construction",
            "map_access",
            "map_keys_values",
            "list_transform",
            "list_filter",
            "list_reduce",
            "array_min_max",
            "array_sort",
            "array_distinct",
            "asof_join_basic",
        ]
        for qid in bigquery_skipped:
            assert qid not in queries_bigquery, f"BigQuery should skip: {qid}"

    def test_clickhouse_skips_pivot_queries(self):

        benchmark = ReadPrimitivesBenchmark()
        queries_clickhouse = benchmark.get_queries(dialect="clickhouse")

        clickhouse_skipped = ["pivot_basic", "unpivot_basic"]
        for qid in clickhouse_skipped:
            assert qid not in queries_clickhouse, f"ClickHouse should skip: {qid}"

    def test_clickhouse_skips_non_comparable_scalar_fallbacks(self):
        benchmark = ReadPrimitivesBenchmark()
        queries_clickhouse = benchmark.get_queries(dialect="clickhouse")

        clickhouse_skipped = [
            "intrinsic_appx_median",
            "window_moving_frame",
            "fulltext_simple_search",
            "fulltext_boolean_search",
            "fulltext_phrase_search",
            "statistical_percentiles",
        ]
        for qid in clickhouse_skipped:
            assert qid not in queries_clickhouse, f"ClickHouse should skip: {qid}"

    def test_datafusion_skips_non_comparable_any_value_fallbacks(self):
        benchmark = ReadPrimitivesBenchmark()
        queries_datafusion = benchmark.get_queries(dialect="datafusion")

        assert "any_value_simple" not in queries_datafusion
        assert "any_value_with_filter" not in queries_datafusion

    def test_duckdb_uses_list_functions_for_arrays(self):

        benchmark = ReadPrimitivesBenchmark()
        queries_duckdb = benchmark.get_queries(dialect="duckdb")

        array_contains_sql = queries_duckdb.get("array_contains", "")
        assert "list_contains" in array_contains_sql.lower()

        array_min_max_sql = queries_duckdb.get("array_min_max", "")
        assert "list_min" in array_min_max_sql.lower()
        assert "list_max" in array_min_max_sql.lower()

    def test_redshift_skips_only_semantically_inexact_window_queries(self):
        benchmark = ReadPrimitivesBenchmark()
        queries_redshift = benchmark.get_queries(dialect="redshift")

        assert "window_moving_frame" not in queries_redshift
        assert "window_running_sum" not in queries_redshift

    def test_redshift_skips_unsupported_statistical_functions(self):
        benchmark = ReadPrimitivesBenchmark()
        queries_redshift = benchmark.get_queries(dialect="redshift")

        assert "statistical_correlation" not in queries_redshift
        assert "timeseries_trend_analysis" not in queries_redshift

    def test_redshift_percentile_variant_preserves_group_cardinality(self):
        benchmark = ReadPrimitivesBenchmark()
        queries_redshift = benchmark.get_queries(dialect="redshift")

        percentile_sql = queries_redshift["statistical_percentiles"].upper()

        assert "SELECT DISTINCT" in percentile_sql
        assert "OVER (PARTITION BY" in percentile_sql

    def test_cloud_array_and_ordering_variants_keep_the_measured_operations(self):
        benchmark = ReadPrimitivesBenchmark()
        snowflake = benchmark.get_queries(dialect="snowflake")
        bigquery = benchmark.get_queries(dialect="bigquery")

        assert "ARRAY_SIZE(parts)" in snowflake["array_length"]
        assert "ARRAY_LENGTH(parts)" in bigquery["array_length"]
        for query_id in ("orderby_all_simple", "orderby_all_desc"):
            assert "ORDER BY ALL" not in bigquery[query_id].upper()
            assert "ORDER BY" in bigquery[query_id].upper()
        assert "UNIX_DATE(o_orderdate)" in bigquery["window_moving_frame"]
        assert "RANGE BETWEEN 30 PRECEDING" in bigquery["window_moving_frame"]

    def test_cloud_json_and_struct_variants_preserve_result_shapes(self):
        benchmark = ReadPrimitivesBenchmark()
        snowflake = benchmark.get_queries(dialect="snowflake")
        bigquery = benchmark.get_queries(dialect="bigquery")

        assert "OBJECT_AGG(" in snowflake["json_aggregates"]
        assert "JSON_OBJECT(" in bigquery["json_aggregates"]
        for queries in (snowflake, bigquery):
            assert "json_extract_nested" not in queries
        assert "ARRAY_AGG(CAST(p_partkey AS STRING) ORDER BY p_partkey)" in bigquery["json_aggregates"]
        assert "ARRAY_AGG(p_retailprice ORDER BY p_partkey)" in bigquery["json_aggregates"]
        assert "OBJECT_CONSTRUCT(" in snowflake["struct_access"]
        for queries in (snowflake, bigquery):
            assert "json_extract_simple" not in queries
        assert "json_extract_simple" not in benchmark.get_queries(dialect="databricks")
        for query_id in ("map_construction", "map_access", "map_keys_values"):
            assert query_id not in snowflake

    def test_bigquery_statistical_variants_remain_exact(self):
        queries = ReadPrimitivesBenchmark().get_queries(dialect="bigquery")
        percentiles = queries["statistical_percentiles"].upper()
        correlation = queries["statistical_correlation"].upper()
        trend = queries["timeseries_trend_analysis"].upper()

        assert "PERCENTILE_CONT(" in percentiles
        assert "APPROX" not in percentiles
        assert "SELECT DISTINCT" in percentiles
        assert "COVAR_POP(" in correlation and "VAR_POP(" in correlation
        assert "WHEN VAR_POP(L_EXTENDEDPRICE) = 0 THEN 1" in correlation
        assert "UNIX_SECONDS(" in trend
        assert "COVAR_POP(" in trend and "VAR_POP(" in trend

    def test_redshift_skips_non_comparable_array_scalar_fallbacks(self):
        benchmark = ReadPrimitivesBenchmark()
        queries_redshift = benchmark.get_queries(dialect="redshift")

        redshift_skipped = [
            "array_contains",
            "array_length",
            "array_min_max",
        ]
        for qid in redshift_skipped:
            assert qid not in queries_redshift, f"Redshift should skip: {qid}"

    def test_datafusion_skips_non_comparable_semistructured_fallbacks(self):
        benchmark = ReadPrimitivesBenchmark()
        queries_datafusion = benchmark.get_queries(dialect="datafusion")

        datafusion_skipped = [
            "array_contains",
            "array_length",
            "array_min_max",
            "array_distinct",
            "list_transform",
            "list_filter",
            "list_reduce",
            "asof_join_basic",
        ]
        for qid in datafusion_skipped:
            assert qid not in queries_datafusion, f"DataFusion should skip: {qid}"

    def test_duckdb_uses_row_for_struct(self):
        benchmark = ReadPrimitivesBenchmark()
        queries_duckdb = benchmark.get_queries(dialect="duckdb")

        struct_sql = queries_duckdb.get("struct_construction", "")
        assert "ROW(" in struct_sql

    def test_new_categories_exist(self):

        from benchbox.core.read_primitives.catalog.loader import load_primitives_catalog

        catalog = load_primitives_catalog()

        categories = set(q.category for q in catalog.queries.values())

        new_categories = ["array", "struct", "map", "lambda", "pivot"]
        for cat in new_categories:
            assert cat in categories, f"Missing category: {cat}"

    def test_all_new_queries_have_valid_sql(self):

        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries()

        new_query_ids = [
            "any_value_simple",
            "any_value_with_filter",
            "groupby_all_simple",
            "groupby_all_complex",
            "orderby_all_simple",
            "orderby_all_desc",
            "array_agg_simple",
            "array_agg_distinct",
            "array_unnest",
            "array_contains",
            "array_length",
            "array_slice",
            "array_min_max",
            "array_sort",
            "array_distinct",
            "struct_construction",
            "struct_access",
            "array_of_struct",
            "map_construction",
            "map_access",
            "map_keys_values",
            "list_transform",
            "list_filter",
            "list_reduce",
            "asof_join_basic",
            "pivot_basic",
            "unpivot_basic",
        ]

        for qid in new_query_ids:
            assert qid in queries, f"Missing query: {qid}"
            sql = queries[qid]
            assert sql and len(sql.strip()) > 0, f"Empty SQL for: {qid}"
            assert "SELECT" in sql.upper(), f"No SELECT in: {qid}"


@pytest.mark.parametrize("dialect", ["presto", "trino", "athena", "bigquery", "spark", "databricks"])
def test_array_and_lambda_queries_do_not_pass_through_the_havoc_list_rewrites(dialect):
    queries = ReadPrimitivesBenchmark(scale_factor=0.01).get_queries(dialect=dialect)

    assert ("list_transform" in queries) == (dialect != "bigquery")
    for query_id in ("array_length", "list_transform"):
        sql = queries.get(query_id)
        if sql is None:
            continue
        assert "_item" not in sql
        assert "_acc" not in sql
        assert "GENERATE_ARRAY(" not in sql


class TestPostgresVariants:
    _DUCKDB_ONLY_FUNCTIONS = (
        "APPROX_DISTINCT",
        "APPROX_COUNT_DISTINCT",
        "APPROX_QUANTILE",
        "APPROX_TOP_K",
        "ARG_MAX",
        "ARG_MIN",
        "MAX_BY",
        "MIN_BY",
        "GROUP BY ALL",
        "ORDER BY ALL",
        "ARRAY_SLICE",
        "ARRAY_MIN",
        "ARRAY_MAX",
        "ARRAY_DISTINCT",
        "ARRAY_SORT",
        "MAP_FROM_ENTRIES",
        "MAP_KEYS",
        "MAP_VALUES",
        "TRANSFORM(",
        "REDUCE(",
        "ASOF JOIN",
        "UNPIVOT",
        "STRUCT(",
        "JSON_EXTRACT(",
        "JSON_VALID",
        "JSON_ARRAYAGG",
        "JSON_OBJECTAGG",
    )

    def test_postgres_runs_every_query_it_can_express_without_duckdb_only_syntax(self):
        benchmark = ReadPrimitivesBenchmark()
        queries = benchmark.get_queries(dialect="postgres")
        variant_ids = {query_id for query_id in queries if benchmark.query_manager.has_variant(query_id, "postgres")}

        assert {
            "approx_count_distinct_simple",
            "approx_quantile_groupby",
            "approx_top_k_lineitem",
            "approx_quantiles_array",
            "json_extract_simple",
            "json_extract_nested",
            "json_aggregates",
            "max_by_simple",
            "groupby_all_complex",
            "orderby_all_desc",
            "array_distinct",
            "map_keys_values",
            "asof_join_basic",
            "unpivot_basic",
        } <= variant_ids
        for query_id in variant_ids:
            sql = queries[query_id].upper()
            assert not [fn for fn in self._DUCKDB_ONLY_FUNCTIONS if fn in sql], query_id

    def test_postgres_json_variants_guard_non_json_comments(self):
        benchmark = ReadPrimitivesBenchmark()

        simple = benchmark.query_manager.get_query("json_extract_simple", dialect="postgres")
        nested = benchmark.query_manager.get_query("json_extract_nested", dialect="postgres")

        assert "o_comment IS JSON" in simple
        assert "c_comment IS JSON" in nested
