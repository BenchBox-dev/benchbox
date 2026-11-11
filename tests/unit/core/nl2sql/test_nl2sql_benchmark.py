# Copyright 2026 Joe Harris / BenchBox Project

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from benchbox.experimental.nl2sql.benchmark import (
    PLATFORM_NL2SQL_PROMPTS,
    NL2SQLBenchmark,
    NL2SQLBenchmarkResults,
    NL2SQLQueryResult,
)
from benchbox.experimental.nl2sql.evaluator import SQLMatchType
from benchbox.experimental.nl2sql.queries import NL2SQLQueryCategory, QueryDifficulty

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestNL2SQLQueryResult:
    def test_query_result_creation(self):

        result = NL2SQLQueryResult(
            query_id="test_query",
            natural_language="Count all orders",
            generated_sql="SELECT COUNT(*) FROM orders",
            expected_sql="SELECT COUNT(*) FROM orders",
            platform="snowflake",
            success=True,
            match_type=SQLMatchType.EXACT,
            generation_time_ms=150.0,
        )

        assert result.query_id == "test_query"
        assert result.success is True
        assert result.match_type == SQLMatchType.EXACT
        assert result.generation_time_ms == 150.0

    def test_query_result_to_dict(self):

        result = NL2SQLQueryResult(
            query_id="test_query",
            natural_language="Count all orders",
            generated_sql="SELECT COUNT(*) FROM orders",
            expected_sql="SELECT COUNT(*) FROM orders",
            platform="snowflake",
            success=True,
            match_type=SQLMatchType.EXACT,
            generation_time_ms=150.0,
            tokens_used=100,
            cost_estimated=0.002,
        )

        data = result.to_dict()

        assert data["query_id"] == "test_query"
        assert data["match_type"] == "exact"
        assert data["tokens_used"] == 100
        assert data["cost_estimated"] == 0.002


class TestNL2SQLBenchmarkResults:
    def test_results_creation(self):

        results = NL2SQLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )

        assert results.platform == "snowflake"
        assert results.completed_at is None
        assert results.accuracy_metrics.total_queries == 0

    def test_add_result(self):

        results = NL2SQLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )

        result = NL2SQLQueryResult(
            query_id="test_query",
            natural_language="Count orders",
            generated_sql="SELECT COUNT(*) FROM orders",
            expected_sql="SELECT COUNT(*) FROM orders",
            platform="snowflake",
            success=True,
            match_type=SQLMatchType.EXACT,
            generation_time_ms=100.0,
            cost_estimated=0.001,
        )

        results.add_result(result)

        assert results.accuracy_metrics.total_queries == 1
        assert results.accuracy_metrics.exact_matches == 1
        assert results.total_generation_time_ms == 100.0
        assert results.total_cost_estimated == 0.001

    def test_add_difficulty_result(self):

        results = NL2SQLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )

        results.add_difficulty_result(QueryDifficulty.EASY, SQLMatchType.EXACT)
        results.add_difficulty_result(QueryDifficulty.EASY, SQLMatchType.SEMANTIC)
        results.add_difficulty_result(QueryDifficulty.HARD, SQLMatchType.MISMATCH)

        assert "easy" in results.difficulty_breakdown
        assert results.difficulty_breakdown["easy"].total_queries == 2
        assert results.difficulty_breakdown["hard"].total_queries == 1

    def test_add_category_result(self):

        results = NL2SQLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )

        results.add_category_result(NL2SQLQueryCategory.AGGREGATION, SQLMatchType.EXACT)
        results.add_category_result(NL2SQLQueryCategory.AGGREGATION, SQLMatchType.SEMANTIC)
        results.add_category_result(NL2SQLQueryCategory.JOINING, SQLMatchType.MISMATCH)

        assert "aggregation" in results.category_breakdown
        assert results.category_breakdown["aggregation"].total_queries == 2

    def test_complete(self):

        results = NL2SQLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )

        assert results.completed_at is None
        results.complete()
        assert results.completed_at is not None

    def test_avg_times(self):

        results = NL2SQLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )

        for i in range(4):
            result = NL2SQLQueryResult(
                query_id=f"q{i}",
                natural_language="Test",
                generated_sql="SELECT 1",
                expected_sql="SELECT 1",
                platform="snowflake",
                success=True,
                match_type=SQLMatchType.EXACT,
                generation_time_ms=100.0,
                execution_time_ms=50.0,
            )
            results.add_result(result)

        assert results.avg_generation_time_ms == 100.0
        assert results.avg_execution_time_ms == 50.0

    def test_results_to_dict(self):

        results = NL2SQLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        results.complete()

        data = results.to_dict()

        assert data["platform"] == "snowflake"
        assert "accuracy_metrics" in data
        assert "difficulty_breakdown" in data
        assert "category_breakdown" in data


