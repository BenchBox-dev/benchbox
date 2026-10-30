# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import gzip
import io
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import urlparse, urlunparse

from benchbox.utils.dialect_utils import create_table_shape, is_create_table, translate_ddl

from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from ..utils.file_format import get_data_extension
from .base import DriverIsolationCapability, PlatformAdapter
from .base.data_loading import (
    CsvDialect,
    DataSourceResolver as DataSourceResolver,
    FileFormatRegistry,
    GzipHandler,
    NoCompressionHandler,
    ZstdHandler,
    resolve_csv_dialect,
)
from .base.mysql_wire import (
    MySqlWireConnectionWrapper,
    MySqlWireLifecycleMixin,
    NoOpTableTuningMixin,
    build_database_config,
)

DORIS_DIALECT = "doris"

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    pymysql = None

try:
    import requests as _requests
except ImportError:
    _requests = None

_DEFAULT_STREAM_LOAD_CHUNK_SIZE = 10 * 1024 * 1024
_DEFAULT_STREAM_LOAD_MAX_FILTER_RATIO = "0"

_DEFAULT_BUCKETS = 10
_BACKEND_READY_TIMEOUT_S = 120.0
_BACKEND_READY_POLL_S = 2.0

_TPCDS_DECIMAL_NULL_POSITIONS: dict[str, list[int]] = {
    "call_center": [29, 30],
    "catalog_returns": list(range(18, 27)),
    "catalog_sales": list(range(19, 34)),
    "customer_address": [11],
    "item": [5, 6],
    "promotion": [5],
    "store": [27, 28],
    "store_returns": list(range(11, 20)),
    "store_sales": list(range(11, 22)),
    "warehouse": [13],
    "web_returns": list(range(15, 24)),
    "web_sales": list(range(19, 34)),
    "web_site": [24, 25],
}


def _fix_tpcds_decimal_nulls(line: str, delimiter: str, positions: list[int]) -> str:
    fields = line.split(delimiter)
    for pos in positions:
        if pos < len(fields) and fields[pos] == "":
            fields[pos] = r"\N"
    return delimiter.join(fields)


def _format_filter_ratio(value: Any) -> str:
    ratio = float(value)
    if math.isnan(ratio) or math.isinf(ratio) or ratio < 0 or ratio > 1:
        raise ValueError(f"stream_load_max_filter_ratio must be between 0 and 1 inclusive, got {value!r}")
    if ratio == 0:
        return "0"
    return format(ratio, "g")


_DORIS_ENGINE_VERSION_RE = re.compile(r"doris[-\s]v?(\d+\.\d+\.\d+(?:-[A-Za-z0-9]+)?)", re.IGNORECASE)

_TPCH_TABLE_KEYS: dict[str, list[str]] = {
    "lineitem": ["l_orderkey", "l_linenumber"],
    "orders": ["o_orderkey"],
    "customer": ["c_custkey"],
    "part": ["p_partkey"],
    "supplier": ["s_suppkey"],
    "partsupp": ["ps_partkey", "ps_suppkey"],
    "nation": ["n_nationkey"],
    "region": ["r_regionkey"],
}

_TPCH_DISTRIBUTION_KEYS: dict[str, str] = {
    table: keys[0] for table, keys in _TPCH_TABLE_KEYS.items() if table != "partsupp"
} | {"partsupp": "ps_partkey"}

_TPCH_PARTITION_TABLES: dict[str, str] = {"lineitem": "l_shipdate", "orders": "o_orderdate"}

_TPCH_YEAR_PARTITIONS = [(f"p{year}", f"{year}-01-01", f"{year + 1}-01-01") for year in range(1992, 1999)]
_TPCH_PARTITION_RANGES: dict[str, list[tuple[str, str, str]]] = {
    "lineitem": _TPCH_YEAR_PARTITIONS,
    "orders": _TPCH_YEAR_PARTITIONS,
}

_TPCH_BLOOM_FILTER_COLUMNS: dict[str, list[str]] = {
    table: [key] for table, key in _TPCH_DISTRIBUTION_KEYS.items() if table not in {"nation", "region"}
}

_TPCH_BITMAP_COLUMNS: dict[str, list[str]] = {
    "lineitem": ["l_returnflag", "l_linestatus"],
    "orders": ["o_orderstatus"],
    "part": ["p_type"],
}


