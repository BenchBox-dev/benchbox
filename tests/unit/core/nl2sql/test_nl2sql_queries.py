# Copyright 2026 Joe Harris / BenchBox Project

import pytest

from benchbox.experimental.nl2sql.queries import (
    NL2SQLQuery,
    NL2SQLQueryCategory,
    NL2SQLQueryManager,
    QueryDifficulty,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestNL2SQLQuery:
    def test_query_creation(self):

        query = NL2SQLQuery(
            query_id="test_query",
            natural_language="Count all orders",
            expected_sql="SELECT COUNT(*) FROM orders",
            category=NL2SQLQueryCategory.AGGREGATION,
            difficulty=QueryDifficulty.EASY,
            description="Test query",
        )

        assert query.query_id == "test_query"
        assert query.natural_language == "Count all orders"
        assert "COUNT(*)" in query.expected_sql
        assert query.category == NL2SQLQueryCategory.AGGREGATION
        assert query.difficulty == QueryDifficulty.EASY

    def test_query_to_dict(self):

        query = NL2SQLQuery(
            query_id="test_query",
            natural_language="Count all orders",
            expected_sql="SELECT COUNT(*) FROM orders",
            category=NL2SQLQueryCategory.AGGREGATION,
            difficulty=QueryDifficulty.EASY,
            tags=["count", "simple"],
        )

        result = query.to_dict()

        assert result["query_id"] == "test_query"
        assert result["category"] == "aggregation"
        assert result["difficulty"] == "easy"
        assert "count" in result["tags"]

    def test_query_with_expected_columns(self):

        query = NL2SQLQuery(
            query_id="test_query",
            natural_language="Show customer names",
            expected_sql="SELECT c_name FROM customer",
            category=NL2SQLQueryCategory.FILTERING,
            difficulty=QueryDifficulty.EASY,
            expected_columns=["c_name"],
        )

        assert query.expected_columns == ["c_name"]


class TestNL2SQLQueryCategory:
    def test_all_categories_exist(self):

        expected_categories = {
            "aggregation",
            "filtering",
            "joining",
            "grouping",
            "sorting",
            "window",
            "subquery",
            "date_time",
            "string_manipulation",
            "complex",
        }

        actual_categories = {c.value for c in NL2SQLQueryCategory}
        assert expected_categories == actual_categories

    def test_category_values(self):

        assert NL2SQLQueryCategory.AGGREGATION.value == "aggregation"
        assert NL2SQLQueryCategory.WINDOW.value == "window"
        assert NL2SQLQueryCategory.COMPLEX.value == "complex"


class TestQueryDifficulty:
    def test_all_difficulties_exist(self):

        expected = {"easy", "medium", "hard", "expert"}
        actual = {d.value for d in QueryDifficulty}
        assert expected == actual

    def test_difficulty_ordering(self):

        assert QueryDifficulty.EASY.value == "easy"
        assert QueryDifficulty.EXPERT.value == "expert"


class TestNL2SQLQueryManager:
    @pytest.fixture
    def query_manager(self):

        return NL2SQLQueryManager()

    def test_manager_initialization(self, query_manager):

        queries = query_manager.get_all_queries()
        assert len(queries) > 0

    def test_get_query_by_id(self, query_manager):

        query = query_manager.get_query("agg_total_revenue")
        assert query is not None
        assert query.query_id == "agg_total_revenue"
        assert "revenue" in query.natural_language.lower()

    def test_get_nonexistent_query(self, query_manager):

        query = query_manager.get_query("nonexistent_query")
        assert query is None

    def test_get_query_ids(self, query_manager):

        ids = query_manager.get_query_ids()
        assert len(ids) > 0
        assert "agg_total_revenue" in ids
        assert "agg_order_count" in ids

    def test_get_queries_by_category(self, query_manager):

        agg_queries = query_manager.get_queries_by_category(NL2SQLQueryCategory.AGGREGATION)
        assert len(agg_queries) > 0
        for query in agg_queries:
            assert query.category == NL2SQLQueryCategory.AGGREGATION

    def test_get_queries_by_difficulty(self, query_manager):

        easy_queries = query_manager.get_queries_by_difficulty(QueryDifficulty.EASY)
        assert len(easy_queries) > 0
        for query in easy_queries:
            assert query.difficulty == QueryDifficulty.EASY

    def test_get_queries_by_tag(self, query_manager):

        count_queries = query_manager.get_queries_by_tag("count")
        assert len(count_queries) > 0
        for query in count_queries:
            assert "count" in query.tags

    def test_export_queries(self, query_manager):

        exported = query_manager.export_queries()
        assert isinstance(exported, dict)
        assert len(exported) > 0

        first_query = next(iter(exported.values()))
        assert "query_id" in first_query
        assert "natural_language" in first_query
        assert "expected_sql" in first_query
        assert "category" in first_query
        assert "difficulty" in first_query

    def test_categories_list(self, query_manager):

        categories = query_manager.get_categories()
        assert NL2SQLQueryCategory.AGGREGATION in categories
        assert NL2SQLQueryCategory.WINDOW in categories

    def test_difficulty_levels_list(self, query_manager):

        difficulties = query_manager.get_difficulty_levels()
        assert QueryDifficulty.EASY in difficulties
        assert QueryDifficulty.EXPERT in difficulties

    def test_query_coverage(self, query_manager):

        for category in NL2SQLQueryCategory:
            queries = query_manager.get_queries_by_category(category)
            assert len(queries) > 0, f"No queries for category: {category.value}"

    def test_difficulty_coverage(self, query_manager):

        for difficulty in QueryDifficulty:
            queries = query_manager.get_queries_by_difficulty(difficulty)
            assert len(queries) > 0, f"No queries for difficulty: {difficulty.value}"

    def test_query_has_schema_context(self, query_manager):

        query = query_manager.get_query("agg_total_revenue")
        assert query is not None
        assert len(query.schema_context) > 0
        assert "customer" in query.schema_context.lower()
        assert "orders" in query.schema_context.lower()


class TestQueryContent:
    @pytest.fixture
    def query_manager(self):

        return NL2SQLQueryManager()

    def test_aggregation_queries_contain_aggregates(self, query_manager):

        queries = query_manager.get_queries_by_category(NL2SQLQueryCategory.AGGREGATION)
        aggregate_functions = {"COUNT", "SUM", "AVG", "MIN", "MAX", "STDDEV"}

        for query in queries:
            sql_upper = query.expected_sql.upper()
            has_aggregate = any(func in sql_upper for func in aggregate_functions)
            assert has_aggregate, f"Query {query.query_id} should contain aggregate function"

    def test_window_queries_contain_over(self, query_manager):

        queries = query_manager.get_queries_by_category(NL2SQLQueryCategory.WINDOW)

        for query in queries:
            assert "OVER" in query.expected_sql.upper(), f"Query {query.query_id} should contain OVER clause"

    def test_join_queries_contain_join(self, query_manager):

        queries = query_manager.get_queries_by_category(NL2SQLQueryCategory.JOINING)

        for query in queries:
            assert "JOIN" in query.expected_sql.upper(), f"Query {query.query_id} should contain JOIN"

    def test_subquery_queries_have_nested_selects(self, query_manager):

        queries = query_manager.get_queries_by_category(NL2SQLQueryCategory.SUBQUERY)

        for query in queries:
            sql_upper = query.expected_sql.upper()
            select_count = sql_upper.count("SELECT")
            assert select_count >= 2, f"Query {query.query_id} should have nested SELECT (found {select_count})"


