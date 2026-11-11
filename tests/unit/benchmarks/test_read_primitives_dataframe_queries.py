# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.dataframe.query import QueryCategory, QueryRegistry
from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.read_primitives.dataframe_queries import (
    REGISTRY,
    SKIP_FOR_DATAFRAME,
    SKIP_FOR_EXPRESSION_FAMILY,
    get_dataframe_queries,
    get_skip_for_dataframe,
    get_skip_for_expression_family,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryRegistry:
    def test_registry_is_query_registry(self):
        assert isinstance(REGISTRY, QueryRegistry)
        assert REGISTRY.benchmark == "Read Primitives"

    def test_registry_has_queries(self):
        assert len(REGISTRY) > 0, "Registry should contain at least one query"

    def test_get_dataframe_queries_returns_registry(self):
        registry = get_dataframe_queries()
        assert registry is REGISTRY

    def test_all_queries_have_both_implementations(self):
        for query in REGISTRY.get_all_queries():
            assert query.has_expression_impl(), f"Query {query.query_id} missing expression_impl"
            assert query.has_pandas_impl(), f"Query {query.query_id} missing pandas_impl"

    def test_all_queries_have_valid_categories(self):
        for query in REGISTRY.get_all_queries():
            assert len(query.categories) > 0, f"Query {query.query_id} has no categories"
            for category in query.categories:
                assert isinstance(category, QueryCategory), (
                    f"Query {query.query_id} has invalid category type: {type(category)}"
                )


class TestSkipLists:
    def test_skip_for_dataframe_contains_correlated_subqueries(self):
        assert len(SKIP_FOR_DATAFRAME) == 5, f"Should have 5 skip entries, got {len(SKIP_FOR_DATAFRAME)}"

    def test_skip_for_dataframe_has_expected_queries(self):
        expected = {
            "optimizer_exists_to_semijoin",
            "optimizer_in_to_exists",
            "optimizer_scalar_subquery_flattening",
            "approx_quantiles_array",
            "approx_top_k_lineitem",
        }
        assert set(SKIP_FOR_DATAFRAME) == expected, (
            f"Skip list should contain correlated-subquery + PySpark-only approximate-aggregates. "
            f"Expected: {expected}, got: {set(SKIP_FOR_DATAFRAME)}"
        )

    def test_get_skip_for_dataframe_returns_copy(self):
        result = get_skip_for_dataframe()
        assert result == SKIP_FOR_DATAFRAME
        assert result is not SKIP_FOR_DATAFRAME

    def test_skip_for_expression_family_is_empty(self):
        assert isinstance(SKIP_FOR_EXPRESSION_FAMILY, list)
        assert len(SKIP_FOR_EXPRESSION_FAMILY) == 0, (
            f"SKIP_FOR_EXPRESSION_FAMILY should be empty, got {SKIP_FOR_EXPRESSION_FAMILY}"
        )

    def test_skip_for_expression_family_all_in_registry(self):
        registry_ids = {q.query_id for q in REGISTRY.get_all_queries()}
        for query_id in SKIP_FOR_EXPRESSION_FAMILY:
            assert query_id in registry_ids, (
                f"Skip list entry '{query_id}' not found in registry. Was it renamed or removed?"
            )

    def test_skip_for_expression_family_no_overlap_with_dataframe(self):
        overlap = set(SKIP_FOR_EXPRESSION_FAMILY) & set(SKIP_FOR_DATAFRAME)
        assert not overlap, (
            f"Queries in both skip lists (redundant): {overlap}. "
            f"Move to SKIP_FOR_DATAFRAME if they should be skipped for all platforms."
        )

    def test_get_skip_for_expression_family_returns_copy(self):
        result = get_skip_for_expression_family()
        assert result == SKIP_FOR_EXPRESSION_FAMILY
        assert result is not SKIP_FOR_EXPRESSION_FAMILY

    def test_skip_lists_have_no_duplicates(self):
        assert len(SKIP_FOR_DATAFRAME) == len(set(SKIP_FOR_DATAFRAME)), "SKIP_FOR_DATAFRAME has duplicates"
        assert len(SKIP_FOR_EXPRESSION_FAMILY) == len(set(SKIP_FOR_EXPRESSION_FAMILY)), (
            "SKIP_FOR_EXPRESSION_FAMILY has duplicates"
        )


class TestBenchmarkIntegration:
    def test_benchmark_has_get_dataframe_queries_method(self):
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01)
        assert hasattr(benchmark, "get_dataframe_queries")

    def test_benchmark_get_dataframe_queries_returns_registry(self):
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01)
        registry = benchmark.get_dataframe_queries()
        assert isinstance(registry, QueryRegistry)
        assert registry is REGISTRY

    def test_benchmark_has_get_dataframe_skip_queries_method(self):
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01)
        assert hasattr(benchmark, "get_dataframe_skip_queries")

    def test_benchmark_get_dataframe_skip_queries_returns_list(self):
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01)
        skip_list = benchmark.get_dataframe_skip_queries()
        assert isinstance(skip_list, list)
        assert len(skip_list) == 5

    def test_benchmark_has_get_expression_family_skip_queries_method(self):
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01)
        assert hasattr(benchmark, "get_expression_family_skip_queries")

    def test_benchmark_get_expression_family_skip_queries_returns_list(self):
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01)
        skip_list = benchmark.get_expression_family_skip_queries()
        assert isinstance(skip_list, list)
        assert len(skip_list) == 0

    def test_benchmark_get_expression_family_skip_queries_matches_constant(self):
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01)
        skip_list = benchmark.get_expression_family_skip_queries()
        assert set(skip_list) == set(SKIP_FOR_EXPRESSION_FAMILY)


