# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.ai_primitives.catalog import (
    AICatalog,
    AIQuery,
    load_ai_catalog,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAIQuery:
    def test_query_creation(self):

        query = AIQuery(
            id="test_query",
            category="generative",
            sql="SELECT 1",
            description="Test query",
            model="llama3-8b",
            estimated_tokens=100,
            batch_size=10,
            cost_per_1k_tokens=0.0003,
        )

        assert query.id == "test_query"
        assert query.category == "generative"
        assert query.sql == "SELECT 1"
        assert query.description == "Test query"
        assert query.model == "llama3-8b"
        assert query.estimated_tokens == 100
        assert query.batch_size == 10
        assert query.cost_per_1k_tokens == 0.0003

    def test_query_defaults(self):

        query = AIQuery(
            id="minimal",
            category="nlp",
            sql="SELECT 1",
        )

        assert query.description is None
        assert query.variants is None
        assert query.skip_on is None
        assert query.model is None
        assert query.estimated_tokens == 100
        assert query.batch_size == 10
        assert query.cost_per_1k_tokens == 0.001

    def test_query_with_variants(self):

        query = AIQuery(
            id="variant_query",
            category="generative",
            sql="SELECT 'base'",
            variants={
                "snowflake": "SELECT SNOWFLAKE.CORTEX.COMPLETE(...)",
                "bigquery": "SELECT ML.GENERATE_TEXT(...)",
            },
        )

        assert query.variants is not None
        assert "snowflake" in query.variants
        assert "bigquery" in query.variants

    def test_query_with_skip_on(self):

        query = AIQuery(
            id="skip_query",
            category="embedding",
            sql="SELECT 1",
            skip_on=["duckdb", "sqlite", "postgresql"],
        )

        assert query.skip_on is not None
        assert "duckdb" in query.skip_on
        assert "sqlite" in query.skip_on
        assert len(query.skip_on) == 3


class TestLoadAICatalog:
    def test_load_catalog_success(self):

        catalog = load_ai_catalog()

        assert isinstance(catalog, AICatalog)
        assert catalog.version >= 1
        assert len(catalog.queries) > 0

    def test_catalog_has_expected_queries(self):

        catalog = load_ai_catalog()

        expected_queries = [
            "generative_complete_simple",
            "nlp_sentiment_single",
            "transform_summarize_short",
            "embedding_single",
        ]

        for query_id in expected_queries:
            assert query_id in catalog.queries, f"Expected query '{query_id}' not found"

    def test_catalog_queries_have_required_fields(self):
        catalog = load_ai_catalog()

        for query_id, query in catalog.queries.items():
            assert query.id == query_id
            assert query.category, f"Query {query_id} missing category"
            assert query.sql, f"Query {query_id} missing SQL"
            assert query.estimated_tokens > 0, f"Query {query_id} has invalid estimated_tokens"
            assert query.batch_size > 0, f"Query {query_id} has invalid batch_size"
            assert query.cost_per_1k_tokens >= 0, f"Query {query_id} has invalid cost_per_1k_tokens"

    def test_catalog_categories(self):

        catalog = load_ai_catalog()

        categories = {q.category for q in catalog.queries.values()}

        expected_categories = {"generative", "nlp", "transform", "embedding"}
        assert expected_categories.issubset(categories)

    def test_catalog_skip_on_includes_unsupported(self):
        catalog = load_ai_catalog()

        unsupported = {"duckdb", "sqlite", "postgresql"}

        for query_id, query in catalog.queries.items():
            if query.skip_on:
                for platform in unsupported:
                    if platform in query.skip_on:
                        break
                else:
                    pass

    def test_catalog_variants_format(self):

        catalog = load_ai_catalog()

        for query_id, query in catalog.queries.items():
            if query.variants:
                assert isinstance(query.variants, dict)
                for dialect, sql in query.variants.items():
                    assert isinstance(dialect, str)
                    assert isinstance(sql, str)
                    assert sql.strip(), f"Empty SQL for variant {dialect} in {query_id}"


class TestCatalogValidation:
    def test_catalog_no_duplicates(self):

        catalog = load_ai_catalog()

        query_ids = list(catalog.queries.keys())
        unique_ids = set(query_ids)

        assert len(query_ids) == len(unique_ids), "Duplicate query IDs found"

    def test_catalog_version_is_integer(self):

        catalog = load_ai_catalog()

        assert isinstance(catalog.version, int)
        assert catalog.version >= 1

    def test_query_categories_are_lowercase(self):

        catalog = load_ai_catalog()

        for query in catalog.queries.values():
            assert query.category == query.category.lower()

    def test_skip_on_dialects_are_lowercase(self):

        catalog = load_ai_catalog()

        for query in catalog.queries.values():
            if query.skip_on:
                for dialect in query.skip_on:
                    assert dialect == dialect.lower()

    def test_variant_dialects_are_lowercase(self):

        catalog = load_ai_catalog()

        for query in catalog.queries.values():
            if query.variants:
                for dialect in query.variants.keys():
                    assert dialect == dialect.lower()


