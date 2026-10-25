from __future__ import annotations

import types
from pathlib import Path

import duckdb
import pytest

from benchbox.core.manifest_utils import write_delimited_manifest
from benchbox.platforms.base.data_loading import RowBatchProcessor
from benchbox.platforms.sqlite import SQLiteAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_SCHEMA = {
    "hits": {
        "columns": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
            {"name": "phrase", "type": "TEXT", "nullable": False},
            {"name": "model", "type": "TEXT", "nullable": True},
            {"name": "qty", "type": "BIGINT", "nullable": False},
        ]
    }
}

_DDL = "CREATE TABLE hits (id INTEGER NOT NULL, phrase TEXT NOT NULL, model TEXT, qty BIGINT NOT NULL)"

_ROWS = (
    '1|""|iphone|10\r\n'
    '2|hello|""|20\r\n'
    '3|"a|b"|"say ""hi"""|30\r\n'
    '4|"line1\nline2"|__NULL__|40\r\n'
    "5|plain|iphone|50\r\n"
    '6|""|""|60\r\n'
)

_PROBES = (
    "SELECT COUNT(*) FROM hits WHERE phrase <> ''",
    "SELECT COUNT(*) FROM hits WHERE model <> ''",
    "SELECT COUNT(*) FROM hits WHERE model IS NULL",
    "SELECT phrase, COUNT(*) FROM hits GROUP BY phrase ORDER BY phrase",
    "SELECT COUNT(DISTINCT model) FROM hits",
    "SELECT COUNT(DISTINCT phrase) FROM hits",
    "SELECT id, phrase, model, qty FROM hits ORDER BY id",
    "SELECT SUM(qty) FROM hits WHERE phrase = ''",
)


@pytest.fixture
def quoted_dir(tmp_path: Path) -> Path:
    (tmp_path / "hits.tbl").write_bytes(_ROWS.encode("utf-8"))
    generator = types.SimpleNamespace(output_dir=tmp_path, scale_factor=1.0, compression_type="none")
    write_delimited_manifest(
        generator, "clickbench", {"hits": tmp_path / "hits.tbl"}, {"hits": 6}, null_marker="__NULL__", quote='"'
    )
    return tmp_path


def _load_sqlite(data_dir: Path):
    benchmark = types.SimpleNamespace(get_schema=lambda: _SCHEMA, tables={"hits": data_dir / "hits.tbl"})
    adapter = SQLiteAdapter(database_path=":memory:")
    connection = adapter.create_connection()
    connection.execute(_DDL)
    stats, _, _ = adapter.load_data(benchmark, connection, data_dir)
    return connection, stats


def _load_duckdb(data_dir: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(_DDL)
    connection.execute(
        f"INSERT INTO hits SELECT * FROM read_csv('{data_dir / 'hits.tbl'}', delim='|', quote='\"', escape='\"', "
        "header=false, nullstr='__NULL__', columns={'id':'INTEGER','phrase':'VARCHAR','model':'VARCHAR','qty':'BIGINT'})"
    )
    return connection


def test_sqlite_load_matches_duckdb_for_quoted_empty_null_and_text(quoted_dir: Path) -> None:
    sqlite_connection, stats = _load_sqlite(quoted_dir)
    duckdb_connection = _load_duckdb(quoted_dir)

    assert stats == {"hits": 6}
    for probe in _PROBES:
        assert sqlite_connection.execute(probe).fetchall() == duckdb_connection.execute(probe).fetchall(), probe


def test_sqlite_quoted_empty_is_empty_string_not_literal_quotes(quoted_dir: Path) -> None:
    connection, _ = _load_sqlite(quoted_dir)

    assert connection.execute("SELECT COUNT(*) FROM hits WHERE phrase = ''").fetchone() == (2,)
    assert connection.execute("SELECT COUNT(*) FROM hits WHERE phrase LIKE '%\"%'").fetchone() == (0,)
    assert connection.execute("SELECT phrase, model FROM hits WHERE id = 3").fetchone() == ("a|b", 'say "hi"')
    assert connection.execute("SELECT phrase FROM hits WHERE id = 4").fetchone() == ("line1\nline2",)
    assert connection.execute("SELECT model FROM hits WHERE id = 4").fetchone() == (None,)


def test_row_batch_processor_without_quote_keeps_legacy_splitting() -> None:
    import io

    processor = RowBatchProcessor()
    batches = list(processor.process_file(io.StringIO('1|""|x\n'), "|", 3))

    assert batches == [([("1", '""', "x")], 1)]
