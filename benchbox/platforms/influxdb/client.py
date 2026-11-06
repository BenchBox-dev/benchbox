# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
import ssl as ssl_module
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

from benchbox.utils.tls import ca_bundle_path

from ._dependencies import (
    FLIGHTSQL_AVAILABLE,
    INFLUXDB3_AVAILABLE,
    FlightSQLClient,
    InfluxDBClient3,
)

logger = logging.getLogger(__name__)


def escape_tag_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ")


def escape_field_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def to_line_protocol(
    measurement: str,
    tags: dict[str, str],
    fields: dict[str, Any],
    timestamp: datetime | int | None = None,
) -> str:
    tag_parts = []
    for key, value in sorted(tags.items()):
        if value is not None and value != "":
            tag_parts.append(f"{escape_tag_value(key)}={escape_tag_value(str(value))}")
    tag_set = ",".join(tag_parts)

    field_parts = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            field_parts.append(f"{key}={str(value).lower()}")
        elif isinstance(value, int):
            field_parts.append(f"{key}={value}i")
        elif isinstance(value, float):
            field_parts.append(f"{key}={value}")
        elif isinstance(value, str):
            field_parts.append(f'{key}="{escape_field_string(value)}"')
        else:
            field_parts.append(f"{key}={value}")
    field_set = ",".join(field_parts)

    if not field_set:
        raise ValueError(f"At least one field is required for measurement '{measurement}'")

    if timestamp is None:
        ts_str = ""
    elif isinstance(timestamp, datetime):
        ts_str = f" {int(timestamp.timestamp() * 1_000_000_000)}"
    else:
        ts_str = f" {timestamp}"

    if tag_set:
        return f"{measurement},{tag_set} {field_set}{ts_str}"
    else:
        return f"{measurement} {field_set}{ts_str}"


def _escape_tag_values(values: Any) -> Any:
    import pyarrow.compute as pc

    for char, escaped in (("\\", "\\\\"), (",", "\\,"), ("=", "\\="), (" ", "\\ ")):
        values = pc.replace_substring(values, char, escaped)
    return values


