from __future__ import annotations

import time
from typing import Any

import pytest

pa = pytest.importorskip("pyarrow")

from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionError
from benchbox.core.result_receipt import cursor_result_count
from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.platforms.base import sql_execution
from benchbox.platforms.base.arrow_result import ArrowRows, FetchedResult, fetch_result, receive_arrow
from benchbox.platforms.base.sql_execution import execute_sql_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]

RECEIPT_SECONDS = 0.05
CONVERSION_SECONDS = 0.4


def _batch(start: int, count: int) -> Any:
    return pa.record_batch(
        [pa.array(range(start, start + count)), pa.array([str(i) for i in range(count)])], names=["a", "b"]
    )


SCHEMA = _batch(0, 1).schema


class StubCursor:
    description = [("a",), ("b",)]

    def __init__(self, reader_factory=None, rows=None):
        self.executed: list[str] = []
        self._reader_factory = reader_factory
        self._rows = rows if rows is not None else [(1, "x")]

    def execute(self, query: str) -> None:
        self.executed.append(query)

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def arrow(self) -> Any:
        return self._reader_factory()

    def close(self) -> None:
        pass


def _builder(captured: dict[str, Any]):
    def build(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "SUCCESS", "execution_time_seconds": kwargs["execution_time"], **kwargs}

    return build


