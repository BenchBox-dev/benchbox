# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock

import pytest

from benchbox.core.ai_primitives.benchmark import (
    SUPPORTED_PLATFORMS,
    UNSUPPORTED_PLATFORMS,
    AIBenchmarkResult,
    AIPrimitivesBenchmark,
    AIQueryResult,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAIPrimitivesBenchmark:
    @pytest.fixture()
    def ai_benchmark(self):
        return AIPrimitivesBenchmark(scale_factor=0.01)

    def test_benchmark_creation(self, ai_benchmark):

        assert ai_benchmark is not None
        assert ai_benchmark.scale_factor == 0.01
        assert ai_benchmark._name == "AI/ML Primitives Benchmark"
        assert ai_benchmark._version == "1.0"

    def test_benchmark_with_cost_limit(self):

        bm = AIPrimitivesBenchmark(max_cost_usd=5.0)

        assert bm.max_cost_usd == 5.0
        assert bm.cost_tracker.budget_usd == 5.0

    def test_benchmark_dry_run_mode(self):

        bm = AIPrimitivesBenchmark(dry_run=True)

        assert bm.dry_run is True

    def test_get_data_source_benchmark(self, ai_benchmark):

        assert ai_benchmark.get_data_source_benchmark() == "tpch"

    def test_shared_tpch_schema_contract(self, ai_benchmark):
        schema = ai_benchmark.get_schema()
        sql = ai_benchmark.get_create_tables_sql(dialect="duckdb")

        assert set(schema) == {"region", "nation", "customer", "supplier", "part", "partsupp", "orders", "lineitem"}
        assert "CREATE TABLE orders" in sql
        assert "CREATE TABLE lineitem" in sql

    def test_generate_data_preserves_sharded_tpch_table_mapping(self, monkeypatch, tmp_path):

        class FakeTPCHBenchmark:
            def __init__(self, scale_factor, output_dir):
                assert scale_factor == 0.01
                assert output_dir == tmp_path
                self.tables = {}

            def generate_data(self):
                self.tables = {
                    "lineitem": [tmp_path / "lineitem.tbl.1", tmp_path / "lineitem.tbl.2"],
                    "orders": tmp_path / "orders.tbl",
                }
                return list(self.tables.values())

        monkeypatch.setattr("benchbox.core.tpch.benchmark.TPCHBenchmark", FakeTPCHBenchmark)
        benchmark = AIPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)

        files = benchmark.generate_data()

        assert benchmark.tables == {
            "lineitem": [tmp_path / "lineitem.tbl.1", tmp_path / "lineitem.tbl.2"],
            "orders": tmp_path / "orders.tbl",
        }
        assert files == [
            tmp_path / "lineitem.tbl.1",
            tmp_path / "lineitem.tbl.2",
            tmp_path / "orders.tbl",
        ]

    def test_is_platform_supported_true(self, ai_benchmark):

        for platform in SUPPORTED_PLATFORMS:
            assert ai_benchmark.is_platform_supported(platform) is True

    def test_is_platform_supported_false(self, ai_benchmark):

        for platform in UNSUPPORTED_PLATFORMS:
            assert ai_benchmark.is_platform_supported(platform) is False

    def test_get_all_queries(self, ai_benchmark):

        queries = ai_benchmark.get_all_queries()

        assert isinstance(queries, dict)
        assert len(queries) > 0

    def test_get_query(self, ai_benchmark):

        sql = ai_benchmark.get_query("generative_complete_simple")

        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_get_query_with_params_raises(self, ai_benchmark):

        with pytest.raises(ValueError, match="don't accept parameters"):
            ai_benchmark.get_query("generative_complete_simple", params={"foo": "bar"})

    def test_get_queries_by_category(self, ai_benchmark):

        generative = ai_benchmark.get_queries_by_category("generative")

        assert isinstance(generative, dict)
        assert len(generative) > 0

    def test_get_query_categories(self, ai_benchmark):

        categories = ai_benchmark.get_query_categories()

        assert "generative" in categories
        assert "nlp" in categories
        assert "transform" in categories
        assert "embedding" in categories

    def test_get_supported_queries_supported_platform(self, ai_benchmark):

        queries = ai_benchmark.get_supported_queries("snowflake")

        assert isinstance(queries, dict)
        assert len(queries) > 0

    def test_get_supported_queries_unsupported_platform(self, ai_benchmark):

        queries = ai_benchmark.get_supported_queries("duckdb")

        assert queries == {}

    @pytest.mark.parametrize("dialect", ["duckdb", "spark", "sqlite"])
    def test_get_queries_unsupported_dialect_is_empty(self, ai_benchmark, dialect):
        assert ai_benchmark.get_queries(dialect=dialect) == {}

    def test_get_queries_supported_dialect(self, ai_benchmark):
        assert len(ai_benchmark.get_queries(dialect="snowflake")) > 0

    def test_get_benchmark_info(self, ai_benchmark):

        info = ai_benchmark.get_benchmark_info()

        assert info["name"] == "AI/ML Primitives Benchmark"
        assert info["data_source"] == "tpch"
        assert "snowflake" in info["supported_platforms"]
        assert "duckdb" in info["unsupported_platforms"]


