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

    assert client.executed == [
        "CREATE TABLE t (a Int32) ENGINE = MergeTree ORDER BY tuple()",
        "ALTER TABLE t MODIFY SETTING enable_block_number_column = 1, enable_block_offset_column = 1",
        "SELECT 1",
    ]
    assert result.fetchone() == (1,)
    assert result.fetchone() is None


def test_lightweight_updates_are_enabled_at_create_not_in_the_update() -> None:
    client = _Client()
    connection = ClickHouseServerConnection(client)

    connection.execute("CREATE TABLE t (a Int32, b Int32)")
    created = len(client.executed)
    connection.execute("UPDATE t SET a = 1 WHERE b = 2")
    connection.execute("UPDATE t SET a = 2 WHERE b = 3")

    assert client.executed[:created] == [
        "CREATE TABLE t (a Int32, b Int32) ENGINE = MergeTree ORDER BY tuple()",
        "ALTER TABLE t MODIFY SETTING enable_block_number_column = 1, enable_block_offset_column = 1",
    ]
    assert client.executed[created:] == ["UPDATE t SET a = 1 WHERE b = 2", "UPDATE t SET a = 2 WHERE b = 3"]


def test_lightweight_updates_can_be_prepared_for_tables_not_created_here() -> None:
    client = _Client()
    connection = ClickHouseServerConnection(client)

    connection.prepare_write("UPDATE t SET a = 1 WHERE b = 2")
    connection.execute("UPDATE t SET a = 1 WHERE b = 2")
    connection.execute("UPDATE t SET a = 2 WHERE b = 3")
    connection.execute("RENAME TABLE s TO t")
    connection.prepare_write("UPDATE t SET a = 3 WHERE b = 4")
    connection.execute("UPDATE t SET a = 3 WHERE b = 4")

    assert len([q for q in client.executed if q.startswith("ALTER TABLE t MODIFY SETTING")]) == 2


def test_non_merge_tree_tables_are_not_altered_at_create() -> None:
    client = _Client()
    ClickHouseServerConnection(client).execute("CREATE TABLE t (a Int32) ENGINE = Memory")

    assert client.executed == ["CREATE TABLE t (a Int32) ENGINE = Memory"]


def test_insert_with_rows_is_passed_through() -> None:
    client = _Client()
    connection = ClickHouseServerConnection(client)

    connection.execute("INSERT INTO t VALUES", iter([(1,)]), settings={"insert_block_size": 8})

    assert client.executed == ["INSERT INTO t VALUES"]


@pytest.mark.parametrize("benchmark_kind", ["write", "transaction"])
@pytest.mark.parametrize(
    "lifecycle_sql",
    [
        "",
        "DROP TABLE t; CREATE TABLE t (a Int32)",
        "RENAME TABLE t TO old_t; RENAME TABLE s TO t",
        "EXCHANGE TABLES t AND s",
        "DROP TABLE unrelated",
    ],
)
def test_operation_update_settings_are_outside_measured_execution(
    tmp_path, monkeypatch, benchmark_kind, lifecycle_sql
) -> None:
    from types import SimpleNamespace

    from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
    from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

    class TimedClient:
        def __init__(self):
            self.now = 0.0
            self.measuring = False
            self.tables = {"t": False, "s": False, "unrelated": False}
            self.measured_updates = 0
            self.alters = 0

        def clock(self):
            self.measuring = not self.measuring
            return self.now

        def execute(self, query):
            tokens = query.split()
            if query.startswith("ALTER TABLE"):
                assert not self.measuring, query
                self.tables[tokens[2]] = True
                self.alters += 1
                self.now += 100.0
            elif query.startswith("UPDATE"):
                assert self.measuring
                assert self.tables[tokens[1]], "UPDATE requires both lightweight-update settings"
                self.measured_updates += 1
                self.now += 0.01
            elif query.startswith("CREATE TABLE"):
                self.tables[tokens[2]] = False
            elif query.startswith("DROP TABLE"):
                del self.tables[tokens[2]]
            elif query.startswith("RENAME TABLE"):
                self.tables[tokens[4]] = self.tables.pop(tokens[2])
            elif query.startswith("EXCHANGE TABLES"):
                left, right = tokens[2], tokens[4]
                self.tables[left], self.tables[right] = self.tables[right], self.tables[left]
            else:
                raise AssertionError(query)
            return []

    benchmark_type = WritePrimitivesBenchmark if benchmark_kind == "write" else TransactionPrimitivesBenchmark
    benchmark = benchmark_type(output_dir=tmp_path)
    operation = SimpleNamespace(
        id="timed_update",
        category="update",
        requires_setup=False,
        write_sql="UPDATE t SET a = 1 WHERE a = 0",
        platform_overrides={},
        validation_queries=[],
        cleanup_sql=None,
    )
    monkeypatch.setattr(benchmark.operations_manager, "get_operation", lambda _operation_id: operation)
    client = TimedClient()
    connection = ClickHouseServerConnection(client)
    monkeypatch.setattr(f"{benchmark_type.__module__}.time", SimpleNamespace(perf_counter=client.clock))

    for _ in range(2):
        result = benchmark.execute_operation(operation.id, connection, platform_key="clickhouse")
        assert result.success, result.error
        assert result.write_duration_ms == pytest.approx(10.0)

    if lifecycle_sql:
        connection.execute(lifecycle_sql)

    for _ in range(2):
        result = benchmark.execute_operation(operation.id, connection, platform_key="clickhouse")
        assert result.success, result.error
        assert result.write_duration_ms == pytest.approx(10.0)

    assert client.measured_updates == 4
    assert client.alters == (2 if lifecycle_sql else 1)
