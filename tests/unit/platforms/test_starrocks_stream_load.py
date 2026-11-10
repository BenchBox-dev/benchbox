from __future__ import annotations

import gzip
import io
import json
import logging
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from benchbox.platforms.starrocks import _dependencies
from benchbox.platforms.starrocks.stream_load import (
    StarRocksParquetHandler,
    StarRocksStreamLoadHandler,
    StreamLoadTarget,
    file_chunks,
    null_mapped_columns_header,
    parquet_csv_chunks,
    stream_load,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TARGET = StreamLoadTarget("sr-host", 8040, "bench", "root", "pw")
LOGGER = logging.getLogger(__name__)


class _FakeConnectionError(Exception):
    pass


class _FakeRequests:
    ConnectionError = _FakeConnectionError

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def put(self, url, headers, data, timeout):
        body = b"".join(data)
        self.calls.append({"url": url, "headers": headers, "body": body})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _ok(rows):
    return SimpleNamespace(
        status_code=200, text=json.dumps({"Status": "Success", "NumberLoadedRows": rows}), headers={}
    )


@pytest.fixture
def fake_requests(monkeypatch):
    def install(*responses):
        fake = _FakeRequests(responses)
        monkeypatch.setattr(_dependencies, "requests", fake)
        monkeypatch.setattr("benchbox.platforms.starrocks.stream_load.time.sleep", lambda _s: None)
        return fake

    return install


class TestNullMappedColumnsHeader:
    def test_maps_empty_only_for_pipe_dialects(self):
        header = null_mapped_columns_header(["a", "b"], text_tokens=False)
        assert header == "tmp_0,tmp_1,`a`=if(lower(tmp_0) in (''),NULL,tmp_0),`b`=if(lower(tmp_1) in (''),NULL,tmp_1)"

    def test_maps_nan_and_inf_tokens_for_comma_dialects(self):
        header = null_mapped_columns_header(["a"], text_tokens=True)
        assert "in ('','nan','inf','-inf','+inf')" in header


class TestFileChunks:
    def test_plain_file_is_streamed_as_bytes_unchanged(self, tmp_path):
        path = tmp_path / "t.tbl"
        payload = b"1|a|\n2|b|\n"
        path.write_bytes(payload)
        assert b"".join(file_chunks(path)) == payload

    def test_gzip_file_is_decompressed_without_parsing_rows(self, tmp_path):
        path = tmp_path / "t.tbl.gz"
        path.write_bytes(gzip.compress(b"1|a|\n"))
        assert b"".join(file_chunks(path)) == b"1|a|\n"


class TestParquetCsvChunks:
    def test_nulls_become_the_stream_load_marker_and_text_is_quoted_when_needed(self, tmp_path):
        path = tmp_path / "t.parquet"
        pq.write_table(
            pa.table({"id": pa.array([1, 2, None], pa.int32()), "name": ["a,b", None, 'say "hi"']}),
            path,
        )
        text = b"".join(parquet_csv_chunks(path)).decode()
        assert text.splitlines() == ['"1","a,b"', '"2","\\N"', '"\\N","say ""hi"""']

    def test_batches_split_rows(self, tmp_path):
        path = tmp_path / "t.parquet"
        pq.write_table(pa.table({"id": list(range(10))}), path)
        assert len(list(parquet_csv_chunks(path, batch_rows=4))) == 3


class TestStreamLoad:
    def test_sends_credentials_headers_and_returns_loaded_rows(self, fake_requests):
        fake = fake_requests(_ok(7))
        rows = stream_load(TARGET, "t", {"format": "CSV"}, lambda: iter([b"a", b"b"]), LOGGER)
        call = fake.calls[0]
        assert rows == 7
        assert call["url"] == "http://sr-host:8040/api/bench/t/_stream_load"
        assert call["body"] == b"ab"
        assert call["headers"]["Authorization"].startswith("Basic ")
        assert call["headers"]["format"] == "CSV"
        assert call["headers"]["max_filter_ratio"] == "0"

    def test_retries_connection_errors_with_a_fresh_body(self, fake_requests):
        fake = fake_requests(_FakeConnectionError("down"), _ok(3))
        assert stream_load(TARGET, "t", {}, lambda: iter([b"x"]), LOGGER) == 3
        assert [call["body"] for call in fake.calls] == [b"x", b"x"]

    def test_failed_status_raises(self, fake_requests):
        fake_requests(SimpleNamespace(status_code=200, text=json.dumps({"Status": "Fail", "Message": "bad"})))
        with pytest.raises(RuntimeError, match="bad"):
            stream_load(TARGET, "t", {}, lambda: iter([b"x"]), LOGGER)

    def test_http_error_raises_after_server_error_retries(self, fake_requests):
        fake_requests(*[SimpleNamespace(status_code=503, text="busy")] * 3)
        with pytest.raises(RuntimeError, match="HTTP 503"):
            stream_load(TARGET, "t", {}, lambda: iter([b"x"]), LOGGER)


class _Cursor:
    def __init__(self, columns):
        self.columns = columns

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return [(name,) for name in self.columns]

    def close(self):
        pass


class _Connection:
    def __init__(self, columns):
        self.columns = columns

    def cursor(self):
        return _Cursor(self.columns)


class TestHandlers:
    def test_pipe_table_streams_file_bytes_with_no_column_mapping(self, fake_requests, tmp_path):
        path = tmp_path / "t.tbl"
        path.write_bytes(b"1|x|\n")
        fake = fake_requests(_ok(1))
        handler = StarRocksStreamLoadHandler("|", TARGET, null_empty_strings=False)
        assert handler.load_table("t", path, _Connection(["a", "b"]), None, LOGGER) == 1
        headers = fake.calls[0]["headers"]
        assert fake.calls[0]["body"] == b"1|x|\n"
        assert headers["column_separator"] == "|"
        assert "columns" not in headers
        assert "skip_header" not in headers

    def test_comma_csv_maps_nulls_server_side_and_skips_the_header(self, fake_requests, tmp_path):
        path = tmp_path / "t.csv"
        path.write_bytes(b"a,b\n1,\n")
        fake = fake_requests(_ok(1))
        handler = StarRocksStreamLoadHandler(",", TARGET, null_empty_strings=True, has_header=True)
        handler.load_table("t", path, _Connection(["a", "b"]), None, LOGGER)
        headers = fake.calls[0]["headers"]
        assert fake.calls[0]["body"] == b"a,b\n1,\n"
        assert headers["skip_header"] == "1"
        assert headers["enclose"] == '"'
        assert headers["columns"].startswith("tmp_0,tmp_1,`a`=if(lower(tmp_0)")

    def test_parquet_loads_named_columns_without_python_rows(self, fake_requests, tmp_path):
        path = tmp_path / "t.parquet"
        pq.write_table(pa.table({"id": [1, 2], "name": ["a", "b"]}), path)
        fake = fake_requests(_ok(2))
        assert StarRocksParquetHandler(TARGET).load_table("t", path, _Connection([]), None, LOGGER) == 2
        headers = fake.calls[0]["headers"]
        assert headers["columns"] == "`id`,`name`"
        assert headers["format"] == "CSV"
        assert io.BytesIO(fake.calls[0]["body"]).read().count(b"\n") == 2

    def test_empty_parquet_sends_nothing(self, fake_requests, tmp_path):
        path = tmp_path / "t.parquet"
        pq.write_table(pa.table({"id": pa.array([], pa.int64())}), path)
        fake = fake_requests()
        assert StarRocksParquetHandler(TARGET).load_table("t", path, _Connection([]), None, LOGGER) == 0
        assert fake.calls == []
