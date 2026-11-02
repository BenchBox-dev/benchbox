from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any

from benchbox.core.transactional.staging_keys import leading_key_columns
from benchbox.core.tuning.generators.starrocks import DEFAULT_BUCKET_COUNT
from benchbox.platforms.base.data_loading import (
    DataSource,
    FileFormatHandler,
    FileFormatRegistry,
    resolve_csv_dialect,
    validate_sql_identifier,
)
from benchbox.platforms.base.sql_execution import execute_sql_query
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.dialect_utils import apply_rewrite, create_table_shape, is_create_table, translate_ddl

logger = logging.getLogger(__name__)


_STREAM_LOAD_NULL_TOKENS = frozenset(("nan", "inf", "-inf", "+inf"))


def _stream_load_treats_empty_as_null(delimiter: str, null_marker: str | None = None) -> bool:
    return delimiter == "," or null_marker == ""


def _stream_load_row(cells: list[str], *, null_empty: bool, delimiter: str) -> str:
    if null_empty:
        null_tokens = _STREAM_LOAD_NULL_TOKENS if delimiter == "," else ()
        cells = ["\\N" if (cell == "" or cell.lower() in null_tokens) else cell for cell in cells]
    return delimiter.join(cells)


def _alias_derived_tables(query: str) -> str:
    return apply_rewrite("add_derived_table_aliases", query, "starrocks", prefix="_sq", quoted=True)


