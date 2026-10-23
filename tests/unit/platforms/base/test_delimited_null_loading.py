from __future__ import annotations

import io
import logging
import sqlite3
import types
from pathlib import Path

import duckdb
import pytest

from benchbox.platforms.base.data_loading import (
    DelimitedFileHandler,
    RowBatchProcessor,
    resolve_null_columns,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_SCHEMA = {
    "facts": {
        "columns": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
            {"name": "qty", "type": "INTEGER", "nullable": True},
            {"name": "price", "type": "DECIMAL(7,2)", "nullable": True},
            {"name": "code", "type": "CHAR(8)", "nullable": False},
            {"name": "note", "type": "VARCHAR(20)", "nullable": True},
        ]
    }
}

_DDL = (
    "CREATE TABLE facts (id INTEGER NOT NULL, qty INTEGER, price DECIMAL(7,2), code CHAR(8) NOT NULL, note VARCHAR(20))"
)

_ROWS = "1|10|2.50|A|hello|\n2|||B||\n3|||C|x|\n4|5|1.00|D|y|\n"


def _benchmark() -> types.SimpleNamespace:
    return types.SimpleNamespace(get_schema=lambda: _SCHEMA)


def _load_sqlite(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(_DDL)
    DelimitedFileHandler("|", null_marker="").load_table(
        "facts", path, connection, _benchmark(), logging.getLogger("test")
    )
    return connection


def _load_duckdb(path: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(_DDL)
    connection.execute(
        f"INSERT INTO facts SELECT id, qty, price, code, note FROM read_csv('{path}', delim='|', header=false, nullstr='', "
        "null_padding=true, names=['id','qty','price','code','note','trailing'], "
        "types={'id':'INTEGER','qty':'INTEGER','price':'DECIMAL(7,2)','code':'VARCHAR','note':'VARCHAR','trailing':'VARCHAR'})"
    )
    return connection


@pytest.fixture
def dat_file(tmp_path: Path) -> Path:
    path = tmp_path / "facts.dat"
    path.write_text(_ROWS, encoding="utf-8")
    return path


class TestNullableNumericColumns:
    def test_sum_count_avg_match_duckdb(self, dat_file: Path) -> None:
        sqlite_con = _load_sqlite(dat_file)
        duck_con = _load_duckdb(dat_file)

        for sql in (
            "SELECT SUM(qty), COUNT(qty), AVG(qty), SUM(price), COUNT(price), AVG(price) FROM facts",
            "SELECT SUM(price), COUNT(price), AVG(price) FROM facts WHERE id IN (2, 3)",
            "SELECT COUNT(*), COUNT(qty), COUNT(note) FROM facts",
        ):
            expected = duck_con.execute(sql).fetchone()
            actual = sqlite_con.execute(sql).fetchone()
            assert expected is not None
            assert actual is not None
            assert [None if v is None else float(v) for v in actual] == [
                None if v is None else float(v) for v in expected
            ], sql

    def test_all_null_group_sums_to_null(self, dat_file: Path) -> None:
        sqlite_con = _load_sqlite(dat_file)
        row = sqlite_con.execute("SELECT SUM(price), COUNT(price), AVG(price) FROM facts WHERE id IN (2, 3)").fetchone()
        assert row == (None, 0, None)

    def test_no_empty_text_left_in_numeric_columns(self, dat_file: Path) -> None:
        sqlite_con = _load_sqlite(dat_file)
        assert sqlite_con.execute(
            "SELECT COUNT(*) FROM facts WHERE typeof(qty) = 'text' OR typeof(price) = 'text'"
        ).fetchone() == (0,)
        assert sqlite_con.execute("SELECT COUNT(*) FROM facts WHERE qty = '' OR price = ''").fetchone() == (0,)


class TestNotNullTextKeepsEmptyString:
    def test_not_null_text_column_stays_empty_string(self, tmp_path: Path) -> None:
        path = tmp_path / "facts.dat"
        path.write_text("1|1|1.00||n|\n", encoding="utf-8")
        connection = sqlite3.connect(":memory:")
        connection.execute(_DDL)
        DelimitedFileHandler("|", null_marker="").load_table(
            "facts", path, connection, _benchmark(), logging.getLogger("test")
        )
        assert connection.execute("SELECT code, typeof(code), note FROM facts").fetchone() == ("", "text", "n")

    def test_not_null_text_never_masked(self) -> None:
        mask = resolve_null_columns(_benchmark(), "facts", 5, include_text=True)
        assert mask == [False, True, True, False, True]

    def test_csv_keeps_empty_string_in_nullable_text(self) -> None:
        mask = resolve_null_columns(_benchmark(), "facts", 5, include_text=False)
        assert mask == [False, True, True, False, False]

    def test_csv_file_nullable_text_stays_empty_but_numeric_is_null(self, tmp_path: Path) -> None:
        path = tmp_path / "facts.csv"
        path.write_text("2,,,B,\n", encoding="utf-8")
        connection = sqlite3.connect(":memory:")
        connection.execute(_DDL)
        DelimitedFileHandler(",").load_table("facts", path, connection, _benchmark(), logging.getLogger("test"))
        assert connection.execute("SELECT qty, price, code, note FROM facts").fetchone() == (None, None, "B", "")


class TestMaskResolution:
    def test_primary_key_without_nullable_flag_is_not_masked(self) -> None:
        benchmark = types.SimpleNamespace(
            get_schema=lambda: {
                "t": {
                    "columns": [{"name": "k", "type": "INTEGER", "primary_key": True}, {"name": "v", "type": "INTEGER"}]
                }
            }
        )
        assert resolve_null_columns(benchmark, "t", 2, include_text=False) == [False, True]

    def test_column_count_mismatch_disables_masking(self) -> None:
        assert resolve_null_columns(_benchmark(), "facts", 4, include_text=True) is None

    def test_no_schema_disables_masking(self) -> None:
        assert resolve_null_columns(types.SimpleNamespace(), "facts", 2, include_text=True) is None

    def test_processor_nulls_masked_and_padded_fields(self) -> None:
        processor = RowBatchProcessor(batch_size=10)
        batches = list(processor.process_file(io.StringIO("1||x\n2\n"), "|", 3, [False, True, False]))
        assert batches[0][0] == [("1", None, "x"), ("2", None, "")]


class TestExplicitMarker:
    def test_explicit_marker_nulls_nullable_text_and_numeric_only(self, tmp_path: Path) -> None:
        path = tmp_path / "facts.dat"
        path.write_text("1|NA|NA|A|NA|\n2|||B||\n", encoding="utf-8")
        connection = sqlite3.connect(":memory:")
        connection.execute(_DDL)
        DelimitedFileHandler("|", null_marker="NA").load_table(
            "facts", path, connection, _benchmark(), logging.getLogger("test")
        )
        assert connection.execute("SELECT id, qty, price, code, note FROM facts ORDER BY id").fetchall() == [
            (1, None, None, "A", None),
            (2, "", "", "B", ""),
        ]


class TestSqliteAdapterLoad:
    def test_adapter_load_nulls_empty_numeric_and_keeps_not_null_text(self, tmp_path: Path) -> None:
        from benchbox.platforms.sqlite import SQLiteAdapter

        (tmp_path / "facts.dat").write_text("1|10|2.50||hello|\n2|||B||\n", encoding="utf-8")
        benchmark = types.SimpleNamespace(
            get_schema=lambda: _SCHEMA,
            tables={"facts": tmp_path / "facts.dat"},
        )
        adapter = SQLiteAdapter(database_path=":memory:")
        connection = adapter.create_connection()
        connection.execute(_DDL)
        stats, _, _ = adapter.load_data(benchmark, connection, tmp_path)

        assert stats == {"facts": 2}
        assert connection.execute("SELECT id, qty, price, code, note FROM facts ORDER BY id").fetchall() == [
            (1, 10, 2.5, "", "hello"),
            (2, None, None, "B", None),
        ]
        assert connection.execute("SELECT SUM(price), COUNT(price) FROM facts WHERE id = 2").fetchone() == (None, 0)
