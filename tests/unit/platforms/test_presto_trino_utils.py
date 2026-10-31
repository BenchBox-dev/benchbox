from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.presto_trino_utils import (
    build_insert_sql,
    json_cell,
    load_file_batches,
    normalize_existing_files,
    show_tables_lower,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestJsonCell:
    def test_text_column_keeps_value_verbatim(self) -> None:
        assert json_cell("O'Brien", "varchar(25)", "") == "O'Brien"
        assert json_cell("12345", "varchar", "") == "12345"

    def test_empty_text_follows_the_null_marker(self) -> None:
        assert json_cell("", "varchar(10)", "") is None
        assert json_cell("", "varchar(10)", None) == ""
        assert json_cell("NA", "varchar(10)", "NA") is None

    def test_non_text_values_are_stripped_and_empty_is_null(self) -> None:
        assert json_cell(" 42 ", "bigint", "") == "42"
        assert json_cell("", "bigint", "") is None
        assert json_cell("", "date", None) is None
        assert json_cell("null", "double", None) is None

    def test_boolean_spellings_are_normalised(self) -> None:
        assert json_cell("True", "boolean", None) == "true"
        assert json_cell("0", "boolean", None) == "false"

    def test_python_values_from_parquet_become_text(self) -> None:
        assert json_cell(datetime.date(2020, 1, 2), "date", None) == "2020-01-02"
        assert json_cell(datetime.datetime(2020, 1, 2, 3, 4, 5), "timestamp(3)", None) == "2020-01-02 03:04:05"
        assert json_cell(True, "boolean", None) == "true"
        assert json_cell(b"\x00\xff", "varbinary", None) == "00ff"

    def test_structured_columns_keep_native_json(self) -> None:
        assert json_cell([1.5, 2.5], "array(double)", None) == [1.5, 2.5]
        assert json_cell("[1, 2]", "array(integer)", "") == [1, 2]
        assert json_cell("", "array(integer)", "") is None


class TestBuildInsertSql:
    def test_projects_each_column_through_its_target_type(self) -> None:
        sql = build_insert_sql("m.s.t", ["bigint", "varchar(10)", "decimal(15,2)", "varbinary"], "[[]]")
        assert sql == (
            "INSERT INTO m.s.t SELECT CAST(c0 AS bigint), c1, CAST(c2 AS decimal(15,2)), from_hex(c3) "
            "FROM UNNEST(CAST(json_parse('[[]]') AS ARRAY(ROW(c0 varchar, c1 varchar, c2 varchar, c3 varchar)))) "
            "AS u(c0, c1, c2, c3)"
        )

    def test_structured_columns_travel_as_their_own_type(self) -> None:
        sql = build_insert_sql("m.s.t", ["array(double)"], "[]")
        assert "ROW(c0 array(double))" in sql
        assert sql.startswith("INSERT INTO m.s.t SELECT c0 FROM")

    def test_single_quotes_in_the_payload_are_escaped(self) -> None:
        assert "json_parse('[[\"O''Brien\"]]')" in build_insert_sql("m.s.t", ["varchar"], '[["O\'Brien"]]')


class TestNormalizeExistingFiles:
    def test_empty_list_returns_empty(self) -> None:
        assert normalize_existing_files([]) == []

    def test_nonexistent_path_excluded(self) -> None:
        result = normalize_existing_files(["/tmp/does-not-exist-xyz.csv"])
        assert result == []

    def test_existing_file_included(self, tmp_path: Path) -> None:
        f = tmp_path / "data.csv"
        f.write_text("a,b\n1,2\n")
        result = normalize_existing_files([str(f)])
        assert result == [f]

    def test_empty_file_excluded(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.csv"
        f.write_text("")
        result = normalize_existing_files([str(f)])
        assert result == []

    def test_single_path_not_in_list_still_works(self, tmp_path: Path) -> None:
        f = tmp_path / "data.csv"
        f.write_text("row\n")
        result = normalize_existing_files(str(f))
        assert result == [f]

    def test_mixed_existing_and_missing(self, tmp_path: Path) -> None:
        good = tmp_path / "good.csv"
        good.write_text("data\n")
        result = normalize_existing_files([str(good), "/tmp/missing-xyz.csv"])
        assert result == [good]

    def test_returns_path_objects(self, tmp_path: Path) -> None:
        f = tmp_path / "data.csv"
        f.write_text("x\n")
        result = normalize_existing_files([str(f)])
        assert all(isinstance(p, Path) for p in result)


class TestShowTablesLower:
    def test_returns_lowercase_table_names_and_closes_cursor(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = [("ORDERS",), ("LineItem",)]
        connection = MagicMock()
        connection.cursor.return_value = cursor

        assert show_tables_lower(connection) == ["orders", "lineitem"]
        cursor.execute.assert_called_once_with("SHOW TABLES")
        cursor.close.assert_called_once_with()

    def test_returns_empty_list_and_closes_cursor_on_error(self) -> None:
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("boom")
        connection = MagicMock()
        connection.cursor.return_value = cursor

        assert show_tables_lower(connection) == []
        cursor.close.assert_called_once_with()


class TestLoadFileBatches:
    @staticmethod
    def _statements(cursor: MagicMock) -> list[str]:
        return [call.args[0] for call in cursor.execute.call_args_list]

    @staticmethod
    def _payload(sql: str) -> list[list[object]]:
        literal = sql.split("json_parse('", 1)[1].split("') AS ARRAY", 1)[0].replace("''", "'")
        return json.loads(literal)

    def test_tpc_file_rows_are_sent_as_one_json_payload(self, tmp_path: Path) -> None:
        f = tmp_path / "data.tbl"
        f.write_text("1|Alice|1992-01-01|\n2||1993-02-02|\n")
        cursor = MagicMock()
        rows = load_file_batches(cursor, f, "s.t", column_types=["bigint", "varchar(10)", "date"])
        assert rows == 2
        (sql,) = self._statements(cursor)
        assert sql.startswith("INSERT INTO s.t SELECT CAST(c0 AS bigint), c1, CAST(c2 AS date) FROM UNNEST(")
        assert self._payload(sql) == [["1", "Alice", "1992-01-01"], ["2", None, "1993-02-02"]]

    def test_quoted_csv_dialect_keeps_embedded_delimiters_and_skips_header(self, tmp_path: Path) -> None:
        f = tmp_path / "data.csv"
        f.write_text('id,name\n1,"Smith, J"\n')
        dialect = CsvDialect(delimiter=",", has_header=True, null_marker=None, normalize_booleans=False, quote='"')
        cursor = MagicMock()
        rows = load_file_batches(cursor, f, "s.t", dialect=dialect, column_types=["integer", "varchar"])
        assert rows == 1
        assert self._payload(self._statements(cursor)[0]) == [["1", "Smith, J"]]

    def test_declared_encoding_is_used_to_read_text(self, tmp_path: Path) -> None:
        f = tmp_path / "data.csv"
        f.write_bytes("1,r\xe9sum\xe9\n".encode("latin-1"))
        dialect = CsvDialect(
            delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote=None, encoding="latin-1"
        )
        cursor = MagicMock()
        load_file_batches(cursor, f, "s.t", dialect=dialect, column_types=["integer", "varchar"])
        assert self._payload(self._statements(cursor)[0]) == [["1", "r\u00e9sum\u00e9"]]

    def test_parquet_is_read_with_pyarrow_not_as_text(self, tmp_path: Path) -> None:
        f = tmp_path / "data.parquet"
        pq.write_table(
            pa.table(
                {
                    "id": pa.array([1, 2], pa.int32()),
                    "label": pa.array(["a", None]),
                    "day": pa.array([datetime.date(2020, 1, 2), None]),
                }
            ),
            f,
        )
        cursor = MagicMock()
        rows = load_file_batches(cursor, f, "s.t", column_types=["integer", "varchar", "date"])
        assert rows == 2
        assert self._payload(self._statements(cursor)[0]) == [["1", "a", "2020-01-02"], ["2", None, None]]

    def test_parquet_lists_are_loaded_as_json_arrays(self, tmp_path: Path) -> None:
        f = tmp_path / "vectors.parquet"
        pq.write_table(pa.table({"id": [1], "embedding": [[0.5, 1.5]]}), f)
        cursor = MagicMock()
        load_file_batches(cursor, f, "s.t", column_types=["bigint", "array(double)"])
        (sql,) = self._statements(cursor)
        assert "ROW(c0 varchar, c1 array(double))" in sql
        assert self._payload(sql) == [["1", [0.5, 1.5]]]

    def test_statements_stay_under_query_text_limit(self, tmp_path: Path) -> None:
        f = tmp_path / "wide.tbl"
        payload = "x" * 20000
        f.write_text("".join(f"{i}|{payload}|\n" for i in range(200)))
        cursor = MagicMock()
        rows = load_file_batches(cursor, f, "s.t", column_types=["bigint", "varchar"])
        statements = self._statements(cursor)
        assert rows == 200
        assert len(statements) > 1
        assert max(len(sql) for sql in statements) <= 500_000
        assert sum(len(self._payload(sql)) for sql in statements) == 200

    def test_every_insert_result_is_drained_so_the_server_can_release_the_query(self, tmp_path: Path) -> None:
        f = tmp_path / "wide.tbl"
        payload = "x" * 20000
        f.write_text("".join(f"{i}|{payload}|\n" for i in range(100)))
        cursor = MagicMock()
        load_file_batches(cursor, f, "s.t", column_types=["bigint", "varchar"])
        assert cursor.execute.call_count > 1
        assert cursor.fetchall.call_count == cursor.execute.call_count

    def test_fetches_column_types_from_describe_when_not_supplied(self, tmp_path: Path) -> None:
        f = tmp_path / "data.tbl"
        f.write_text("1|a|\n")
        cursor = MagicMock()
        cursor.fetchall.return_value = [("id", "bigint", "", ""), ("name", "varchar", "", "")]
        load_file_batches(cursor, f, "s.t")
        statements = self._statements(cursor)
        assert statements[0] == "DESCRIBE s.t"
        assert self._payload(statements[1]) == [["1", "a"]]
