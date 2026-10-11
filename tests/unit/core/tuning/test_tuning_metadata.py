from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.tuning.interface import (
    BenchmarkTunings,
    TableTuning,
    TuningColumn,
    TuningType,
    UnifiedTuningConfiguration,
)
from benchbox.core.tuning.metadata import (
    MetadataValidationResult,
    TuningMetadata,
    TuningMetadataManager,
    _normalize_platform_key,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Cursor:
    def __init__(self, fetchall_data=None, fetchone_data=None):
        self.fetchall_data = fetchall_data or []
        self.fetchone_data = fetchone_data
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.fetchall_data

    def fetchone(self):
        return self.fetchone_data


class _Conn:
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


class _ExecuteOnlyConn:
    def __init__(self, rows):
        self.rows = rows
        self.executed: list[str] = []

    def execute(self, sql):
        self.executed.append(sql)
        return self.rows


class _Adapter:
    def __init__(self, platform_name: str = "duckdb", config_type: Optional[str] = None):
        self.platform_name = platform_name
        self.platform_config = {"type": config_type} if config_type is not None else {}
        if config_type is not None:
            self.canonical_platform_type = config_type
        self.connections: list[_Conn] = []

    def create_connection(self, **_kwargs):
        conn = _Conn(_Cursor())
        self.connections.append(conn)
        return conn

    def close_connection(self, _conn):
        return None


def _col(name: str, order: int = 1) -> TuningColumn:
    return TuningColumn(name=name, type="INTEGER", order=order)


def test_tuning_metadata_round_trip_to_dict_from_dict():
    metadata = TuningMetadata(
        table_name="orders",
        tuning_type="sorting",
        column_name="o_orderkey",
        column_order=1,
        configuration_hash="abc",
        created_at=datetime(2026, 2, 8, 0, 0, 0),
        platform="duckdb",
    )

    rebuilt = TuningMetadata.from_dict(metadata.to_dict())
    assert rebuilt.table_name == "orders"
    assert rebuilt.created_at.year == 2026


def test_metadata_validation_result_helpers():
    result = MetadataValidationResult()
    result.add_warning("warn")
    result.add_error("err")

    assert result.is_valid is False
    assert result.has_issues() is True
    assert result.errors == ["err"]
    assert result.warnings == ["warn"]


def test_fetch_helpers_support_execute_only_connections():
    manager = TuningMetadataManager(_Adapter(platform_name="clickhouse-local"))
    connection = _ExecuteOnlyConn([("orders", "sorting")])

    assert manager._fetch_all(connection, "SELECT ...") == [("orders", "sorting")]
    assert manager._fetch_one(connection, "SELECT ...") == ("orders", "sorting")
    assert connection.executed == ["SELECT ...", "SELECT ..."]


class _QueryJobConn:
    def __init__(self, rows):
        self.rows = rows
        self.queried: list[str] = []

    def query(self, sql):
        self.queried.append(sql)
        rows = self.rows

        class _Job:
            def result(self):
                return rows

        return _Job()


def test_fetch_helpers_support_query_job_connections():
    manager = TuningMetadataManager(_Adapter(platform_name="bigquery"))
    connection = _QueryJobConn([("orders", "sorting")])

    assert manager._fetch_all(connection, "SELECT ...") == [("orders", "sorting")]
    assert manager._fetch_one(connection, "SELECT ...") == ("orders", "sorting")
    assert connection.queried == ["SELECT ...", "SELECT ..."]


def test_fetch_one_query_job_connection_empty():
    manager = TuningMetadataManager(_Adapter(platform_name="bigquery"))
    assert manager._fetch_one(_QueryJobConn([]), "SELECT ...") is None


def test_fetch_helpers_qualify_metadata_reads_through_adapter():

    class _QualifyingAdapter(_Adapter):
        def _qualify_table_names(self, sql):
            return sql.replace("benchbox_tuning_metadata", "`my-proj.my_ds.BENCHBOX_TUNING_METADATA`")

    manager = TuningMetadataManager(_QualifyingAdapter(platform_name="bigquery"))
    connection = _QueryJobConn([("orders", "sorting")])

    assert manager._fetch_all(connection, "SELECT * FROM benchbox_tuning_metadata") == [("orders", "sorting")]
    assert connection.queried == ["SELECT * FROM `my-proj.my_ds.BENCHBOX_TUNING_METADATA`"]

    connection = _QueryJobConn([("orders", "sorting")])
    assert manager._fetch_one(connection, "SELECT * FROM benchbox_tuning_metadata") == ("orders", "sorting")
    assert connection.queried == ["SELECT * FROM `my-proj.my_ds.BENCHBOX_TUNING_METADATA`"]


def test_fetch_helpers_query_job_without_adapter_qualification_passes_sql_through():

    class _PlainAdapter:
        platform_name = "custom"
        platform_config = {}

    manager = TuningMetadataManager(_PlainAdapter())
    connection = _QueryJobConn([("orders", "sorting")])
    assert manager._fetch_all(connection, "SELECT ...") == [("orders", "sorting")]
    assert connection.queried == ["SELECT ..."]


class _JobStyleAdapter(_Adapter):
    def __init__(self):
        super().__init__(platform_name="bigquery")
        self.executed_sql: list[str] = []

    def execute_query(self, connection, sql, _query_id):
        self.executed_sql.append(sql)
        return {"result": []}

    def create_connection(self, **_kwargs):
        return _QueryJobConn([])

    def _qualify_table_names(self, sql):
        return sql.replace("benchbox_tuning_metadata", "`my-proj.my_ds.BENCHBOX_TUNING_METADATA`")


def _manager_with_job_adapter():
    manager = TuningMetadataManager(_JobStyleAdapter())
    manager._connection_kwargs = dict
    return manager


def test_batch_insert_records_supports_query_job_connections():
    manager = _manager_with_job_adapter()
    record = TuningMetadata(
        table_name="orders",
        tuning_type="sorting",
        column_name="o_orderkey",
        column_order=1,
        configuration_hash="abc",
        created_at=datetime(2026, 2, 8, 0, 0, 0),
        platform="bigquery",
    )
    manager._batch_insert_records([record])
    (sql,) = manager.platform_adapter.executed_sql
    assert "`my-proj.my_ds.BENCHBOX_TUNING_METADATA`" in sql
    assert "?" not in sql
    assert "'orders'" in sql and "'o_orderkey'" in sql


def test_clear_tunings_uses_where_true_for_bigquery():
    manager = _manager_with_job_adapter()
    manager._table_exists_check = lambda: True
    assert manager.clear_tunings() is True
    (sql,) = manager.platform_adapter.executed_sql
    assert sql == "DELETE FROM `my-proj.my_ds.BENCHBOX_TUNING_METADATA` WHERE TRUE"


@pytest.mark.parametrize(
    ("platform", "needle"),
    [
        ("bigquery", "CREATE TABLE"),
        ("snowflake", "TIMESTAMP_NTZ"),
        ("redshift", "CREATE TABLE IF NOT EXISTS"),
        ("clickhouse", "ENGINE = MergeTree()"),
        ("duckdb", "CREATE TABLE IF NOT EXISTS"),
    ],
)
def test_create_table_sql_varies_by_platform(platform, needle):
    manager = TuningMetadataManager(_Adapter(platform_name=platform))
    assert needle in manager._get_create_table_sql()
    assert "ENCODE AUTO" not in manager._get_create_table_sql()


def test_bigquery_table_uses_bigquery_types():
    sql = TuningMetadataManager(_Adapter(platform_name="bigquery"))._get_create_table_sql()
    assert "STRING NOT NULL" in sql
    assert "INT64 NOT NULL" in sql
    assert "TIMESTAMP NOT NULL" in sql
    assert "VARCHAR" not in sql
    assert "INTEGER" not in sql


@pytest.mark.parametrize(
    "platform",
    [
        "duckdb",
        "motherduck",
        "sqlite",
        "postgresql",
        "timescaledb",
        "pg_duckdb",
        "pg-duckdb",
        "pg_mooncake",
        "pg-mooncake",
        "paradedb",
        "citus",
        "cedardb",
    ],
)
def test_create_index_sql_uses_plain_form_where_supported(platform):
    sql = TuningMetadataManager(_Adapter(platform_name=platform))._get_create_index_sql()
    assert sql is not None
    assert "CREATE INDEX IF NOT EXISTS" in sql


@pytest.mark.parametrize(
    "platform",
    [
        "azure_synapse",
        "fabric_warehouse",
        "synapse",
        "fabric_dw",
        "azure-synapse",
        "fabric-warehouse",
        "Azure Synapse",
        "Fabric Warehouse",
    ],
)
def test_create_index_sql_uses_sys_indexes_guard_on_tsql(platform):
    sql = TuningMetadataManager(_Adapter(platform_name=platform))._get_create_index_sql()
    assert sql is not None
    assert "IF NOT EXISTS" in sql
    assert "sys.indexes" in sql
    assert "CREATE INDEX" in sql


@pytest.mark.parametrize(
    "platform",
    [
        "clickhouse",
        "clickhouse-local",
        "clickhouse-server",
        "clickhouse-cloud",
        "bigquery",
        "snowflake",
        "databricks",
        "redshift",
        "trino",
        "presto",
        "athena",
        "spark",
        "datafusion",
        "questdb",
        "doris",
        "ducklake",
        "firebolt",
        "singlestore",
        "mystery_engine",
    ],
)
def test_create_index_sql_is_table_only_where_unsupported_or_unverified(platform):
    assert TuningMetadataManager(_Adapter(platform_name=platform))._get_create_index_sql() is None


_ADAPTER_NAME_SEEDS = {
    "DatabricksDataFrameAdapter": ({"execution_mode": "dataframe"}, "constructor default when unconfigured"),
    "FireboltAdapter": ({"deployment_mode": "core"}, "single-node default; cloud spelling covered by key tests"),
}

_SQLGLOT_PARSE_WITHOUT_DIALECT = frozenset({"datafusion", "influxdb"})

_NON_SQL_DATAFRAME_PLATFORMS = {
    "pandas": "dataframe-only execution with no SQL DDL surface",
    "polars": "dataframe-only execution with no SQL DDL surface",
    "dask": "dataframe-only execution with no SQL DDL surface",
    "cudf": "dataframe-only execution with no SQL DDL surface",
}


def _metadata_ddl_registry_cases() -> list:
    cases = []
    for registry_platform in sorted(PlatformRegistry.get_sql_platforms()):
        adapter_class = PlatformRegistry.get_adapter_class(registry_platform)
        probe = adapter_class.__new__(adapter_class)
        seeds, _reason = _ADAPTER_NAME_SEEDS.get(adapter_class.__name__, ({}, ""))
        for seed_key, seed_value in seeds.items():
            setattr(probe, seed_key, seed_value)
        adapter_dialect = probe.get_target_dialect()
        sqlglot_read = None if adapter_dialect in _SQLGLOT_PARSE_WITHOUT_DIALECT else adapter_dialect
        cases.append(
            pytest.param(registry_platform, probe.platform_name, adapter_dialect, sqlglot_read, id=registry_platform)
        )
    return cases


def test_dataframe_only_platforms_stay_out_of_metadata_ddl_scope():
    sql_platforms = set(PlatformRegistry.get_sql_platforms())
    assert _NON_SQL_DATAFRAME_PLATFORMS
    for platform, reason in _NON_SQL_DATAFRAME_PLATFORMS.items():
        assert reason
        assert platform not in sql_platforms


@pytest.mark.parametrize("registry_platform,adapter_name,adapter_dialect,sqlglot_read", _metadata_ddl_registry_cases())
def test_metadata_ddl_parses_for_target_dialect(registry_platform, adapter_name, adapter_dialect, sqlglot_read):
    import sqlglot

    by_config = TuningMetadataManager(_Adapter(config_type=registry_platform))
    by_name = TuningMetadataManager(_Adapter(platform_name=adapter_name))
    assert by_config._get_create_table_sql() == by_name._get_create_table_sql()
    assert by_config._get_create_index_sql() == by_name._get_create_index_sql()
    statements = [by_config._get_create_table_sql()]
    index_sql = by_config._get_create_index_sql()
    if index_sql is not None:
        statements.append(index_sql)
    assert statements
    for sql in statements:
        if sqlglot_read is None:
            parsed = sqlglot.parse_one(sql)
        else:
            parsed = sqlglot.parse_one(sql, read=sqlglot_read)
        assert type(parsed).__name__ != "Command", f"{registry_platform}/{adapter_dialect}: {sql[:80]}"


@pytest.mark.parametrize("registry_platform", ["synapse", "fabric_dw"])
def test_registry_tsql_platforms_use_sys_indexes_guard(registry_platform):
    adapter_class = PlatformRegistry.get_adapter_class(registry_platform)
    adapter_name = adapter_class.__new__(adapter_class).platform_name
    for adapter in (_Adapter(config_type=registry_platform), _Adapter(platform_name=adapter_name)):
        sql = TuningMetadataManager(adapter)._get_create_index_sql()
        assert sql is not None
        assert "IF NOT EXISTS" in sql
        assert "sys.indexes" in sql


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("synapse", "synapse"),
        ("azure_synapse", "synapse"),
        ("azure-synapse", "synapse"),
        ("Azure Synapse", "synapse"),
        ("fabric_dw", "fabric_dw"),
        ("fabric-dw", "fabric_dw"),
        ("fabric_warehouse", "fabric_dw"),
        ("Fabric Warehouse", "fabric_dw"),
        ("pg-duckdb", "pg_duckdb"),
        ("pg_duckdb", "pg_duckdb"),
        ("pg-mooncake", "pg_mooncake"),
        ("pg_mooncake", "pg_mooncake"),
        ("timescaledb", "timescaledb"),
        ("TimescaleDB", "timescaledb"),
        ("ducklake", "ducklake"),
        ("DuckLake", "ducklake"),
        ("clickhouse-cloud", "clickhouse_cloud"),
        ("ClickHouse Cloud", "clickhouse_cloud"),
        ("Firebolt (Core)", "firebolt_(core)"),
        ("Firebolt (Cloud)", "firebolt_(cloud)"),
    ],
)
def test_platform_key_normalization_collapses_real_spellings(raw, expected):
    assert _normalize_platform_key(raw) == expected


