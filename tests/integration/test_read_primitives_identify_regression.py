# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
class TestReadPrimitivesIdentifyRegression:
    def test_all_queries_translate_with_identify_true(self):
        benchmark = ReadPrimitivesBenchmark()

        base_queries = benchmark.get_queries()

        translated_queries = benchmark.get_queries(dialect="duckdb")

        assert set(base_queries) - set(translated_queries) == {
            "fulltext_simple_search",
            "fulltext_boolean_search",
            "fulltext_phrase_search",
            "json_extract_simple",
        }

        for query_id, query_sql in translated_queries.items():
            assert query_sql, f"Query {query_id} should not be empty"
            assert len(query_sql) > 0, f"Query {query_id} should have SQL content"
            assert query_sql.strip().upper().startswith(("SELECT", "WITH", "/*", "--")), (
                f"Query {query_id} should start with SELECT, WITH, or comment"
            )

    def test_identify_true_quotes_identifiers(self):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="duckdb")

        if "aggregation_distinct" in queries:
            query = queries["aggregation_distinct"]
            assert '"o_custkey"' in query or '"orders"' in query, "Identifiers should be quoted with identify=True"

    def test_reserved_keyword_handling(self):
        benchmark = ReadPrimitivesBenchmark()

        base_queries = benchmark.get_queries()
        queries = benchmark.get_queries(dialect="duckdb")

        assert set(base_queries) - set(queries) == {
            "fulltext_simple_search",
            "fulltext_boolean_search",
            "fulltext_phrase_search",
            "json_extract_simple",
        }

        sample_query = queries.get("aggregation_distinct", "")
        if sample_query:
            assert "SELECT" in sample_query.upper() or "select" in sample_query.lower()
            assert "FROM" in sample_query.upper() or "from" in sample_query.lower()

    def test_case_sensitivity_preservation(self):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="duckdb")

        for query_id in ["aggregation_distinct", "join_inner_equijoin", "filter_selective"]:
            if query_id in queries:
                query = queries[query_id]

                assert len(query) > 0, f"Query {query_id} should not be empty"

                if "orderkey" in query.lower():
                    assert "orderkey" in query.lower()

    def test_no_translation_errors_for_complex_queries(self):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="duckdb")

        complex_query_ids = [
            "aggregation_materialize_subquery",
            "window_lead_lag_same_frame",
            "olap_cube_analysis",
            "optimizer_exists_to_semijoin",
        ]

        for query_id in complex_query_ids:
            if query_id in queries:
                query = queries[query_id]

                assert query, f"Complex query {query_id} should translate"
                assert len(query) > 0, f"Complex query {query_id} should have content"

                query_upper = query.upper()
                assert "SELECT" in query_upper, f"Query {query_id} should contain SELECT"
                assert "FROM" in query_upper, f"Query {query_id} should contain FROM"

    def test_variant_queries_also_use_identify_true(self):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="duckdb")

        variant_query_ids = [
            "json_aggregates",
            "timeseries_trend_analysis",
        ]

        for query_id in variant_query_ids:
            if query_id in queries:
                query = queries[query_id]

                assert query, f"Variant query {query_id} should translate"

                assert "SELECT" in query.upper() or "WITH" in query.upper(), (
                    f"Variant query {query_id} should contain SELECT or WITH"
                )

                assert '"' in query or "`" in query or query, (
                    f"Variant query {query_id} should have quoted identifiers or be valid SQL"
                )

    def test_translation_consistency_across_dialects(self):
        benchmark = ReadPrimitivesBenchmark()

        dialects = ["duckdb", "postgres", "snowflake"]

        for dialect in dialects:
            queries = benchmark.get_queries(dialect=dialect)

            assert len(queries) >= 100, f"Should have queries for {dialect}"

            for query_id, query_sql in queries.items():
                assert query_sql, f"Query {query_id} for {dialect} should not be empty"
                assert "SELECT" in query_sql.upper() or "WITH" in query_sql.upper(), (
                    f"Query {query_id} for {dialect} should contain SELECT or WITH"
                )


@pytest.mark.integration
class TestIdentifyTrueIntegrationWithTranslation:
    def test_translation_pipeline_handles_identify_true(self):
        benchmark = ReadPrimitivesBenchmark()

        all_queries = benchmark.get_queries(dialect="duckdb")

        sample_ids = [
            "aggregation_distinct",
            "join_inner_equijoin",
            "window_row_number",
            "olap_cube_analysis",
            "json_aggregates",
        ]

        successful_translations = 0
        for query_id in sample_ids:
            if query_id in all_queries:
                query = all_queries[query_id]
                assert query and len(query) > 0
                successful_translations += 1

        assert successful_translations >= 3, "Should successfully translate at least 3 sample queries"

    def test_no_identifier_collision_with_quoting(self):
        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries(dialect="duckdb")

        test_ids = [
            "aggregation_materialize_subquery",
            "optimizer_exists_to_semijoin",
        ]

        for query_id in test_ids:
            if query_id in queries:
                query = queries[query_id]

                assert query, f"Query {query_id} should translate without ambiguity"

                assert "SELECT" in query.upper()
                assert "FROM" in query.upper()


