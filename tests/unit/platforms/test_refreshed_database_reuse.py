from __future__ import annotations

import logging
import shutil
from pathlib import Path

import duckdb
import pytest

from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.core.tpch.refresh import apply_rf1, apply_rf2, generate_refresh_update_sets
from benchbox.core.tpch.schema import TABLES
from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpch]

SCALE = 0.01


@pytest.fixture(scope="module")
def pristine_database(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("tpch-sf001")
    staging = root / "data"
    TPCHDataGenerator(scale_factor=SCALE, output_dir=staging, quiet=True).generate()
    path = root / "pristine.duckdb"
    connection = duckdb.connect(str(path))
    for table in TABLES:
        columns = ", ".join(f"{column.name} {column.get_sql_type()}" for column in table.columns)
        connection.execute(f"CREATE TABLE {table.name} ({columns})")
        names = ", ".join(column.name for column in table.columns)
        typed = ", ".join(f"'{column.name}': 'VARCHAR'" for column in table.columns)
        connection.execute(
            f"INSERT INTO {table.name} ({names}) SELECT {names} FROM read_csv('{staging / (table.name.lower() + '.tbl')}', "
            f"delim='|', header=false, auto_detect=false, null_padding=true, columns={{{typed}, '_trailing': 'VARCHAR'}})"
        )
    connection.close()
    return path


@pytest.fixture(scope="module")
def refresh_update_set(tmp_path_factory):
    return generate_refresh_update_sets(SCALE, 1, 1, tmp_path_factory.mktemp("dbgen"))[1]


@pytest.fixture
def output_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "benchmark_runs"
    (root / "databases").mkdir(parents=True)
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(root))
    return root


def _database_copy(pristine_database: Path, directory: Path) -> Path:
    target = directory / "tpch_sf001.duckdb"
    shutil.copy(pristine_database, target)
    return target


def _refresh(path: Path, update_set, *, rf1: bool = True, rf2: bool = True) -> None:
    connection = duckdb.connect(str(path))
    try:
        adapter = DuckDBAdapter(database_path=str(path))
        refresh_connection = PlatformAdapterConnection(connection.cursor(), adapter, maintenance_mode=True)
        if rf1:
            apply_rf1(refresh_connection, update_set)
        if rf2:
            apply_rf2(refresh_connection, update_set)
    finally:
        connection.close()


def _adapter_for(path: Path) -> DuckDBAdapter:
    adapter = DuckDBAdapter(database_path=str(path))
    adapter.benchmark_instance = TPCHBenchmark(scale_factor=SCALE)
    adapter.scale_factor = SCALE
    return adapter


def _counts(path: Path) -> dict[str, int]:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        return {t.name: connection.execute(f"SELECT COUNT(*) FROM {t.name}").fetchone()[0] for t in TABLES}
    finally:
        connection.close()


def test_database_refreshed_by_rf1_and_rf2_is_not_reused(pristine_database, refresh_update_set, output_root, caplog):
    path = _database_copy(pristine_database, output_root / "databases")
    _refresh(path, refresh_update_set)
    adapter = _adapter_for(path)

    with caplog.at_level(logging.WARNING):
        adapter.handle_existing_database()

    assert adapter.database_was_reused is False
    assert not path.exists()
    assert "modified by TPC-H refresh functions" in caplog.text


def test_database_refreshed_by_rf1_alone_is_not_reused(pristine_database, refresh_update_set, output_root):
    path = _database_copy(pristine_database, output_root / "databases")
    _refresh(path, refresh_update_set, rf2=False)
    adapter = _adapter_for(path)

    adapter.handle_existing_database()

    assert adapter.database_was_reused is False
    assert not path.exists()


def test_refresh_that_kept_every_table_count_is_still_not_reused(pristine_database, refresh_update_set, output_root):
    path = _database_copy(pristine_database, output_root / "databases")
    before = _counts(path)
    connection = duckdb.connect(str(path))
    try:
        connection.execute("UPDATE lineitem SET L_QUANTITY = L_QUANTITY + 1")
        adapter = DuckDBAdapter(database_path=str(path))
        apply_rf1_connection = PlatformAdapterConnection(connection.cursor(), adapter, maintenance_mode=True)
        from benchbox.core.tpch import refresh

        refresh._record_refresh_applied(apply_rf1_connection)
    finally:
        connection.close()
    assert _counts(path) == before
    reused = _adapter_for(path)

    reused.handle_existing_database()

    assert reused.database_was_reused is False


def test_fresh_unrefreshed_database_is_reused(pristine_database, output_root):
    path = _database_copy(pristine_database, output_root / "databases")
    adapter = _adapter_for(path)

    adapter.handle_existing_database()

    assert adapter.database_was_reused is True
    assert path.exists()


def test_refreshed_database_outside_output_root_is_refused_not_deleted(
    pristine_database, refresh_update_set, output_root, tmp_path
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    path = _database_copy(pristine_database, elsewhere)
    _refresh(path, refresh_update_set)
    adapter = _adapter_for(path)

    with pytest.raises(RuntimeError, match="outside the BenchBox output directory"):
        adapter.handle_existing_database()

    assert path.exists()