def test_index_creation_failure_does_not_fail_table_creation(caplog):

    class _IndexFailingCursor(_Cursor):
        def execute(self, sql, params=None):
            if sql.strip().upper().startswith("CREATE INDEX") or "sys.indexes" in sql:
                raise RuntimeError("index boom")
            return super().execute(sql, params)

    class _IndexFailingConn(_Conn):
        def __init__(self):
            super().__init__(_IndexFailingCursor())

    class _IndexFailingAdapter(_Adapter):
        def create_connection(self, **_kwargs):
            conn = _IndexFailingConn()
            self.connections.append(conn)
            return conn

    manager = TuningMetadataManager(_IndexFailingAdapter(platform_name="duckdb"))
    with caplog.at_level("WARNING"):
        assert manager.create_metadata_table() is True
    assert "non-fatal" in caplog.text


def test_table_creation_failure_still_fails_closed(caplog):

    class _TableFailingCursor(_Cursor):
        def execute(self, sql, params=None):
            if sql.strip().upper().startswith("CREATE TABLE"):
                raise RuntimeError("table boom")
            return super().execute(sql, params)

    class _TableFailingConn(_Conn):
        def __init__(self):
            super().__init__(_TableFailingCursor())

    class _TableFailingAdapter(_Adapter):
        def create_connection(self, **_kwargs):
            conn = _TableFailingConn()
            self.connections.append(conn)
            return conn

    manager = TuningMetadataManager(_TableFailingAdapter(platform_name="duckdb"))
    assert manager.create_metadata_table() is False