class TestNL2SQLBenchmark:
    @pytest.fixture
    def nl2sql_benchmark(self):
        return NL2SQLBenchmark(scale_factor=1.0, execute_validation=False)

    def test_benchmark_properties(self, nl2sql_benchmark):

        assert nl2sql_benchmark.name == "Natural Language to SQL Testing Framework"
        assert nl2sql_benchmark.version == "1.0"
        assert "NL2SQL" in nl2sql_benchmark.description or "natural language" in nl2sql_benchmark.description.lower()

    def test_supported_platforms(self, nl2sql_benchmark):

        platforms = nl2sql_benchmark.get_supported_platforms()

        assert "snowflake" in platforms
        assert "bigquery" in platforms
        assert "databricks" in platforms

    def test_get_queries(self, nl2sql_benchmark):

        queries = nl2sql_benchmark.get_queries()

        assert len(queries) > 0
        for nl_text in queries.values():
            assert isinstance(nl_text, str)
            assert len(nl_text) > 0

    def test_get_query(self, nl2sql_benchmark):

        nl_text = nl2sql_benchmark.get_query("agg_total_revenue")

        assert "revenue" in nl_text.lower()

    def test_get_query_invalid(self, nl2sql_benchmark):

        with pytest.raises(ValueError) as exc_info:
            nl2sql_benchmark.get_query("nonexistent_query")

        assert "Unknown query ID" in str(exc_info.value)

    def test_get_expected_sql(self, nl2sql_benchmark):

        sql = nl2sql_benchmark.get_expected_sql("agg_total_revenue")

        assert "SELECT" in sql.upper()
        assert "FROM" in sql.upper()

    def test_get_queries_by_category(self, nl2sql_benchmark):

        queries = nl2sql_benchmark.get_queries_by_category(NL2SQLQueryCategory.AGGREGATION)

        assert len(queries) > 0
        for q in queries:
            assert q.category == NL2SQLQueryCategory.AGGREGATION

    def test_get_queries_by_difficulty(self, nl2sql_benchmark):

        queries = nl2sql_benchmark.get_queries_by_difficulty(QueryDifficulty.EASY)

        assert len(queries) > 0
        for q in queries:
            assert q.difficulty == QueryDifficulty.EASY

    def test_generate_data(self, nl2sql_benchmark):

        result = nl2sql_benchmark.generate_data()

        assert result == []

    def test_export_benchmark_spec(self, nl2sql_benchmark):

        spec = nl2sql_benchmark.export_benchmark_spec()

        assert spec["name"] == nl2sql_benchmark.name
        assert spec["version"] == nl2sql_benchmark.version
        assert "supported_platforms" in spec
        assert "categories" in spec
        assert "difficulty_levels" in spec
        assert "queries" in spec
        assert "platform_configs" in spec


