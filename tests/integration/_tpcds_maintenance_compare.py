from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest, TPCDSMaintenanceTestConfig
from benchbox.core.tpcds.schema import get_create_all_tables_sql
from benchbox.core.tpcds.schema.registry import TABLES
from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
from benchbox.platforms.duckdb import DuckDBAdapter

SCALE_FACTOR = 0.01
FACT_TABLES = (
    "store_sales",
    "store_returns",
    "catalog_sales",
    "catalog_returns",
    "web_sales",
    "web_returns",
    "inventory",
)
DECIMAL_TOLERANCE = Decimal("0.01")


def build_duckdb(root: Path, generated: dict[str, Any]) -> Path:
    import duckdb

    database = root / "base.duckdb"
    connection = duckdb.connect(str(database))
    try:
        connection.execute(get_create_all_tables_sql(enable_primary_keys=False, enable_foreign_keys=False))
        for table in TABLES:
            for path in generated.get(table.name) or []:
                columns = ", ".join(f"'{column.name}': '{column.get_sql_type()}'" for column in table.columns)
                names = ", ".join(column.name for column in table.columns)
                literal = str(path).replace("'", "''")
                connection.execute(
                    f"INSERT INTO {table.name} ({names}) SELECT {names} FROM read_csv('{literal}', delim='|', "
                    f"header=false, nullstr='', auto_detect=false, columns={{{columns}}})"
                )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return database


def run_maintenance(factory: Callable[[], Any], dialect: str) -> dict[str, Any]:
    return TPCDSMaintenanceTest(
        benchmark=None, connection_factory=factory, scale_factor=SCALE_FACTOR, dialect=dialect
    ).run(TPCDSMaintenanceTestConfig(scale_factor=SCALE_FACTOR, refresh_runs=1, first_refresh_set=1))


def run_duckdb(database: Path) -> dict[str, Any]:
    import duckdb

    def factory():
        return PlatformAdapterConnection(duckdb.connect(str(database)), DuckDBAdapter(), maintenance_mode=True)

    return run_maintenance(factory, "duckdb")


def duckdb_fact_rows(database: Path) -> dict[str, list[tuple[str, ...]]]:
    import duckdb

    connection = duckdb.connect(str(database), read_only=True)
    try:
        return {table: fact_rows(lambda sql: connection.execute(sql).fetchall(), table) for table in FACT_TABLES}
    finally:
        connection.close()


def normalise(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (Decimal, float)):
        return f"{value:.2f}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def fact_rows(fetch: Callable[[str], Any], table: str) -> list[tuple[str, ...]]:
    return sorted(tuple(normalise(value) for value in row) for row in fetch(f"SELECT * FROM {table}"))


def _decimal_close(left: str, right: str) -> bool:
    if "." not in left or "." not in right:
        return False
    try:
        return abs(Decimal(left) - Decimal(right)) <= DECIMAL_TOLERANCE
    except ArithmeticError:
        return False


def assert_same_rows(table: str, reference: list[tuple[str, ...]], actual: list[tuple[str, ...]]) -> None:
    assert len(reference) == len(actual), f"{table}: {len(reference)} reference rows, {len(actual)} actual rows"
    if reference == actual:
        return
    mismatches = [
        (left, right)
        for left, right in zip(reference, actual, strict=True)
        if left != right and not all(a == b or _decimal_close(a, b) for a, b in zip(left, right, strict=True))
    ]
    assert not mismatches, f"{table}: {len(mismatches)} rows differ, first {mismatches[0]}"