def test_athena_metadata_is_unsupported_with_a_clear_message(caplog):
    manager = TuningMetadataManager(_Adapter(platform_name="athena"))
    with caplog.at_level("ERROR"):
        assert manager.create_metadata_table() is False
    assert "Athena" in caplog.text
    assert "Iceberg" in caplog.text


class _StatefulCursor(_Cursor):
    def __init__(self):
        super().__init__()
        self.rows: list[tuple] = []

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("INSERT INTO") and params is not None:
            self.rows.append(tuple(params))
        else:
            super().execute(sql, params)

    def fetchall(self):
        return sorted(self.rows)

    def fetchone(self):
        return (1,)


class _StatefulConn(_Conn):
    def __init__(self):
        super().__init__(_StatefulCursor())


def _round_trip_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.table_tunings["events"] = TableTuning(
        table_name="events",
        sorting=[TuningColumn(name="event_id", type="INTEGER", order=1)],
    )
    return config


@pytest.mark.parametrize(
    "platform",
    [
        "duckdb",
        "postgresql",
        "sqlite",
        "datafusion",
        "snowflake",
        "trino",
        "redshift",
        "databricks",
        "clickhouse-server",
        "bigquery",
    ],
)
def test_metadata_save_load_round_trip_per_platform(platform, caplog):
    adapter = _Adapter(platform_name=platform)
    manager = TuningMetadataManager(adapter, connection=_StatefulConn())
    config = _round_trip_config()
    with caplog.at_level("WARNING"):
        assert manager.save_unified_tunings(config) is True
    assert "Failed to save tuning metadata" not in caplog.text
    assert "Failed to save unified tunings" not in caplog.text
    assert "Failed to create metadata table" not in caplog.text

    loaded = manager.load_unified_tunings()
    assert loaded is not None
    assert set(loaded.table_tunings) == {"events"}