class TestNL2SQLPromptBuilding:
    @pytest.fixture
    def nl2sql_benchmark(self):
        return NL2SQLBenchmark(scale_factor=1.0)

    def test_platform_prompts_exist(self):

        assert "snowflake" in PLATFORM_NL2SQL_PROMPTS
        assert "bigquery" in PLATFORM_NL2SQL_PROMPTS
        assert "databricks" in PLATFORM_NL2SQL_PROMPTS

    def test_prompt_config_structure(self):

        for platform, config in PLATFORM_NL2SQL_PROMPTS.items():
            assert "system" in config
            assert "template" in config
            assert "function" in config
            assert "model" in config

    def test_build_nl2sql_prompt(self, nl2sql_benchmark):
        query = nl2sql_benchmark.query_manager.get_query("agg_total_revenue")
        prompt = nl2sql_benchmark._build_nl2sql_prompt(query, "snowflake")

        assert query.schema_context in prompt
        assert query.natural_language in prompt

    def test_build_platform_sql_snowflake(self, nl2sql_benchmark):

        query = nl2sql_benchmark.query_manager.get_query("agg_total_revenue")
        sql = nl2sql_benchmark._build_platform_sql(query, "snowflake")

        assert "SNOWFLAKE.CORTEX.COMPLETE" in sql
        assert "mistral-large" in sql

    def test_build_platform_sql_bigquery(self, nl2sql_benchmark):

        query = nl2sql_benchmark.query_manager.get_query("agg_total_revenue")
        sql = nl2sql_benchmark._build_platform_sql(query, "bigquery")

        assert "ML.GENERATE_TEXT" in sql
        assert "gemini-pro" in sql

    def test_build_platform_sql_databricks(self, nl2sql_benchmark):

        query = nl2sql_benchmark.query_manager.get_query("agg_total_revenue")
        sql = nl2sql_benchmark._build_platform_sql(query, "databricks")

        assert "ai_query" in sql
        assert "databricks-meta-llama" in sql

    def test_build_platform_sql_invalid(self, nl2sql_benchmark):

        query = nl2sql_benchmark.query_manager.get_query("agg_total_revenue")

        with pytest.raises(ValueError) as exc_info:
            nl2sql_benchmark._build_platform_sql(query, "invalid_platform")

        assert "Unsupported platform" in str(exc_info.value)


class TestNL2SQLResultExtraction:
    @pytest.fixture
    def nl2sql_benchmark(self):
        return NL2SQLBenchmark(scale_factor=1.0)

    def test_extract_generated_sql_dict(self, nl2sql_benchmark):

        result = [{"generated_sql": "SELECT COUNT(*) FROM orders"}]
        sql = nl2sql_benchmark._extract_generated_sql(result, "snowflake")

        assert sql == "SELECT COUNT(*) FROM orders"

    def test_extract_generated_sql_tuple(self, nl2sql_benchmark):

        result = [("SELECT COUNT(*) FROM orders",)]
        sql = nl2sql_benchmark._extract_generated_sql(result, "snowflake")

        assert sql == "SELECT COUNT(*) FROM orders"

    def test_extract_generated_sql_list(self, nl2sql_benchmark):

        result = [["SELECT COUNT(*) FROM orders"]]
        sql = nl2sql_benchmark._extract_generated_sql(result, "snowflake")

        assert sql == "SELECT COUNT(*) FROM orders"

    def test_extract_generated_sql_with_markdown(self, nl2sql_benchmark):

        result = [{"generated_sql": "```sql\nSELECT COUNT(*) FROM orders\n```"}]
        sql = nl2sql_benchmark._extract_generated_sql(result, "snowflake")

        assert not sql.startswith("```")
        assert not sql.endswith("```")
        assert "SELECT" in sql

    def test_extract_generated_sql_empty(self, nl2sql_benchmark):

        assert nl2sql_benchmark._extract_generated_sql(None, "snowflake") == ""
        assert nl2sql_benchmark._extract_generated_sql([], "snowflake") == ""


class TestNL2SQLTokenEstimation:
    @pytest.fixture
    def nl2sql_benchmark(self):
        return NL2SQLBenchmark(scale_factor=1.0)

    def test_estimate_tokens(self, nl2sql_benchmark):

        text = "SELECT COUNT(*) FROM orders WHERE status = 'active'"
        tokens = nl2sql_benchmark._estimate_tokens(text)

        expected = len(text) // 4
        assert abs(tokens - expected) <= 1

    def test_estimate_tokens_minimum(self, nl2sql_benchmark):

        tokens = nl2sql_benchmark._estimate_tokens("")
        assert tokens >= 1

        tokens = nl2sql_benchmark._estimate_tokens("a")
        assert tokens >= 1


