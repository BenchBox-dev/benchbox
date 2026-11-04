from __future__ import annotations

import gzip
import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.doris import DorisAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

COLUMNS = ["ws_id", "ws_price", "ws_note", "ws_qty"]
TPC_DIALECT = CsvDialect("|", False, "", False, None)
ROWS = [
    "1|12.50|alpha|3|",
    "2||beta|4|",
    "3|7.25||5|",
    "4|||6|",
    "5|1.00|gamma||",
    "6|2.00|delta|7|",
]


def _legacy_rewrite(line: str, delimiter: str) -> str:
    trailing = line.endswith(delimiter)
    fields = line.split(delimiter)
    if trailing:
        fields.pop()
    joined = delimiter.join(r"\N" if field == "" else field for field in fields)
    return joined + delimiter if trailing else joined


def _legacy_rows(lines: list[str], delimiter: str) -> list[list[str | None]]:
    parsed = []
    for line in lines:
        fields = _legacy_rewrite(line, delimiter).split(delimiter)
        if line.endswith(delimiter):
            fields.pop()
        parsed.append([None if field == r"\N" else field for field in fields])
    return parsed


def _doris_rows(body: bytes, columns_header: str, delimiter: str, ncols: int) -> list[list[str | None]]:
    mappings = dict(re.findall(r"(\w+)=nullif\((tmp_\d+),''\)", columns_header))
    assert list(mappings) == COLUMNS[:ncols]
    parsed = []
    for line in body.decode("utf-8").split("\n"):
        if not line:
            continue
        fields = line.split(delimiter)[:ncols]
        parsed.append([None if field == "" else field for field in fields])
    return parsed


def _ok(rows: int = 1) -> Mock:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"Status": "Success", "NumberLoadedRows": rows}
    return response


def _adapter(**config) -> DorisAdapter:
    try:
        return DorisAdapter(database="test_db", **config)
    except ImportError:
        pytest.skip("pymysql not installed")


def _capture(adapter: DorisAdapter, path: Path, columns: list[str]) -> tuple[bytes, dict[str, str], int]:
    bodies: list[bytes] = []
    seen_headers: list[dict[str, str]] = []

    def _put(url, data, headers, **kwargs):
        bodies.append(data.read() if hasattr(data, "read") else data)
        seen_headers.append(headers)
        return _ok()

    with patch("benchbox.platforms.doris._requests") as mock_requests:
        mock_requests.put.side_effect = _put
        adapter._stream_load_file("web_sales", path, TPC_DIALECT, columns)
        calls = mock_requests.put.call_count
    return b"".join(bodies), seen_headers[0], calls


class TestStreamLoadSendsFileBytes:
    def test_single_load_sends_file_bytes_unchanged(self, tmp_path):
        data_file = tmp_path / "web_sales.dat"
        raw = ("\n".join(ROWS) + "\n").encode("latin-1")
        data_file.write_bytes(raw)

        sent, headers, calls = _capture(_adapter(), data_file, COLUMNS)

        assert calls == 1
        assert sent == raw
        assert headers["columns"].startswith("tmp_0,tmp_1,tmp_2,tmp_3,ws_id=nullif(tmp_0,''),")

    def test_chunked_load_sends_file_bytes_unchanged(self, tmp_path):
        data_file = tmp_path / "web_sales.dat"
        raw = ("\n".join(ROWS * 5) + "\n").encode("latin-1")
        data_file.write_bytes(raw)

        sent, headers, calls = _capture(_adapter(stream_load_chunk_size=40), data_file, COLUMNS)

        assert calls > 1
        assert sent == raw
        assert "columns" in headers

    def test_chunks_end_on_line_boundaries(self, tmp_path):
        data_file = tmp_path / "web_sales.dat"
        data_file.write_bytes(("\n".join(ROWS * 5) + "\n").encode("latin-1"))
        bodies: list[bytes] = []

        with patch("benchbox.platforms.doris._requests") as mock_requests:
            mock_requests.put.side_effect = lambda url, data, headers, **kw: (bodies.append(data), _ok())[1]
            _adapter(stream_load_chunk_size=40)._stream_load_file("web_sales", data_file, TPC_DIALECT, COLUMNS)

        assert all(body.endswith(b"\n") for body in bodies)

    @pytest.mark.parametrize("chunk_size", [10 * 1024 * 1024, 20])
    def test_latin1_data_is_sent_as_utf8(self, tmp_path, chunk_size):
        data_file = tmp_path / "web_sales.dat"
        rows = ["1|12.50|Jos\xe9|3|", "2||Mu\xf1oz|4|", "3|7.25|plain|5|"]
        data_file.write_bytes(("\n".join(rows) + "\n").encode("latin-1"))

        sent, _, _ = _capture(_adapter(stream_load_chunk_size=chunk_size), data_file, COLUMNS)

        assert sent == ("\n".join(rows) + "\n").encode("utf-8")

    def test_compressed_file_is_decompressed_not_rewritten(self, tmp_path):
        data_file = tmp_path / "web_sales.dat.gz"
        raw = ("\n".join(ROWS) + "\n").encode("latin-1")
        data_file.write_bytes(gzip.compress(raw))

        sent, _, _ = _capture(_adapter(), data_file, COLUMNS)

        assert sent == raw

    @pytest.mark.parametrize("chunk_size", [10 * 1024 * 1024, 20])
    def test_crlf_and_blank_lines_are_removed_from_tpc_bodies(self, tmp_path, chunk_size):
        data_file = tmp_path / "web_sales.dat"
        data_file.write_bytes(b"1|12.50|alpha|3|\r\n\r\n2||beta|4|\r\n3|7.25||5|\r\n")

        sent, _, _ = _capture(_adapter(stream_load_chunk_size=chunk_size), data_file, COLUMNS)

        assert b"\r" not in sent
        assert b"\n\n" not in sent
        assert sent.replace(b"\n", b"") == b"1|12.50|alpha|3|2||beta|4|3|7.25||5|"

    def test_non_tpc_compressed_file_is_sent_as_chunk_stream(self, tmp_path):
        data_file = tmp_path / "events.csv.gz"
        raw = ("\n".join(f"{i},name{i}" for i in range(5000)) + "\n").encode("utf-8")
        data_file.write_bytes(gzip.compress(raw))
        sent: list[bytes] = []

        def _put(url, data, headers, **kwargs):
            assert not hasattr(data, "read")
            sent.append(b"".join(data))
            return _ok(5000)

        with patch("benchbox.platforms.doris._requests") as mock_requests:
            mock_requests.put.side_effect = _put
            loaded = _adapter()._stream_load_single("events", data_file, CsvDialect(",", False, None, False, None))

        assert loaded == 5000
        assert b"".join(sent) == raw

    def test_header_line_dropped_once_in_chunked_load(self, tmp_path):
        data_file = tmp_path / "web_sales.dat"
        data_file.write_bytes(("ws_id|ws_price|ws_note|ws_qty\n" + "\n".join(ROWS * 3) + "\n").encode("latin-1"))
        bodies: list[bytes] = []

        with patch("benchbox.platforms.doris._requests") as mock_requests:
            mock_requests.put.side_effect = lambda url, data, headers, **kw: (bodies.append(data), _ok())[1]
            _adapter(stream_load_chunk_size=40)._stream_load_file(
                "web_sales", data_file, CsvDialect("|", True, "", False, None), COLUMNS
            )

        assert b"ws_id" not in b"".join(bodies)
        assert b"".join(bodies).count(b"\n") == len(ROWS) * 3

    def test_null_handling_requires_column_names(self, tmp_path):
        data_file = tmp_path / "web_sales.dat"
        data_file.write_bytes(b"1|2|\n")

        with pytest.raises(ValueError, match="column names"):
            _adapter()._stream_load_file("web_sales", data_file, TPC_DIALECT)

    def test_non_tpc_load_has_no_column_mapping(self, tmp_path):
        data_file = tmp_path / "t.csv"
        data_file.write_bytes(b"1,a\n")

        with patch("benchbox.platforms.doris._requests") as mock_requests:
            mock_requests.put.return_value = _ok()
            _adapter()._stream_load_file("t", data_file, CsvDialect(",", False, None, False, None))

        assert "columns" not in mock_requests.put.call_args.kwargs["headers"]


