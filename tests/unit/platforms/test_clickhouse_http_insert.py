from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.clickhouse.client import ClickHouseCloudClient, ClickHouseServerConnection
from benchbox.platforms.clickhouse.http_insert import HttpEndpoint, http_insert

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_ENDPOINT = HttpEndpoint(host="ch.example", port=8123, database="bench", username="u", password="p")


def _response(status: int = 200, summary: str | None = '{"written_rows":"7"}', text: str = "") -> SimpleNamespace:
    headers = {} if summary is None else {"X-ClickHouse-Summary": summary}
    return SimpleNamespace(status_code=status, headers=headers, text=text)


def test_http_insert_posts_the_chunk_iterator_and_returns_written_rows():
    chunks = iter([b"1|a\n", b"2|b\n"])
    with patch("benchbox.platforms.clickhouse.http_insert._post_request", return_value=_response()) as post:
        written = http_insert(_ENDPOINT, "t", "CSV", chunks, {"max_insert_block_size": 5, "format_csv_delimiter": "|"})

    assert written == 7
    args, kwargs = post.call_args
    assert args == ("http://ch.example:8123/",)
    assert kwargs["data"] is chunks
    assert kwargs["auth"] == ("u", "p")
    assert kwargs["params"] == {
        "query": "INSERT INTO t FORMAT CSV",
        "database": "bench",
        "max_insert_block_size": "5",
        "format_csv_delimiter": "|",
    }


def test_http_insert_names_the_file_columns_so_unknown_names_fail():
    with patch("benchbox.platforms.clickhouse.http_insert._post_request", return_value=_response()) as post:
        http_insert(_ENDPOINT, "t", "Parquet", iter([b"x"]), {}, ["id", "name"])

    assert post.call_args.kwargs["params"]["query"] == "INSERT INTO t (id, name) FORMAT Parquet"


def test_http_insert_uses_https_when_secure():
    secure = HttpEndpoint("ch.example", 8443, "bench", "u", "p", secure=True)
    with patch("benchbox.platforms.clickhouse.http_insert._post_request", return_value=_response()) as post:
        http_insert(secure, "t", "Parquet", iter([b"x"]), {})

    assert post.call_args.args == ("https://ch.example:8443/",)


def test_http_insert_raises_on_server_error():
    with patch(
        "benchbox.platforms.clickhouse.http_insert._post_request",
        return_value=_response(status=500, text="Code: 60. Table missing"),
    ):
        with pytest.raises(RuntimeError, match="failed with 500: Code: 60"):
            http_insert(_ENDPOINT, "t", "CSV", iter([b"x"]), {})


def test_http_insert_requires_the_summary_header():
    with patch("benchbox.platforms.clickhouse.http_insert._post_request", return_value=_response(summary=None)):
        with pytest.raises(RuntimeError, match="X-ClickHouse-Summary"):
            http_insert(_ENDPOINT, "t", "CSV", iter([b"x"]), {})


def test_server_connection_insert_stream_targets_the_selected_database():
    connection = ClickHouseServerConnection(Mock(), _ENDPOINT)
    connection._database = "other"
    with patch("benchbox.platforms.clickhouse.client.http_insert", return_value=3) as insert:
        assert connection.insert_stream("t", "CSV", iter([b"x"]), {"a": 1}) == 3

    assert insert.call_args.args[0].database == "other"


def test_server_connection_without_http_endpoint_refuses_stream_insert():
    with pytest.raises(RuntimeError, match="no HTTP endpoint"):
        ClickHouseServerConnection(Mock()).insert_stream("t", "CSV", iter([]), {})


def test_cloud_client_streams_through_raw_insert():
    client = ClickHouseCloudClient.__new__(ClickHouseCloudClient)
    client._client = Mock()
    client._client.raw_insert.return_value = SimpleNamespace(written_rows=9)
    chunks = iter([b"x"])

    assert client.insert_stream("t", "CSV", chunks, {"a": 1}) == 9
    client._client.raw_insert.assert_called_once_with(
        "t", column_names=None, insert_block=chunks, settings={"a": 1}, fmt="CSV"
    )


def test_cloud_client_names_the_file_columns():
    client = ClickHouseCloudClient.__new__(ClickHouseCloudClient)
    client._client = Mock()
    client._client.raw_insert.return_value = SimpleNamespace(written_rows=2)

    client.insert_stream("t", "Parquet", iter([b"x"]), {}, ["id", "name"])

    assert client._client.raw_insert.call_args.kwargs["column_names"] == ["id", "name"]
