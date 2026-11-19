from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.tpcds.generator import TPCDSDataGenerator
from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS
from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
from benchbox.platforms.duckdb import DuckDBAdapter
from tests.integration._tpcds_maintenance_compare import (
    FACT_TABLES,
    SCALE_FACTOR,
    build_duckdb,
    duckdb_fact_rows,
    run_maintenance,
)

duckdb = pytest.importorskip("duckdb")

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.tpcds]

RETURNS_DELETES = ("DF_CS", "DF_SS", "DF_WS")


@pytest.fixture(scope="module")
def base_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tpcds_rewrite_base")
    generated = TPCDSDataGenerator(scale_factor=SCALE_FACTOR, output_dir=root / "data", quiet=True).generate()
    return build_duckdb(root, generated)


def _run(database: Path, dialect: str) -> dict[str, Any]:
    def factory():
        return PlatformAdapterConnection(duckdb.connect(str(database)), DuckDBAdapter(), maintenance_mode=True)

    return run_maintenance(factory, dialect)


@pytest.fixture(scope="module")
def both_runs(base_database: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("tpcds_rewrite_runs")
    runs: dict[str, Any] = {}
    for dialect in ("duckdb", "clickhouse"):
        database = root / f"{dialect}.duckdb"
        shutil.copyfile(base_database, database)
        runs[dialect] = {"result": _run(database, dialect), "rows": duckdb_fact_rows(database)}
    return runs


def test_clickhouse_statements_replace_the_correlated_exists_with_an_uncorrelated_tuple_in() -> None:
    for function in MAINTENANCE_FUNCTIONS:
        original = " ".join(function.statements_for("duckdb")).upper()
        rewritten = " ".join(function.statements_for("clickhouse")).upper()
        if function.name in RETURNS_DELETES:
            assert "EXISTS" in original
            assert "EXISTS" not in rewritten
            assert ") IN (SELECT" in rewritten
        else:
            assert function.statements_for("clickhouse") == function.statements_for("duckdb")


def test_both_dialects_run_all_eleven_functions(both_runs: dict[str, Any]) -> None:
    for dialect, run in both_runs.items():
        assert run["result"]["errors"] == [], dialect
        assert run["result"]["success"] is True, dialect
        assert run["result"]["successful_operations"] == 11, dialect


def test_rewrite_changes_the_same_row_counts_per_function(both_runs: dict[str, Any]) -> None:
    original = {op.function: op.rows_by_table for op in both_runs["duckdb"]["result"]["operations"]}
    rewritten = {op.function: op.rows_by_table for op in both_runs["clickhouse"]["result"]["operations"]}

    assert rewritten == original
    assert all(delta < 0 for name in (*RETURNS_DELETES, "DF_I") for delta in original[name].values())


@pytest.mark.parametrize("table", FACT_TABLES)
def test_rewrite_leaves_identical_rows_in_every_fact_table(both_runs: dict[str, Any], table: str) -> None:
    assert both_runs["clickhouse"]["rows"][table] == both_runs["duckdb"]["rows"][table]
