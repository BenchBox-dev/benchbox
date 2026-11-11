# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.experimental.aiml_functions import AIMLFunctionsBenchmark
from benchbox.experimental.aiml_functions.benchmark import (
    AIMLBenchmarkResults,
    AIMLQueryResult,
)
from benchbox.experimental.aiml_functions.functions import AIMLFunctionCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAIMLQueryResult:
    def test_basic_creation(self):
        result = AIMLQueryResult(
            query_id="sentiment_single",
            function_id="sentiment_analysis",
            platform="snowflake",
            success=True,
            execution_time_ms=150.5,
        )
        assert result.query_id == "sentiment_single"
        assert result.function_id == "sentiment_analysis"
        assert result.platform == "snowflake"
        assert result.success is True
        assert result.execution_time_ms == 150.5

    def test_failed_result(self):
        result = AIMLQueryResult(
            query_id="test",
            function_id="test",
            platform="snowflake",
            success=False,
            execution_time_ms=50.0,
            error_message="Function not available",
        )
        assert result.success is False
        assert result.error_message == "Function not available"

    def test_with_metrics(self):
        result = AIMLQueryResult(
            query_id="test",
            function_id="test",
            platform="snowflake",
            success=True,
            execution_time_ms=100.0,
            row_count=50,
            tokens_estimated=5000,
            cost_estimated=0.05,
        )
        assert result.row_count == 50
        assert result.tokens_estimated == 5000
        assert result.cost_estimated == 0.05

    def test_to_dict(self):
        result = AIMLQueryResult(
            query_id="test",
            function_id="sentiment",
            platform="snowflake",
            success=True,
            execution_time_ms=100.0,
            row_count=10,
        )
        d = result.to_dict()
        assert d["query_id"] == "test"
        assert d["function_id"] == "sentiment"
        assert d["platform"] == "snowflake"
        assert d["success"] is True
        assert d["execution_time_ms"] == 100.0
        assert d["row_count"] == 10
        assert "timestamp" in d


class TestAIMLBenchmarkResults:
    def test_basic_creation(self):
        from datetime import datetime, timezone

        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        assert results.platform == "snowflake"
        assert results.total_queries == 0
        assert results.successful_queries == 0
        assert results.failed_queries == 0

    def test_add_successful_result(self):
        from datetime import datetime, timezone

        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        results.add_result(
            AIMLQueryResult(
                query_id="q1",
                function_id="f1",
                platform="snowflake",
                success=True,
                execution_time_ms=100.0,
            )
        )
        assert results.total_queries == 1
        assert results.successful_queries == 1
        assert results.failed_queries == 0
        assert results.total_execution_time_ms == 100.0

    def test_add_failed_result(self):
        from datetime import datetime, timezone

        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        results.add_result(
            AIMLQueryResult(
                query_id="q1",
                function_id="f1",
                platform="snowflake",
                success=False,
                execution_time_ms=50.0,
            )
        )
        assert results.total_queries == 1
        assert results.successful_queries == 0
        assert results.failed_queries == 1

    def test_add_multiple_results(self):
        from datetime import datetime, timezone

        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        for i in range(5):
            results.add_result(
                AIMLQueryResult(
                    query_id=f"q{i}",
                    function_id="f",
                    platform="snowflake",
                    success=i % 2 == 0,
                    execution_time_ms=100.0,
                    cost_estimated=0.01,
                )
            )
        assert results.total_queries == 5
        assert results.successful_queries == 3
        assert results.failed_queries == 2
        assert results.total_execution_time_ms == 500.0
        assert results.total_cost_estimated == pytest.approx(0.05)

    def test_complete(self):
        from datetime import datetime, timezone

        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        assert results.completed_at is None
        results.complete()
        assert results.completed_at is not None

    def test_to_dict(self):
        from datetime import datetime, timezone

        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        results.add_result(
            AIMLQueryResult(
                query_id="q1",
                function_id="f1",
                platform="snowflake",
                success=True,
                execution_time_ms=100.0,
            )
        )
        results.complete()
        d = results.to_dict()
        assert d["platform"] == "snowflake"
        assert d["total_queries"] == 1
        assert d["successful_queries"] == 1
        assert d["success_rate"] == 1.0
        assert d["avg_execution_time_ms"] == 100.0
        assert len(d["query_results"]) == 1