def _run(cursor: StubCursor, fetch_mode: str, captured: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return execute_sql_query(
        cursor,
        "select 1",
        "q1",
        log_verbose=lambda message: None,
        build_query_result_with_validation=_builder(captured),
        validate_row_count=False,
        fetch_mode=fetch_mode,
        fetch_arrow=lambda stub: stub.arrow(),
        **extra,
    )


class TestTimerScope:
    def test_only_receipt_is_inside_the_interval(self, monkeypatch):
        monkeypatch.setenv("BENCHBOX_EMIT_RESULT_DIGEST", "1")
        original = ArrowRows._materialize

        def slow_materialize(self):
            if self._rows is None:
                time.sleep(CONVERSION_SECONDS)
            return original(self)

        monkeypatch.setattr(ArrowRows, "_materialize", slow_materialize)

        def reader():
            def batches():
                for index in range(2):
                    time.sleep(RECEIPT_SECONDS)
                    yield _batch(index * 3, 3)

            return pa.RecordBatchReader.from_batches(SCHEMA, batches())

        captured: dict[str, Any] = {}
        wall_start = time.perf_counter()
        result = _run(StubCursor(reader), FETCH_MODE_ARROW, captured)
        wall = time.perf_counter() - wall_start

        assert result["fetch_mode"] == FETCH_MODE_ARROW
        assert captured["actual_row_count"] == 6
        assert 2 * RECEIPT_SECONDS <= result["execution_time_seconds"] < CONVERSION_SECONDS
        assert wall >= 2 * RECEIPT_SECONDS + CONVERSION_SECONDS
        assert captured["result_digest"]

    def test_streaming_reader_is_drained_before_the_timer_stops(self, monkeypatch):
        stopped = {"value": False}
        real_elapsed = sql_execution.elapsed_seconds

        def stopping_elapsed(start: float) -> float:
            stopped["value"] = True
            return real_elapsed(start)

        monkeypatch.setattr(sql_execution, "elapsed_seconds", stopping_elapsed)
        consumed: list[int] = []

        def reader():
            def batches():
                for index in range(4):
                    if stopped["value"]:
                        raise AssertionError("a batch was consumed after the timer stopped")
                    consumed.append(index)
                    yield _batch(index * 2, 2)

            return pa.RecordBatchReader.from_batches(SCHEMA, batches())

        captured: dict[str, Any] = {}
        result = _run(StubCursor(reader), FETCH_MODE_ARROW, captured)

        assert result["status"] == "SUCCESS"
        assert consumed == [0, 1, 2, 3]
        assert captured["actual_row_count"] == 8

    def test_rows_mode_uses_fetchall_even_when_arrow_is_offered(self):
        captured: dict[str, Any] = {}
        result = _run(
            StubCursor(lambda: pytest.fail("arrow path must not run"), rows=[(1, "x"), (2, "y")]),
            FETCH_MODE_ROWS,
            captured,
        )
        assert result["fetch_mode"] == FETCH_MODE_ROWS
        assert captured["materialized_rows"] == [(1, "x"), (2, "y")]
        assert captured["first_row"] == (1, "x")

    def test_arrow_request_without_an_arrow_source_records_rows(self):
        captured: dict[str, Any] = {}
        result = execute_sql_query(
            StubCursor(rows=[(1, "x")]),
            "select 1",
            "q1",
            log_verbose=lambda message: None,
            build_query_result_with_validation=_builder(captured),
            validate_row_count=False,
            fetch_mode=FETCH_MODE_ARROW,
        )
        assert result["fetch_mode"] == FETCH_MODE_ROWS

    def test_statements_without_a_result_set_return_no_rows(self):
        cursor = StubCursor(rows=[(1, "x")])
        cursor.description = None
        captured: dict[str, Any] = {}
        result = execute_sql_query(
            cursor,
            "create table t(a int)",
            "ddl",
            log_verbose=lambda message: None,
            build_query_result_with_validation=_builder(captured),
            validate_row_count=False,
        )
        assert result["fetch_mode"] == FETCH_MODE_ROWS
        assert captured["actual_row_count"] == 0
        assert captured["first_row"] is None


class TestArrowRows:
    def test_length_and_first_row_do_not_build_rows(self):
        rows = ArrowRows(pa.Table.from_batches([_batch(0, 3)]))
        assert len(rows) == 3
        assert rows.first() == (0, "0")
        assert rows._rows is None

    def test_rows_match_python_tuples_and_are_cached(self):
        table = pa.Table.from_batches([_batch(0, 2), _batch(2, 1)])
        rows = ArrowRows(table)
        assert list(rows) == [(0, "0"), (1, "1"), (2, "0")]
        assert rows[1] == (1, "1")
        assert rows._rows is not None

    def test_empty_table_has_no_first_row(self):
        rows = ArrowRows(pa.Table.from_batches([_batch(0, 0)]))
        assert len(rows) == 0
        assert rows.first() is None
        assert list(rows) == []

    def test_column_less_table_yields_empty_tuples(self):
        rows = ArrowRows(pa.table({}).select([]))
        assert len(rows) == 0

    def test_converters_apply_to_rows_and_first_row(self):
        rows = ArrowRows(pa.Table.from_batches([_batch(0, 2)]))
        rows.convert_columns({0: lambda value: value + 100})
        assert rows.first() == (100, "0")
        assert list(rows) == [(100, "0"), (101, "1")]

    def test_receive_accepts_table_reader_batch_and_iterables(self):
        table = pa.Table.from_batches([_batch(0, 2)])
        assert receive_arrow(table) is table
        assert receive_arrow(pa.RecordBatchReader.from_batches(SCHEMA, [_batch(0, 2), _batch(2, 2)])).num_rows == 4
        assert receive_arrow(_batch(0, 2)).num_rows == 2
        assert receive_arrow([_batch(0, 2), _batch(2, 1)]).num_rows == 3
        with pytest.raises(ValueError, match="at least one record batch"):
            receive_arrow([])

    def test_fetched_result_first_row_for_rows_and_arrow(self):
        assert FetchedResult(FETCH_MODE_ROWS, [(1,)]).first_row == (1,)
        assert FetchedResult(FETCH_MODE_ROWS, []).first_row is None
        fetched = fetch_result(
            FETCH_MODE_ARROW, fetch_rows=list, fetch_arrow=lambda: pa.Table.from_batches([_batch(5, 1)])
        )
        assert fetched.first_row == (5, "0")
        assert fetched.row_count == 1


class CountingCursor:
    def __init__(self, **attributes: Any) -> None:
        self.fetched = False
        for name, value in attributes.items():
            setattr(self, name, value)

    def fetchall(self) -> list:
        self.fetched = True
        return [(1,), (2,)]


class TestResultCountWithoutRows:
    def test_platform_result_count_wins(self):
        cursor = CountingCursor(platform_result={"rows_returned": 7})
        assert cursor_result_count(cursor) == 7
        assert not cursor.fetched

    def test_row_count_callable(self):
        cursor = CountingCursor(row_count=lambda: 9)
        assert cursor_result_count(cursor) == 9
        assert not cursor.fetched

    def test_rowcount_attribute_does_not_skip_the_receipt(self):
        cursor = CountingCursor(rowcount=3)
        assert cursor_result_count(cursor) == 2
        assert cursor.fetched

    def test_arrow_num_rows(self):
        cursor = CountingCursor(num_rows=11)
        assert cursor_result_count(cursor) == 11
        assert not cursor.fetched

    def test_arrow_table_method(self):
        cursor = CountingCursor(to_arrow_table=lambda: pa.Table.from_batches([_batch(0, 5)]))
        assert cursor_result_count(cursor) == 5
        assert not cursor.fetched

    def test_cursor_without_fetchall_counts_zero(self):
        assert cursor_result_count(object()) == 0

    @pytest.mark.parametrize(
        "module",
        [
            "benchbox.core.tpch.power_test",
            "benchbox.core.tpcds.power_test",
            "benchbox.core.tpch.throughput_test",
            "benchbox.core.tpcds.throughput_test",
        ],
    )
    def test_timed_sites_share_the_count_helper(self, module):
        import importlib

        assert importlib.import_module(module).cursor_result_count is cursor_result_count


class TestOptionRegistration:
    def test_fetch_mode_is_a_platform_option(self):
        assert PlatformHookRegistry.parse_options("duckdb", [("fetch_mode", "rows")])["fetch_mode"] == "rows"
        assert PlatformHookRegistry.parse_options("duckdb", [("fetch_mode", "arrow")])["fetch_mode"] == "arrow"
        with pytest.raises(PlatformOptionError, match="Allowed values"):
            PlatformHookRegistry.parse_options("duckdb", [("fetch_mode", "pandas")])