def _normalize_doris_engine_version(
    mysql_protocol_version: object | None,
    version_comment: object | None,
) -> str | None:
    if version_comment is not None:
        comment = str(version_comment).strip()
        match = _DORIS_ENGINE_VERSION_RE.search(comment)
        if match:
            return match.group(1)
        if comment:
            return comment

    if mysql_protocol_version is None:
        return None

    version_text = str(mysql_protocol_version).strip()
    return version_text or None


def _read_doris_version_details(cursor: Any) -> tuple[str | None, str | None, str | None]:
    mysql_protocol_version = None
    version_comment = None

    try:
        cursor.execute("SELECT version()")
        version_row = cursor.fetchone()
        if version_row:
            mysql_protocol_version = version_row[0]
    except Exception:
        pass

    try:
        cursor.execute("SELECT @@version_comment")
        comment_row = cursor.fetchone()
        if comment_row:
            version_comment = comment_row[0]
    except Exception:
        pass

    platform_version = _normalize_doris_engine_version(mysql_protocol_version, version_comment)
    mysql_protocol_version_str = str(mysql_protocol_version) if mysql_protocol_version is not None else None
    version_comment_str = str(version_comment) if version_comment is not None else None
    return platform_version, mysql_protocol_version_str, version_comment_str


def _backend_is_ready(alive: object, total_capacity: object) -> bool:
    if str(alive).strip().lower() not in {"true", "1"}:
        return False
    try:
        return float(cast(Any, total_capacity)) > 0
    except (TypeError, ValueError):
        return False


class _DorisConnectionWrapper(MySqlWireConnectionWrapper):
    optimize_contains_create = True