_TS_AWARE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}(:?[0-9]{2})?)$"
_TS_NAIVE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}([T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?)?$"
_TS_EPOCH_SECONDS = r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$"


def _timestamp_ns(values: Any) -> Any:
    import pyarrow as pa
    import pyarrow.compute as pc

    def convert(pattern: str, target: Any) -> Any:
        matched = pc.match_substring_regex(values, pattern)
        selected = pc.if_else(matched, values, None)
        try:
            return pc.cast(pc.cast(selected, target), pa.int64())
        except pa.ArrowInvalid:
            distinct = pc.unique(selected).drop_null()
            parsed = []
            for value in distinct.to_pylist():
                try:
                    parsed.append(pc.cast(pc.cast(pa.array([value]), target), pa.int64())[0].as_py())
                except pa.ArrowInvalid:
                    parsed.append(None)
            return pc.take(pa.array(parsed, pa.int64()), pc.index_in(selected, value_set=distinct))

    seconds = pc.if_else(pc.match_substring_regex(values, _TS_EPOCH_SECONDS), values, None)
    epoch = pc.cast(pc.multiply(pc.cast(seconds, pa.float64()), 1_000_000_000), pa.int64())
    return pc.coalesce(convert(_TS_AWARE, pa.timestamp("ns", tz="UTC")), convert(_TS_NAIVE, pa.timestamp("ns")), epoch)


def _field_token(values: Any, float_only: bool) -> Any:
    import pyarrow as pa
    import pyarrow.compute as pc

    is_float = pc.match_substring_regex(
        values, r"(?i)^[+-]?(([0-9]+\.?[0-9]*|\.[0-9]+)(e[+-]?[0-9]+)?|inf|infinity|nan)$"
    )
    as_float = pc.cast(pc.if_else(is_float, values, "0"), pa.float64())
    float_token = pc.if_else(is_float, pc.cast(as_float, pa.string()), None)
    if float_only:
        return float_token
    is_int = pc.and_(is_float, pc.and_(pc.is_finite(as_float), pc.less(pc.abs(as_float), 9.0e18)))
    is_int = pc.and_(is_int, pc.equal(pc.floor(as_float), as_float))
    as_int = pc.cast(pc.if_else(is_int, as_float, 0.0), pa.int64())
    int_token = pc.if_else(is_int, pc.binary_join_element_wise(pc.cast(as_int, pa.string()), "i", ""), None)
    return pc.coalesce(int_token, float_token)


def table_to_line_protocol(
    measurement: str,
    table: Any,
    tag_columns: list[str],
    field_columns: list[str],
    timestamp_column: str | None,
    float_only_fields: frozenset[str] = frozenset(),
    string_fields: frozenset[str] = frozenset(),
) -> Any:
    import pyarrow as pa
    import pyarrow.compute as pc

    names = set(table.schema.names)
    parts = []
    for tag in sorted(tag for tag in tag_columns if tag in names):
        values = table.column(tag)
        valid = pc.and_(pc.is_valid(values), pc.not_equal(values, ""))
        escaped = pc.binary_join_element_wise(
            ",", pa.scalar(escape_tag_value(tag) + "="), _escape_tag_values(values), "", null_handling="emit_null"
        )
        parts.append(pc.if_else(valid, escaped, ""))

    field_parts = []
    for field in (field for field in field_columns if field in names):
        values = pc.utf8_trim_whitespace(table.column(field))
        values = pc.if_else(pc.equal(values, ""), None, values)
        if field in string_fields:
            escaped = pc.replace_substring(pc.replace_substring(values, "\\", "\\\\"), '"', '\\"')
            token = pc.binary_join_element_wise('"', escaped, '"', "", null_handling="emit_null")
        else:
            token = _field_token(values, field in float_only_fields)
        part = pc.binary_join_element_wise(",", pa.scalar(field + "="), token, "", null_handling="emit_null")
        field_parts.append(pc.fill_null(part, ""))

    rows = table.num_rows
    if not field_parts:
        return pa.array([], type=pa.string())
    fields = pc.utf8_slice_codeunits(pc.binary_join_element_wise(*field_parts, ""), 1)
    tags = pc.binary_join_element_wise(*parts, "") if parts else pa.array([""] * rows, type=pa.string())

    if timestamp_column and timestamp_column in names:
        stamp = _timestamp_ns(table.column(timestamp_column))
        suffix = pc.fill_null(
            pc.binary_join_element_wise(" ", pc.cast(stamp, pa.string()), "", null_handling="emit_null"), ""
        )
    else:
        suffix = pa.array([""] * rows, type=pa.string())

    lines = pc.binary_join_element_wise(pa.scalar(measurement), tags, " ", fields, suffix, "")
    return pc.filter(lines, pc.not_equal(fields, ""))


class InfluxDBConnection:
    def __init__(
        self,
        host: str,
        token: str,
        database: str,
        port: int = 443,
        ssl: bool = True,
        org: str | None = None,
        verify_ssl: bool = True,
        ca_cert_path: str | None = None,
    ):
        self.host = host
        self.token = token
        self.database = database
        self.port = port
        self.ssl = ssl
        self.org = org
        self.verify_ssl = verify_ssl
        self.ca_cert_path = ca_cert_path

        self._client = None
        self._client_type: str | None = None

        protocol = "https" if ssl else "http"
        self._url = f"{protocol}://{host}:{port}"

    def ensure_database(self, timeout_s: float = 30.0) -> None:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self._url}/api/v3/configure/database",
            data=json.dumps({"db": self.database}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            context = None
            if self.ssl:
                context = ssl_module.create_default_context(cafile=self.ca_cert_path or ca_bundle_path())
                if not self.verify_ssl:
                    context.check_hostname = False
                    context.verify_mode = ssl_module.CERT_NONE
            with urllib.request.urlopen(request, timeout=timeout_s, context=context):
                return
        except urllib.error.HTTPError as e:
            if e.code == 409:
                return
            raise ConnectionError(f"Failed to create InfluxDB database '{self.database}': HTTP {e.code}") from e
        except OSError as e:
            raise ConnectionError(f"Failed to create InfluxDB database '{self.database}': {e}") from e

    def connect(self) -> None:
        if INFLUXDB3_AVAILABLE:
            self._connect_influxdb3()
        elif FLIGHTSQL_AVAILABLE:
            self._connect_flightsql()
        else:
            raise ImportError(
                "No InfluxDB client library available. Install one of:\n"
                "  - influxdb3-python: uv add influxdb3-python\n"
                "  - flightsql-dbapi: uv add flightsql-dbapi"
            )

    def _connect_influxdb3(self) -> None:
        try:
            kwargs: dict[str, Any] = {}
            if not self.verify_ssl:
                kwargs["verify_ssl"] = False
            if self.ca_cert_path:
                kwargs["ssl_ca_cert"] = self.ca_cert_path

            self._client = InfluxDBClient3(
                host=self._url,
                token=self.token,
                database=self.database,
                org=self.org,
                **kwargs,
            )
            self._client_type = "influxdb3"
            logger.info(f"Connected to InfluxDB via influxdb3-python: {self._url}")
        except Exception as e:
            raise ConnectionError(f"Failed to connect to InfluxDB at {self._url}: {e}") from e

    def _connect_flightsql(self) -> None:
        try:
            grpc_host = f"{self.host}:{self.port}"

            kwargs: dict[str, Any] = {}
            if not self.verify_ssl:
                kwargs["disable_server_verification"] = True

            self._client = FlightSQLClient(
                host=grpc_host,
                token=self.token,
                metadata={"database": self.database},
                features={"metadata-reflection": "true"},
                **kwargs,
            )
            self._client_type = "flightsql"
            logger.info(f"Connected to InfluxDB via flightsql-dbapi: {grpc_host}")
        except Exception as e:
            raise ConnectionError(f"Failed to connect to InfluxDB at {self.host}:{self.port}: {e}") from e

    def execute(self, query: str, params: dict[str, Any] | None = None) -> list[tuple]:
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")

        try:
            if self._client_type == "influxdb3":
                return self._execute_influxdb3(query)
            elif self._client_type == "flightsql":
                return self._execute_flightsql(query)
            else:
                raise RuntimeError(f"Unknown client type: {self._client_type}")
        except Exception as e:
            raise RuntimeError(f"InfluxDB query failed: {e}") from e

    def _execute_influxdb3(self, query: str) -> list[tuple]:
        table = self._client.query(query)

        if table is None or table.num_rows == 0:
            return []

        dict_data = table.to_pydict()
        if not dict_data:
            return []
        columns = list(dict_data.keys())
        num_rows = len(dict_data[columns[0]])
        return [tuple(dict_data[col][i] for col in columns) for i in range(num_rows)]

    def _execute_flightsql(self, query: str) -> list[tuple]:
        info = self._client.execute(query)

        if not info.endpoints:
            return []

        ticket = info.endpoints[0].ticket
        reader = self._client.do_get(ticket)

        table = reader.read_all()

        if table.num_rows == 0:
            return []

        rows = []
        for i in range(table.num_rows):
            row = tuple(table.column(j)[i].as_py() for j in range(table.num_columns))
            rows.append(row)
        return rows

    def fetchone(self) -> tuple | None:
        raise NotImplementedError(
            "InfluxDB FlightSQL doesn't support cursor-based fetching. Use execute() to get all results at once."
        )

    def fetchall(self) -> list[tuple]:
        raise NotImplementedError(
            "InfluxDB FlightSQL returns all results in execute(). Use execute() instead of fetchall()."
        )

    def close(self) -> None:
        if self._client is not None:
            try:
                if hasattr(self._client, "close"):
                    self._client.close()
            except Exception as e:
                logger.warning(f"Error closing InfluxDB connection: {e}")
            finally:
                self._client = None
                self._client_type = None

    def commit(self) -> None:
        pass

    def write_line_protocol(self, lines: list[str], precision: str = "ns") -> int:
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")

        if self._client_type != "influxdb3":
            raise RuntimeError("Write operations require influxdb3-python client. flightsql-dbapi is read-only.")

        try:
            data = "\n".join(lines)
            self._client.write(data, write_precision=precision)
            return len(lines)
        except Exception as e:
            raise RuntimeError(f"InfluxDB write failed: {e}") from e

    def write_table(
        self,
        measurement: str,
        table: Any,
        tag_columns: list[str],
        field_columns: list[str],
        timestamp_column: str | None = "time",
        precision: str = "ns",
        batch_size: int = 10000,
        float_only_fields: frozenset[str] = frozenset(),
        string_fields: frozenset[str] = frozenset(),
    ) -> int:
        lines = table_to_line_protocol(
            measurement, table, tag_columns, field_columns, timestamp_column, float_only_fields, string_fields
        )
        for start in range(0, len(lines), batch_size):
            self.write_line_protocol(lines.slice(start, batch_size).to_pylist(), precision)
        return len(lines)

    def test_connection(self) -> bool:
        try:
            result = self.execute("SELECT 1")
            return len(result) > 0
        except Exception:
            return False

    @property
    def is_connected(self) -> bool:
        return self._client is not None


__all__ = ["InfluxDBConnection"]