class TestNL2SQLEvaluation:
    @pytest.fixture
    def nl2sql_benchmark(self):
        return NL2SQLBenchmark(scale_factor=1.0, execute_validation=False)

    def test_evaluate_nl2sql_invalid_query(self, nl2sql_benchmark):

        mock_conn = MagicMock()
        result = nl2sql_benchmark.evaluate_nl2sql(mock_conn, "invalid_query", "snowflake")

        assert result.success is False
        assert result.match_type == SQLMatchType.ERROR
        assert "Unknown query ID" in result.error_message

    def test_evaluate_nl2sql_unsupported_platform(self, nl2sql_benchmark):

        mock_conn = MagicMock()
        result = nl2sql_benchmark.evaluate_nl2sql(mock_conn, "agg_total_revenue", "mysql")

        assert result.success is False
        assert result.match_type == SQLMatchType.ERROR
        assert "Unsupported platform" in result.error_message

    def test_evaluate_nl2sql_connection_error(self, nl2sql_benchmark):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Connection failed")

        result = nl2sql_benchmark.evaluate_nl2sql(mock_conn, "agg_total_revenue", "snowflake")

        assert result.success is False
        assert result.match_type == SQLMatchType.ERROR
        assert "failed" in result.error_message.lower()

    def test_evaluate_nl2sql_successful(self, nl2sql_benchmark):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"generated_sql": "SELECT SUM(l_extendedprice * (1 - l_discount)) AS total_revenue FROM lineitem"}
        ]
        mock_conn.execute.return_value = mock_cursor

        result = nl2sql_benchmark.evaluate_nl2sql(mock_conn, "agg_total_revenue", "snowflake")

        assert result.generation_time_ms > 0
        assert result.generated_sql != ""
        assert result.tokens_used > 0
        assert result.cost_estimated > 0


class TestNL2SQLBenchmarkRun:
    @pytest.fixture
    def nl2sql_benchmark(self):
        return NL2SQLBenchmark(scale_factor=1.0, execute_validation=False)

    def test_run_benchmark_filters_by_difficulty(self, nl2sql_benchmark):

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [{"generated_sql": "SELECT 1"}]
        mock_conn.execute.return_value = mock_cursor

        results = nl2sql_benchmark.run_benchmark(
            mock_conn,
            platform="snowflake",
            difficulties=[QueryDifficulty.EASY],
        )

        for qr in results.query_results:
            query = nl2sql_benchmark.query_manager.get_query(qr.query_id)
            assert query.difficulty == QueryDifficulty.EASY

    def test_run_benchmark_filters_by_category(self, nl2sql_benchmark):

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [{"generated_sql": "SELECT 1"}]
        mock_conn.execute.return_value = mock_cursor

        results = nl2sql_benchmark.run_benchmark(
            mock_conn,
            platform="snowflake",
            categories=[NL2SQLQueryCategory.AGGREGATION],
        )

        for qr in results.query_results:
            query = nl2sql_benchmark.query_manager.get_query(qr.query_id)
            assert query.category == NL2SQLQueryCategory.AGGREGATION

    def test_run_benchmark_specific_queries(self, nl2sql_benchmark):

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [{"generated_sql": "SELECT 1"}]
        mock_conn.execute.return_value = mock_cursor

        results = nl2sql_benchmark.run_benchmark(
            mock_conn,
            platform="snowflake",
            query_ids=["agg_total_revenue", "agg_order_count"],
        )

        assert len(results.query_results) == 2
        query_ids = {qr.query_id for qr in results.query_results}
        assert "agg_total_revenue" in query_ids
        assert "agg_order_count" in query_ids

    def test_run_benchmark_completes(self, nl2sql_benchmark):

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [{"generated_sql": "SELECT 1"}]
        mock_conn.execute.return_value = mock_cursor

        results = nl2sql_benchmark.run_benchmark(
            mock_conn,
            platform="snowflake",
            query_ids=["agg_total_revenue"],
        )

        assert results.completed_at is not None
        assert isinstance(results.query_results, list)


