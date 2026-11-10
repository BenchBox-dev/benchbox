import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.results.fetch_mode import ConversionClock
from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY
from benchbox.platforms.databricks.adapter import DatabricksAdapter
from benchbox.platforms.motherduck import MotherDuckAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _databricks_adapter():
    return DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/test",
        access_token="token",
    )


def _databricks_connection(rows, query_id="stmt-1"):
    cursor = Mock()
    cursor.fetchone.return_value = ("use_cached_result", "false")
    cursor.fetchall.return_value = rows
    cursor.query_id = query_id
    connection = Mock()
    connection.cursor.return_value = cursor
    return connection, cursor


def _query_info(statement_id, total=120, compile_ms=20, exec_ms=80, cached=False):
    metrics = SimpleNamespace(
        total_time_ms=total,
        compilation_time_ms=compile_ms,
        execution_time_ms=exec_ms,
        result_fetch_time_ms=5,
        result_from_cache=cached,
    )
    return SimpleNamespace(query_id=statement_id, metrics=metrics)


class TestDatabricksTimingContract:
    def test_declares_method_two_and_history_source(self):
        adapter = _databricks_adapter()

        assert adapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert adapter.platform_timing_source == "databricks.query_history_api"
        assert adapter.platform_timing_definition == "server_complete"

    def test_records_request_count_result_bytes_and_statement_timing(self):
        adapter = _databricks_adapter()
        connection, cursor = _databricks_connection([(1, "a"), (2, "b")])
        client = Mock()
        client.query_history.list.return_value = SimpleNamespace(res=[_query_info("stmt-1")])

        with patch.object(adapter, "_query_history_client", return_value=client):
            result = adapter.execute_query(connection, "SELECT 1", "q1")

        assert result["status"] == "SUCCESS"
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0
        timing = result["platform_timing"]
        assert timing["status"] in {"valid", "suspect"}
        assert timing["requests"][0]["id"] == "stmt-1"
        assert timing["platform_ms"] == 120.0
        assert timing["compile_ms"] == 20.0
        assert timing["exec_ms"] == 80.0
        assert timing["cache_hit"] is False
        assert "queue_ms" not in timing
        kwargs = client.query_history.list.call_args.kwargs
        assert kwargs["filter_by"].statement_ids == ["stmt-1"]
        assert kwargs["include_metrics"] is True

    def test_multi_statement_batch_counts_each_request(self):
        adapter = _databricks_adapter()
        connection, cursor = _databricks_connection([(1,)])
        ids = {"SELECT 1": "a", "SELECT 2": "b"}

        def execute(statement):
            if statement in ids:
                cursor.query_id = ids[statement]

        cursor.execute.side_effect = execute
        client = Mock()
        client.query_history.list.return_value = SimpleNamespace(res=[_query_info("a"), _query_info("b")])

        with patch.object(adapter, "_query_history_client", return_value=client):
            result = adapter.execute_query(connection, "SELECT 1; SELECT 2", "q1")

        assert result["request_count"] == 2
        assert [request["id"] for request in result["platform_timing"]["requests"]] == ["a", "b"]

    def test_missing_statement_id_marks_timing_unavailable_without_lookup(self):
        adapter = _databricks_adapter()
        connection, cursor = _databricks_connection([(1,)], query_id=None)

        with patch.object(adapter, "_query_history_client") as factory:
            result = adapter.execute_query(connection, "SELECT 1", "q1")

        factory.assert_not_called()
        assert result["platform_timing"]["status"] == "unavailable"

    def test_history_lookup_failure_is_unavailable(self):
        adapter = _databricks_adapter()
        connection, _ = _databricks_connection([(1,)])
        client = Mock()
        client.query_history.list.side_effect = RuntimeError("boom")

        with patch.object(adapter, "_query_history_client", return_value=client):
            result = adapter.execute_query(connection, "SELECT 1", "q1")

        assert result["status"] == "SUCCESS"
        assert result["platform_timing"]["status"] == "unavailable"

    def test_history_lag_is_polled_within_budget(self):
        adapter = _databricks_adapter()
        client = Mock()
        client.query_history.list.side_effect = [
            SimpleNamespace(res=[]),
            SimpleNamespace(res=[_query_info("stmt-1")]),
        ]

        with (
            patch.object(adapter, "_query_history_client", return_value=client),
            patch("benchbox.platforms.databricks.adapter.time.sleep"),
        ):
            timings = adapter.fetch_platform_timings(Mock(), ["stmt-1"])

        assert timings["stmt-1"].platform_ms == 120.0
        assert client.query_history.list.call_count == 2

    def test_session_setup_is_excluded_from_outer_and_inner_timers(self):
        adapter = _databricks_adapter()
        connection, _ = _databricks_connection([(1,)])
        client = Mock()
        client.query_history.list.return_value = SimpleNamespace(res=[_query_info("stmt-1")])

        def slow_setup(*args, **kwargs):
            time.sleep(0.05)

        clock = ConversionClock().start()
        try:
            with (
                patch.object(adapter, "_query_history_client", return_value=client),
                patch.object(adapter, "_ensure_session_cache_disabled", side_effect=slow_setup),
            ):
                result = adapter.execute_query(connection, "SELECT 1", "q1")
        finally:
            excluded = clock.finish()

        assert result["status"] == "SUCCESS"
        assert result["execution_time_seconds"] < 0.05
        assert excluded >= 0.05


