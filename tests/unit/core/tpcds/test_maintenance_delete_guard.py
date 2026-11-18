from __future__ import annotations

from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from benchbox.core.tpcds.maintenance_functions import (
    MAINTENANCE_FUNCTIONS_BY_NAME,
    delete_target_and_count_sql,
)
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest
from benchbox.core.tpcds.refresh_data import SOURCE_TABLES, SOURCE_TABLES_BY_NAME, RefreshSet, inline_staging_sql

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


def _refresh_set(tmp_path: Path, delete_rows: str = "2000-01-02|2000-01-03\n") -> RefreshSet:
    files = {table.name: tmp_path / f"{table.name}.dat" for table in SOURCE_TABLES}
    files["s_delete"].write_text(delete_rows, encoding="utf-8")
    counts = {table.name: 1 for table in SOURCE_TABLES}
    return RefreshSet(set_id=1, directory=tmp_path, files=files, row_counts=counts)


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


def test_a_correct_delete_passes_and_removes_the_predicate_rows(monkeypatch, database, tmp_path) -> None:
    operation = _harness(monkeypatch, database)._execute_function(
        MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], _refresh_set(tmp_path)
    )

    assert operation.success is True, operation.error
    assert operation.rows_by_table["store_sales"] == -20
    assert operation.rows_by_table["store_returns"] == -10
    assert _rows(database, "store_sales") == 80
    assert _rows(database, "store_returns") == 40


def test_an_over_delete_fails_naming_the_table_and_both_counts(monkeypatch, database, tmp_path) -> None:
    operation = _harness(monkeypatch, database, "store_sales")._execute_function(
        MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], _refresh_set(tmp_path)
    )

    assert operation.success is False
    assert "DF_SS removed 100 rows from store_sales; its predicate matches 20 rows" in operation.error


def test_an_over_delete_of_the_returns_table_fails(monkeypatch, database, tmp_path) -> None:
    operation = _harness(monkeypatch, database, "store_returns")._execute_function(
        MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], _refresh_set(tmp_path)
    )

    assert operation.success is False
    assert "removed 50 rows from store_returns; its predicate matches 10 rows" in operation.error


def test_guard_counts_add_no_clock_reads_to_the_measured_interval(monkeypatch, database, tmp_path) -> None:
    ticks = iter([0.0, 10.0])
    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.mono_time", lambda: next(ticks))
    operation = _harness(monkeypatch, database)._execute_function(
        MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], _refresh_set(tmp_path)
    )

    assert operation.success is True, operation.error
    assert operation.duration == 10.0


def test_predicate_counts_run_before_staging_exists(monkeypatch, database, tmp_path) -> None:
    seen: list[str] = []

    class _Recording(_Connection):
        def execute(self, sql: str):
            if sql.startswith("SELECT COUNT(*)") and " WHERE " in sql:
                seen.append(sql)
            if sql.startswith("CREATE TABLE s_delete"):
                seen.append("STAGING")
            return super().execute(sql)

    harness = _harness(monkeypatch, database)
    harness.connection_factory = lambda: _Recording(database)

    operation = harness._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], _refresh_set(tmp_path))

    assert operation.success is True, operation.error
    assert len(seen) == 3
    assert seen[-1] == "STAGING"
    assert all("UNION ALL" not in sql and "AS s_delete" in sql for sql in seen[:-1])


def test_empty_staging_file_expects_no_removals(monkeypatch, database, tmp_path) -> None:
    refresh_set = _refresh_set(tmp_path, delete_rows="")
    operation = _harness(monkeypatch, database)._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], refresh_set)

    assert operation.success is False
    assert "removed 10 rows from store_returns; its predicate matches 0 rows" in operation.error


def test_inline_staging_sql_renders_typed_values_and_handles_empty_files(tmp_path) -> None:
    path = tmp_path / "s_delete.dat"
    path.write_text("2000-01-02|2000-01-03\n2001-02-03|2001-02-04\n", encoding="utf-8")
    table = SOURCE_TABLES_BY_NAME["s_delete"]

    sql = inline_staging_sql(table, path)

    assert sql == (
        "(SELECT CAST('2000-01-02' AS DATE) AS delete_date1, CAST('2000-01-03' AS DATE) AS delete_date2 "
        "UNION ALL SELECT CAST('2001-02-03' AS DATE) AS delete_date1, CAST('2001-02-04' AS DATE) AS delete_date2) "
        "AS s_delete"
    )
    path.write_text("", encoding="utf-8")
    assert inline_staging_sql(table, path) is None
