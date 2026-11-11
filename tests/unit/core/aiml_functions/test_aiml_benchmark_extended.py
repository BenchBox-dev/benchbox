# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from benchbox.experimental.aiml_functions import AIMLFunctionsBenchmark
from benchbox.experimental.aiml_functions.benchmark import (
    AIMLBenchmarkResults,
    AIMLQueryResult,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAIMLBenchmarkResultsEdgeCases:
    def test_to_dict_without_complete(self) -> None:
        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        d = results.to_dict()
        assert d["completed_at"] is None

    def test_to_dict_zero_queries_success_rate(self) -> None:
        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        d = results.to_dict()
        assert d["success_rate"] == 0
        assert d["avg_execution_time_ms"] == 0

    def test_cost_accumulates_across_results(self) -> None:
        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        for _ in range(3):
            results.add_result(
                AIMLQueryResult(
                    query_id="q",
                    function_id="f",
                    platform="snowflake",
                    success=True,
                    execution_time_ms=50.0,
                    cost_estimated=0.10,
                )
            )
        assert results.total_cost_estimated == pytest.approx(0.30)


class TestAIMLFunctionsBenchmarkGetQueries:
    @pytest.fixture
    def bm(self) -> AIMLFunctionsBenchmark:
        return AIMLFunctionsBenchmark(scale_factor=1.0, seed=42)

    def test_get_queries_returns_snowflake_queries(self, bm: AIMLFunctionsBenchmark) -> None:
        queries = bm.get_queries()
        assert isinstance(queries, dict)
        assert len(queries) > 0

    def test_get_queries_values_are_strings(self, bm: AIMLFunctionsBenchmark) -> None:
        queries = bm.get_queries()
        for v in queries.values():
            assert isinstance(v, str)
            assert len(v) > 0

    def test_get_all_queries_no_platform_uses_fallback(self, bm: AIMLFunctionsBenchmark) -> None:
        queries = bm.get_all_queries(platform=None)
        assert isinstance(queries, dict)
        assert len(queries) > 0

    def test_get_all_queries_databricks(self, bm: AIMLFunctionsBenchmark) -> None:
        queries = bm.get_all_queries(platform="databricks")
        assert isinstance(queries, dict)
        assert len(queries) > 0


class TestAIMLFunctionsBenchmarkExecuteQuery:
    @pytest.fixture
    def bm(self) -> AIMLFunctionsBenchmark:
        return AIMLFunctionsBenchmark(scale_factor=1.0, seed=42)

    def test_execute_query_unknown_id_returns_failure(self, bm: AIMLFunctionsBenchmark) -> None:
        conn = MagicMock()
        result = bm.execute_query(conn, "no_such_query", platform="snowflake")
        assert result.success is False
        assert "Unknown" in result.error_message

    def test_execute_query_unavailable_platform_returns_failure(self, bm: AIMLFunctionsBenchmark) -> None:
        conn = MagicMock()
        result = bm.execute_query(conn, "sentiment_single", platform="mysql")
        assert result.success is False
        assert "platform" in result.error_message.lower() or "not available" in result.error_message.lower()

    def test_execute_query_infers_platform_from_connection(self, bm: AIMLFunctionsBenchmark) -> None:
        conn = MagicMock()
        conn.platform = "snowflake"
        result = bm.execute_query(conn, "sentiment_single")
        assert result.platform == "snowflake"

    def test_execute_query_uses_unknown_when_no_platform_attr(self, bm: AIMLFunctionsBenchmark) -> None:
        conn = MagicMock(spec=[])
        result = bm.execute_query(conn, "no_such_query")
        assert result.platform == "unknown"

    def test_execute_query_success_with_fetchall_result(self, bm: AIMLFunctionsBenchmark) -> None:
        conn = MagicMock()
        result_mock = MagicMock()
        result_mock.fetchall.return_value = [("row1",), ("row2",)]
        conn.execute.return_value = result_mock

        result = bm.execute_query(conn, "sentiment_single", platform="snowflake")
        assert result.success is True
        assert result.row_count == 2

    def test_execute_query_success_with_rowcount_result(self, bm: AIMLFunctionsBenchmark) -> None:
        conn = MagicMock()
        result_mock = MagicMock(spec=["rowcount"])
        result_mock.rowcount = 5
        conn.execute.return_value = result_mock

        result = bm.execute_query(conn, "sentiment_single", platform="snowflake")
        assert result.success is True
        assert result.row_count == 5

    def test_execute_query_captures_execution_error(self, bm: AIMLFunctionsBenchmark) -> None:
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("function not found")

        result = bm.execute_query(conn, "sentiment_single", platform="snowflake")
        assert result.success is False
        assert "function not found" in result.error_message


class TestAIMLFunctionsBenchmarkDescription:
    def test_description_property(self) -> None:
        bm = AIMLFunctionsBenchmark()
        assert isinstance(bm.description, str)
        assert len(bm.description) > 10

    def test_name_matches_version(self) -> None:
        bm = AIMLFunctionsBenchmark()
        assert bm.name
        assert bm.version


class TestAIMLQueryResultToDict:
    def test_to_dict_fields(self) -> None:
        r = AIMLQueryResult(
            query_id="q1",
            function_id="f1",
            platform="snowflake",
            success=True,
            execution_time_ms=100.0,
            row_count=5,
            tokens_estimated=50,
            cost_estimated=0.05,
        )
        d = r.to_dict()
        assert d["query_id"] == "q1"
        assert d["function_id"] == "f1"
        assert d["platform"] == "snowflake"
        assert d["success"] is True
        assert d["row_count"] == 5
        assert d["tokens_estimated"] == 50
        assert d["cost_estimated"] == pytest.approx(0.05)
        assert "timestamp" in d


class TestAIMLBenchmarkResultsComplete:
    def test_complete_sets_completed_at(self) -> None:
        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        assert results.completed_at is None
        results.complete()
        assert results.completed_at is not None

    def test_add_result_failed_increments_failed_queries(self) -> None:
        results = AIMLBenchmarkResults(
            platform="snowflake",
            started_at=datetime.now(timezone.utc),
        )
        results.add_result(
            AIMLQueryResult(
                query_id="q",
                function_id="f",
                platform="snowflake",
                success=False,
                execution_time_ms=10.0,
            )
        )
        assert results.failed_queries == 1
        assert results.successful_queries == 0


class TestAIMLFunctionsBenchmarkAdditional:
    @pytest.fixture
    def bm(self) -> AIMLFunctionsBenchmark:
        return AIMLFunctionsBenchmark(scale_factor=1.0, seed=42)

    def test_get_supported_platforms_returns_set(self, bm: AIMLFunctionsBenchmark) -> None:
        platforms = bm.get_supported_platforms()
        assert isinstance(platforms, set)
        assert len(platforms) > 0

    def test_get_functions_returns_dict(self, bm: AIMLFunctionsBenchmark) -> None:
        funcs = bm.get_functions()
        assert isinstance(funcs, dict)
        assert len(funcs) > 0

    def test_get_functions_for_platform(self, bm: AIMLFunctionsBenchmark) -> None:
        funcs = bm.get_functions_for_platform("snowflake")
        assert isinstance(funcs, list)

    def test_get_queries_for_platform(self, bm: AIMLFunctionsBenchmark) -> None:
        qids = bm.get_queries_for_platform("snowflake")
        assert isinstance(qids, list)
        assert len(qids) > 0

    def test_get_query_raises_for_unknown_id(self, bm: AIMLFunctionsBenchmark) -> None:
        with pytest.raises(ValueError, match="Unknown query ID"):
            bm.get_query("no_such_id", platform="snowflake")

    def test_get_query_raises_when_no_platform(self, bm: AIMLFunctionsBenchmark) -> None:
        qid = bm.get_queries_for_platform("snowflake")[0]
        with pytest.raises(ValueError, match="Platform must be specified"):
            bm.get_query(qid, platform=None)

    def test_get_query_raises_for_unavailable_platform(self, bm: AIMLFunctionsBenchmark) -> None:
        qid = bm.get_queries_for_platform("snowflake")[0]
        with pytest.raises(ValueError, match="not available for platform"):
            bm.get_query(qid, platform="mysql")

    def test_get_categories_returns_list(self, bm: AIMLFunctionsBenchmark) -> None:
        cats = bm.get_categories()
        assert isinstance(cats, list)
        assert len(cats) > 0

    def test_export_benchmark_spec_returns_dict(self, bm: AIMLFunctionsBenchmark) -> None:
        spec = bm.export_benchmark_spec()
        assert isinstance(spec, dict)
        assert "name" in spec
        assert "functions" in spec
        assert "queries" in spec

    def test_generate_data_invalid_format_raises(self, bm: AIMLFunctionsBenchmark) -> None:
        with pytest.raises(ValueError, match="Unsupported output format"):
            bm.generate_data(output_format="parquet")

    def test_generate_data_csv_format(self, bm: AIMLFunctionsBenchmark, tmp_path) -> None:
        bm.output_dir = tmp_path
        result = bm.generate_data(output_format="csv")
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_generate_data_with_table_filter(self, bm: AIMLFunctionsBenchmark, tmp_path) -> None:
        bm.output_dir = tmp_path
        all_files = bm.generate_data(output_format="csv")
        if all_files:
            first_table = list(all_files.keys())[0]
            filtered = bm.generate_data(output_format="csv", tables=[first_table])
            assert first_table in filtered

    def test_setup_tables_with_mock_connection(self, bm: AIMLFunctionsBenchmark) -> None:
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.execute.return_value = None
        result = bm.setup_tables(conn, platform="snowflake")
        assert isinstance(result, dict)
        assert conn.execute.called

    def test_run_benchmark_with_mock_connection(self, bm: AIMLFunctionsBenchmark) -> None:
        from unittest.mock import MagicMock, patch

        conn = MagicMock()
        conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))

        result = bm.run_benchmark(conn, platform="snowflake", setup_data=False)
        assert result is not None
        assert result.platform == "snowflake"

    def test_run_benchmark_with_query_ids_filter(self, bm: AIMLFunctionsBenchmark) -> None:
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))

        qids = bm.get_queries_for_platform("snowflake")[:1]
        result = bm.run_benchmark(conn, platform="snowflake", query_ids=qids, setup_data=False)
        assert result.total_queries <= 1

    def test_run_benchmark_with_categories_filter(self, bm: AIMLFunctionsBenchmark) -> None:
        from unittest.mock import MagicMock

        from benchbox.experimental.aiml_functions.benchmark import AIMLFunctionCategory

        conn = MagicMock()
        conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))

        cats = bm.get_categories()
        if cats:
            result = bm.run_benchmark(conn, platform="snowflake", categories=[cats[0]], setup_data=False)
            assert result is not None