class TestMotherDuckTimingContract:
    def test_method_two_with_request_count_and_bytes(self):
        adapter = MotherDuckAdapter(token="tok")
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [(1,), (2,)]

        result = adapter.execute_query(connection, "SELECT 1", "Q1")

        assert adapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert adapter.platform_timing_source is None
        assert result["request_count"] == 1
        assert result["result_bytes"] == 16
        assert "platform_timing" not in result


class TestPrestoTrinoTimingContract:
    def _adapter(self):
        from benchbox.platforms.trino import TrinoAdapter

        try:
            return TrinoAdapter(host="localhost")
        except ImportError:
            pytest.skip("Trino drivers not installed")

    def test_trino_records_query_id_and_runtime_queries_timing(self):
        adapter = self._adapter()
        query_cursor = Mock()
        query_cursor.fetchall.return_value = [(1, "x")]
        query_cursor.query_id = "20260101_000000_00001_abcde"
        lookup_cursor = Mock()
        lookup_cursor.fetchall.return_value = [("20260101_000000_00001_abcde", 3, 10, 20, 500)]
        connection = Mock()
        connection.cursor.side_effect = [query_cursor, lookup_cursor]

        result = adapter.execute_query(connection, "SELECT 1;", "q1")

        assert adapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert adapter.platform_timing_source == "system.runtime.queries"
        assert adapter.platform_timing_definition == "includes_fetch"
        query_cursor.execute.assert_called_once_with("SELECT 1")
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0
        request = result["platform_timing"]["requests"][0]
        assert request["id"] == "20260101_000000_00001_abcde"
        assert request["fields"]["elapsed_ms"] == 500
        assert result["platform_timing"]["queue_ms"] == 3.0
        assert result["platform_timing"]["compile_ms"] == 30.0
        sql = lookup_cursor.execute.call_args.args[0]
        assert "system.runtime.queries" in sql
        assert "query_id IN ('20260101_000000_00001_abcde')" in sql
        assert "planning_time_ms" in sql

    def test_presto_uses_distributed_planning_column(self):
        from benchbox.platforms.presto import PrestoAdapter

        try:
            adapter = PrestoAdapter(host="localhost")
        except ImportError:
            pytest.skip("Presto drivers not installed")
        cursor = Mock()
        cursor.fetchall.return_value = []
        connection = Mock()
        connection.cursor.return_value = cursor

        with (
            patch("benchbox.platforms.presto_trino_adapter_base.time.sleep"),
            patch("benchbox.platforms.presto_trino_adapter_base._RUNTIME_QUERIES_BUDGET_SECONDS", 0.0),
        ):
            adapter.fetch_platform_timings(connection, ["q_1"])

        assert "distributed_planning_time_ms" in cursor.execute.call_args.args[0]

    def test_rejects_unsafe_statement_id(self):
        adapter = self._adapter()

        with pytest.raises(ValueError):
            adapter.fetch_platform_timings(Mock(), ["x'; DROP TABLE t; --"])

    def test_missing_query_id_is_unavailable(self):
        adapter = self._adapter()
        cursor = Mock()
        cursor.fetchall.return_value = [(1,)]
        cursor.query_id = None
        connection = Mock()
        connection.cursor.return_value = cursor

        result = adapter.execute_query(connection, "SELECT 1", "q1")

        assert result["platform_timing"]["status"] == "unavailable"
        assert result["request_count"] == 1
