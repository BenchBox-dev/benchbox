from __future__ import annotations

import sqlite3

import pytest

from benchbox.core.results import fetch_mode as fetch_mode_module
from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY, outside_query_timer

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestDuckDBTimingContract:
    @pytest.fixture(autouse=True)
    def _duckdb(self):
        pytest.importorskip("duckdb")
        pytest.importorskip("pyarrow")

    def _adapter(self, mode):
        from benchbox.platforms.duckdb import DuckDBAdapter

        return DuckDBAdapter(database_path=":memory:", fetch_mode=mode)

    def test_declares_query_only_method(self):
        from benchbox.platforms.duckdb import DuckDBAdapter

        assert DuckDBAdapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert DuckDBAdapter.platform_timing_source is None

    def test_arrow_result_reports_one_request_and_table_bytes(self):
        import duckdb

        connection = duckdb.connect()
        result = self._adapter(FETCH_MODE_ARROW).execute_query(
            connection, "SELECT range AS a FROM range(100)", "q", validate_row_count=False
        )
        expected = connection.execute("SELECT range AS a FROM range(100)").to_arrow_table().nbytes
        assert result["request_count"] == 1
        assert result["result_bytes"] == expected
        assert "platform_timing" not in result

    def test_rows_result_reports_bytes(self):
        import duckdb

        connection = duckdb.connect()
        result = self._adapter(FETCH_MODE_ROWS).execute_query(
            connection, "SELECT 'abc' AS a", "q", validate_row_count=False
        )
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0

    def test_validation_runs_outside_the_timer(self, monkeypatch):
        import time
        from types import SimpleNamespace

        import duckdb

        from benchbox.core.expected_results.models import ValidationMode
        from benchbox.core.validation import query_validation

        def slow_validate(self, **kwargs):
            time.sleep(0.15)
            return SimpleNamespace(
                is_valid=True,
                warning_message=None,
                error_message=None,
                expected_row_count=1,
                validation_mode=ValidationMode.EXACT,
                unvalidated=False,
            )

        monkeypatch.setattr(query_validation.QueryValidator, "validate_query_result", slow_validate)
        clock = fetch_mode_module.ConversionClock().start()
        try:
            result = self._adapter(FETCH_MODE_ROWS).execute_query(
                duckdb.connect(), "SELECT 1 AS a", "q", benchmark_type="tpch", scale_factor=1.0
            )
        finally:
            excluded = clock.finish()
        assert result["status"] == "SUCCESS"
        assert result["execution_time_seconds"] < 0.1
        assert excluded >= 0.15

    def test_failed_query_has_no_timing_fields(self):
        import duckdb

        result = self._adapter(FETCH_MODE_ROWS).execute_query(
            duckdb.connect(), "SELECT * FROM missing_table", "q", validate_row_count=False
        )
        assert result["status"] == "FAILED"
        assert "request_count" not in result

    def test_profiling_pragmas_are_outside_the_timer(self):
        import duckdb

        adapter = self._adapter(FETCH_MODE_ROWS)
        adapter.show_query_plans = True
        adapter.capture_plans = False
        statements = []

        class Recording:
            def __init__(self, inner):
                self.inner = inner

            def execute(self, sql, *args):
                statements.append(sql)
                return self.inner.execute(sql, *args)

        result = adapter.execute_query(Recording(duckdb.connect()), "SELECT 1", "q", validate_row_count=False)
        assert result["request_count"] == 1
        assert any("enable_profiling" in sql for sql in statements)


class TestSQLiteTimingContract:
    def _adapter(self):
        from benchbox.platforms.sqlite import SQLiteAdapter

        return SQLiteAdapter(database_path=":memory:")

    def test_declares_query_only_method(self):
        from benchbox.platforms.sqlite import SQLiteAdapter

        assert SQLiteAdapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert SQLiteAdapter.platform_timing_source is None

    def test_reports_one_request_and_row_bytes(self):
        connection = sqlite3.connect(":memory:")
        result = self._adapter().execute_query(connection, "SELECT 'abc', 7", "q", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0
        assert "platform_timing" not in result

    def test_failed_query_has_no_timing_fields(self):
        connection = sqlite3.connect(":memory:")
        result = self._adapter().execute_query(connection, "SELECT * FROM nope", "q", validate_row_count=False)
        assert result["status"] == "FAILED"
        assert "request_count" not in result


class TestDataFusionTimingContract:
    @pytest.fixture(autouse=True)
    def _datafusion(self):
        pytest.importorskip("datafusion")
        pytest.importorskip("pyarrow")

    def test_declares_query_only_method(self):
        from benchbox.platforms.datafusion import DataFusionAdapter

        assert DataFusionAdapter.timing_method == TIMING_METHOD_QUERY_ONLY

    def test_reports_one_request_and_arrow_bytes(self):
        from datafusion import SessionContext

        from benchbox.platforms.datafusion import DataFusionAdapter

        adapter = DataFusionAdapter()
        result = adapter.execute_query(
            SessionContext(), "SELECT 1 AS a UNION ALL SELECT 2", "q", validate_row_count=False
        )
        assert result["status"] == "SUCCESS"
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0


class TestOutsideQueryTimerIsSafeWhenNested:
    def test_nested_scopes_do_not_raise(self):
        with outside_query_timer():
            with outside_query_timer():
                pass
