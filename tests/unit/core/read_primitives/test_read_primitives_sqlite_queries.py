import sqlite3

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.read_primitives.catalog import load_primitives_catalog
from benchbox.core.read_primitives.schema import TABLES, get_create_table_sql
from benchbox.platforms.sqlite import _register_sqlite_compatibility_functions

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

_ROWS_PER_TABLE = 24


def _seed_value(column: dict, row: int):
    sql_type = column["type"].upper()
    name = column["name"]
    if sql_type.startswith(("INT", "BIGINT", "SMALLINT")):
        return row % 4 + 1 if name.endswith(("key", "number")) or "key" in name else row + 1
    if sql_type.startswith(("DECIMAL", "NUMERIC", "FLOAT", "DOUBLE")):
        return float(row * 7 % 11) + 1.5
    if sql_type == "DATE":
        return f"199{5 + row % 2}-{row % 12 + 1:02d}-15"
    return f"{name}_{row % 5}"


def _build_connection(*, with_adapter_functions: bool) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    if with_adapter_functions:
        _register_sqlite_compatibility_functions(connection)
    for table_name, table in TABLES.items():
        connection.execute(get_create_table_sql(table_name, "sqlite", False, False))
        placeholders = ", ".join("?" for _ in table["columns"])
        rows = [[_seed_value(column, row) for column in table["columns"]] for row in range(_ROWS_PER_TABLE)]
        connection.executemany(f"INSERT INTO {table_name} VALUES ({placeholders})", rows)
    return connection


@pytest.fixture(scope="module")
def adapter_connection():
    connection = _build_connection(with_adapter_functions=True)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def stock_connection():
    connection = _build_connection(with_adapter_functions=False)
    yield connection
    connection.close()


def _runnable_sqlite_queries() -> dict[str, str]:
    return ReadPrimitivesBenchmark(scale_factor=0.01).get_queries(dialect="sqlite")


@pytest.mark.parametrize("query_id", sorted(_runnable_sqlite_queries()))
def test_non_skipped_catalog_query_executes_on_sqlite(adapter_connection, stock_connection, query_id):
    sql = _runnable_sqlite_queries()[query_id]
    has_variant = ReadPrimitivesBenchmark(scale_factor=0.01).query_manager.has_variant(query_id, "sqlite")
    connection = adapter_connection if has_variant else stock_connection
    cursor = connection.execute(sql)
    cursor.fetchall()

    contract = load_primitives_catalog().queries[query_id].result_contract
    if contract is not None:
        assert [description[0] for description in cursor.description] == [column.name for column in contract.columns]
