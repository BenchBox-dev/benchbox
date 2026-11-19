import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.tpcds.generator import TPCDSDataGenerator
from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS
from benchbox.core.tpcds.maintenance_test import maintenance_unsupported_reason
from benchbox.core.tpcds.schema import get_create_all_tables_sql
from benchbox.core.tpcds.schema.registry import TABLES
from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
from benchbox.platforms.cedardb import CedarDBAdapter
from tests.integration._tpcds_maintenance_compare import (
    FACT_TABLES,
    SCALE_FACTOR,
    assert_same_rows,
    build_duckdb,
    duckdb_fact_rows,
    fact_rows,
    run_duckdb,
    run_maintenance,
)

from .conftest import skip_unless_docker_service

duckdb = pytest.importorskip("duckdb")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_cedardb,
    pytest.mark.slow,
    pytest.mark.tpcds,
]

_INSERTED_ROWS = {
    "LF_CR": {"catalog_returns": 8},
    "LF_CS": {"catalog_sales": 72},
    "LF_I": {"inventory": 540},
    "LF_SR": {"store_returns": 13},
    "LF_SS": {"store_sales": 144},
    "LF_WR": {"web_returns": 2},
    "LF_WS": {"web_sales": 24},
}
_DELETED_FROM = {
    "DF_CS": ("catalog_returns", "catalog_sales"),
    "DF_SS": ("store_returns", "store_sales"),
    "DF_WS": ("web_returns", "web_sales"),
    "DF_I": ("inventory",),
}


def _load_table(connection: Any, table_name: str, columns: str, path: Path) -> None:
    with connection.cursor() as cursor:
        with cursor.copy(f"COPY {table_name} ({columns}) FROM STDIN WITH (FORMAT csv, DELIMITER '|', NULL '')") as copy:
            with path.open(encoding="utf-8") as source:
                while chunk := source.read(65536):
                    copy.write(chunk)
    connection.commit()


@pytest.fixture(scope="module")
def cedardb_vs_duckdb(tmp_path_factory):
    host = os.getenv("CEDARDB_HOST", "localhost")
    port = int(os.getenv("CEDARDB_PORT", "5435"))
    skip_unless_docker_service(host, port, platform="CedarDB")
    schema = f"tpcds_maint_{uuid.uuid4().hex[:8]}"
    adapter = CedarDBAdapter(
        host=host,
        port=port,
        username=os.getenv("CEDARDB_USER", "benchbox"),
        password=os.getenv("CEDARDB_PASSWORD", "Benchbox1!"),
        database=os.getenv("CEDARDB_DATABASE", "benchbox_test"),
        schema=schema,
    )
    adapter.skip_database_management = True
    connection = adapter.create_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cursor.execute(f'SET search_path TO "{schema}"')
        connection.commit()
        for statement in (part for part in get_create_all_tables_sql(False, False).split(";") if part.strip()):
            cursor.execute(statement)
        connection.commit()
        cursor.close()

        root = tmp_path_factory.mktemp("cedardb_tpcds_maintenance")
        generated = TPCDSDataGenerator(scale_factor=SCALE_FACTOR, output_dir=root / "data", quiet=True).generate()
        for table in TABLES:
            columns = ", ".join(column.name for column in table.columns)
            for path in generated.get(table.name) or []:
                _load_table(connection, table.name, columns, Path(path))
        database = build_duckdb(root, generated)

        def factory():
            wrapper = PlatformAdapterConnection(
                adapter.new_stream_connection(connection), adapter, maintenance_mode=True
            )
            wrapper.benchmark_type = "tpcds"
            wrapper.scale_factor = SCALE_FACTOR
            return wrapper

        cedar_result = run_maintenance(factory, adapter.get_target_dialect())
        duck_result = run_duckdb(database)
        cursor = connection.cursor()
        cedar_rows = {table: fact_rows(lambda sql: _fetch(cursor, sql), table) for table in FACT_TABLES}
        cursor.close()
        yield adapter, cedar_result, duck_result, cedar_rows, duckdb_fact_rows(database)
    finally:
        try:
            cursor = connection.cursor()
            cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            connection.commit()
        finally:
            adapter.close_connection(connection)


def _fetch(cursor: Any, sql: str) -> list[tuple[Any, ...]]:
    cursor.execute(sql)
    return cursor.fetchall()


def test_adapter_is_not_reported_as_unsupported(cedardb_vs_duckdb):
    adapter, *_ = cedardb_vs_duckdb
    assert maintenance_unsupported_reason(adapter) is None


def test_all_eleven_functions_complete(cedardb_vs_duckdb):
    _, result, *_ = cedardb_vs_duckdb
    assert result["errors"] == []
    assert result["success"] is True
    assert [op.function for op in result["operations"]] == [function.name for function in MAINTENANCE_FUNCTIONS]
    assert result["successful_operations"] == 11
    assert {op.transactional for op in result["operations"]} == {True}


@pytest.mark.parametrize("function", sorted(_INSERTED_ROWS))
def test_load_function_inserts_every_staged_row(cedardb_vs_duckdb, function):
    _, result, *_ = cedardb_vs_duckdb
    operation = next(op for op in result["operations"] if op.function == function)
    assert operation.success, operation.error
    assert operation.rows_by_table == _INSERTED_ROWS[function]


@pytest.mark.parametrize("function", sorted(_DELETED_FROM))
def test_delete_function_removes_rows(cedardb_vs_duckdb, function):
    _, result, *_ = cedardb_vs_duckdb
    operation = next(op for op in result["operations"] if op.function == function)
    assert operation.success, operation.error
    assert set(operation.rows_by_table) == set(_DELETED_FROM[function])
    assert all(delta < 0 for delta in operation.rows_by_table.values())


def test_each_function_changes_the_same_rows_per_table_as_duckdb(cedardb_vs_duckdb):
    _, cedar_result, duck_result, *_ = cedardb_vs_duckdb
    cedar = {op.function: op.rows_by_table for op in cedar_result["operations"]}
    duck = {op.function: op.rows_by_table for op in duck_result["operations"]}
    assert cedar == duck


@pytest.mark.parametrize("table", FACT_TABLES)
def test_remaining_and_inserted_rows_match_duckdb_per_table(cedardb_vs_duckdb, table):
    *_, cedar_rows, duck_rows = cedardb_vs_duckdb
    assert_same_rows(table, duck_rows[table], cedar_rows[table])
