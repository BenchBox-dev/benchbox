from __future__ import annotations

import threading
import time

import pytest

from benchbox.platforms.base.link_probe import probe_statement_overhead

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class _MockCursor:
    def __init__(self) -> None:
        self.executed_queries: list[str] = []
        self.closed = False

    def execute(self, query: str) -> None:
        self.executed_queries.append(query)

    def fetchall(self) -> list[tuple[int]]:
        return [(1,)]

    def close(self) -> None:
        self.closed = True


class _MockConnection:
    def __init__(self, cursor: _MockCursor | None = None) -> None:
        self._cursor = cursor or _MockCursor()

    def cursor(self) -> _MockCursor:
        return self._cursor


class _QueryApiJob:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay

    def result(self, timeout: float | None = None) -> list[tuple[int]]:
        if self.delay:
            time.sleep(self.delay)
        return [(1,)]


class _QueryApiClient:
    def __init__(self, delay: float = 0.0) -> None:
        self.queries: list[str] = []
        self.delay = delay

    def query(self, sql: str) -> _QueryApiJob:
        self.queries.append(sql)
        return _QueryApiJob(self.delay)


def test_probe_statement_overhead_success() -> None:
    cursor = _MockCursor()
    conn = _MockConnection(cursor)

    result = probe_statement_overhead(conn, timeout_seconds=5.0, sample_count=5)

    assert result["collection_status"] == "available"
    assert result["source"] == "observed"
    overhead = result["statement_overhead_ms"]
    assert overhead["samples"] == 5
    assert overhead["min"] >= 0.0
    assert overhead["median"] >= 0.0
    assert overhead["min"] <= overhead["median"]

    assert len(cursor.executed_queries) == 6
    assert all(q == "SELECT 1" for q in cursor.executed_queries)
    assert cursor.closed is True


def test_probe_statement_overhead_custom_sample_count() -> None:
    cursor = _MockCursor()
    conn = _MockConnection(cursor)

    result = probe_statement_overhead(conn, timeout_seconds=5.0, sample_count=3)

    assert result["collection_status"] == "available"
    overhead = result["statement_overhead_ms"]
    assert overhead["samples"] == 3

    assert len(cursor.executed_queries) == 4
    assert cursor.closed is True


def test_probe_statement_overhead_none_connection() -> None:
    result = probe_statement_overhead(None)
    assert result["collection_status"] == "partial"
    assert result["source"] == "unavailable"
    assert result["collection_error_class"] == "ValueError"
    assert result["collection_error_message"] == "ValueError: statement overhead probe failed"


def test_probe_statement_overhead_missing_cursor_and_query() -> None:
    class _NoCursorClient:
        pass

    result = probe_statement_overhead(_NoCursorClient())
    assert result["collection_status"] == "partial"
    assert result["source"] == "unavailable"
    assert result["collection_error_class"] == "AttributeError"
    assert result["collection_error_message"] == "AttributeError: statement overhead probe failed"


def test_probe_statement_overhead_execution_failure() -> None:
    class _FailingCursor(_MockCursor):
        def execute(self, query: str) -> None:
            raise RuntimeError("Database execution failed")

    cursor = _FailingCursor()
    conn = _MockConnection(cursor)

    result = probe_statement_overhead(conn)
    assert result["collection_status"] == "partial"
    assert result["source"] == "unavailable"
    assert result["collection_error_class"] == "RuntimeError"

    assert result["collection_error_message"] == "RuntimeError: statement overhead probe failed"
    assert cursor.closed is True


def test_probe_statement_overhead_hung_execute_returns_timeout() -> None:
    class _HangingCursor(_MockCursor):
        def execute(self, query: str) -> None:
            time.sleep(30.0)

    cursor = _HangingCursor()
    conn = _MockConnection(cursor)

    started = time.monotonic()
    result = probe_statement_overhead(conn, timeout_seconds=0.5, sample_count=5)
    elapsed = time.monotonic() - started

    assert result["collection_status"] == "partial"
    assert result["collection_error_class"] == "TimeoutError"
    assert result["collection_error_message"] == "TimeoutError: statement overhead probe failed"

    assert elapsed < 10.0


def test_probe_statement_overhead_query_api_client() -> None:
    client = _QueryApiClient()

    result = probe_statement_overhead(client, timeout_seconds=5.0, sample_count=3)

    assert result["collection_status"] == "available"
    assert result["source"] == "observed"
    assert result["statement_overhead_ms"]["samples"] == 3
    assert len(client.queries) == 4
    assert all(q == "SELECT 1" for q in client.queries)


def test_probe_statement_overhead_raw_identifiers_never_published() -> None:
    class _LeakyCursor(_MockCursor):
        def execute(self, query: str) -> None:
            raise ConnectionError("could not connect to 10.1.2.3:5432 as admin:secret@dbhost01 (fe80::1)")

    conn = _MockConnection(_LeakyCursor())

    result = probe_statement_overhead(conn)

    message = result["collection_error_message"]
    assert result["collection_error_class"] == "ConnectionError"
    for leaked in ("10.1.2.3", "5432", "admin", "secret", "dbhost01", "fe80::1"):
        assert leaked not in message


def test_probe_worker_thread_is_daemon() -> None:
    seen: dict[str, bool] = {}

    class _SpyCursor(_MockCursor):
        def execute(self, query: str) -> None:
            seen["daemon"] = threading.current_thread().daemon
            super().execute(query)

    probe_statement_overhead(_MockConnection(_SpyCursor()), timeout_seconds=5.0, sample_count=1)
    assert seen.get("daemon") is True