class StarRocksWorkloadMixin:
    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()

        enable_primary_keys, enable_foreign_keys = self._get_constraint_configuration()
        self._log_constraint_configuration(enable_primary_keys, enable_foreign_keys)

        try:
            effective_config = self.get_effective_tuning_configuration()
            schema_sql = benchmark.get_create_tables_sql(
                dialect="duckdb",
                tuning_config=effective_config,
            )

            table_tunings = None
            if self.tuning_enabled and effective_config is not None:
                table_tunings = effective_config.table_tunings

            schema_sql_clean = re.sub(r"--[^\n]*\n?", "", schema_sql)
            statements = [stmt.strip() for stmt in schema_sql_clean.split(";") if stmt.strip()]

            cursor = connection.cursor()
            try:
                try:
                    cursor.execute('ADMIN SET FRONTEND CONFIG("tablet_create_timeout_second"="600")')
                except Exception:
                    pass

                for statement in statements:
                    statement = self._translate_schema_statement(statement, table_tunings)
                    cursor.execute(statement)
                    self.logger.debug(f"Executed schema statement: {statement[:100]}...")
            finally:
                cursor.close()

            self.logger.info(f"Schema created (PKs: {enable_primary_keys}, FKs: {enable_foreign_keys})")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise

        return elapsed_seconds(start_time)

    def _translate_schema_statement(self, statement: str, table_tunings: dict[str, Any] | None = None) -> str:
        if not is_create_table(statement):
            return statement

        source_dialect = "starrocks" if "`" in statement else "standard"
        table_name, columns, key = create_table_shape(statement, source_dialect)
        if not columns:
            return "; ".join(
                translate_ddl(statement, "starrocks", source_dialect=source_dialect, **self._ddl_options())
            )

        table_tuning, tuning_clauses = self._resolve_table_tuning(table_name, table_tunings)
        layout = self._starrocks_layout(columns, key, table_tuning)
        if staging_key := leading_key_columns(table_name, columns):
            layout |= {"key_model": "primary", "key_columns": staging_key, "distribute_by": [staging_key[0]]}
        undefined = sorted(
            {name.lower() for field in ("distribute_by", "partition_by", "sort_by") for name in layout.get(field, [])}
            - set(columns)
        )
        if undefined:
            raise ValueError(f"Tuned columns {undefined} are not defined by table {table_name!r}")
        if table_tunings is not None and layout["key_model"] == "primary":
            self._record_starrocks_tuning_to_ledger(table_name, [f"PRIMARY KEY ({', '.join(key)})"])
        if tuning_clauses is not None:
            self._record_starrocks_tuning_to_ledger(
                table_name,
                [
                    clause
                    for clause in (tuning_clauses.partition_by, tuning_clauses.distribute_by, tuning_clauses.order_by)
                    if clause
                ],
            )
        return "; ".join(
            translate_ddl(
                statement, "starrocks", source_dialect=source_dialect, default_layout=layout, **self._ddl_options()
            )
        )

    @staticmethod
    def _ddl_options() -> dict[str, Any]:
        return {"strip_foreign_keys": True, "type_map": "starrocks"}

    @staticmethod
    def _starrocks_layout(columns: list[str], key: list[str], table_tuning: Any) -> dict[str, Any]:
        first = columns[0]
        primary = bool(key) and set(key) == set(columns[: len(set(key))])
        layout: dict[str, Any] = {
            "key_model": "primary" if primary else "duplicate",
            "key_columns": [] if primary else [first],
            "distribution": "hash",
            "distribute_by": [first],
            "buckets": DEFAULT_BUCKET_COUNT,
        }
        if table_tuning is not None:
            from benchbox.core.tuning.interface import TuningType

            def tuned(tuning_type: Any) -> list[str]:
                return [c.name for c in sorted(table_tuning.get_columns_by_type(tuning_type), key=lambda c: c.order)]

            distribution = tuned(TuningType.DISTRIBUTION)
            if distribution:
                layout["distribute_by"] = distribution[:1]
            layout["partition_by"] = tuned(TuningType.PARTITIONING)
            layout["sort_by"] = tuned(TuningType.SORTING)
        return layout

    def _resolve_table_tuning(self, table_name: str | None, table_tunings: dict[str, Any] | None):
        if not table_tunings or table_name is None:
            return None, None

        table_tuning = None
        for configured_name, configured_tuning in table_tunings.items():
            if str(configured_name).upper() == table_name.upper():
                table_tuning = configured_tuning
                break

        if table_tuning is None or not table_tuning.has_any_tuning():
            return None, None

        from benchbox.core.tuning.ddl_generator import get_ddl_generator

        clauses = get_ddl_generator("starrocks").generate_tuning_clauses(table_tuning)
        if clauses.is_empty():
            return None, None
        return table_tuning, clauses

    def _record_starrocks_tuning_to_ledger(self, table_name: str | None, clauses: list[str]) -> None:
        ledger = getattr(self, "_applied_tuning_ledger", None)
        if ledger is None or not clauses:
            return
        try:
            from benchbox.core.tuning.applied_ledger import PHASE_DDL

            for clause in clauses:
                ledger.record(clause, PHASE_DDL, mechanism="starrocks_ddl_generator", table=table_name)
        except Exception as exc:
            self.logger.debug("StarRocks applied-ledger record degraded: %s", exc)

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        from benchbox.platforms.base.data_loading import DataLoader

        def starrocks_handler_factory(file_path, adapter, benchmark_instance, table_name=None, data_source=None):
            base_ext = FileFormatRegistry.get_base_data_extension(file_path)

            if base_ext in (".tbl", ".dat", ".csv"):
                dialect_source = data_source or DataSource(source_type="starrocks_handler", tables={})
                dialect = resolve_csv_dialect(
                    dialect_source, table_name or file_path.stem, file_path, benchmark_instance
                )
                null_empty = _stream_load_treats_empty_as_null(dialect.delimiter, dialect.null_marker)
                return StarRocksStreamLoadHandler(
                    delimiter=dialect.delimiter,
                    host=self.host,
                    http_port=self.http_port,
                    database=self.database,
                    username=self.username,
                    password=self.password,
                    null_empty_strings=null_empty,
                    has_header=dialect.has_header,
                )
            elif base_ext == ".parquet":
                return StarRocksParquetHandler()
            return None

        cursor = connection.cursor()
        try:
            cursor.execute("SET query_timeout = 86400")
            cursor.execute("SET SESSION net_read_timeout = 86400")
            cursor.execute("SET SESSION net_write_timeout = 86400")
            cursor.execute("SET SESSION max_allowed_packet = 67108864")
        except Exception:
            pass
        finally:
            cursor.close()

        loader = DataLoader(
            adapter=self,
            benchmark=benchmark,
            connection=connection,
            data_dir=data_dir,
            handler_factory=starrocks_handler_factory,
            tuning_config=self.unified_tuning_configuration if self.tuning_enabled else None,
        )
        table_stats, loading_time = loader.load()
        return table_stats, loading_time, None

    def _get_existing_tables(self, connection) -> list[str]:
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SHOW TABLES")
                tables = [row[0].lower() for row in cursor.fetchall()]
                return tables
            finally:
                cursor.close()
        except Exception as e:
            self.logger.debug(f"Failed to get existing tables: {e}")
            return []

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
        query = _alias_derived_tables(query)
        result = execute_sql_query(
            connection,
            query,
            query_id,
            log_verbose=self.log_verbose,
            build_query_result_with_validation=self._build_query_result_with_validation,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )
        result.setdefault("translated_query", None)
        return result


