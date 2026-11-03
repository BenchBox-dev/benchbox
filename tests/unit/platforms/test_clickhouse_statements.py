from __future__ import annotations

import re

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
        "CREATE TABLE t (a Int32) ENGINE = MergeTree ORDER BY tuple()"
    ]
    assert prepare_clickhouse_statements("CREATE TABLE t AS SELECT * FROM s") == [
        "CREATE TABLE t ENGINE = MergeTree ORDER BY tuple() AS SELECT * FROM s"
    ]
    explicit = "CREATE TABLE t (a Int32) ENGINE = Memory"
    assert prepare_clickhouse_statements(explicit) == [explicit]


def test_only_tables_updated_later_in_the_script_get_update_settings() -> None:
    settings = "enable_block_number_column = 1, enable_block_offset_column = 1"
    assert prepare_clickhouse_statements(
        "CREATE TABLE t AS SELECT * FROM s; CREATE TABLE u (a Int32); UPDATE t SET a = 1"
    ) == [
        f"CREATE TABLE t ENGINE = MergeTree ORDER BY tuple() SETTINGS {settings} AS SELECT * FROM s",
        "CREATE TABLE u (a Int32) ENGINE = MergeTree ORDER BY tuple()",
        "UPDATE t SET a = 1 WHERE 1",
    ]
    assert (
        prepare_clickhouse_statements(
            "CREATE TABLE t ENGINE = MergeTree ORDER BY a SETTINGS index_granularity = 64; "
            "RENAME TABLE t TO v; UPDATE v SET a = 1 WHERE a = 0"
        )[0]
        == f"CREATE TABLE t ENGINE = MergeTree ORDER BY a SETTINGS {settings}, index_granularity = 64"
    )
    assert (
        prepare_clickhouse_statements(
            "CREATE TABLE t (a Int32) ENGINE = MergeTree ORDER BY tuple() "
            "SETTINGS enable_block_number_column = 0, enable_block_offset_column = 0; UPDATE t SET a = 1"
        )[0]
        == f"CREATE TABLE t (a Int32) ENGINE = MergeTree ORDER BY tuple() SETTINGS {settings}"
    )


def test_series_unnest_becomes_range() -> None:
    assert prepare_clickhouse_statements(
        "INSERT INTO t SELECT n FROM (SELECT arrayJoin(GENERATE_SERIES(0, 99)) AS n)"
    ) == ["INSERT INTO t SELECT n FROM (SELECT arrayJoin(range(0, 100)) AS n)"]


class _TimedClient:
    def __init__(self):
        self.now = 0.0
        self.measuring = False
        self.database = "default"
        self.tables = {
            "default.t": False,
            "default.s": False,
            "default.unrelated": False,
            "other.t": False,
        }
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

    def _qualify(self, name):
        return name if "." in name else f"{self.database}.{name}"

    def execute(self, query):
        tokens = query.split()
        if query == "SELECT currentDatabase()":
            return [(self.database,)]
        if query.startswith("USE"):
            self.database = tokens[1]
        elif query.startswith("ALTER TABLE"):
            assert not self.measuring, query
            self.tables[self._qualify(tokens[2])] = True
            self.alters += 1
            self.now += 100.0
        elif query.startswith("UPDATE"):
            assert self.measuring
            assert self.tables[self._qualify(tokens[1])], "UPDATE requires both lightweight-update settings"
            self.measured_updates += 1
            self.now += 0.01
        elif query.startswith("CREATE TABLE IF NOT EXISTS"):
            self.tables.setdefault(self._qualify(tokens[5]), _enables_updates(query))
        elif query.startswith("EXISTS TABLE"):
            return [(int(self._qualify(tokens[2]) in self.tables),)]
        elif query.startswith("CREATE TABLE"):
            self.tables[self._qualify(tokens[2])] = _enables_updates(query)
            if self.measuring:
                self.measured_creates += 1
                self.now += 0.01
        elif query.startswith("DROP TABLE"):
            del self.tables[self._qualify(tokens[2])]
        elif query.startswith("RENAME TABLE"):
            self.tables[self._qualify(tokens[4])] = self.tables.pop(self._qualify(tokens[2]))
        elif query.startswith("EXCHANGE TABLES"):
            left, right = self._qualify(tokens[2]), self._qualify(tokens[4])
            self.tables[left], self.tables[right] = self.tables[right], self.tables[left]
        else:
            raise AssertionError(query)
        return []


def _enables_updates(query):
    values = dict(re.findall(r"(enable_block_(?:number|offset)_column)\s*=\s*(\w+)", query))
    return values.get("enable_block_number_column") == "1" and values.get("enable_block_offset_column") == "1"


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

    expected_alters = 1 if lifecycle_sql in ("", "DROP TABLE unrelated") else 2
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


@pytest.mark.parametrize(
    ("script", "expected_alters"),
    [
        ("RENAME TABLE s TO t2; UPDATE t2 SET a = 1 WHERE a = 0", 1),
        ("CREATE TABLE t2 (a Int32); UPDATE t2 SET a = 1 WHERE a = 0", 0),
        ("CREATE TABLE t2 AS SELECT * FROM s; UPDATE t2 SET a = 1 WHERE a = 0", 0),
        ("EXCHANGE TABLES t AND s; UPDATE t SET a = 1 WHERE a = 0", 1),
        ("CREATE TABLE t3 (a Int32); RENAME TABLE t3 TO t4; UPDATE t4 SET a = 1 WHERE a = 0", 0),
        ("CREATE TABLE IF NOT EXISTS s (a Int32); UPDATE s SET a = 1 WHERE a = 0", 1),
        ("CREATE TABLE IF NOT EXISTS t5 (a Int32); UPDATE t5 SET a = 1 WHERE a = 0", 0),
        ("EXCHANGE TABLES default.t AND default.s; UPDATE t SET a = 1 WHERE a = 0", 1),
        ("USE other; UPDATE t SET a = 1 WHERE a = 0", 1),
        (
            "CREATE TABLE t6 (a Int32) ENGINE = MergeTree ORDER BY tuple() "
            "SETTINGS enable_block_number_column = 0, enable_block_offset_column = 0; UPDATE t6 SET a = 1 WHERE a = 0",
            0,
        ),
    ],
)
def test_updates_after_in_script_lifecycle_are_prepared_against_the_table_they_reach(
    timed_primitive, script, expected_alters
) -> None:
    execute, connection, client = timed_primitive
    client.tables["default.t"] = True
    connection._lightweight_update_tables.add("default.t")
    result = execute(script, "update")
    assert result.success, result.error
    assert client.alters == expected_alters


def test_no_op_create_if_not_exists_does_not_mark_an_existing_table_ready(timed_primitive) -> None:
    execute, connection, client = timed_primitive
    connection.execute("CREATE TABLE IF NOT EXISTS s (a Int32)")
    assert "default.s" not in connection._lightweight_update_tables
    result = execute("UPDATE s SET a = 1 WHERE a = 0", "update")
    assert result.success, result.error
    assert client.alters == 1


def test_table_created_in_an_earlier_script_gets_settings_before_its_timed_update(timed_primitive) -> None:
    execute, connection, client = timed_primitive
    connection.execute("CREATE TABLE copied AS SELECT * FROM t")
    for _ in range(2):
        result = execute("UPDATE copied SET a = 1 WHERE a = 0", "update")
        assert result.success, result.error
        assert result.write_duration_ms == pytest.approx(10.0)
    assert client.alters == 1