class TestQueryCategories:
    def test_has_aggregation_queries(self):
        agg_queries = REGISTRY.get_queries_by_category(QueryCategory.AGGREGATE)
        assert len(agg_queries) > 0

    def test_has_filter_queries(self):
        filter_queries = REGISTRY.get_queries_by_category(QueryCategory.FILTER)
        assert len(filter_queries) > 0

    def test_has_sort_queries(self):
        sort_queries = REGISTRY.get_queries_by_category(QueryCategory.SORT)
        assert len(sort_queries) > 0

    def test_has_window_queries(self):
        window_queries = REGISTRY.get_queries_by_category(QueryCategory.WINDOW)
        assert len(window_queries) > 0

    def test_has_join_queries(self):
        join_queries = REGISTRY.get_queries_by_category(QueryCategory.JOIN)
        assert len(join_queries) > 0


class TestPlatformSupport:
    def test_all_queries_support_polars(self):
        for query in REGISTRY.get_all_queries():
            assert query.supports_platform("polars"), f"Query {query.query_id} should support Polars"

    def test_all_queries_support_pandas(self):
        for query in REGISTRY.get_all_queries():
            assert query.supports_platform("pandas"), f"Query {query.query_id} should support Pandas"

    def test_all_queries_support_pyspark(self):
        for query in REGISTRY.get_all_queries():
            assert query.supports_platform("pyspark"), f"Query {query.query_id} should support PySpark"


class TestQueryMetadata:
    def test_all_queries_have_query_id(self):
        for query in REGISTRY.get_all_queries():
            assert query.query_id, "Query should have a query_id"
            assert isinstance(query.query_id, str)

    def test_all_queries_have_query_name(self):
        for query in REGISTRY.get_all_queries():
            assert query.query_name, f"Query {query.query_id} should have a query_name"

    def test_all_queries_have_description(self):
        for query in REGISTRY.get_all_queries():
            assert query.description, f"Query {query.query_id} should have a description"

    def test_query_ids_are_unique(self):
        query_ids = REGISTRY.get_query_ids()
        assert len(query_ids) == len(set(query_ids)), "Query IDs should be unique"


class TestSqlDataframeParity:
    def test_all_dataframe_queries_have_sql_counterpart(self):
        from benchbox.core.read_primitives.queries import ReadPrimitivesQueryManager

        sql_ids = set(ReadPrimitivesQueryManager().get_all_queries().keys())
        df_ids = {q.query_id for q in REGISTRY.get_all_queries()}
        skip_ids = set(SKIP_FOR_DATAFRAME)

        df_expected_in_sql = df_ids - skip_ids
        missing_sql = df_expected_in_sql - sql_ids
        assert not missing_sql, (
            f"DataFrame queries without SQL catalog counterpart: {sorted(missing_sql)}. "
            f"Add SQL entries to catalog/queries.yaml to maintain parity."
        )

    def test_all_sql_queries_have_dataframe_counterpart(self):
        from benchbox.core.read_primitives.queries import ReadPrimitivesQueryManager

        sql_ids = set(ReadPrimitivesQueryManager().get_all_queries().keys())
        df_ids = {q.query_id for q in REGISTRY.get_all_queries()}
        skip_ids = set(SKIP_FOR_DATAFRAME)

        sql_expected_in_df = sql_ids - skip_ids
        missing_df = sql_expected_in_df - df_ids
        assert not missing_df, (
            f"SQL queries without DataFrame registry counterpart: {sorted(missing_df)}. "
            f"Add DataFrame implementations to dataframe_queries.py or add to SKIP_FOR_DATAFRAME."
        )


class TestQueryImplementationSignatures:
    def test_expression_impls_are_callable(self):
        for query in REGISTRY.get_all_queries():
            impl = query.expression_impl
            assert callable(impl), f"Query {query.query_id} expression_impl should be callable"

    def test_pandas_impls_are_callable(self):
        for query in REGISTRY.get_all_queries():
            impl = query.pandas_impl
            assert callable(impl), f"Query {query.query_id} pandas_impl should be callable"


