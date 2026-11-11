from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.singlestore import SingleStoreAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GUARD = "`price` = IF(@price IS NULL OR @price REGEXP '^-?[0-9]+(\\\\.[0-9]+)?$', @price, 1e30)"
COLUMNS = [("id", "int(11)"), ("price", "decimal(15,2)"), ("note", "varchar(10)")]


class _Cursor:
    def __init__(self, columns: list[tuple[str, str]]) -> None:
        self.statements: list[str] = []
        self._columns = columns
        self._pending: list[tuple] = []
        self._counts = [0, 1]

    def execute(self, sql: str) -> None:
        self.statements.append(sql)
        if "information_schema.COLUMNS" in sql:
            self._pending = list(self._columns)
        elif sql.startswith("SELECT COUNT(*)"):
            self._pending = [(self._counts.pop(0),)]
        else:
            self._pending = []

    def fetchone(self) -> tuple | None:
        return self._pending.pop(0) if self._pending else None

    def fetchall(self) -> list[tuple]:
        rows, self._pending = self._pending, []
        return rows

    def close(self) -> None:
        pass

    @property
    def load_sql(self) -> str:
        return next(s for s in self.statements if s.startswith("LOAD DATA"))


class _Connection:
    def __init__(self, columns: list[tuple[str, str]]) -> None:
        self.cursor_obj = _Cursor(columns)

    def cursor(self) -> _Cursor:
        return self.cursor_obj


def _dialect(delimiter: str, *, header: bool = False, null_marker: str | None = None) -> CsvDialect:
    return CsvDialect(
        delimiter=delimiter, has_header=header, null_marker=null_marker, normalize_booleans=False, quote=None
    )


def _infile(tmp_path: Path, name: str, dialect: CsvDialect, columns=COLUMNS, strip: bool = False) -> _Cursor:
    path = tmp_path / name
    path.write_text("1|1.50|a|\n", encoding="utf-8")
    connection = _Connection(columns)
    SingleStoreAdapter.__new__(SingleStoreAdapter)._load_data_infile(connection, "t", path, dialect, strip)
    return connection.cursor_obj


def test_bulk_path_reads_the_real_columns_of_the_loaded_table(tmp_path):
    cursor = _infile(tmp_path, "x.tbl", _dialect("|"), strip=True)

    columns_sql = next(s for s in cursor.statements if "information_schema.COLUMNS" in s)
    assert "TABLE_SCHEMA = DATABASE()" in columns_sql
    assert "TABLE_NAME = 't'" in columns_sql
    assert "ORDER BY ORDINAL_POSITION" in columns_sql


def test_tbl_load_guards_decimal_columns_through_a_variable(tmp_path):
    sql = _infile(tmp_path, "x.tbl", _dialect("|"), strip=True).load_sql

    assert sql.endswith(f"LINES TERMINATED BY '\\n' (`id`, @price, `note`) SET {GUARD}")


def test_null_marker_and_header_clauses_stay_before_the_column_list(tmp_path):
    sql = _infile(tmp_path, "x.csv", _dialect(",", header=True, null_marker="")).load_sql

    assert (
        "FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' NULL DEFINED BY '' "
        f"LINES TERMINATED BY '\\n' IGNORE 1 LINES (`id`, @price, `note`) SET {GUARD}"
    ) in sql


def test_every_decimal_column_gets_its_own_guard(tmp_path):
    columns = [("a", "decimal(7,2)"), ("b", "bigint(20)"), ("c", "DECIMAL(15,2)")]

    sql = _infile(tmp_path, "x.dat", _dialect("|"), columns=columns).load_sql

    assert "(@a, `b`, @c) SET " in sql
    assert "`a` = IF(@a IS NULL OR @a REGEXP" in sql
    assert "`c` = IF(@c IS NULL OR @c REGEXP" in sql


def test_tables_without_decimal_columns_load_without_a_column_list(tmp_path):
    columns = [("id", "int(11)"), ("name", "varchar(10)"), ("when", "date")]

    sql = _infile(tmp_path, "x.dat", _dialect("|"), columns=columns).load_sql

    assert " SET " not in sql
    assert "(`" not in sql and "(@" not in sql


def test_parquet_text_load_guards_decimal_columns_and_passes_null(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = tmp_path / "x.parquet"
    pq.write_table(pa.table({"id": [1], "price": ["1.50"], "note": ["a"]}), path)
    connection = _Connection(COLUMNS)

    SingleStoreAdapter.__new__(SingleStoreAdapter)._load_parquet_infile(connection, "t", path)

    sql = connection.cursor_obj.load_sql
    assert "FIELDS TERMINATED BY '\\t' ESCAPED BY '\\\\' LINES TERMINATED BY '\\n'" in sql
    assert sql.endswith(f"(`id`, @price, `note`) SET {GUARD}")


def test_refresh_ingest_uses_the_same_guard_expression_as_the_bulk_paths(tmp_path):
    from unittest.mock import Mock

    cursor = Mock()
    cursor.rowcount = 1
    raw = Mock()
    raw.cursor.return_value = cursor
    refresh_columns = [("id", "INTEGER"), ("price", "DECIMAL(15,2)"), ("note", "VARCHAR(10)")]

    SingleStoreAdapter.__new__(SingleStoreAdapter).ingest_refresh_file(
        Mock(connection=raw), "t", refresh_columns, tmp_path / "x.dat", null_marker=""
    )

    assert cursor.execute.call_args.args[0].endswith(f"(`id`, @price, `note`) SET {GUARD}")