class TestCostEstimation:
    @pytest.fixture()
    def ai_bm(self):
        return AIPrimitivesBenchmark()

    def test_estimate_cost_all_queries(self, ai_bm):

        total_cost, estimates = ai_bm.estimate_cost("snowflake")

        assert total_cost > 0
        assert len(estimates) > 0

    def test_estimate_cost_specific_queries(self, ai_bm):

        queries = ["generative_complete_simple", "nlp_sentiment_single"]
        total_cost, estimates = ai_bm.estimate_cost("snowflake", queries=queries)

        assert len(estimates) == 2

    def test_estimate_cost_by_category(self, ai_bm):

        total_cost, estimates = ai_bm.estimate_cost("snowflake", categories=["generative"])

        assert total_cost > 0
        for estimate in estimates:
            entry = ai_bm.query_manager.get_query_entry(estimate.query_id)
            assert entry.category == "generative"

    def test_estimate_cost_unsupported_platform(self, ai_bm):

        total_cost, estimates = ai_bm.estimate_cost("duckdb")

        assert total_cost == 0
        assert len(estimates) == 0


class TestQueryExecution:
    @pytest.fixture()
    def ai_bm(self):
        return AIPrimitivesBenchmark()

    @pytest.fixture()
    def mock_connection(self):
        connection = Mock()
        cursor = Mock()
        cursor.fetchall.return_value = [("result1",), ("result2",)]
        connection.execute.return_value = cursor
        return connection

    def test_execute_query_success(self, ai_bm, mock_connection):

        result = ai_bm.execute_query(
            "generative_complete_simple",
            mock_connection,
            platform="snowflake",
        )

        assert isinstance(result, AIQueryResult)
        assert result.success is True
        assert result.execution_time_ms > 0
        assert result.rows_processed == 2

    def test_execute_query_skipped_platform(self, ai_bm, mock_connection):

        result = ai_bm.execute_query(
            "generative_complete_simple",
            mock_connection,
            platform="duckdb",
        )

        assert result.success is False
        assert "not supported" in result.error

    def test_execute_query_failure(self, ai_bm):

        connection = Mock()
        connection.execute.side_effect = Exception("Database error")

        result = ai_bm.execute_query(
            "generative_complete_simple",
            connection,
            platform="snowflake",
        )

        assert result.success is False
        assert "Database error" in result.error


class TestBenchmarkRun:
    @pytest.fixture()
    def ai_bm(self):
        return AIPrimitivesBenchmark()

    @pytest.fixture()
    def mock_connection(self):
        connection = Mock()
        cursor = Mock()
        cursor.fetchall.return_value = [("result",)]
        connection.execute.return_value = cursor
        return connection

    def test_run_benchmark_dry_run(self, ai_bm, mock_connection):

        result = ai_bm.run_benchmark(
            mock_connection,
            platform="snowflake",
            dry_run=True,
        )

        assert isinstance(result, AIBenchmarkResult)
        assert result.dry_run is True
        assert result.total_cost_estimated_usd > 0
        mock_connection.execute.assert_not_called()

    def test_run_benchmark_unsupported_platform(self, ai_bm, mock_connection):

        result = ai_bm.run_benchmark(
            mock_connection,
            platform="duckdb",
        )

        assert result.skipped_queries > 0
        mock_connection.execute.assert_not_called()

    def test_run_benchmark_specific_queries(self, ai_bm, mock_connection):

        result = ai_bm.run_benchmark(
            mock_connection,
            platform="snowflake",
            queries=["generative_complete_simple"],
            dry_run=True,
        )

        assert result.total_queries == 1

    def test_run_benchmark_by_category(self, ai_bm, mock_connection):

        result = ai_bm.run_benchmark(
            mock_connection,
            platform="snowflake",
            categories=["embedding"],
            dry_run=True,
        )

        assert result.total_queries > 0
        for qr in result.query_results:
            assert qr.category == "embedding"

    def test_run_benchmark_budget_exceeded(self, ai_bm, mock_connection):
        ai_bm.max_cost_usd = 0.00001

        with pytest.raises(ValueError, match="exceeds budget"):
            ai_bm.run_benchmark(
                mock_connection,
                platform="snowflake",
            )


class TestAIQueryResult:
    def test_result_creation(self):

        result = AIQueryResult(
            query_id="test",
            category="generative",
            execution_time_ms=100.5,
            success=True,
            rows_processed=10,
        )

        assert result.query_id == "test"
        assert result.category == "generative"
        assert result.execution_time_ms == 100.5
        assert result.success is True
        assert result.rows_processed == 10

    def test_result_defaults(self):

        result = AIQueryResult(
            query_id="test",
            category="nlp",
        )

        assert result.execution_time_ms == 0.0
        assert result.success is True
        assert result.error is None
        assert result.result_sample == []


class TestAIBenchmarkResult:
    def test_result_creation(self):

        result = AIBenchmarkResult(
            platform="snowflake",
            scale_factor=0.01,
            total_queries=10,
            successful_queries=8,
            failed_queries=2,
        )

        assert result.platform == "snowflake"
        assert result.total_queries == 10
        assert result.successful_queries == 8
        assert result.failed_queries == 2

    def test_result_defaults(self):

        result = AIBenchmarkResult()

        assert result.benchmark == "AI Primitives"
        assert result.platform == ""
        assert result.dry_run is False
        assert result.query_results == []