class TestAIMLFunctionsBenchmark:
    @pytest.fixture
    def aiml_benchmark(self):
        return AIMLFunctionsBenchmark(scale_factor=1.0, seed=42)

    def test_basic_creation(self, aiml_benchmark):
        assert aiml_benchmark.name == "AI/ML SQL Function Performance Testing"
        assert aiml_benchmark.version == "1.0"
        assert aiml_benchmark.scale_factor == 1.0

    def test_supported_platforms(self, aiml_benchmark):
        platforms = aiml_benchmark.get_supported_platforms()
        assert "snowflake" in platforms
        assert "bigquery" in platforms
        assert "databricks" in platforms

    def test_get_functions(self, aiml_benchmark):
        functions = aiml_benchmark.get_functions()
        assert len(functions) > 0
        assert "sentiment_analysis" in functions

    def test_get_functions_for_platform(self, aiml_benchmark):
        functions = aiml_benchmark.get_functions_for_platform("snowflake")
        assert len(functions) > 0
        assert any(f["function_id"] == "sentiment_analysis" for f in functions)

    def test_get_queries_for_platform(self, aiml_benchmark):
        queries = aiml_benchmark.get_queries_for_platform("snowflake")
        assert len(queries) > 0
        assert "sentiment_single" in queries

    def test_get_query(self, aiml_benchmark):
        sql = aiml_benchmark.get_query("sentiment_single", platform="snowflake")
        assert sql is not None
        assert "SNOWFLAKE.CORTEX.SENTIMENT" in sql

    def test_get_query_unknown(self, aiml_benchmark):
        with pytest.raises(ValueError, match="Unknown query ID"):
            aiml_benchmark.get_query("unknown_query", platform="snowflake")

    def test_get_query_missing_platform(self, aiml_benchmark):
        with pytest.raises(ValueError, match="Platform must be specified"):
            aiml_benchmark.get_query("sentiment_single")

    def test_get_query_unsupported_platform(self, aiml_benchmark):
        with pytest.raises(ValueError, match="not available for platform"):
            aiml_benchmark.get_query("sentiment_single", platform="mysql")

    def test_get_all_queries(self, aiml_benchmark):
        queries = aiml_benchmark.get_all_queries()
        assert len(queries) > 0

    def test_get_all_queries_for_platform(self, aiml_benchmark):
        queries = aiml_benchmark.get_all_queries(platform="snowflake")
        assert len(queries) > 0
        assert all(isinstance(q, str) for q in queries.values())

    def test_get_categories(self, aiml_benchmark):
        categories = aiml_benchmark.get_categories()
        assert AIMLFunctionCategory.SENTIMENT in categories
        assert AIMLFunctionCategory.COMPLETION in categories

    def test_export_benchmark_spec(self, aiml_benchmark):
        spec = aiml_benchmark.export_benchmark_spec()
        assert spec["name"] == "AI/ML SQL Function Performance Testing"
        assert "supported_platforms" in spec
        assert "categories" in spec
        assert "functions" in spec
        assert "queries" in spec
        assert "data_manifest" in spec


class TestAIMLBenchmarkDataGeneration:
    @pytest.fixture
    def aiml_benchmark(self):
        return AIMLFunctionsBenchmark(scale_factor=1.0, seed=42)

    def test_generate_data(self, aiml_benchmark, tmp_path):
        aiml_benchmark.output_dir = tmp_path
        files = aiml_benchmark.generate_data()
        assert "aiml_sample_data" in files
        assert "aiml_long_texts" in files

    def test_generate_data_subset(self, aiml_benchmark, tmp_path):
        aiml_benchmark.output_dir = tmp_path
        files = aiml_benchmark.generate_data(tables=["aiml_sample_data"])
        assert "aiml_sample_data" in files
        assert "aiml_long_texts" not in files

    def test_generate_data_invalid_format(self, aiml_benchmark, tmp_path):
        aiml_benchmark.output_dir = tmp_path
        with pytest.raises(ValueError, match="Unsupported output format"):
            aiml_benchmark.generate_data(output_format="parquet")


class TestAIMLBenchmarkScaling:
    def test_scale_factor_1(self):
        bm = AIMLFunctionsBenchmark(scale_factor=1.0)
        assert bm.data_generator.num_samples == 100

    def test_scale_factor_0_1(self):
        bm = AIMLFunctionsBenchmark(scale_factor=0.1)
        assert bm.data_generator.num_samples == 10

    def test_scale_factor_minimum(self):
        bm = AIMLFunctionsBenchmark(scale_factor=0.01)
        assert bm.data_generator.num_samples >= 10

    def test_scale_factor_10(self):
        bm = AIMLFunctionsBenchmark(scale_factor=10)
        assert bm.data_generator.num_samples == 1000


