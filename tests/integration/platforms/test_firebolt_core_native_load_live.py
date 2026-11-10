import os
import platform
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox import TPCH
from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.firebolt import FireboltAdapter, FireboltColumnCountError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_firebolt_core,
]

TPCH_TABLES = ("customer", "lineitem", "nation", "orders", "part", "partsupp", "region", "supplier")


def _emit(line: str) -> None:
    print(f"[firebolt-core-native-load] {line}", file=sys.stderr, flush=True)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"{line}\n\n")


def _engine_version(cursor) -> str:
    for statement in ("SELECT version()", "SELECT VERSION()"):
        try:
            cursor.execute(statement)
            row = cursor.fetchone()
            if row:
                return str(row[0])
        except Exception as exc:
            _emit(f"{statement} failed: {exc}")
    return "unknown"


def _file_row_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


@pytest.fixture(scope="module")
def adapter(live_firebolt_core_adapter):
    return live_firebolt_core_adapter


@pytest.fixture
def connection(adapter):
    conn = adapter.create_connection()
    try:
        yield conn
    finally:
        adapter.close_connection(conn)


@pytest.fixture(scope="module")
def tpch_sf001(tmp_path_factory):
    output_dir = tmp_path_factory.mktemp("firebolt_core_tpch_sf001")
    benchmark = TPCH(scale_factor=0.01, output_dir=output_dir, verbose=False)
    benchmark.generate_data()
    return benchmark, output_dir


class TestFireboltCoreNativeLoadLive:
    def test_tpch_sf001_native_load_row_counts(self, adapter, connection, tpch_sf001, monkeypatch):
        benchmark, data_dir = tpch_sf001

        cursor = connection.cursor()
        version = _engine_version(cursor)
        image = os.getenv("FIREBOLT_CORE_IMAGE_REF", "unspecified")
        host = f"{platform.node()} {platform.system()} {platform.machine()}"
        _emit(f"Firebolt Core version: {version}; image: {image}; runner: {host}")

        data_source = adapter._resolve_data_files(benchmark, data_dir)
        expected = {
            table.lower(): sum(_file_row_count(Path(p)) for p in paths) for table, paths in data_source.tables.items()
        }
        _emit(f"expected counts from generated files: {expected}")
        assert set(expected) == set(TPCH_TABLES), f"unexpected generated tables: {sorted(expected)}"
        assert all(count > 0 for count in expected.values()), f"empty generated table: {expected}"

        native_calls: list[str] = []
        insert_calls: list[str] = []
        original_post = adapter._post_core_upload
        original_insert = adapter._load_file_via_insert

        def spy_post(sql, part_name, file_path):
            native_calls.append(Path(file_path).name)
            return original_post(sql, part_name, file_path)

        def spy_insert(*args, **kwargs):
            insert_calls.append(str(args[3]) if len(args) > 3 else "?")
            return original_insert(*args, **kwargs)

        monkeypatch.setattr(adapter, "_post_core_upload", spy_post)
        monkeypatch.setattr(adapter, "_load_file_via_insert", spy_insert)

        adapter.create_schema(benchmark, connection)
        table_stats, load_time, _ = adapter.load_data(benchmark, connection, data_dir)
        _emit(f"adapter-reported counts: {table_stats}; load time {load_time:.2f}s")

        actual = {}
        for table in TPCH_TABLES:
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            actual[table] = int(cursor.fetchone()[0])
        _emit(f"server counts: {actual}")
        _emit(f"native upload files: {native_calls}; INSERT fallback tables: {insert_calls}")
        _emit(
            "| table | expected | server |\n|---|---|---|\n"
            + "\n".join(f"| {t} | {expected[t]} | {actual[t]} |" for t in TPCH_TABLES)
        )

        assert actual == expected, f"server row counts differ from generated files: server={actual} expected={expected}"
        assert dict(table_stats) == expected, (
            f"adapter-reported counts differ: adapter={table_stats} expected={expected}"
        )
        assert len(native_calls) == len(TPCH_TABLES), (
            f"native upload path not used for every table: uploads={native_calls} insert_fallbacks={insert_calls}"
        )
        assert not insert_calls, f"INSERT fallback was used: {insert_calls}"

    def test_malformed_file_rejected_with_column_count_error(self, adapter, connection, tmp_path):
        cursor = connection.cursor()
        table = "benchbox_native_load_malformed"
        cursor.execute(f'DROP TABLE IF EXISTS "{table}"')
        cursor.execute(f'CREATE TABLE "{table}" (id INT, name TEXT, amount INT)')
        try:
            bad = tmp_path / f"{table}.tbl"
            bad.write_text("1|alpha|10|\n2|beta|\n3|gamma|30|\n", encoding="utf-8")

            dialect = _tbl_dialect()
            column_types = adapter._core_table_column_types(cursor, table)
            assert column_types is not None and len(column_types) == 3, f"column types: {column_types}"
            sql = adapter._build_core_upload_sql(f'"{table}"', column_types, "benchbox_load", dialect)
            _emit(f"native load SQL: {sql}")

            with pytest.raises(FireboltColumnCountError) as load_error:
                adapter._load_file_via_core_upload(cursor, table, f'"{table}"', bad, dialect)
            message = str(load_error.value)
            _emit(f"adapter error for malformed file: {message}")
            assert "column count" in message.lower()

            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            assert int(cursor.fetchone()[0]) == 0, "malformed file was silently loaded"

            with pytest.raises(FireboltColumnCountError):
                adapter._load_data_via_insert(_single_table_benchmark(table, bad), connection, tmp_path)

            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            assert int(cursor.fetchone()[0]) == 0, "malformed benchmark load left rows in the table"

            try:
                adapter._post_core_upload(sql, "benchbox_load", bad)
                _emit("server accepted the malformed upload without error")
            except RuntimeError as exc:
                _emit(f"server error for malformed upload: {exc}")
            cursor.execute(f'DELETE FROM "{table}"')
        finally:
            cursor.execute(f'DROP TABLE IF EXISTS "{table}"')


def _single_table_benchmark(table, path):
    benchmark = Mock()
    benchmark.tables = {table: str(path)}
    return benchmark


def _tbl_dialect():
    return CsvDialect(delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None)
