from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.tpcds.maintenance_operations import DEFAULT_MAINTENANCE_SEED, MaintenanceOperations
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest, TPCDSMaintenanceTestConfig
from benchbox.core.tpcds.schema import get_create_all_tables_sql

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _SharedConnection:
    dialect = "duckdb"

    def __init__(self, raw, events):
        self.connection = raw
        self._events = events

    def execute(self, sql, params=None):
        if params is None:
            return self.connection.execute(sql)
        return self.connection.execute(sql, params)

    def close(self):
        self._events.append("close")


@pytest.fixture
def raw_duckdb():
    duckdb = pytest.importorskip("duckdb")
    raw = duckdb.connect()
    raw.execute(get_create_all_tables_sql(enable_primary_keys=True, enable_foreign_keys=False))
    yield raw
    raw.close()


def _maintenance_test(raw, events):
    def factory():
        events.append("open")
        return _SharedConnection(raw, events)

    return TPCDSMaintenanceTest(benchmark=None, connection_factory=factory, scale_factor=0.01)


def _snapshot(raw):
    snapshot = {}
    tables = [row[0] for row in raw.execute("SELECT table_name FROM information_schema.tables").fetchall()]
    for table in sorted(tables):
        columns = [row[0] for row in raw.execute(f"DESCRIBE {table}").fetchall()]
        count = raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        checksums = {}
        for column in columns:
            checksums[column] = raw.execute(
                f"SELECT md5(string_agg(CAST({column} AS VARCHAR), '|' ORDER BY CAST({column} AS VARCHAR))) FROM {table}"
            ).fetchone()[0]
        snapshot[table] = (count, checksums)
    return snapshot


def _run_with_seed(seed):
    duckdb = pytest.importorskip("duckdb")
    raw = duckdb.connect()
    raw.execute(get_create_all_tables_sql(enable_primary_keys=True, enable_foreign_keys=False))
    try:
        test = _maintenance_test(raw, [])
        config = TPCDSMaintenanceTestConfig(
            scale_factor=0.01, maintenance_operations=7, operation_interval=0.0, seed=seed
        )
        result = test.run(config)
        return result, _snapshot(raw)
    finally:
        raw.close()


def test_connection_setup_and_initialize_precede_interval_and_close_follows(raw_duckdb):
    events: list[str] = []
    test = _maintenance_test(raw_duckdb, events)

    real_initialize = test.maintenance_ops.initialize
    real_execute = test.maintenance_ops.execute_operation

    def initialize(*args, **kwargs):
        real_initialize(*args, **kwargs)
        events.append("initialize")

    def execute(*args, **kwargs):
        events.append("execute")
        return real_execute(*args, **kwargs)

    ticks = iter(range(1, 100))

    def clock():
        events.append("clock")
        return float(next(ticks))

    with (
        patch.object(test.maintenance_ops, "initialize", initialize),
        patch.object(test.maintenance_ops, "execute_operation", execute),
        patch("benchbox.core.tpcds.maintenance_test.mono_time", clock),
    ):
        operation = test._execute_maintenance_operation("INSERT", "store_sales", 0)

    assert events == ["open", "initialize", "clock", "execute", "clock", "close"]
    assert operation.duration == operation.end_time - operation.start_time


def test_same_seed_reproduces_rows_and_different_seed_differs():
    first, first_snapshot = _run_with_seed(1234)
    second, second_snapshot = _run_with_seed(1234)
    other, other_snapshot = _run_with_seed(4321)

    assert first["successful_operations"] > 0
    assert first_snapshot == second_snapshot
    assert first_snapshot != other_snapshot
    assert {first["seed"], second["seed"]} == {1234}
    assert other["seed"] == 4321


def test_default_seed_is_fixed_and_recorded(raw_duckdb):
    test = _maintenance_test(raw_duckdb, [])
    result = test.run(TPCDSMaintenanceTestConfig(scale_factor=0.01, maintenance_operations=1, operation_interval=0.0))

    assert result["seed"] == DEFAULT_MAINTENANCE_SEED
    assert MaintenanceOperations().seed == DEFAULT_MAINTENANCE_SEED