class TestAIMLBenchmarkQueryCoverage:
    @pytest.fixture
    def aiml_benchmark(self):
        return AIMLFunctionsBenchmark()

    def test_snowflake_query_coverage(self, aiml_benchmark):
        queries = aiml_benchmark.get_queries_for_platform("snowflake")
        assert len(queries) >= 10
        assert "sentiment_single" in queries
        assert "completion_simple" in queries
        assert "embedding_single" in queries

    def test_databricks_query_coverage(self, aiml_benchmark):
        queries = aiml_benchmark.get_queries_for_platform("databricks")
        assert len(queries) >= 5
        assert "sentiment_single" in queries

    def test_bigquery_query_coverage(self, aiml_benchmark):
        queries = aiml_benchmark.get_queries_for_platform("bigquery")
        assert len(queries) >= 2

    def test_query_sql_validity(self, aiml_benchmark):
        for platform in ["snowflake", "databricks", "bigquery"]:
            queries = aiml_benchmark.get_queries_for_platform(platform)
            for query_id in queries:
                sql = aiml_benchmark.get_query(query_id, platform=platform)
                assert isinstance(sql, str)
                assert len(sql) > 0
                assert "SELECT" in sql.upper() or "WITH" in sql.upper()


class TestAIMLBenchmarkIntegration:
    @pytest.fixture
    def aiml_benchmark(self):
        return AIMLFunctionsBenchmark(scale_factor=1.0, seed=42)

    def test_full_spec_export(self, aiml_benchmark):
        spec = aiml_benchmark.export_benchmark_spec()

        assert "name" in spec
        assert "version" in spec
        assert "supported_platforms" in spec
        assert "categories" in spec
        assert "functions" in spec
        assert "queries" in spec
        assert "data_manifest" in spec

        assert len(spec["supported_platforms"]) >= 3
        assert len(spec["categories"]) >= 5
        assert len(spec["functions"]["functions"]) >= 7
        assert len(spec["queries"]) >= 10
        assert spec["data_manifest"]["tables"]["aiml_sample_data"]["row_count"] >= 100


class TestAIMLBenchmarkExtraCoverage:
    @pytest.fixture
    def bm(self):
        return AIMLFunctionsBenchmark(scale_factor=1.0, seed=42)

    def test_get_queries_returns_snowflake(self, bm):
        queries = bm.get_queries()
        assert isinstance(queries, dict)
        assert len(queries) > 0

    def test_get_functions_returns_dict(self, bm):
        functions = bm.get_functions()
        assert isinstance(functions, dict)
        assert len(functions) > 0

    def test_get_functions_for_platform(self, bm):
        fns = bm.get_functions_for_platform("snowflake")
        assert isinstance(fns, list)
        assert len(fns) > 0

    def test_get_all_queries_no_platform(self, bm):
        queries = bm.get_all_queries(platform=None)
        assert isinstance(queries, dict)
        assert len(queries) > 0

    def test_execute_query_unknown_id(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        result = bm.execute_query(conn, "nonexistent_xyz_query", platform="snowflake")
        assert result.success is False
        assert "Unknown query ID" in result.error_message

    def test_execute_query_unsupported_platform(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        result = bm.execute_query(conn, "sentiment_single", platform="mysql")
        assert result.success is False
        assert "not available" in result.error_message

    def test_execute_query_success_with_fetchall(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("positive",), ("negative",)]
        conn.execute.return_value = mock_result
        result = bm.execute_query(conn, "sentiment_single", platform="snowflake")
        assert result.query_id == "sentiment_single"

    def test_execute_query_exception_returns_failure(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("connection lost")
        result = bm.execute_query(conn, "sentiment_single", platform="snowflake")
        assert result.success is False
        assert "connection lost" in result.error_message

    def test_execute_query_detects_platform_from_connection(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.platform = "snowflake"
        conn.execute.return_value = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        result = bm.execute_query(conn, "sentiment_single", platform=None)
        assert result.platform == "snowflake"

    def test_setup_tables_with_mock_connection(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.execute.return_value = None
        results = bm.setup_tables(conn, "snowflake")
        assert isinstance(results, dict)
        create_keys = [k for k in results if k.startswith("create_")]
        assert len(create_keys) > 0

    def test_run_benchmark_with_specific_query_ids(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))

        results = bm.run_benchmark(conn, platform="snowflake", query_ids=["sentiment_single"], setup_data=False)
        assert isinstance(results, AIMLBenchmarkResults)
        assert results.total_queries == 1

    def test_run_benchmark_with_categories(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))

        results = bm.run_benchmark(
            conn,
            platform="snowflake",
            categories=[AIMLFunctionCategory.SENTIMENT],
            setup_data=False,
        )
        assert isinstance(results, AIMLBenchmarkResults)

    def test_run_benchmark_all_queries(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))

        results = bm.run_benchmark(conn, platform="snowflake", setup_data=False)
        assert isinstance(results, AIMLBenchmarkResults)
        assert results.completed_at is not None

    def test_run_benchmark_platform_from_connection(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.platform = "databricks"
        conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))

        results = bm.run_benchmark(conn, setup_data=False)
        assert results.platform == "databricks"

    def test_run_benchmark_to_dict(self, bm):
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))

        results = bm.run_benchmark(conn, platform="snowflake", query_ids=["sentiment_single"], setup_data=False)
        d = results.to_dict()
        assert "platform" in d
        assert "total_queries" in d
        assert "query_results" in d


