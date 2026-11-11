from __future__ import annotations

import csv
import hashlib
import io
import socket
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import benchbox.platforms.questdb as questdb_module
from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.questdb import QuestDBAdapter, _ilp_escape_measurement, _ilp_escape_tag_key

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def _no_ilp_timestamp_columns(monkeypatch):
    monkeypatch.setattr(QuestDBAdapter, "_ilp_timestamp_columns", lambda self, table_name, designated: frozenset())


@pytest.fixture()
def adapter(monkeypatch):
    monkeypatch.setattr(questdb_module, "psycopg", Mock())
    return QuestDBAdapter(ilp_host="localhost", ilp_port=9009)


def _reference_escape_field(col_name, value):
    if not value:
        return None
    key = _ilp_escape_tag_key(col_name)
    try:
        return f"{key}={int(value)}i"
    except ValueError:
        pass
    try:
        return f"{key}={float(value)}"
    except ValueError:
        pass
    return f'{key}="' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _reference_epoch_ns(text):
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return str(int(datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc).timestamp()) * 10**9)
        except ValueError:
            continue
    return None


def _reference_lines(path, measurement, columns, delimiter, ts_col):
    lines = []
    with open(path, encoding="utf-8", newline="") as stream:
        for row in csv.reader(stream, delimiter=delimiter):
            if len(row) == len(columns) + 1 and not row[-1]:
                row = row[:-1]
            if len(row) != len(columns):
                continue
            fields, ts = [], None
            for name, value in zip(columns, row):
                value = value.strip()
                if not value:
                    continue
                if ts_col and name == ts_col:
                    ts = _reference_epoch_ns(value)
                    continue
                fields.append(_reference_escape_field(name, value))
            if fields:
                line = f"{_ilp_escape_measurement(measurement)} {','.join(fields)}"
                lines.append(line + (f" {ts}" if ts else ""))
    return lines


def _split_unquoted(text, sep):
    parts, current, quoted, i = [], [], False, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            current.append(text[i : i + 2])
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        if ch == sep and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts


