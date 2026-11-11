# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.ai_primitives.queries import AIQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAIQueryManager:
    @pytest.fixture()
    def manager(self):
        return AIQueryManager()

    def test_manager_creation(self, manager):

        assert manager is not None
        assert manager.catalog_version >= 1

    def test_get_all_queries(self, manager):

        queries = manager.get_all_queries()

        assert isinstance(queries, dict)
        assert len(queries) > 0
        for query_id, sql in queries.items():
            assert isinstance(query_id, str)
            assert isinstance(sql, str)

    def test_get_query_valid_id(self, manager):

        queries = manager.get_all_queries()
        first_id = list(queries.keys())[0]

        sql = manager.get_query(first_id)
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_get_query_invalid_id(self, manager):

        with pytest.raises(ValueError, match="Invalid query ID"):
            manager.get_query("nonexistent_query_12345")

    def test_get_query_with_dialect(self, manager):

        sql = manager.get_query("generative_complete_simple", dialect="snowflake")
        assert "SNOWFLAKE.CORTEX" in sql or "placeholder" not in sql.lower()

    def test_get_query_skipped_dialect(self, manager):

        with pytest.raises(ValueError, match="not supported on dialect"):
            manager.get_query("generative_complete_simple", dialect="duckdb")

    def test_get_query_entry(self, manager):

        entry = manager.get_query_entry("generative_complete_simple")

        assert entry.id == "generative_complete_simple"
        assert entry.category == "generative"
        assert entry.sql is not None

    def test_get_query_entry_invalid(self, manager):

        with pytest.raises(ValueError, match="Invalid query ID"):
            manager.get_query_entry("nonexistent_query")

    def test_get_queries_by_category(self, manager):

        generative = manager.get_queries_by_category("generative")

        assert isinstance(generative, dict)
        assert len(generative) > 0
        for query_id in generative.keys():
            assert "generative" in query_id

    def test_get_queries_by_category_empty(self, manager):

        result = manager.get_queries_by_category("nonexistent_category")
        assert result == {}

    def test_get_query_categories(self, manager):

        categories = manager.get_query_categories()

        assert isinstance(categories, list)
        assert len(categories) > 0
        assert "generative" in categories
        assert "nlp" in categories
        assert "transform" in categories
        assert "embedding" in categories

    def test_get_supported_queries_snowflake(self, manager):

        supported = manager.get_supported_queries("snowflake")

        assert isinstance(supported, dict)
        assert len(supported) > 0

        for query_id, sql in supported.items():
            assert sql is not None

    def test_get_supported_queries_unsupported(self, manager):

        supported = manager.get_supported_queries("duckdb")

        assert len(supported) == 0

    def test_get_query_cost_estimate(self, manager):

        cost = manager.get_query_cost_estimate("generative_complete_simple", num_rows=10)

        assert isinstance(cost, float)
        assert cost > 0

    def test_get_query_cost_estimate_scales_with_rows(self, manager):

        cost_10 = manager.get_query_cost_estimate("nlp_sentiment_batch", num_rows=10)
        cost_100 = manager.get_query_cost_estimate("nlp_sentiment_batch", num_rows=100)

        assert cost_100 > cost_10
        assert cost_100 / cost_10 == pytest.approx(10.0, rel=0.1)


class TestQueryVariants:
    @pytest.fixture()
    def manager(self):
        return AIQueryManager()

    def test_snowflake_variants_use_cortex(self, manager):

        supported = manager.get_supported_queries("snowflake")

        for query_id, sql in supported.items():
            assert "SNOWFLAKE.CORTEX" in sql or "placeholder" in sql.lower()

    def test_bigquery_variants_use_ml(self, manager):

        supported = manager.get_supported_queries("bigquery")

        for query_id, sql in supported.items():
            assert "ML." in sql or "placeholder" in sql.lower()

    def test_databricks_variants_use_ai(self, manager):

        supported = manager.get_supported_queries("databricks")

        for query_id, sql in supported.items():
            assert "ai_" in sql.lower() or "placeholder" in sql.lower()

    def test_base_sql_used_when_no_variant(self, manager):

        sql_base = manager.get_query("generative_complete_simple")

        assert "placeholder" in sql_base.lower()