@pytest.mark.parametrize("platform", ["clickhouse", "clickhouse-local", "clickhouse-server"])
def test_clickhouse_create_table_sql_does_not_corrupt_varchar_columns(platform):
    sql = TuningMetadataManager(_Adapter(platform_name=platform))._get_create_table_sql()

    assert "VARCHAR(255) NOT NULL" in sql
    assert "VARCHAR(50) NOT NULL" in sql
    assert "VARCHAR(64) NOT NULL" in sql
    assert sql.count("ENGINE = MergeTree()") == 1
    assert sql.rstrip().endswith("ENGINE = MergeTree() ORDER BY (table_name, tuning_type)")


def test_create_index_sql_skips_platforms_without_indexes():
    assert TuningMetadataManager(_Adapter("clickhouse"))._get_create_index_sql() is None
    assert TuningMetadataManager(_Adapter("bigquery"))._get_create_index_sql() is None
    assert "CREATE INDEX" in TuningMetadataManager(_Adapter("duckdb"))._get_create_index_sql()


def test_table_exists_check_caches_true_result(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    calls = {"n": 0}

    def _fetch_one(_conn, _sql):
        calls["n"] += 1
        return (1,)

    monkeypatch.setattr(manager, "_fetch_one", _fetch_one)

    assert manager._table_exists_check() is True
    assert manager._table_exists_check() is True
    assert calls["n"] == 1


def test_table_exists_check_treats_missing_managed_table_as_fresh_database(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    monkeypatch.setattr(
        manager,
        "_fetch_one",
        lambda _conn, _sql: (_ for _ in ()).throw(
            RuntimeError("Catalog Error: Table with name benchbox_tuning_metadata does not exist!")
        ),
    )

    assert manager._table_exists_check() is False
    assert manager.last_load_error is None


def test_table_exists_check_recognizes_databricks_missing_table_sqlstate(monkeypatch):
    manager = TuningMetadataManager(_Adapter("databricks"))
    message = "[TABLE_OR_VIEW_NOT_FOUND] The table or view `benchbox_tuning_metadata` cannot be found. SQLSTATE: 42P01"
    monkeypatch.setattr(
        manager,
        "_fetch_one",
        lambda _conn, _sql: (_ for _ in ()).throw(RuntimeError(message)),
    )

    assert manager._table_exists_check() is False
    assert manager.last_load_error is None


def test_table_exists_check_preserves_unrelated_probe_failure(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    monkeypatch.setattr(
        manager,
        "_fetch_one",
        lambda _conn, _sql: (_ for _ in ()).throw(RuntimeError("network unavailable")),
    )

    assert manager._table_exists_check() is False
    assert manager.last_load_error == "network unavailable"


def test_rebuild_tunings_from_records_groups_columns_by_table_and_type():
    manager = TuningMetadataManager(_Adapter("duckdb"))
    records = [
        ("orders", TuningType.SORTING.value, "o_orderkey", 1, "h", datetime.now(), "duckdb"),
        ("orders", TuningType.PARTITIONING.value, "o_orderdate", 1, "h", datetime.now(), "duckdb"),
    ]

    tunings = manager._rebuild_tunings_from_records(records, "tpch")

    table = tunings.get_table_tuning("orders")
    assert table is not None
    assert [c.name for c in table.sorting or []] == ["o_orderkey"]
    assert [c.name for c in table.partitioning or []] == ["o_orderdate"]


def test_compare_tuning_configurations_detects_missing_and_mismatch():
    manager = TuningMetadataManager(_Adapter("duckdb"))

    expected = BenchmarkTunings("tpch")
    expected.add_table_tuning(TableTuning(table_name="orders", sorting=[_col("o_orderkey", 1)]))

    existing = BenchmarkTunings("tpch")
    existing.add_table_tuning(TableTuning(table_name="orders", sorting=[_col("o_orderdate", 1)]))
    existing.add_table_tuning(TableTuning(table_name="lineitem", sorting=[_col("l_orderkey", 1)]))

    result = MetadataValidationResult()
    manager._compare_tuning_configurations(expected, existing, result)

    assert result.is_valid is False
    assert "lineitem" in result.extra_tables
    assert any("mismatch" in e for e in result.errors)


def test_compare_tuning_configurations_rejects_extra_table_tunings():
    manager = TuningMetadataManager(_Adapter("duckdb"))
    expected = BenchmarkTunings("tpch")
    expected.add_table_tuning(TableTuning(table_name="orders", sorting=[_col("o_orderkey")]))
    existing = BenchmarkTunings("tpch")
    existing.add_table_tuning(TableTuning(table_name="orders", sorting=[_col("o_orderkey")]))
    existing.add_table_tuning(TableTuning(table_name="lineitem", sorting=[_col("l_orderkey")]))

    result = MetadataValidationResult()
    manager._compare_tuning_configurations(expected, existing, result)

    assert result.is_valid is False
    assert result.extra_tables == {"lineitem"}
    assert any("lineitem" in error for error in result.errors)


def test_clear_tunings_returns_true_when_no_table_exists(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    monkeypatch.setattr(manager, "_table_exists_check", lambda: False)
    assert manager.clear_tunings() is True


def test_get_metadata_summary_formats_query_result(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    monkeypatch.setattr(manager, "_table_exists_check", lambda: True)
    monkeypatch.setattr(
        manager,
        "_fetch_one",
        lambda _conn, _sql: (10, 2, 3, 1, datetime(2026, 1, 1), datetime(2026, 2, 1)),
    )

    summary = manager.get_metadata_summary()

    assert summary["table_exists"] is True
    assert summary["total_records"] == 10
    assert summary["unique_tables"] == 2


def test_execute_sql_falls_back_to_cursor_execution_without_adapter_execute_query():
    adapter = _Adapter("duckdb")
    manager = TuningMetadataManager(adapter)
    conn = _Conn(_Cursor(fetchall_data=[("ok",)]))

    rows = manager._execute_sql(conn, "SELECT 1")

    assert rows == [("ok",)]


def test_execute_sql_prefers_platform_adapter_execute_query_path():
    adapter = _Adapter("duckdb")
    adapter.execute_query = lambda _conn, _sql, _qid: {"result": [("adapter",)]}
    manager = TuningMetadataManager(adapter)

    rows = manager._execute_sql(_Conn(_Cursor()), "SELECT 1")
    assert rows == [("adapter",)]


def test_save_tunings_builds_and_batches_records(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    tunings = BenchmarkTunings("tpch")
    tunings.add_table_tuning(
        TableTuning(
            table_name="orders",
            partitioning=[_col("o_orderdate", 1)],
            sorting=[_col("o_orderkey", 2)],
        )
    )

    captured = {"records": None}
    monkeypatch.setattr(manager, "create_metadata_table", lambda: True)
    monkeypatch.setattr(manager, "clear_tunings", lambda _bn=None: True)
    monkeypatch.setattr(manager, "_batch_insert_records", lambda records: captured.__setitem__("records", records))

    assert manager.save_tunings(tunings) is True
    assert captured["records"] is not None
    assert len(captured["records"]) == 2
    assert {r.tuning_type for r in captured["records"]} == {
        TuningType.PARTITIONING.value,
        TuningType.SORTING.value,
    }


def test_save_tunings_short_circuits_when_metadata_table_fails(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    monkeypatch.setattr(manager, "create_metadata_table", lambda: False)
    assert manager.save_tunings(BenchmarkTunings("tpch")) is False


def test_save_unified_tunings_rejects_invalid_type(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    unified = UnifiedTuningConfiguration()
    monkeypatch.setattr(manager, "save_tunings", lambda _bt: True)
    assert manager.save_unified_tunings(unified) is True
    assert manager.save_unified_tunings(object()) is False


def test_load_tunings_paths(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))

    monkeypatch.setattr(manager, "_table_exists_check", lambda: False)
    assert manager.load_tunings() is None

    monkeypatch.setattr(manager, "_table_exists_check", lambda: True)
    monkeypatch.setattr(manager, "_fetch_all", lambda _conn, _sql: [])
    assert manager.load_tunings() is None

    rows = [
        ("orders", TuningType.SORTING.value, "o_orderkey", 1, "h", datetime.now(), "duckdb"),
    ]
    monkeypatch.setattr(manager, "_fetch_all", lambda _conn, _sql: rows)
    loaded = manager.load_tunings("tpch")
    assert loaded is not None
    assert loaded.get_table_tuning("orders") is not None


def test_validate_tunings_reports_missing_and_success(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    expected = BenchmarkTunings("tpch")
    expected.add_table_tuning(TableTuning(table_name="orders", sorting=[_col("o_orderkey", 1)]))

    monkeypatch.setattr(manager, "load_tunings", lambda _bn=None: None)
    missing = manager.validate_tunings(expected)
    assert missing.is_valid is False
    assert any("No tuning metadata found" in e for e in missing.errors)

    monkeypatch.setattr(manager, "load_tunings", lambda _bn=None: expected)
    ok = manager.validate_tunings(expected)
    assert ok.is_valid is True


def test_get_metadata_summary_handles_no_data_and_errors(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    monkeypatch.setattr(manager, "_table_exists_check", lambda: True)
    monkeypatch.setattr(manager, "_fetch_one", lambda _conn, _sql: None)
    assert manager.get_metadata_summary() == {"table_exists": True, "no_data": True}

    monkeypatch.setattr(manager, "_table_exists_check", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    summary = manager.get_metadata_summary()
    assert summary["table_exists"] is False
    assert "boom" in summary["error"]


def test_hash_section_is_stable_regardless_of_key_order():
    manager = TuningMetadataManager(_Adapter("duckdb"))
    payload_a = {"b": 1, "a": 2}
    payload_b = {"a": 2, "b": 1}
    assert manager._hash_section(payload_a) == manager._hash_section(payload_b)
    assert manager._hash_section({"a": 3}) != manager._hash_section({"a": 4})


def test_build_section_marker_records_shape():
    manager = TuningMetadataManager(_Adapter("databricks"))
    unified = UnifiedTuningConfiguration()
    unified.enable_platform_optimization(TuningType.Z_ORDERING, columns=["o_orderdate"])

    records = manager._build_section_marker_records(unified, "databricks", datetime(2026, 7, 16))

    assert {r.tuning_type for r in records} == {
        TuningMetadataManager._TUNING_TYPE_SCHEMA_VERSION,
        TuningMetadataManager._TUNING_TYPE_CONSTRAINTS_HASH,
        TuningMetadataManager._TUNING_TYPE_PLATFORM_OPT_HASH,
        TuningMetadataManager._TUNING_TYPE_TABLE_ATTRIBUTES_HASH,
    }
    assert all(r.table_name == TuningMetadataManager._SECTION_MARKER_TABLE for r in records)

    version_record = next(r for r in records if r.tuning_type == TuningMetadataManager._TUNING_TYPE_SCHEMA_VERSION)
    assert version_record.configuration_hash == str(TuningMetadataManager._METADATA_SCHEMA_VERSION)


def test_rebuild_tunings_from_records_skips_section_marker_rows():
    manager = TuningMetadataManager(_Adapter("duckdb"))
    records = [
        ("orders", TuningType.SORTING.value, "o_orderkey", 1, "h", datetime.now(), "duckdb"),
        (
            TuningMetadataManager._SECTION_MARKER_TABLE,
            TuningMetadataManager._TUNING_TYPE_SCHEMA_VERSION,
            "schema_version",
            2,
            "2",
            datetime.now(),
            "duckdb",
        ),
        (
            TuningMetadataManager._SECTION_MARKER_TABLE,
            TuningMetadataManager._TUNING_TYPE_PLATFORM_OPT_HASH,
            "platform_optimizations_hash",
            0,
            "deadbeef",
            datetime.now(),
            "duckdb",
        ),
    ]

    tunings = manager._rebuild_tunings_from_records(records, "tpch")

    assert tunings.get_table_names() == ["orders"]
    assert TuningMetadataManager._SECTION_MARKER_TABLE not in tunings.table_tunings


def test_compare_section_hashes_warns_when_no_markers_found(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    monkeypatch.setattr(manager, "_load_section_markers", dict)

    result = MetadataValidationResult()
    manager._compare_section_hashes(UnifiedTuningConfiguration(), result)

    assert result.is_valid is True
    assert not result.drifted_sections
    assert any("older BenchBox" in w for w in result.warnings)


def test_compare_section_hashes_detects_platform_optimization_drift(monkeypatch):
    manager = TuningMetadataManager(_Adapter("databricks"))

    saved = UnifiedTuningConfiguration()
    saved.enable_platform_optimization(TuningType.Z_ORDERING, columns=["o_orderdate"])
    saved_markers = {
        record.tuning_type: record.configuration_hash
        for record in manager._build_section_marker_records(saved, "databricks", datetime.now())
    }
    monkeypatch.setattr(manager, "_load_section_markers", lambda: saved_markers)

    drifted = UnifiedTuningConfiguration()

    result = MetadataValidationResult()
    manager._compare_section_hashes(drifted, result)

    assert result.is_valid is False
    assert result.drifted_sections == {TuningMetadataManager._PLATFORM_OPTIMIZATIONS_SECTION}
    assert any("Platform-optimization" in e for e in result.errors)


def test_compare_section_hashes_detects_constraint_drift(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))

    saved = UnifiedTuningConfiguration()
    saved_markers = {
        record.tuning_type: record.configuration_hash
        for record in manager._build_section_marker_records(saved, "duckdb", datetime.now())
    }
    monkeypatch.setattr(manager, "_load_section_markers", lambda: saved_markers)

    drifted = UnifiedTuningConfiguration()
    drifted.unique_constraints.enabled = False

    result = MetadataValidationResult()
    manager._compare_section_hashes(drifted, result)

    assert result.is_valid is False
    assert result.drifted_sections == {TuningMetadataManager._CONSTRAINTS_SECTION}
    assert any("constraint" in e.lower() for e in result.errors)


def test_compare_section_hashes_no_drift_when_matching(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))

    config = UnifiedTuningConfiguration()
    markers = {
        record.tuning_type: record.configuration_hash
        for record in manager._build_section_marker_records(config, "duckdb", datetime.now())
    }
    monkeypatch.setattr(manager, "_load_section_markers", lambda: markers)

    result = MetadataValidationResult()
    manager._compare_section_hashes(config, result)

    assert result.is_valid is True
    assert not result.drifted_sections


def test_table_attribute_hash_uses_declared_column_order():
    manager = TuningMetadataManager(_Adapter("duckdb"))
    first = UnifiedTuningConfiguration()
    first.table_tunings["orders"] = TableTuning(
        table_name="orders",
        sorting=[_col("o_orderdate", 2), _col("o_orderkey", 1)],
    )
    second = UnifiedTuningConfiguration()
    second.table_tunings["orders"] = TableTuning(
        table_name="orders",
        sorting=[_col("o_orderkey", 1), _col("o_orderdate", 2)],
    )

    assert manager._table_attributes_payload(first) == manager._table_attributes_payload(second)


@pytest.mark.parametrize(
    "missing_marker",
    [
        TuningMetadataManager._TUNING_TYPE_CONSTRAINTS_HASH,
        TuningMetadataManager._TUNING_TYPE_PLATFORM_OPT_HASH,
        TuningMetadataManager._TUNING_TYPE_TABLE_ATTRIBUTES_HASH,
    ],
)
def test_compare_section_hashes_v3_fails_closed_when_required_marker_is_missing(monkeypatch, missing_marker):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    config = UnifiedTuningConfiguration()
    markers = {
        record.tuning_type: record.configuration_hash
        for record in manager._build_section_marker_records(config, "duckdb", datetime.now())
    }
    markers.pop(missing_marker)
    monkeypatch.setattr(manager, "_load_section_markers", lambda: markers)

    result = MetadataValidationResult()
    manager._compare_section_hashes(config, result)

    assert result.is_valid is False
    assert result.errors == [
        f"Incomplete tuning metadata section markers; database reuse is unsafe (missing: {missing_marker})"
    ]


def test_compare_section_hashes_v2_uses_legacy_shape_without_false_drift(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    expected = UnifiedTuningConfiguration()
    expected.primary_keys.enabled = False
    legacy_constraints = {
        "unique_constraints": expected.unique_constraints.to_dict(),
        "check_constraints": expected.check_constraints.to_dict(),
    }
    markers = {
        manager._TUNING_TYPE_SCHEMA_VERSION: "2",
        manager._TUNING_TYPE_CONSTRAINTS_HASH: manager._hash_section(legacy_constraints),
        manager._TUNING_TYPE_PLATFORM_OPT_HASH: manager._hash_section(expected.platform_optimizations.to_dict()),
    }
    monkeypatch.setattr(manager, "_load_section_markers", lambda: markers)

    result = MetadataValidationResult()
    manager._compare_section_hashes(expected, result)

    assert result.is_valid is True
    assert any("Legacy tuning metadata schema" in warning for warning in result.warnings)


@pytest.mark.parametrize("schema_version", ["not-a-version", "999"])
def test_compare_section_hashes_fails_closed_on_unreadable_or_future_version(monkeypatch, schema_version):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    monkeypatch.setattr(
        manager,
        "_load_section_markers",
        lambda: {manager._TUNING_TYPE_SCHEMA_VERSION: schema_version},
    )

    result = MetadataValidationResult()
    manager._compare_section_hashes(UnifiedTuningConfiguration(), result)

    assert result.is_valid is False
    assert len(result.errors) == 1


def test_validate_tunings_distinguishes_load_error_from_missing_metadata(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))

    def fail_load(_benchmark_name=None):
        manager.last_load_error = "network unavailable"
        return None

    monkeypatch.setattr(manager, "load_tunings", fail_load)
    result = manager.validate_tunings(BenchmarkTunings("tpch"))

    assert result.is_valid is False
    assert result.errors == ["Failed to load tuning metadata: network unavailable"]


def test_connection_kwargs_uses_database_under_validation():
    adapter = _Adapter("duckdb")
    adapter.platform_config = {"database": "default", "read_only": False, "role": "default"}

    assert TuningMetadataManager(
        adapter,
        connection_config={"database": "validated", "read_only": True},
    )._connection_kwargs() == {
        "database": "validated",
        "read_only": True,
        "role": "default",
    }


def test_marker_write_failure_is_nonfatal_but_observable(monkeypatch):
    manager = TuningMetadataManager(_Adapter("duckdb"))
    monkeypatch.setattr(manager, "save_tunings", lambda _config: True)
    monkeypatch.setattr(manager, "create_metadata_table", lambda: True)
    monkeypatch.setattr(
        manager,
        "_batch_insert_records",
        lambda _records: (_ for _ in ()).throw(RuntimeError("marker write failed")),
    )

    assert manager.save_unified_tunings(UnifiedTuningConfiguration()) is True
    assert manager.marker_save_failed is True


class _FakeCursor:
    def __init__(self, table: list[tuple]):
        self._table = table
        self._result: Any = []

    def execute(self, sql: str, params: Optional[list] = None) -> None:
        upper = sql.strip().upper()
        if upper.startswith("CREATE TABLE") or upper.startswith("CREATE INDEX"):
            self._result = []
        elif upper.startswith("DELETE FROM"):
            self._table.clear()
            self._result = []
        elif upper.startswith("INSERT INTO"):
            self._table.append(tuple(params))
            self._result = []
        elif "SELECT COUNT(*)" in upper and "LIMIT 1" in upper:
            self._result = (1,)
        elif "WHERE TABLE_NAME =" in upper:
            marker_table = sql.split("'")[1]
            self._result = [(row[1], row[4]) for row in self._table if row[0] == marker_table]
        elif upper.startswith("SELECT TABLE_NAME"):
            self._result = list(self._table)
        else:
            self._result = []

    def fetchall(self) -> list:
        return self._result if isinstance(self._result, list) else []

    def fetchone(self):
        if isinstance(self._result, tuple):
            return self._result
        return self._result[0] if self._result else None


class _FakeConn:
    def __init__(self, table: list[tuple]):
        self._table = table

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._table)

    def commit(self) -> None:
        pass


class _FakeAdapter:
    def __init__(self, platform_name: str = "duckdb"):
        self.platform_name = platform_name
        self.canonical_platform_type = platform_name
        self.platform_config: dict[str, Any] = {}
        self.table: list[tuple] = []

    def create_connection(self, **_kwargs) -> _FakeConn:
        return _FakeConn(self.table)

    def close_connection(self, _conn) -> None:
        return None


def test_validate_unified_tunings_detects_platform_optimization_drift_on_reuse():
    adapter = _FakeAdapter("databricks")

    original = UnifiedTuningConfiguration()
    original.enable_platform_optimization(TuningType.Z_ORDERING, columns=["o_orderdate"])
    assert TuningMetadataManager(adapter).save_unified_tunings(original) is True

    drifted = UnifiedTuningConfiguration()
    result = TuningMetadataManager(adapter).validate_unified_tunings(drifted)

    assert result.is_valid is False
    assert result.drifted_sections == {TuningMetadataManager._PLATFORM_OPTIMIZATIONS_SECTION}
    assert any("Platform-optimization" in e for e in result.errors)


def test_validate_unified_tunings_detects_primary_and_foreign_key_drift_on_reuse():
    adapter = _FakeAdapter("duckdb")
    saved = UnifiedTuningConfiguration()
    saved.primary_keys.enabled = True
    saved.foreign_keys.enabled = True
    assert TuningMetadataManager(adapter).save_unified_tunings(saved) is True

    expected = UnifiedTuningConfiguration()
    expected.primary_keys.enabled = False
    expected.foreign_keys.enabled = False
    result = TuningMetadataManager(adapter).validate_unified_tunings(expected)

    assert result.is_valid is False
    assert TuningMetadataManager._CONSTRAINTS_SECTION in result.drifted_sections
    assert any("primary" in error.lower() or "foreign" in error.lower() for error in result.errors)


def test_validate_unified_tunings_detects_sort_attribute_drift_on_reuse():
    adapter = _FakeAdapter("duckdb")
    saved = UnifiedTuningConfiguration()
    saved.table_tunings["orders"] = TableTuning(
        table_name="orders",
        sorting=[
            TuningColumn(
                name="o_orderkey",
                type="INTEGER",
                order=1,
                sort_order="DESC",
                nulls_position="FIRST",
                compression="zstd",
            )
        ],
    )
    assert TuningMetadataManager(adapter).save_unified_tunings(saved) is True

    expected = UnifiedTuningConfiguration()
    expected.table_tunings["orders"] = TableTuning(table_name="orders", sorting=[_col("o_orderkey")])
    result = TuningMetadataManager(adapter).validate_unified_tunings(expected)

    assert result.is_valid is False
    assert TuningMetadataManager._TABLE_ATTRIBUTES_SECTION in result.configuration_mismatches


def test_validate_unified_tunings_old_format_table_loads_without_error():
    adapter = _FakeAdapter("duckdb")
    adapter.table.append(("orders", TuningType.SORTING.value, "o_orderkey", 1, "somehash", datetime.now(), "duckdb"))

    unified = UnifiedTuningConfiguration()
    unified.table_tunings["orders"] = TableTuning(table_name="orders", sorting=[_col("o_orderkey", 1)])

    result = TuningMetadataManager(adapter).validate_unified_tunings(unified)

    assert result.is_valid is True
    assert any("older BenchBox" in w for w in result.warnings)


def test_validate_unified_tunings_section_only_config_matches_is_valid():
    adapter = _FakeAdapter("databricks")

    config = UnifiedTuningConfiguration()
    config.enable_platform_optimization(TuningType.Z_ORDERING, columns=["o_orderdate"])
    assert TuningMetadataManager(adapter).save_unified_tunings(config) is True

    result = TuningMetadataManager(adapter).validate_unified_tunings(config)

    assert result.is_valid is True
    assert result.errors == []
    assert not result.drifted_sections


def test_validate_unified_tunings_section_only_config_detects_drift_without_false_hard_error():
    adapter = _FakeAdapter("databricks")

    saved = UnifiedTuningConfiguration()
    saved.enable_platform_optimization(TuningType.Z_ORDERING, columns=["o_orderdate"])
    assert TuningMetadataManager(adapter).save_unified_tunings(saved) is True

    drifted = UnifiedTuningConfiguration()
    result = TuningMetadataManager(adapter).validate_unified_tunings(drifted)

    assert result.is_valid is False
    assert result.drifted_sections == {TuningMetadataManager._PLATFORM_OPTIMIZATIONS_SECTION}
    assert any("Platform-optimization" in e for e in result.errors)
    assert not any("No tuning metadata found" in e for e in result.errors)


def test_validate_tunings_still_hard_errors_on_a_truly_empty_table():
    adapter = _FakeAdapter("duckdb")

    unified = UnifiedTuningConfiguration()
    unified.table_tunings["orders"] = TableTuning(table_name="orders", sorting=[_col("o_orderkey", 1)])

    result = TuningMetadataManager(adapter).validate_unified_tunings(unified)

    assert result.is_valid is False
    assert any("No tuning metadata found in database" in e for e in result.errors)


def test_load_unified_tunings_section_only_config_is_not_none():
    adapter = _FakeAdapter("databricks")

    config = UnifiedTuningConfiguration()
    config.enable_platform_optimization(TuningType.Z_ORDERING, columns=["o_orderdate"])
    assert TuningMetadataManager(adapter).save_unified_tunings(config) is True

    loaded = TuningMetadataManager(adapter).load_unified_tunings()

    assert loaded is not None
    assert isinstance(loaded, UnifiedTuningConfiguration)


class _CountingAdapter(_FakeAdapter):
    def __init__(self, platform_name: str = "duckdb"):
        super().__init__(platform_name)
        self.opened = 0
        self.closed: list[object] = []

    def create_connection(self, **_kwargs) -> _FakeConn:
        self.opened += 1
        return super().create_connection()

    def close_connection(self, conn) -> None:
        self.closed.append(conn)
        return None


def test_manager_reuses_supplied_connection_and_never_closes_it():
    adapter = _CountingAdapter("duckdb")
    shared = adapter.create_connection()
    assert adapter.opened == 1

    config = UnifiedTuningConfiguration()
    config.enable_platform_optimization(TuningType.Z_ORDERING, columns=["o_orderdate"])
    manager = TuningMetadataManager(adapter, connection=shared)
    assert manager.save_unified_tunings(config) is True
    assert manager.validate_unified_tunings(config).is_valid is True
    assert manager.get_metadata_summary()["table_exists"] is True
    assert manager.clear_tunings() is True

    assert adapter.opened == 1
    assert shared not in adapter.closed


def test_manager_without_supplied_connection_still_opens_temp_connections():
    adapter = _CountingAdapter("duckdb")

    config = UnifiedTuningConfiguration()
    assert TuningMetadataManager(adapter).save_unified_tunings(config) is True

    assert adapter.opened >= 1
    assert len(adapter.closed) == adapter.opened


class _NoCursorConn:
    def __init__(self):
        self.executed: list[str] = []

    def execute(self, sql):
        self.executed.append(sql)
        return {}


class _ExecuteQueryAdapter(_Adapter):
    def execute_query(self, connection, sql, *_args, **_kwargs):
        return connection.execute(sql)


def test_format_literal_datetime_uses_space_separator():
    manager = TuningMetadataManager(_Adapter())
    assert manager._format_literal(datetime(2026, 10, 6, 20, 56, 51, 252740)) == "'2026-10-06 20:56:51'"
    aware = datetime(2026, 10, 6, 20, 56, 51, tzinfo=timezone(timedelta(hours=5)))
    assert manager._format_literal(aware) == "'2026-10-06 15:56:51'"
    assert manager._format_literal(None) == "NULL"
    assert manager._format_literal(True) == "TRUE"
    assert manager._format_literal(7) == "7"
    assert manager._format_literal("o'clock") == "'o''clock'"


def test_batch_insert_inline_datetime_has_no_iso_t_separator():
    adapter = _ExecuteQueryAdapter(platform_name="clickhouse-server")
    connection = _NoCursorConn()
    manager = TuningMetadataManager(adapter, connection=connection)

    record = manager._build_run_kind_record(datetime(2026, 10, 6, 20, 56, 51, 252740))
    manager._batch_insert_records([record])

    assert len(connection.executed) == 1
    assert "'2026-10-06 20:56:51'" in connection.executed[0]
    assert "2026-10-06T20:56" not in connection.executed[0]