class StarRocksStreamLoadHandler(FileFormatHandler):
    def __init__(
        self,
        delimiter: str,
        host: str,
        http_port: int,
        database: str,
        username: str,
        password: str,
        *,
        null_empty_strings: bool = True,
        has_header: bool = False,
    ) -> None:
        self.delimiter = delimiter
        self.host = host
        self.http_port = http_port
        self.database = database
        self.username = username
        self.password = password
        self.null_empty_strings = null_empty_strings
        self.has_header = has_header

    def get_delimiter(self) -> str:
        return self.delimiter

    def load_table(
        self,
        table_name: str,
        file_path: Path,
        connection: Any,
        benchmark: Any,
        logger: Any,
    ) -> int:
        import base64
        import json as _json
        import time as _time

        import requests as _requests

        validate_sql_identifier(table_name, "table name")

        url = f"http://{self.host}:{self.http_port}/api/{self.database}/{table_name}/_stream_load"
        creds = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        headers = {
            "Authorization": f"Basic {creds}",
            "Expect": "100-continue",
            "column_separator": self.delimiter,
            "format": "CSV",
            "max_filter_ratio": "0",
            "strict_mode": "false",
        }
        if self.null_empty_strings:
            headers["null_format"] = "\\N"

        compression_handler = FileFormatRegistry.get_compression_handler(file_path)

        def _data_stream():
            with compression_handler.open(file_path) as f:
                reader = csv.reader(f, delimiter=self.delimiter)
                if self.has_header:
                    next(reader, None)
                for row in reader:
                    line = _stream_load_row(
                        row,
                        null_empty=self.null_empty_strings,
                        delimiter=self.delimiter,
                    )
                    yield (line + "\n").encode("utf-8")

        max_attempts = 3
        last_err: Exception | None = None
        resp: Any = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = _requests.put(url, headers=headers, data=_data_stream(), timeout=7200)
                if resp.status_code in (200, 307):
                    try:
                        body = _json.loads(resp.text)
                        msg = body.get("Message", "")
                        if ":0" in msg and attempt < max_attempts:
                            logger.warning(
                                "STREAM LOAD BE unavailable for %s (attempt %d/%d, msg=%r); retrying",
                                table_name,
                                attempt,
                                max_attempts,
                                msg,
                            )
                            _time.sleep(10 * attempt)
                            continue
                    except Exception:
                        pass
                    break
                if 500 <= resp.status_code < 600 and attempt < max_attempts:
                    logger.warning(
                        "STREAM LOAD HTTP %s for %s (attempt %d/%d); retrying",
                        resp.status_code,
                        table_name,
                        attempt,
                        max_attempts,
                    )
                    _time.sleep(5 * attempt)
                    continue
                raise RuntimeError(f"STREAM LOAD HTTP {resp.status_code} for {table_name}: {resp.text[:500]}")
            except _requests.ConnectionError as e:
                last_err = e
                if attempt < max_attempts:
                    logger.warning(
                        "STREAM LOAD connection error for %s (attempt %d/%d): %s; retrying",
                        table_name,
                        attempt,
                        max_attempts,
                        e,
                    )
                    _time.sleep(5 * attempt)
                    continue
                raise RuntimeError(f"STREAM LOAD connection error for {table_name}: {e}") from e
        else:
            raise RuntimeError(f"STREAM LOAD exhausted retries for {table_name}: {last_err}")

        result = _json.loads(resp.text)
        status = result.get("Status", "")
        if status not in ("Success", "Publish Timeout"):
            raise RuntimeError(f"STREAM LOAD failed for {table_name}: {result.get('Message', result)}")

        return int(result.get("NumberLoadedRows", 0))


