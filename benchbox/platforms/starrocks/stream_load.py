from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchbox.platforms.base.data_loading import (
    FileFormatHandler,
    FileFormatRegistry,
    _open_binary_stream,
    validate_sql_identifier,
)
from benchbox.platforms.starrocks import _dependencies

_CHUNK_BYTES = 8 * 1024 * 1024
_NULL_MARKER = "\\N"
_NULL_TEXT_TOKENS = ("nan", "inf", "-inf", "+inf")
_MAX_ATTEMPTS = 3
_SUCCESS_STATUSES = frozenset(("Success", "Publish Timeout"))


def treats_empty_as_null(delimiter: str, null_marker: str | None = None) -> bool:
    return delimiter == "," or null_marker == ""


def null_mapped_columns_header(columns: list[str], *, text_tokens: bool) -> str:
    tokens = ("", *_NULL_TEXT_TOKENS) if text_tokens else ("",)
    token_list = ",".join(f"'{token}'" for token in tokens)
    sources = [f"tmp_{index}" for index in range(len(columns))]
    mappings = [
        f"`{column}`=if(lower({source}) in ({token_list}),NULL,{source})"
        for column, source in zip(columns, sources, strict=True)
    ]
    return ",".join(sources + mappings)


def file_chunks(file_path: Path) -> Iterator[bytes]:
    handler = FileFormatRegistry.get_compression_handler(file_path)
    with _open_binary_stream(file_path, handler) as source:
        while chunk := source.read(_CHUNK_BYTES):
            yield chunk


def parquet_csv_chunks(file_path: Path, batch_rows: int = 65_536) -> Iterator[bytes]:
    import pyarrow as pa
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq

    options = pacsv.WriteOptions(include_header=False, quoting_style="needed")
    for batch in pq.ParquetFile(file_path).iter_batches(batch_size=batch_rows):
        sink = pa.BufferOutputStream()
        pacsv.write_csv(pa.Table.from_batches([batch]), sink, options)
        yield sink.getvalue().to_pybytes()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _authorization(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


@dataclass(frozen=True)
class StreamLoadTarget:
    host: str
    http_port: int
    database: str
    username: str
    password: str


def stream_load(
    target: StreamLoadTarget,
    table_name: str,
    headers: dict[str, str],
    chunks: Callable[[], Iterator[bytes]],
    logger: Any,
) -> int:
    requests = _dependencies.requests
    if requests is None:
        raise ImportError("StarRocks Stream Load requires the 'requests' package")

    url = f"http://{target.host}:{target.http_port}/api/{target.database}/{table_name}/_stream_load"
    request_headers = {
        "Authorization": _authorization(target.username, target.password),
        "Expect": "100-continue",
        "max_filter_ratio": "0",
        "strict_mode": "false",
        **headers,
    }

    response: Any = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = requests.put(url, headers=request_headers, data=chunks(), timeout=7200)
        except requests.ConnectionError as exc:
            if attempt < _MAX_ATTEMPTS:
                logger.warning(
                    "STREAM LOAD connection error for %s (attempt %d/%d): %s; retrying",
                    table_name,
                    attempt,
                    _MAX_ATTEMPTS,
                    exc,
                )
                _sleep(5 * attempt)
                continue
            raise RuntimeError(f"STREAM LOAD connection error for {table_name}: {exc}") from exc
        if response.status_code in (200, 307):
            if attempt < _MAX_ATTEMPTS and _backend_unavailable(response):
                logger.warning(
                    "STREAM LOAD BE unavailable for %s (attempt %d/%d); retrying", table_name, attempt, _MAX_ATTEMPTS
                )
                _sleep(10 * attempt)
                continue
            break
        if 500 <= response.status_code < 600 and attempt < _MAX_ATTEMPTS:
            logger.warning(
                "STREAM LOAD HTTP %s for %s (attempt %d/%d); retrying",
                response.status_code,
                table_name,
                attempt,
                _MAX_ATTEMPTS,
            )
            _sleep(5 * attempt)
            continue
        raise RuntimeError(f"STREAM LOAD HTTP {response.status_code} for {table_name}: {response.text[:500]}")

    result = json.loads(response.text)
    if result.get("Status", "") not in _SUCCESS_STATUSES:
        raise RuntimeError(f"STREAM LOAD failed for {table_name}: {result.get('Message', result)}")
    return int(result.get("NumberLoadedRows", 0))


def _backend_unavailable(response: Any) -> bool:
    try:
        return ":0" in json.loads(response.text).get("Message", "")
    except ValueError:
        return False


def _table_columns(connection: Any, table_name: str) -> list[str]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s ORDER BY ordinal_position",
            (table_name,),
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()


class StarRocksStreamLoadHandler(FileFormatHandler):
    def __init__(
        self,
        delimiter: str,
        target: StreamLoadTarget,
        *,
        null_empty_strings: bool = True,
        has_header: bool = False,
    ) -> None:
        self.delimiter = delimiter
        self.target = target
        self.null_empty_strings = null_empty_strings
        self.has_header = has_header

    def get_delimiter(self) -> str:
        return self.delimiter

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validate_sql_identifier(table_name, "table name")
        headers = {"column_separator": self.delimiter, "format": "CSV", "enclose": '"'}
        if self.has_header:
            headers["skip_header"] = "1"
        if self.null_empty_strings:
            columns = _table_columns(connection, table_name)
            headers["columns"] = null_mapped_columns_header(columns, text_tokens=self.delimiter == ",")
        return stream_load(self.target, table_name, headers, lambda: file_chunks(file_path), logger)


class StarRocksParquetHandler(FileFormatHandler):
    def __init__(self, target: StreamLoadTarget) -> None:
        self.target = target

    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validate_sql_identifier(table_name, "table name")
        import pyarrow.parquet as pq

        parquet_file = pq.ParquetFile(file_path)
        if parquet_file.metadata.num_rows == 0:
            return 0
        names = [validate_sql_identifier(name, "column name") for name in parquet_file.schema_arrow.names]
        headers = {
            "column_separator": ",",
            "format": "CSV",
            "enclose": '"',
            "columns": null_mapped_columns_header(names, text_tokens=True),
        }
        return stream_load(self.target, table_name, headers, lambda: parquet_csv_chunks(file_path), logger)
