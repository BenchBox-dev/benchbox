from __future__ import annotations

import os
from pathlib import Path

import pytest

from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.singlestore import SingleStoreAdapter

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_singlestore,
]

_HOST_PORT = int(os.getenv("SINGLESTORE_HOST_PORT", "13306"))
_PASSWORD = os.getenv("SINGLESTORE_PASSWORD", "benchbox")
_TABLE = "lvi_decimal_load"
_REFRESH_COLUMNS = [("id", "INTEGER"), ("price", "DECIMAL(15,2)"), ("note", "VARCHAR(10)")]

_MALFORMED = ["abc", "1.2.3", "", "1e5", " 1", "1 ", "--1", "1,5", "NaN"]
_PATHS = ["tbl", "csv_header", "dat_null_marker", "parquet", "refresh"]


@pytest.fixture
def decimal_table():
    skip_unless_docker_service("localhost", _HOST_PORT, platform="SingleStore")
    adapter = SingleStoreAdapter(
        host="localhost", port=_HOST_PORT, username="root", password=_PASSWORD, database="benchbox_test"
    )
    adapter.skip_database_management = True
    connection = adapter.create_connection()
    cursor = connection.cursor()
    cursor.execute(f"DROP TABLE IF EXISTS `{_TABLE}`")
    cursor.execute(f"CREATE TABLE `{_TABLE}` (id INT NOT NULL, price DECIMAL(15,2), note VARCHAR(10), SHARD KEY (id))")
    try:
        yield adapter, connection, cursor
    finally:
        cursor.execute(f"DROP TABLE IF EXISTS `{_TABLE}`")
        adapter.close_connection(connection)


def _load(adapter, connection, tmp_path: Path, path_kind: str, value: str | None) -> int:
    text = "" if value is None else value
    if path_kind == "tbl":
        path = tmp_path / "x.tbl"
        path.write_text(f"1|{text}|ab|\n", encoding="utf-8")
        dialect = CsvDialect(delimiter="|", has_header=False, null_marker=None, normalize_booleans=False, quote=None)
        return adapter._load_data_infile(connection, _TABLE, path, dialect, True)
    if path_kind == "csv_header":
        path = tmp_path / "x.csv"
        path.write_text(f"id,price,note\n1,{text},ab\n", encoding="utf-8")
        dialect = CsvDialect(delimiter=",", has_header=True, null_marker=None, normalize_booleans=False, quote='"')
        return adapter._load_data_infile(connection, _TABLE, path, dialect, False)
    if path_kind == "dat_null_marker":
        path = tmp_path / "x.dat"
        path.write_text(f"1|{text}|ab\n", encoding="utf-8")
        dialect = CsvDialect(delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None)
        return adapter._load_data_infile(connection, _TABLE, path, dialect, False)
    if path_kind == "parquet":
        pa = pytest.importorskip("pyarrow")
        pq = pytest.importorskip("pyarrow.parquet")
        path = tmp_path / "x.parquet"
        pq.write_table(pa.table({"id": [1], "price": [value], "note": ["ab"]}), path)
        return adapter._load_parquet_infile(connection, _TABLE, path)
    path = tmp_path / "x.dat"
    path.write_text(f"1|{text}|ab\n", encoding="utf-8")
    return adapter.ingest_refresh_file(connection, _TABLE, _REFRESH_COLUMNS, path, None)


def _rows(cursor) -> list[tuple]:
    cursor.execute(f"SELECT id, CAST(price AS CHAR), note FROM `{_TABLE}`")
    return [tuple(row) for row in cursor.fetchall()]


@pytest.mark.parametrize("path_kind", _PATHS)
@pytest.mark.parametrize("value", _MALFORMED)
def test_malformed_decimal_aborts_the_load(decimal_table, tmp_path, path_kind, value):
    if path_kind == "dat_null_marker" and value == "":
        pytest.skip("an empty field is the NULL marker for this dialect")
    adapter, connection, cursor = decimal_table

    with pytest.raises(Exception):
        _load(adapter, connection, tmp_path, path_kind, value)

    assert _rows(cursor) == []


@pytest.mark.parametrize("path_kind", _PATHS)
@pytest.mark.parametrize(
    ("value", "expected"),
    [("12.50", "12.50"), ("-3.25", "-3.25"), ("0", "0.00"), ("1234567890123.45", "1234567890123.45")],
)
def test_valid_decimal_loads_unchanged(decimal_table, tmp_path, path_kind, value, expected):
    adapter, connection, cursor = decimal_table

    assert _load(adapter, connection, tmp_path, path_kind, value) == 1

    assert _rows(cursor) == [(1, expected, "ab")]


@pytest.mark.parametrize("path_kind", ["dat_null_marker", "parquet"])
def test_null_decimal_loads_as_null(decimal_table, tmp_path, path_kind):
    adapter, connection, cursor = decimal_table

    assert _load(adapter, connection, tmp_path, path_kind, "" if path_kind == "dat_null_marker" else None) == 1

    assert _rows(cursor) == [(1, None, "ab")]


def test_csv_header_row_is_skipped_with_decimal_guard(decimal_table, tmp_path):
    adapter, connection, cursor = decimal_table

    assert _load(adapter, connection, tmp_path, "csv_header", "7.10") == 1

    assert _rows(cursor) == [(1, "7.10", "ab")]
