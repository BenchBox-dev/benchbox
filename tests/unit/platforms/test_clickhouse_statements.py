from __future__ import annotations

import pytest

from benchbox.platforms.clickhouse.client import ClickHouseServerConnection
from benchbox.platforms.clickhouse.statements import prepare_clickhouse_statements

pytestmark = [pytest.mark.unit, pytest.mark.fast]


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
        "CREATE TABLE t (a Int32) ENGINE = MergeTree ORDER BY tuple() "
        "SETTINGS enable_block_number_column = 1, enable_block_offset_column = 1"
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


class _TimedClient:
    def __init__(self):
        self.now = 0.0
        self.measuring = False
        self.tables = {"t": False, "s": False, "unrelated": False}
        self.measured_updates = 0
        self.measured_creates = 0
        self.alters = 0

    def clock(self):
        assert not self.measuring
        self.measuring = True
        return self.now

    def elapsed(self, start):
        assert self.measuring
        self.measuring = False
        return self.now - start

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
            has_settings = "enable_block_number_column" in query and "enable_block_offset_column" in query
            self.tables[tokens[2]] = has_settings
            if self.measuring:
                self.measured_creates += 1
                self.now += 0.01
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


@pytest.fixture(params=("write", "transaction"))
def timed_primitive(request, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
    from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

    benchmark_type = WritePrimitivesBenchmark if request.param == "write" else TransactionPrimitivesBenchmark
    benchmark = benchmark_type(output_dir=tmp_path)
    client = _TimedClient()
    connection = ClickHouseServerConnection(client)
    monkeypatch.setattr(f"{benchmark_type.__module__}.mono_time", client.clock)
    monkeypatch.setattr(f"{benchmark_type.__module__}.elapsed_seconds", client.elapsed)

    def execute(sql, category):
        operation = SimpleNamespace(
            id="timed_operation",
            category=category,
            requires_setup=False,
            write_sql=sql,
            platform_overrides={},
            validation_queries=[],
            cleanup_sql=None,
        )
        monkeypatch.setattr(benchmark.operations_manager, "get_operation", lambda _operation_id: operation)
        return benchmark.execute_operation(operation.id, connection, platform_key="clickhouse")

    return execute, connection, client


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
def test_operation_update_settings_are_outside_measured_execution(timed_primitive, lifecycle_sql) -> None:
    execute, connection, client = timed_primitive
    for _ in range(2):
        result = execute("UPDATE t SET a = 1 WHERE a = 0", "update")
        assert result.success, result.error
        assert result.write_duration_ms == pytest.approx(10.0)

    if lifecycle_sql:
        connection.execute(lifecycle_sql)

    for _ in range(2):
        result = execute("UPDATE t SET a = 1 WHERE a = 0", "update")
        assert result.success, result.error
        assert result.write_duration_ms == pytest.approx(10.0)

    expected_alters = 1 if lifecycle_sql in ("", "DROP TABLE t; CREATE TABLE t (a Int32)") else 2
    assert client.alters == expected_alters


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE created (a Int32)",
        "CREATE TABLE created (a Int32) ENGINE = MergeTree ORDER BY a",
        "CREATE TABLE created AS SELECT * FROM t",
    ],
)
def test_operation_create_does_not_time_additional_update_setup(timed_primitive, sql) -> None:
    execute, _, client = timed_primitive
    result = execute(sql, "ddl")
    assert result.success, result.error
    assert result.write_duration_ms == pytest.approx(10.0)
    assert client.measured_creates == 1
    assert client.alters == 0
