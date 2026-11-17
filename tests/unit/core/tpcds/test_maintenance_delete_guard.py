from __future__ import annotations

from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from benchbox.core.tpcds.maintenance_functions import (
    MAINTENANCE_FUNCTIONS_BY_NAME,
    delete_target_and_count_sql,
)
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest
from benchbox.core.tpcds.refresh_data import SOURCE_TABLES, RefreshSet

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Connection:
    def __init__(self, database, over_delete_table: str | None = None) -> None:
        self._database = database
        self._over_delete_table = over_delete_table

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
    connection.execute("CREATE TABLE date_dim (d_date_sk INTEGER, d_date DATE)")
    connection.execute("INSERT INTO date_dim SELECT i, DATE '2000-01-01' + CAST(i AS INTEGER) FROM range(0, 10) t(i)")
    connection.execute(
        "CREATE TABLE store_sales (ss_sold_date_sk INTEGER, ss_ticket_number INTEGER, ss_item_sk INTEGER)"
    )
    connection.execute("INSERT INTO store_sales SELECT i % 10, i, i FROM range(0, 100) t(i)")
    connection.execute("CREATE TABLE store_returns (sr_ticket_number INTEGER, sr_item_sk INTEGER)")
    connection.execute("INSERT INTO store_returns SELECT i, i FROM range(0, 100, 2) t(i)")
    yield connection
    connection.close()


def _refresh_set() -> RefreshSet:
    files = {table.name: Path(f"/refresh/{table.name}.dat") for table in SOURCE_TABLES}
    counts = {table.name: 1 for table in SOURCE_TABLES}
    return RefreshSet(set_id=1, directory=Path("/refresh"), files=files, row_counts=counts)


def _harness(monkeypatch: pytest.MonkeyPatch, database, over_delete_table: str | None = None) -> TPCDSMaintenanceTest:
    def load_refresh_file(connection, table, path, expected_rows):
        connection.execute("INSERT INTO s_delete VALUES (DATE '2000-01-02', DATE '2000-01-03')")
        return expected_rows

    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.load_refresh_file", load_refresh_file)
    return TPCDSMaintenanceTest(
        benchmark=None,
        connection_factory=lambda: _Connection(database, over_delete_table),
        scale_factor=0.01,
    )


def _rows(database, table: str) -> int:
    return database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_count_sql_is_derived_from_the_delete_predicate() -> None:
    table, count_sql = delete_target_and_count_sql("DELETE FROM store_sales WHERE ss_sold_date_sk IN (SELECT 1)")
    assert table == "store_sales"
    assert count_sql == "SELECT COUNT(*) FROM store_sales WHERE ss_sold_date_sk IN (SELECT 1)"
    assert delete_target_and_count_sql("INSERT INTO store_sales SELECT 1") is None


def test_a_correct_delete_passes_and_removes_the_predicate_rows(monkeypatch, database) -> None:
    operation = _harness(monkeypatch, database)._execute_function(
        MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], _refresh_set()
    )

    assert operation.success is True, operation.error
    assert operation.rows_by_table["store_sales"] == -20
    assert operation.rows_by_table["store_returns"] == -10
    assert _rows(database, "store_sales") == 80
    assert _rows(database, "store_returns") == 40


def test_an_over_delete_fails_naming_the_table_and_both_counts(monkeypatch, database) -> None:
    operation = _harness(monkeypatch, database, "store_sales")._execute_function(
        MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], _refresh_set()
    )

    assert operation.success is False
    assert "DF_SS removed 100 rows from store_sales; its predicate matches 20 rows" in operation.error


def test_an_over_delete_of_the_returns_table_fails(monkeypatch, database) -> None:
    operation = _harness(monkeypatch, database, "store_returns")._execute_function(
        MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], _refresh_set()
    )

    assert operation.success is False
    assert "removed 50 rows from store_returns; its predicate matches 10 rows" in operation.error


def test_guard_counts_are_excluded_from_the_measured_duration(monkeypatch, database) -> None:
    interval_ticks = iter([0.0, 10.0])
    outside_ticks = iter(float(value) for value in range(1000))
    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.mono_time", lambda: next(interval_ticks))
    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test._outside_interval_clock", lambda: next(outside_ticks))
    operation = _harness(monkeypatch, database)._execute_function(
        MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], _refresh_set()
    )

    assert operation.success is True, operation.error
    assert operation.duration == 6.0