class DorisAdapter(NoOpTableTuningMixin, MySqlWireLifecycleMixin, PlatformAdapter):
    plan_capture_phase_eligible = True
    default_service_port = 9030

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    connection_operation_name = "Doris connection"
    current_database_sql = "SELECT database()"

    @property
    def platform_name(self) -> str:
        return "Apache Doris"

    def get_target_dialect(self) -> str:
        return DORIS_DIALECT

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        try:
            parser.add_argument(
                "--doris-host",
                dest="host",
                default=None,
                help="Doris FE node hostname",
            )
            parser.add_argument(
                "--doris-port",
                dest="port",
                type=int,
                default=None,
                help="Doris MySQL protocol port (default: 9030)",
            )
            parser.add_argument(
                "--doris-http-port",
                dest="http_port",
                type=int,
                default=None,
                help="Doris Stream Load HTTP port (default: 8030)",
            )
            parser.add_argument(
                "--doris-database",
                dest="database",
                help="Doris database name (auto-generated if not specified)",
            )
            parser.add_argument(
                "--doris-username",
                dest="username",
                default=None,
                help="Doris username (default: root)",
            )
            parser.add_argument(
                "--doris-password",
                dest="password",
                help="Doris password",
            )
            parser.add_argument(
                "--doris-use-tls",
                dest="use_tls",
                action="store_true",
                default=False,
                help="Use HTTPS for Stream Load API (default: HTTP)",
            )
        except Exception:
            pass

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> DorisAdapter:
        adapter_config = {}

        adapter_config["host"] = config.get("host", "localhost")
        adapter_config["port"] = config.get("port", 9030)
        adapter_config["http_port"] = config.get("http_port", 8030)
        adapter_config["be_http_port"] = config.get("be_http_port", 8040)
        adapter_config["username"] = config.get("username", "root")
        adapter_config["password"] = config.get("password", "")

        if config.get("database"):
            adapter_config["database"] = config["database"]
        elif db_env := os.environ.get("DORIS_DATABASE"):
            adapter_config["database"] = db_env
        elif config.get("benchmark") and config.get("scale_factor") is not None:
            from benchbox.utils.scale_factor import format_benchmark_name

            benchmark_name = format_benchmark_name(config["benchmark"], config["scale_factor"])
            adapter_config["database"] = f"benchbox_{benchmark_name}".lower().replace("-", "_")
        else:
            adapter_config["database"] = "benchbox"

        adapter_config["use_tls"] = config.get("use_tls", False)

        adapter_config["force_recreate"] = config.get("force", False)

        for key in [
            "stream_load_chunk_size",
            "stream_load_max_filter_ratio",
            "table_model",
            "default_buckets",
            "replication_num",
            "enable_partitioning",
            "enable_bloom_filter",
            "enable_bitmap_index",
            "verify_ssl",
            "ca_cert_path",
        ]:
            if key in config:
                adapter_config[key] = config[key]

        for key in [
            "tuning_config",
            "tuning_enabled",
            "unified_tuning_configuration",
            "tuning_source",
            "tuning_source_file",
            "verbose_enabled",
            "very_verbose",
        ]:
            if key in config:
                adapter_config[key] = config[key]

        return cls(**adapter_config)

    def __init__(self, **config):
        super().__init__(**config)

        if pymysql is None:
            available, missing = check_platform_dependencies("doris")
            if not available:
                error_msg = get_dependency_error_message("doris", missing)
                raise ImportError(error_msg)

        self._dialect = DORIS_DIALECT

        self.host = config.get("host", "localhost")
        self.port = config.get("port", 9030)
        self.http_port = config.get("http_port", 8030)
        self.be_http_port = config.get("be_http_port", 8040)
        self.database = config.get("database", "benchbox")
        self.username = config.get("username", "root")
        self.password = config.get("password", "")
        self.use_tls = config.get("use_tls", False)
        self.verify_ssl = config.get("verify_ssl") if config.get("verify_ssl") is not None else True
        self.ca_cert_path = config.get("ca_cert_path")

        self.stream_load_chunk_size: int = config.get("stream_load_chunk_size", _DEFAULT_STREAM_LOAD_CHUNK_SIZE)
        self.stream_load_max_filter_ratio = _format_filter_ratio(
            config.get("stream_load_max_filter_ratio", _DEFAULT_STREAM_LOAD_MAX_FILTER_RATIO)
        )

        self.table_model: str = config.get("table_model", "duplicate").lower()
        if self.table_model not in ("duplicate", "aggregate", "unique"):
            raise ValueError(f"Invalid table_model '{self.table_model}': must be 'duplicate', 'aggregate', or 'unique'")

        self.default_buckets: int = config.get("default_buckets", _DEFAULT_BUCKETS)

        self.enable_partitioning: bool = config.get("enable_partitioning", False)

        self.replication_num: int = int(config.get("replication_num", 1))

        self.enable_bloom_filter: bool = config.get("enable_bloom_filter", False)
        self.enable_bitmap_index: bool = config.get("enable_bitmap_index", False)

        if not self._validate_identifier(self.database):
            raise ValueError(f"Invalid database identifier: {self.database}")

    def _admin_connect(self) -> Any:
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.username,
            password=self.password,
            connect_timeout=10,
        )

    def _connect_database(self, **connection_config: Any) -> Any:
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.username,
            password=self.password,
            database=self.database,
            connect_timeout=10,
            read_timeout=300,
            write_timeout=300,
            charset="utf8mb4",
        )

    def _ready_backend_count(self, conn: Any) -> int:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT Alive, TotalCapacity FROM backends()")
            return sum(1 for alive, capacity in cursor.fetchall() if _backend_is_ready(alive, capacity))
        finally:
            cursor.close()

    def _await_backends_ready(self) -> None:
        deadline = time.monotonic() + _BACKEND_READY_TIMEOUT_S
        required = max(1, self.replication_num)
        ready = 0
        while True:
            try:
                conn = self._admin_connect()
            except Exception as exc:
                self.logger.debug(f"Skipping Doris backend readiness wait: {exc}")
                return
            try:
                ready = self._ready_backend_count(conn)
            except Exception as exc:
                self.logger.debug(f"Skipping Doris backend readiness wait: {exc}")
                return
            finally:
                conn.close()
            if ready >= required:
                return
            if time.monotonic() >= deadline:
                self.logger.warning(
                    f"Doris reports {ready} of {required} required backends alive with disk capacity after "
                    f"{_BACKEND_READY_TIMEOUT_S:.0f}s; table creation may fail"
                )
                return
            time.sleep(_BACKEND_READY_POLL_S)

    def create_connection(self, **connection_config: Any) -> Any:
        self._await_backends_ready()
        return super().create_connection(**connection_config)

    def _wrap_database_connection(self, conn: Any) -> Any:
        return _DorisConnectionWrapper(conn, ddl_optimizer=self._translate_create_table)

    def _create_schema_with_tuning(self, benchmark: Any, source_dialect: str = "standard") -> str:
        return benchmark.get_create_tables_sql(
            dialect="standard", tuning_config=self.get_effective_tuning_configuration()
        )

    def _transform_schema_statement(self, stmt: str, benchmark: Any) -> str:
        scale_factor: float | None = getattr(benchmark, "scale_factor", None)
        return self._translate_create_table(stmt, scale_factor=scale_factor)

    def _load_resolved_data_file(
        self,
        benchmark: Any,
        connection: Any,
        table_name: str,
        source_table_name: str,
        data_file: Path,
        data_source: Any,
    ) -> int:
        dialect = resolve_csv_dialect(data_source, source_table_name, data_file, benchmark)
        if _requests is not None:
            return self._stream_load_file(table_name, data_file, dialect)
        return self._insert_load_file(connection, table_name, data_file, dialect)

    def _stream_load_file(self, table_name: str, data_file: Path, dialect: CsvDialect) -> int:
        data_file = Path(data_file)

        if get_data_extension(data_file) == ".parquet":
            return self._stream_load_parquet(table_name, data_file)

        file_size = data_file.stat().st_size

        if file_size > self.stream_load_chunk_size:
            return self._stream_load_file_chunked(table_name, data_file, dialect)

        return self._stream_load_single(table_name, data_file, dialect)

    @staticmethod
    def _open_data_file_text(path: Path):
        import contextlib

        handler = FileFormatRegistry.get_compression_handler(path)

        @contextlib.contextmanager
        def _ctx():
            if isinstance(handler, ZstdHandler):
                import zstandard

                dctx = zstandard.ZstdDecompressor()
                with open(path, "rb") as raw, dctx.stream_reader(raw) as reader:
                    yield io.TextIOWrapper(reader, encoding="latin-1", newline="")
            elif isinstance(handler, GzipHandler):
                yield gzip.open(path, "rt", encoding="latin-1")
            elif isinstance(handler, NoCompressionHandler):
                yield open(path, encoding="latin-1")
            else:
                raise ValueError(f"Unsupported compression handler {type(handler).__name__} for {path}")

        return _ctx()

    @staticmethod
    def _open_data_file_binary(path: Path):
        import contextlib

        handler = FileFormatRegistry.get_compression_handler(path)

        @contextlib.contextmanager
        def _ctx():
            if isinstance(handler, ZstdHandler):
                import zstandard

                dctx = zstandard.ZstdDecompressor()
                with open(path, "rb") as raw:
                    yield dctx.stream_reader(raw)
            elif isinstance(handler, GzipHandler):
                yield gzip.open(path, "rb")
            elif isinstance(handler, NoCompressionHandler):
                yield open(path, "rb")
            else:
                raise ValueError(f"Unsupported compression handler {type(handler).__name__} for {path}")

        return _ctx()

    def _stream_load_put(self, url: str, data: bytes | Callable[[], Any], headers: dict):
        kwargs = {
            "auth": (self.username, self.password),
            "timeout": 1800,
            "verify": self.ca_cert_path if self.ca_cert_path else self.verify_ssl,
            "allow_redirects": False,
        }

        _max_attempts = 3
        _backoff_seconds = [5, 15, 45]

        def _put_once(target_url: str):
            if callable(data):
                with cast(Any, data)() as body:
                    return _requests.put(target_url, data=body, headers=headers, **kwargs)
            return _requests.put(target_url, data=data, headers=headers, **kwargs)

        for attempt in range(_max_attempts):
            try:
                resp = _put_once(url)

                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    if location:
                        parsed = urlparse(location)
                        scheme = "https" if self.use_tls else "http"
                        rewritten = urlunparse(
                            parsed._replace(
                                scheme=scheme,
                                netloc=f"{self.host}:{self.be_http_port}",
                            )
                        )
                        resp = _put_once(rewritten)

                return resp

            except (_requests.exceptions.ConnectionError, _requests.exceptions.Timeout) as exc:
                if attempt == _max_attempts - 1:
                    raise
                wait = _backoff_seconds[attempt]
                self.logger.warning(
                    f"Stream Load connection error (attempt {attempt + 1}/{_max_attempts}), retrying in {wait}s: {exc}"
                )
                time.sleep(wait)

        raise RuntimeError("unreachable")

    def _stream_load_headers(
        self,
        *,
        format_name: str,
        delimiter: str | None = None,
        is_tpc: bool = False,
        skip_header: bool = False,
    ) -> dict[str, str]:
        headers = {
            "Expect": "100-continue",
            "format": format_name,
            "max_filter_ratio": self.stream_load_max_filter_ratio,
        }
        if delimiter is not None:
            headers["column_separator"] = delimiter
        if is_tpc:
            headers["trim_double_quotes"] = "true"
        if skip_header:
            headers["skip_lines"] = "1"
        return headers

    def _handle_stream_load_response(self, resp: Any, *, context: str) -> dict[str, Any]:
        if resp.status_code != 200:
            raise RuntimeError(f"{context} failed with status {resp.status_code}: {resp.text}")

        result = resp.json()
        status = result.get("Status")
        if status not in ("Success", "Publish Timeout"):
            message = result.get("Message", "Unknown error")
            error_url = result.get("ErrorURL", "")
            if error_url:
                self.logger.error(f"{context} error detail URL: {error_url}")
                try:
                    err_resp = _requests.get(error_url, auth=(self.username, self.password), timeout=30)
                    self.logger.error(f"{context} error rows:\n{err_resp.text[:2000]}")
                except Exception as url_err:
                    self.logger.debug(f"Could not fetch error URL: {url_err}")
            raise RuntimeError(f"{context} failed: {status} - {message}")

        filtered_rows = int(result.get("NumberFilteredRows", 0) or 0)
        unselected_rows = int(result.get("NumberUnselectedRows", 0) or 0)
        if filtered_rows > 0:
            message = (
                f"{context} filtered {filtered_rows} row(s) (max_filter_ratio={self.stream_load_max_filter_ratio})"
            )
            if self.stream_load_max_filter_ratio == "0":
                raise RuntimeError(f"{message}; refusing silent partial load")
            self.logger.warning(message)
        if unselected_rows > 0:
            self.logger.warning(f"{context} skipped {unselected_rows} row(s) via Doris WHERE filtering")

        return result

    def _stream_load_single(self, table_name: str, data_file: Path, dialect: CsvDialect) -> int:
        delimiter = dialect.delimiter
        is_tpc = dialect.null_marker is not None

        scheme = "https" if self.use_tls else "http"
        url = f"{scheme}://{self.host}:{self.http_port}/api/{self.database}/{table_name}/_stream_load"

        headers = self._stream_load_headers(
            format_name="csv", delimiter=delimiter, is_tpc=is_tpc, skip_header=dialect.has_header
        )

        decimal_positions = _TPCDS_DECIMAL_NULL_POSITIONS.get(table_name, [])

        if is_tpc:
            with self._open_data_file_text(data_file) as f:
                lines = f.read().splitlines()
            if decimal_positions:
                lines = [_fix_tpcds_decimal_nulls(line, delimiter, decimal_positions) for line in lines]
            data = "\n".join(lines).encode("utf-8")
        else:
            with self._open_data_file_binary(data_file) as f:
                data = f.read()

        resp = self._stream_load_put(url, data, headers)
        result = self._handle_stream_load_response(resp, context="Stream Load")
        return int(result.get("NumberLoadedRows", 0))

    def _stream_load_parquet(self, table_name: str, data_file: Path) -> int:
        scheme = "https" if self.use_tls else "http"
        url = f"{scheme}://{self.host}:{self.http_port}/api/{self.database}/{table_name}/_stream_load"

        headers = self._stream_load_headers(format_name="parquet")
        resp = self._stream_load_put(url, lambda: self._open_data_file_binary(data_file), headers)
        result = self._handle_stream_load_response(resp, context="Stream Load (Parquet)")
        return int(result.get("NumberLoadedRows", 0))

    def _stream_load_file_chunked(self, table_name: str, data_file: Path, dialect: CsvDialect) -> int:
        delimiter = dialect.delimiter
        is_tpc = dialect.null_marker is not None

        scheme = "https" if self.use_tls else "http"
        url = f"{scheme}://{self.host}:{self.http_port}/api/{self.database}/{table_name}/_stream_load"

        headers = self._stream_load_headers(format_name="csv", delimiter=delimiter, is_tpc=is_tpc)

        decimal_positions = _TPCDS_DECIMAL_NULL_POSITIONS.get(table_name, [])

        total_rows = 0
        chunk_num = 0

        with self._open_data_file_text(data_file) as f:
            if dialect.has_header:
                next(f, None)
            while True:
                chunk_lines: list[str] = []
                chunk_bytes = 0

                for line in f:
                    line = line.rstrip("\n").rstrip("\r")
                    if not line:
                        continue

                    if decimal_positions:
                        line = _fix_tpcds_decimal_nulls(line, delimiter, decimal_positions)

                    chunk_lines.append(line)
                    chunk_bytes += len(line.encode("utf-8", errors="replace")) + 1

                    if chunk_bytes >= self.stream_load_chunk_size:
                        break

                if not chunk_lines:
                    break

                chunk_num += 1
                data = "\n".join(chunk_lines).encode("utf-8", errors="replace")
                self.log_verbose(
                    f"Stream Load chunk {chunk_num} for {table_name}: {len(chunk_lines)} lines, {len(data):,} bytes"
                )

                resp = self._stream_load_put(url, data, headers)
                result = self._handle_stream_load_response(resp, context=f"Stream Load chunk {chunk_num}")
                chunk_rows = int(result.get("NumberLoadedRows", 0))
                total_rows += chunk_rows

        self.log_verbose(
            f"Chunked Stream Load complete for {table_name}: {chunk_num} chunks, {total_rows:,} total rows"
        )
        return total_rows

    def _insert_load_file(self, connection: Any, table_name: str, data_file: Path, dialect: CsvDialect) -> int:
        delimiter = dialect.delimiter
        is_tpc = dialect.null_marker is not None
        row_count = 0
        batch_size = 1000

        cursor = connection.cursor()

        with open(data_file, encoding="utf-8") as f:
            if dialect.has_header:
                next(f, None)
            batch = []
            for line in f:
                line = line.strip()
                if not line:
                    continue

                if is_tpc and line.endswith(delimiter):
                    line = line[: -len(delimiter)]

                values = line.split(delimiter)
                batch.append(values)

                if len(batch) >= batch_size:
                    self._execute_batch_insert(cursor, table_name, batch)
                    row_count += len(batch)
                    batch = []

            if batch:
                self._execute_batch_insert(cursor, table_name, batch)
                row_count += len(batch)

        connection.commit()
        cursor.close()

        return row_count

    def _execute_batch_insert(self, cursor: Any, table_name: str, rows: list[list[str]]) -> None:
        if not rows:
            return

        num_cols = len(rows[0])
        placeholders = ", ".join(["%s"] * num_cols)
        sql = f"INSERT INTO `{table_name}` VALUES ({placeholders})"

        cursor.executemany(sql, rows)

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        cursor = connection.cursor()

        try:
            cursor.execute("SET enable_sql_cache = false")
        except Exception as e:
            self.logger.debug(f"Could not disable SQL cache: {e}")

        try:
            cursor.execute("SHOW VARIABLES LIKE 'enable_sql_cache'")
            row = cursor.fetchone()
            if row and str(row[1]).lower() not in ("false", "0", "off"):
                self.logger.warning(
                    f"Cache disable validation failed: enable_sql_cache = {row[1]} "
                    "(expected false). Benchmark results may include cached queries."
                )
            else:
                self.log_verbose("Cache disable validated: enable_sql_cache = false")
        except Exception as e:
            self.logger.debug(f"Could not validate cache setting: {e}")

        try:
            cursor.execute("SET parallel_fragment_exec_instance_num = 8")
        except Exception as e:
            self.logger.debug(f"Could not set parallel execution: {e}")

        if benchmark_type == "olap":
            try:
                cursor.execute("SET exec_mem_limit = 5368709120")
            except Exception as e:
                self.logger.debug(f"Could not set exec_mem_limit: {e}")

        cursor.close()

    def _explain_query_prefix(self, explain_options: dict[str, Any] | None = None) -> str:
        verbose = explain_options.get("verbose", False) if explain_options else False
        return "EXPLAIN VERBOSE" if verbose else "EXPLAIN SHAPE PLAN"

    def _format_query_plan_rows(self, rows: Any) -> str:
        return "\n".join(str(row[0]) for row in rows)

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.doris import DorisQueryPlanParser

        return DorisQueryPlanParser()

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        return self.execute_query_with_plan_capture(
            super().execute_query,
            connection,
            query,
            query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "doris",
            "platform_name": "Apache Doris",
            "host": self.host,
            "port": self.port,
            "dialect": DORIS_DIALECT,
            "configuration": {
                "database": self.database,
                "http_port": self.http_port,
                "stream_load_available": _requests is not None,
                "table_model": self.table_model,
                "default_buckets": self.default_buckets,
                "stream_load_chunk_size": self.stream_load_chunk_size,
                "stream_load_max_filter_ratio": self.stream_load_max_filter_ratio,
                "enable_partitioning": self.enable_partitioning,
                "enable_bloom_filter": self.enable_bloom_filter,
                "enable_bitmap_index": self.enable_bitmap_index,
            },
        }

        if connection:
            try:
                cursor = connection.cursor()

                platform_version, mysql_protocol_version, version_comment = _read_doris_version_details(cursor)
                if platform_version:
                    platform_info["platform_version"] = platform_version
                if mysql_protocol_version:
                    platform_info["configuration"]["mysql_protocol_version"] = mysql_protocol_version
                if version_comment:
                    platform_info["configuration"]["version_comment"] = version_comment

                cursor.execute("SELECT database()")
                db_row = cursor.fetchone()
                if db_row:
                    platform_info["configuration"]["current_database"] = db_row[0]

                cursor.close()

            except Exception as e:
                self.logger.debug(f"Error getting platform info: {e}")

        if pymysql:
            platform_info["client_library_version"] = pymysql.__version__

        return platform_info

    def _translate_create_table(self, stmt: str, scale_factor: float | None = None) -> str:
        if not is_create_table(stmt):
            return stmt
        options: dict[str, Any] = {
            "strip_foreign_keys": True,
            "strip_primary_keys": True,
            "type_map": "doris",
        }
        table_name, columns, _ = create_table_shape(stmt)
        if not columns:
            return stmt
        key_model = self.table_model if self.table_model in {"duplicate", "aggregate", "unique"} else "duplicate"
        tpch_keys = _TPCH_TABLE_KEYS.get(table_name, [])
        key_columns = tpch_keys if tpch_keys and set(tpch_keys) <= set(columns) else []
        distribution_key = _TPCH_DISTRIBUTION_KEYS.get(table_name)
        if distribution_key not in columns:
            distribution_key = None
        options["default_layout"] = {
            "key_model": key_model,
            "key_columns": key_columns,
            "partition_clause": self.get_partition_clause(table_name) or None,
            "distribution": "hash",
            "distribute_by": [distribution_key] if distribution_key else [],
            "buckets": self._compute_bucket_count(table_name, scale_factor),
            "properties": {"replication_num": str(self.replication_num), "storage_medium": "HDD"},
        }
        return "; ".join(translate_ddl(stmt, "doris", source_dialect="standard", **options))

    def _compute_bucket_count(self, table_name: str, scale_factor: float | None = None) -> int:
        if not isinstance(scale_factor, (int, float)) or scale_factor <= 0:
            return self.default_buckets

        sf1_rows = {
            "lineitem": 6_000_000,
            "orders": 1_500_000,
            "partsupp": 800_000,
            "customer": 150_000,
            "part": 200_000,
            "supplier": 10_000,
            "nation": 25,
            "region": 5,
        }
        base_rows = sf1_rows.get(table_name, 100_000)
        estimated_rows = base_rows * scale_factor
        computed = max(self.default_buckets, int(math.ceil(estimated_rows / 500_000)))
        return min(computed, 128)

    def get_partition_clause(self, table_name: str) -> str:
        if not self.enable_partitioning:
            return ""

        partition_col = _TPCH_PARTITION_TABLES.get(table_name)
        if not partition_col:
            return ""

        ranges = _TPCH_PARTITION_RANGES.get(table_name, [])
        if not ranges:
            return ""

        partition_defs = []
        for part_name, _start, end in ranges:
            partition_defs.append(f'PARTITION {part_name} VALUES LESS THAN ("{end}")')

        partitions_str = ",\n    ".join(partition_defs)
        return f"PARTITION BY RANGE({partition_col}) (\n    {partitions_str}\n)"

    def _create_secondary_indexes(
        self,
        connection: Any,
        columns_by_table: dict[str, list[str]],
        index_type: str,
        index_name_prefix: str,
        tables: list[str] | None = None,
    ) -> list[str]:
        target_tables = tables or list(columns_by_table.keys())
        executed_stmts: list[str] = []
        cursor = connection.cursor()
        try:
            for table_name in target_tables:
                for col in columns_by_table.get(table_name, []):
                    if not self._validate_identifier(table_name) or not self._validate_identifier(col):
                        continue
                    idx_name = f"idx_{index_name_prefix}_{col}"
                    stmt = f"CREATE INDEX `{idx_name}` ON `{table_name}` (`{col}`) USING {index_type}"
                    try:
                        cursor.execute(stmt)
                        executed_stmts.append(stmt)
                        self.log_verbose(f"Created {index_type} index: {idx_name} on {table_name}.{col}")
                    except Exception as e:
                        self.logger.warning(f"Failed to create {index_type} index {idx_name}: {e}")
        finally:
            cursor.close()
        return executed_stmts

    def create_bloom_filter_indexes(self, connection: Any, tables: list[str] | None = None) -> list[str]:
        return self._create_secondary_indexes(connection, _TPCH_BLOOM_FILTER_COLUMNS, "BLOOM_FILTER", "bloom", tables)

    def create_bitmap_indexes(self, connection: Any, tables: list[str] | None = None) -> list[str]:
        return self._create_secondary_indexes(connection, _TPCH_BITMAP_COLUMNS, "BITMAP", "bitmap", tables)

    def validate_platform_capabilities(self, benchmark_type: str):
        errors = []
        warnings = []

        if pymysql is None:
            errors.append("pymysql library not available - install with 'pip install pymysql'")
        else:
            try:
                version = pymysql.__version__
                version_parts = version.split(".")
                major = int(version_parts[0])
                if major < 1:
                    warnings.append(f"pymysql version {version} is older - consider upgrading")
            except (AttributeError, ValueError, IndexError):
                warnings.append("Could not determine pymysql version")

        if _requests is None:
            warnings.append(
                "requests library not available - Stream Load disabled, falling back to INSERT loading. "
                "Install with 'pip install requests' for better performance."
            )

        platform_info = {
            "platform": self.platform_name,
            "benchmark_type": benchmark_type,
            "dry_run_mode": self.dry_run_mode,
            "pymysql_available": pymysql is not None,
            "requests_available": _requests is not None,
            "host": self.host,
            "port": self.port,
            "http_port": self.http_port,
            "database": self.database,
        }

        if pymysql:
            platform_info["pymysql_version"] = getattr(pymysql, "__version__", "unknown")

        try:
            from benchbox.core.validation import ValidationResult

            return ValidationResult(
                is_valid=len(errors) == 0,
                errors=errors,
                warnings=warnings,
                details=platform_info,
            )
        except ImportError:
            return None

    def _populate_connection_health_details(
        self,
        cursor: Any,
        warnings: list[str],
        connection_info: dict[str, Any],
    ) -> None:
        platform_version, mysql_protocol_version, version_comment = _read_doris_version_details(cursor)
        if platform_version:
            connection_info["server_version"] = platform_version
        else:
            warnings.append("Could not query Doris version")
        if mysql_protocol_version:
            connection_info["mysql_protocol_version"] = mysql_protocol_version
        if version_comment:
            connection_info["version_comment"] = version_comment

    def _validate_data_integrity(self, benchmark, connection, table_stats: dict) -> tuple:

        validation_details: dict[str, Any] = {}
        try:
            accessible_tables = []
            inaccessible_tables = []
            for table_name in table_stats:
                try:
                    cursor = connection.cursor()
                    try:
                        cursor.execute(f"SELECT 1 FROM `{table_name}` LIMIT 1")
                        accessible_tables.append(table_name)
                    finally:
                        cursor.close()
                except Exception:
                    inaccessible_tables.append(table_name)

            if inaccessible_tables:
                validation_details["inaccessible_tables"] = inaccessible_tables
                validation_details["constraints_enabled"] = False
                return "FAILED", validation_details
            validation_details["accessible_tables"] = accessible_tables
            validation_details["constraints_enabled"] = True
            return "PASSED", validation_details
        except Exception as e:
            validation_details["constraints_enabled"] = False
            validation_details["integrity_error"] = str(e)
            return "FAILED", validation_details

    _supported_tuning_type_names = ("PARTITIONING", "SORTING", "DISTRIBUTION", "PRIMARY_KEYS")


def _build_doris_config(
    platform: str,
    options: dict[str, Any],
    overrides: dict[str, Any],
    info: Any,
) -> Any:
    return build_database_config(
        platform=platform,
        options=options,
        overrides=overrides,
        info=info,
        default_name="Apache Doris",
        default_driver_package="pymysql",
        fields={
            "host": lambda m: m.get("host") or os.environ.get("DORIS_HOST", "localhost"),
            "port": lambda m: int(m.get("port") or os.environ.get("DORIS_PORT", "9030")),
            "http_port": lambda m: int(m.get("http_port") or os.environ.get("DORIS_HTTP_PORT", "8030")),
            "be_http_port": lambda m: int(m.get("be_http_port") or os.environ.get("DORIS_BE_HTTP_PORT", "8040")),
            "username": lambda m: (
                m.get("username") or os.environ.get("DORIS_USER") or os.environ.get("DORIS_USERNAME", "root")
            ),
            "password": lambda m: m.get("password") or os.environ.get("DORIS_PASSWORD", ""),
            "database": lambda m: m.get("database") or os.environ.get("DORIS_DATABASE"),
        },
    )
