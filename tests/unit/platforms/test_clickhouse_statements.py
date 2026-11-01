from __future__ import annotations

import pytest

from benchbox.platforms.clickhouse.client import ClickHouseServerConnection
from benchbox.platforms.clickhouse.statements import prepare_clickhouse_statements

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Client:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, query, *args, **kwargs):
        self.executed.append(query)
        return [(1,)] if query.upper().startswith("SELECT") else []


def test_multi_statement_scripts_are_split() -> None:
    assert prepare_clickhouse_statements("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]


def test_single_select_is_untouched() -> None:
    assert prepare_clickhouse_statements("SELECT 1") == ["SELECT 1"]


def test_update_and_delete_without_where_get_a_true_predicate() -> None:
    assert prepare_clickhouse_statements("UPDATE t SET a = 1") == ["UPDATE t SET a = 1 WHERE 1"]
    assert prepare_clickhouse_statements("DELETE FROM t") == ["DELETE FROM t WHERE 1"]
    assert prepare_clickhouse_statements("DELETE FROM t WHERE a = 1") == ["DELETE FROM t WHERE a = 1"]


def test_create_table_without_engine_gets_a_merge_tree() -> None:
    assert prepare_clickhouse_statements("CREATE TABLE t (a Int32)") == [
        "CREATE TABLE t (a Int32) ENGINE = MergeTree ORDER BY tuple()"
    ]
    assert prepare_clickhouse_statements("CREATE TABLE t AS SELECT * FROM s") == [
        "CREATE TABLE t ENGINE = MergeTree ORDER BY tuple() AS SELECT * FROM s"
    ]
    explicit = "CREATE TABLE t (a Int32) ENGINE = Memory"
    assert prepare_clickhouse_statements(explicit) == [explicit]


def test_series_unnest_becomes_range() -> None:
    assert prepare_clickhouse_statements(
        "INSERT INTO t SELECT n FROM (SELECT arrayJoin(GENERATE_SERIES(0, 99)) AS n)"
    ) == ["INSERT INTO t SELECT n FROM (SELECT arrayJoin(range(0, 100)) AS n)"]


def test_connection_runs_each_statement_and_returns_fetchable_rows() -> None:
    client = _Client()
    connection = ClickHouseServerConnection(client)

    result = connection.execute("CREATE TABLE t (a Int32); SELECT 1")

    assert client.executed == ["CREATE TABLE t (a Int32) ENGINE = MergeTree ORDER BY tuple()", "SELECT 1"]
    assert result.fetchone() == (1,)
    assert result.fetchone() is None


def test_lightweight_updates_are_enabled_once_per_table_and_reset_by_ddl() -> None:
    client = _Client()
    connection = ClickHouseServerConnection(client)

    connection.execute("UPDATE t SET a = 1 WHERE b = 2")
    connection.execute("UPDATE t SET a = 2 WHERE b = 3")
    connection.execute("DROP TABLE t")
    connection.execute("UPDATE t SET a = 3 WHERE b = 4")

    alters = [q for q in client.executed if q.startswith("ALTER TABLE t MODIFY SETTING")]
    assert len(alters) == 2


def test_insert_with_rows_is_passed_through() -> None:
    client = _Client()
    connection = ClickHouseServerConnection(client)

    connection.execute("INSERT INTO t VALUES", iter([(1,)]), settings={"insert_block_size": 8})

    assert client.executed == ["INSERT INTO t VALUES"]
