from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from benchbox.core.tpcds.generator import TPCDSDataGenerator
from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS
from benchbox.core.tpcds.schema.registry import TABLES
from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
from benchbox.platforms.clickhouse.client import ClickHouseLocalClient
from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter
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
from tests.utilities.optional_engines import require_chdb

pytest.importorskip("duckdb")
require_chdb()

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.tpcds]


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return TPCDSDataGenerator(
        scale_factor=SCALE_FACTOR, output_dir=tmp_path_factory.mktemp("tpcds_chdb_data"), quiet=True
    ).generate()


def _build_chdb(root: Path, generated: dict[str, Any]) -> ClickHouseLocalClient:
    root.mkdir(parents=True, exist_ok=True)
    client = ClickHouseLocalClient(db_path=str(root / "tpcds.chdb"))
    adapter = ClickHouseLocalAdapter()
    for table in TABLES:
        definitions = ", ".join(
            f"{column.name} {'Nullable(' + column.get_sql_type() + ')' if column.nullable and not column.primary_key else column.get_sql_type()}"
            for column in table.columns
        )
        client.execute(f"CREATE TABLE {table.name} ({definitions}) ENGINE = MergeTree ORDER BY tuple()")
        columns = [(column.name, column.get_sql_type()) for column in table.columns]
        for path in generated.get(table.name) or []:
            adapter.ingest_refresh_file(client, table.name, columns, Path(path), null_marker="")
    return client


def _run_chdb(client: ClickHouseLocalClient) -> dict[str, Any]:
    adapter = ClickHouseLocalAdapter()

    def factory():
        return PlatformAdapterConnection(
            adapter.new_stream_connection(client, benchmark_type="tpcds"), adapter, maintenance_mode=True
        )

    return run_maintenance(factory, adapter.get_target_dialect())


@pytest.fixture(scope="module")
def both_engines(tmp_path_factory: pytest.TempPathFactory, generated: dict[str, Any]) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("tpcds_chdb_equivalence")
    database = build_duckdb(root, generated)
    client = _build_chdb(root / "chdb", generated)
    try:
        duck_result = run_duckdb(database)
        chdb_result = _run_chdb(client)
        yield {
            "duck_result": duck_result,
            "chdb_result": chdb_result,
            "duck_rows": duckdb_fact_rows(database),
            "chdb_rows": {table: fact_rows(lambda sql: client.execute(sql).fetchall(), table) for table in FACT_TABLES},
            "tables_after": sorted(
                row[0] for row in client.execute("SELECT name FROM system.tables WHERE database = currentDatabase()")
            ),
        }
    finally:
        client.close()


def test_every_function_succeeds_on_clickhouse_local(both_engines: dict[str, Any]) -> None:
    result = both_engines["chdb_result"]

    assert result["errors"] == []
    assert result["success"] is True
    assert result["total_operations"] == result["successful_operations"] == len(MAINTENANCE_FUNCTIONS)
    assert result["integrity_valid"] is True


def test_refresh_steps_are_recorded_as_non_transactional_on_clickhouse_local(both_engines: dict[str, Any]) -> None:
    assert {operation.transactional for operation in both_engines["chdb_result"]["operations"]} == {False}
    assert {operation.transactional for operation in both_engines["duck_result"]["operations"]} == {True}


def test_each_function_changes_the_same_rows_per_table_as_duckdb(both_engines: dict[str, Any]) -> None:
    duck = {operation.function: operation for operation in both_engines["duck_result"]["operations"]}
    chdb = {operation.function: operation for operation in both_engines["chdb_result"]["operations"]}

    assert set(duck) == set(chdb)
    for name, operation in chdb.items():
        assert operation.rows_by_table == duck[name].rows_by_table, name
        assert operation.staged_rows == duck[name].staged_rows, name
    assert all(delta < 0 for name in ("DF_CS", "DF_SS", "DF_WS", "DF_I") for delta in chdb[name].rows_by_table.values())


@pytest.mark.parametrize("table", FACT_TABLES)
def test_remaining_and_inserted_rows_match_duckdb_per_table(both_engines: dict[str, Any], table: str) -> None:
    assert_same_rows(table, both_engines["duck_rows"][table], both_engines["chdb_rows"][table])


def test_staging_tables_are_dropped(both_engines: dict[str, Any]) -> None:
    assert not [name for name in both_engines["tables_after"] if name.startswith("s_")]