def _parse_line(line):
    measurement, fields, *ts = _split_unquoted(line, " ")
    parsed = []
    for field in _split_unquoted(fields, ","):
        key, _, raw = field.partition("=")
        if raw.startswith('"'):
            value = ("s", raw[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        elif raw.endswith("i"):
            value = ("i", int(raw[:-1]))
        else:
            value = ("f", float(raw))
        parsed.append((key, value))
    return measurement, tuple(parsed), tuple(ts)


def _column_checksums(lines):
    sums: dict[str, str] = {}
    for key in sorted({k for line in lines for k, _ in _parse_line(line)[1]} | {"__ts__"}):
        digest = hashlib.sha256()
        for line in lines:
            _, fields, ts = _parse_line(line)
            if key == "__ts__":
                digest.update(repr(ts).encode())
            else:
                digest.update(repr(dict(fields).get(key)).encode())
        sums[key] = digest.hexdigest()
    return sums


class _IlpSink:
    def __init__(self):
        self.payloads: list[bytes] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        connection, _ = self._listener.accept()
        with connection:
            while chunk := connection.recv(65536):
                self.payloads.append(chunk)

    def close(self):
        self._thread.join(timeout=10)
        self._listener.close()


def _load_to_sink(adapter, path, table, columns, dialect):
    sink = _IlpSink()
    adapter.ilp_host = "127.0.0.1"
    adapter.ilp_port = sink.port
    try:
        with patch.object(adapter, "_get_table_columns", return_value=columns):
            rows = adapter._load_table_via_ilp(table, path, dialect)
    finally:
        sink.close()
    return rows, b"".join(sink.payloads).decode()


def _send_lines(adapter, path, table, columns, dialect, monkeypatch):
    rows, text = _load_to_sink(adapter, path, table, columns, dialect)
    return rows, text.splitlines()


def _assert_parity(adapter, path, table, columns, delimiter, ts_col, monkeypatch):
    expected = _reference_lines(path, table, columns, delimiter, ts_col)
    dialect = CsvDialect(delimiter, False, None, False, None)
    rows, actual = _send_lines(adapter, path, table, columns, dialect, monkeypatch)
    assert rows == len(actual) == len(expected)
    assert sorted(_parse_line(line) for line in actual) == sorted(_parse_line(line) for line in expected)
    assert _column_checksums(sorted(actual, key=_parse_line)) == _column_checksums(sorted(expected, key=_parse_line))
    return actual


class TestIlpArrowParity:
    def test_mixed_types_nulls_dates_and_escaping(self, adapter, tmp_path, monkeypatch):
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerows(
            [
                [1, "plain", "12.50", "1994-01-02", "true", " padded "],
                [-2, 'has "quotes", commas', "0.1", "1994-01-02 10:20:30", "false", "back\\slash"],
                [3, "line break", "1e5", "1994-01-02T01:02:03", "", "a b=c"],
                [4, "", "", "", "true", ""],
                ["", "", "", "", "", ""],
                [5, "x", "5.0", "2000-02-29", "false", "y"],
                [7, "wrong width"],
                [9_000_000_000, "big", "-3.25", "1970-01-01", "true", "z"],
            ]
        )
        path = tmp_path / "t.csv"
        path.write_text(buffer.getvalue(), encoding="utf-8")
        columns = ["id", "name", "price", "o_orderdate", "flag", "note"]

        actual = _assert_parity(adapter, path, "orders", columns, ",", "o_orderdate", monkeypatch)

        assert len(actual) == 6

    def test_pipe_delimited_with_trailing_delimiter_and_mixed_widths(self, adapter, tmp_path, monkeypatch):
        path = tmp_path / "store_sales.dat"
        path.write_text("1|2|\n3|4|5\n7|8|9|\n|10|\n||\n", encoding="utf-8")

        actual = _assert_parity(adapter, path, "store_sales", ["a", "b", "c"], "|", None, monkeypatch)

        assert len(actual) == 4

    def test_tbl_lineitem_rows_without_trailing_delimiter(self, adapter, tmp_path, monkeypatch):
        path = tmp_path / "lineitem.tbl"
        path.write_text(
            "1|155190|7706|1|17|21168.23|0.04|0.02|N|O|1996-03-13|comment, one|\n"
            '2|67310|7311|2|36|45983.16|0.09|0.06|R|F|1996-04-12|"q" two|\n',
            encoding="utf-8",
        )
        columns = [
            "l_orderkey",
            "l_partkey",
            "l_suppkey",
            "l_linenumber",
            "l_quantity",
            "l_extendedprice",
            "l_discount",
            "l_tax",
            "l_returnflag",
            "l_linestatus",
            "l_shipdate",
            "l_comment",
        ]
        stripped = tmp_path / "expected.csv"
        stripped.write_text(path.read_text(encoding="utf-8").replace("|\n", "\n"), encoding="utf-8")
        expected = _reference_lines(stripped, "lineitem", columns, "|", "l_shipdate")

        rows, actual = _send_lines(
            adapter, path, "lineitem", columns, CsvDialect("|", False, None, False, None), monkeypatch
        )

        assert rows == len(expected) == 2
        assert sorted(_parse_line(line) for line in actual) == sorted(_parse_line(line) for line in expected)

    def test_batches_send_at_most_1000_rows_per_write(self, adapter, tmp_path, monkeypatch):
        path = tmp_path / "t.csv"
        path.write_text("".join(f"{i},v{i}\n" for i in range(2500)), encoding="utf-8")
        rows, text = _load_to_sink(adapter, path, "t", ["id", "name"], CsvDialect(",", False, None, False, None))

        assert rows == 2500
        assert text.count("\n") == 2500

    def test_each_write_is_capped_at_1000_rows(self):
        assert questdb_module._ILP_SEND_ROWS == 1000


class TestParquetCsvParity:
    def test_arrow_csv_matches_pandas_to_csv(self, adapter, tmp_path):
        table = pa.table(
            {
                "i": pa.array([1, None, 3, -4], type=pa.int64()),
                "f": pa.array([1.5, 2.0, None, -0.25], type=pa.float64()),
                "d": pa.array([Decimal("12.50"), None, Decimal("0.01"), Decimal("-7.00")], type=pa.decimal128(10, 2)),
                "b": pa.array([True, False, None, True]),
                "dt": pa.array([date(1994, 1, 2), None, date(2000, 2, 29), date(1970, 1, 1)]),
                "ts": pa.array(
                    [datetime(2024, 1, 2, 3, 4, 5), None, datetime(2024, 12, 31, 23, 59, 59), datetime(1999, 1, 1)],
                    type=pa.timestamp("us"),
                ),
                "s": pa.array(["a,b", 'q"uote', "line\nbreak", None]),
            }
        )
        path = tmp_path / "t.parquet"
        pq.write_table(table, path)

        old_text = pq.read_table(path).to_pandas().to_csv(index=False)
        captured: list[bytes] = []

        def _post(url, **kwargs):
            captured.append(kwargs["files"]["data"][1].read())
            return Mock(status_code=200, text="| Rows imported | 4 |")

        with (
            patch.object(adapter, "_import_schema", return_value=None),
            patch.object(adapter, "_unpatterned_timestamp_columns", return_value=[]),
            patch.object(questdb_module, "_http_post", side_effect=_post),
        ):
            rows = adapter._load_parquet_via_chunked_csv("t", path)

        old_rows = list(csv.reader(io.StringIO(old_text)))
        new_rows = list(csv.reader(io.StringIO(captured[0].decode())))
        assert rows == 4
        assert old_rows[0] == new_rows[0] == list(table.column_names)
        assert len(old_rows) == len(new_rows) == 5

        def normalize(name, value):
            if value == "":
                return None
            if name in ("i",):
                return int(float(value))
            if name == "f":
                return float(value)
            if name == "d":
                return Decimal(value)
            if name == "b":
                return value.lower() == "true"
            if name == "ts":
                return datetime.fromisoformat(value)
            return value

        names = old_rows[0]
        for column, name in enumerate(names):
            old = [normalize(name, row[column]) for row in old_rows[1:]]
            new = [normalize(name, row[column]) for row in new_rows[1:]]
            assert old == new, name
            assert hashlib.sha256(repr(old).encode()).digest() == hashlib.sha256(repr(new).encode()).digest()