class TestNullParityWithLegacyRewrite:
    @pytest.mark.parametrize("chunk_size", [10 * 1024 * 1024, 40])
    def test_rows_and_nulls_match_legacy_rewrite(self, tmp_path, chunk_size):
        data_file = tmp_path / "web_sales.dat"
        data_file.write_bytes(("\n".join(ROWS) + "\n").encode("latin-1"))

        sent, headers, _ = _capture(_adapter(stream_load_chunk_size=chunk_size), data_file, COLUMNS)

        new_rows = _doris_rows(sent, headers["columns"], "|", len(COLUMNS))
        assert new_rows == _legacy_rows(ROWS, "|")
        assert len(new_rows) == len(ROWS)
        assert new_rows[1][1] is None
        assert new_rows[2][2] is None
        assert new_rows[4][3] is None

    def test_columns_header_covers_every_table_column(self):
        header = _adapter()._stream_load_headers(format_name="csv", delimiter="|", is_tpc=True, columns=COLUMNS)

        assert header["columns"] == (
            "tmp_0,tmp_1,tmp_2,tmp_3,"
            "ws_id=nullif(tmp_0,''),ws_price=nullif(tmp_1,''),ws_note=nullif(tmp_2,''),ws_qty=nullif(tmp_3,'')"
        )


class TestLoadResolvedDataFile:
    def test_tpc_table_columns_come_from_describe(self, tmp_path):
        data_file = tmp_path / "web_sales.dat"
        data_file.write_bytes(b"1|2|\n")
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [("ws_id", "int"), ("ws_price", "decimal(7,2)")]
        adapter = _adapter()

        with (
            patch("benchbox.platforms.doris.resolve_csv_dialect", return_value=TPC_DIALECT),
            patch.object(adapter, "_stream_load_file", return_value=1) as stream_load,
        ):
            adapter._load_resolved_data_file(Mock(), connection, "web_sales", "web_sales", data_file, Mock())

        cursor.execute.assert_called_once_with("DESCRIBE `web_sales`")
        assert stream_load.call_args.args[3] == ["ws_id", "ws_price"]

    def test_non_tpc_table_skips_describe(self, tmp_path):
        data_file = tmp_path / "t.csv"
        data_file.write_bytes(b"1,a\n")
        connection = Mock()
        adapter = _adapter()

        with (
            patch(
                "benchbox.platforms.doris.resolve_csv_dialect", return_value=CsvDialect(",", False, None, False, None)
            ),
            patch.object(adapter, "_stream_load_file", return_value=1) as stream_load,
        ):
            adapter._load_resolved_data_file(Mock(), connection, "t", "t", data_file, Mock())

        connection.cursor.assert_not_called()
        assert stream_load.call_args.args[3] is None
