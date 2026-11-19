from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

duckdb = pytest.importorskip("duckdb")

from benchbox.core.tpch.maintenance_test import TPCHMaintenanceTest
from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    apply_rf2,
    capture_rf2_baseline,
    verify_rf2_deletes,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpch]


class _Connection:
    def __init__(self, database, over_delete_table: str | None = None) -> None:
        self._database = database
        self._over_delete_table = over_delete_table
        self.platform_adapter = SimpleNamespace(record_refresh_applied=lambda _connection: None)

    def execute(self, sql: str):
        if self._over_delete_table is not None and sql.startswith(f"DELETE FROM {self._over_delete_table} "):
            sql = f"DELETE FROM {self._over_delete_table}"
        return self._database.execute(sql)

    def commit(self) -> None:
        self._database.commit()

    def rollback(self) -> None:
        self._database.rollback()

    def close(self) -> None:
        pass


@pytest.fixture()
def database():
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE orders (o_orderkey INTEGER)")
    connection.execute("INSERT INTO orders SELECT i FROM range(1, 21) t(i)")
    connection.execute("CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER)")
    connection.execute("INSERT INTO lineitem SELECT i, n FROM range(1, 21) t(i), range(1, 4) u(n)")
    yield connection
    connection.close()


@pytest.fixture()
def update_set(tmp_path: Path) -> TPCHRefreshUpdateSet:
    delete_path = tmp_path / "delete.1"
    delete_path.write_text("3|\n4|\n5|\n", encoding="utf-8")
    return TPCHRefreshUpdateSet(
        set_id=1,
        orders_path=tmp_path / "orders.tbl.u1",
        lineitem_path=tmp_path / "lineitem.tbl.u1",
        delete_path=delete_path,
    )


def _rows(database, table: str) -> int:
    return database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_baseline_counts_the_delete_keys_and_table_totals(database, update_set) -> None:
    baseline = capture_rf2_baseline(_Connection(database), update_set)

    assert (baseline.orders_total, baseline.lineitems_total) == (20, 60)
    assert (baseline.orders_for_keys, baseline.lineitems_for_keys) == (3, 9)


def test_a_correct_delete_passes_the_delta_check(database, update_set) -> None:
    connection = _Connection(database)
    baseline = capture_rf2_baseline(connection, update_set)

    deletes = apply_rf2(connection, update_set)
    verify_rf2_deletes(connection, update_set, baseline, deletes)

    assert (deletes.orders, deletes.lineitems) == (3, 9)
    assert (_rows(database, "orders"), _rows(database, "lineitem")) == (17, 51)


@pytest.mark.parametrize(
    ("table", "removed", "expected"),
    [("lineitem", 60, 9), ("orders", 20, 3)],
)
def test_an_over_delete_fails_naming_the_table_and_counts(database, update_set, table, removed, expected) -> None:
    connection = _Connection(database, table)
    baseline = capture_rf2_baseline(connection, update_set)

    deletes = apply_rf2(connection, update_set)

    with pytest.raises(ValueError, match=f"removed {removed} rows from {table}, expected {expected} rows"):
        verify_rf2_deletes(connection, update_set, baseline, deletes)


def test_a_driver_that_misreports_the_count_fails(database, update_set) -> None:
    connection = _Connection(database)
    baseline = capture_rf2_baseline(connection, update_set)
    deletes = apply_rf2(connection, update_set)
    inflated = type(deletes)(orders=deletes.orders, lineitems=deletes.lineitems + 1)

    with pytest.raises(ValueError, match="driver reported 10 rows deleted from lineitem, expected 9"):
        verify_rf2_deletes(connection, update_set, baseline, inflated)


def test_maintenance_rf2_succeeds_for_a_correct_delete(database, update_set) -> None:
    operation = TPCHMaintenanceTest(connection_factory=lambda: _Connection(database))._execute_rf2(0, update_set)

    assert operation.success is True, operation.error
    assert operation.rows_affected == 12


def test_maintenance_rf2_fails_for_an_over_delete(database, update_set) -> None:
    operation = TPCHMaintenanceTest(connection_factory=lambda: _Connection(database, "lineitem"))._execute_rf2(
        0, update_set
    )

    assert operation.success is False
    assert "removed 60 rows from lineitem, expected 9 rows" in operation.error