class StarRocksCSVHandler(FileFormatHandler):
    def __init__(self, delimiter: str, *, null_empty_strings: bool = True, has_header: bool = False):
        self.delimiter = delimiter
        self.null_empty_strings = null_empty_strings
        self.has_header = has_header

    def get_delimiter(self) -> str:
        return self.delimiter

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validate_sql_identifier(table_name, "table name")

        row_count = 0
        batch_size = 9_999
        compression_handler = FileFormatRegistry.get_compression_handler(file_path)
        sql: str | None = None

        cursor = connection.cursor()
        try:
            with compression_handler.open(file_path) as f:
                reader = csv.reader(f, delimiter=self.delimiter)
                if self.has_header:
                    next(reader, None)
                batch: list[list] = []

                for row in reader:
                    if self.null_empty_strings:
                        batch.append(
                            [
                                None if cell == "" or cell.lower() in ("nan", "inf", "-inf", "+inf") else cell
                                for cell in row
                            ]
                        )
                    else:
                        batch.append(list(row))
                    row_count += 1

                    if sql is None:
                        placeholders = ", ".join(["%s"] * len(row))
                        sql = f"INSERT INTO `{table_name}` VALUES ({placeholders})"

                    if len(batch) >= batch_size:
                        cursor.executemany(sql, batch)
                        batch = []

                if batch and sql:
                    cursor.executemany(sql, batch)

        except Exception as e:
            logger.error(f"Failed to load data from {file_path} into {table_name}: {e}")
            raise
        finally:
            cursor.close()

        return row_count


class StarRocksParquetHandler(FileFormatHandler):
    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validate_sql_identifier(table_name, "table name")

        try:
            import pyarrow.parquet as pq
        except ImportError as e:
            raise RuntimeError("pyarrow is required to load Parquet files") from e

        pf = pq.ParquetFile(file_path)
        if pf.metadata.num_rows == 0:
            return 0

        schema = pf.schema_arrow
        column_names = schema.names
        validated_cols = [validate_sql_identifier(c, "column name") for c in column_names]
        placeholders = ", ".join(["%s"] * len(validated_cols))
        columns_str = ", ".join(f"`{c}`" for c in validated_cols)
        insert_sql = f"INSERT INTO `{table_name}` ({columns_str}) VALUES ({placeholders})"

        num_cols = len(column_names)
        if num_cols <= 30:
            batch_size = 10_000
        elif num_cols <= 100:
            batch_size = 2_000
        else:
            batch_size = 100

        row_count = 0
        cursor = connection.cursor()
        try:
            for batch in pf.iter_batches(batch_size=batch_size):
                rows = [tuple(row[col] for col in column_names) for row in batch.to_pylist()]
                if rows:
                    cursor.executemany(insert_sql, rows)
                    row_count += len(rows)
        except Exception as e:
            logger.error(f"Failed to load Parquet data from {file_path} into {table_name}: {e}")
            raise
        finally:
            cursor.close()

        return row_count


__all__ = ["StarRocksWorkloadMixin", "StarRocksCSVHandler", "StarRocksParquetHandler"]
