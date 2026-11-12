from __future__ import annotations

import pytest

from benchbox.platforms.presto import PrestoAdapter
from benchbox.platforms.trino import TrinoAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _FakeCursor:
    description = (("value",),)

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str) -> None:
        self.executed.append(sql)

    def fetchall(self) -> list[tuple[int]]:
        return [(1,)]

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self) -> None:
        self.cursors: list[_FakeCursor] = []

    def cursor(self) -> _FakeCursor:
        cursor = _FakeCursor()
        self.cursors.append(cursor)
        return cursor


def _adapter(adapter_class: type) -> object:
    try:
        return adapter_class(host="localhost", port=8080, catalog="memory", schema="default", username="user")
    except ImportError:
        pytest.skip("driver not installed")


@pytest.mark.parametrize("adapter_class", [TrinoAdapter, PrestoAdapter])
def test_power_connection_adapter_executes_queries_on_a_real_connection(adapter_class: type) -> None:
    adapter = _adapter(adapter_class)
    connection = _FakeConnection()

    power_connection = adapter._make_power_connection_adapter(connection, "tpcds", 0.01)
    power_connection._validate_row_count = False
    power_connection.set_query_context("1")
    result = power_connection.execute("SELECT 1").platform_result

    assert result["status"] == "SUCCESS"
    assert result.get("error") is None
    assert [cursor.executed for cursor in connection.cursors][0] == ["SELECT 1"]
