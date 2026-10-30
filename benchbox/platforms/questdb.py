# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import csv
import re
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.dialect_utils import translate_ddl

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        ForeignKeyConfiguration,
        PlatformOptimizationConfiguration,
        PrimaryKeyConfiguration,
    )

from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from ..utils.file_format import get_data_extension
from .base import DriverIsolationCapability, PlatformAdapter, PsycopgConnectionMixin
from .base.data_loading import (
    CsvDialect,
    DataSourceResolver,
    normalize_table_paths,
    prepare_local_load_file,
    resolve_csv_dialect,
)
from .base.sql_execution import execute_sql_query
from .questdb_rewrites import rewrite_query

QUESTDB_DIALECT = "questdb"

QUESTDB_TYPE_MAP: dict[str, str] = {"TIME": "VARCHAR", "DECIMAL": "DOUBLE"}
QUESTDB_PARAMETERLESS_TYPES: tuple[str, ...] = ("VARCHAR", "DOUBLE")

try:
    import psycopg
except ImportError:
    psycopg = None

TPCH_TIMESTAMP_COLUMNS: dict[str, str] = {
    "lineitem": "l_shipdate",
    "orders": "o_orderdate",
    "partsupp": None,
    "part": None,
    "supplier": None,
    "customer": None,
    "nation": None,
    "region": None,
}

TPCH_SYMBOL_COLUMNS: dict[str, list[str]] = {
    "lineitem": ["l_returnflag", "l_linestatus", "l_shipinstruct", "l_shipmode"],
    "orders": ["o_orderstatus", "o_orderpriority"],
    "part": ["p_brand", "p_type", "p_container", "p_mfgr"],
    "supplier": [],
    "partsupp": [],
    "customer": ["c_mktsegment"],
    "nation": ["n_name"],
    "region": ["r_name"],
}

TPCH_PARTITION_DEFAULTS: dict[str, str] = {
    "lineitem": "MONTH",
    "orders": "MONTH",
}

TPCH_DATE_COLUMNS: dict[str, list[str]] = {
    "lineitem": ["l_shipdate", "l_commitdate", "l_receiptdate"],
    "orders": ["o_orderdate"],
}


class QuestDBAdapter(PsycopgConnectionMixin, PlatformAdapter):
    plan_capture_phase_eligible = True
    default_service_port = 8812

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    _max_identifier_length = 127

    @property
    def platform_name(self) -> str:
        return "QuestDB"

    def get_target_dialect(self) -> str:
        return QUESTDB_DIALECT

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        try:
            parser.add_argument(
                "--questdb-host",
                dest="host",
                default="localhost",
                help="QuestDB server hostname",
            )
            parser.add_argument(
                "--questdb-pg-port",
                dest="pg_port",
                type=int,
                default=8812,
                help="QuestDB PostgreSQL wire protocol port",
            )
            parser.add_argument(
                "--questdb-http-port",
                dest="http_port",
                type=int,
                default=9000,
                help="QuestDB REST API HTTP port",
            )
            parser.add_argument(
                "--questdb-ilp-port",
                dest="ilp_port",
                type=int,
                default=9009,
                help="QuestDB ILP (InfluxDB Line Protocol) port",
            )
            parser.add_argument(
                "--questdb-username",
                dest="username",
                default="admin",
                help="QuestDB username",
            )
            parser.add_argument(
                "--questdb-password",
                dest="password",
                default="quest",
                help="QuestDB password",
            )
            parser.add_argument(
                "--questdb-database",
                dest="database",
                default="qdb",
                help="QuestDB database name",
            )
            parser.add_argument(
                "--questdb-use-tls",
                dest="use_tls",
                action="store_true",
                default=False,
                help="Use HTTPS for REST API endpoints (default: HTTP)",
            )
            parser.add_argument(
                "--questdb-loading-method",
                dest="loading_method",
                choices=["rest", "ilp"],
                default="rest",
                help="Data loading method: 'rest' (CSV import, default) or 'ilp' (InfluxDB Line Protocol)",
            )
            parser.add_argument(
                "--questdb-partition-by",
                dest="partition_by",
                choices=["DAY", "MONTH", "YEAR", "NONE"],
                default=None,
                help="Partition granularity for time-series tables (default: auto per table)",
            )
        except Exception:
            pass

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> QuestDBAdapter:
        adapter_config = {}

        adapter_config["host"] = config.get("host", "localhost")
        adapter_config["pg_port"] = config.get("pg_port", config.get("port", 8812))
        adapter_config["http_port"] = config.get("http_port", 9000)
        adapter_config["ilp_port"] = config.get("ilp_port", 9009)
        adapter_config["ilp_host"] = config.get("ilp_host", config.get("host", "localhost"))
        adapter_config["username"] = config.get("username", "admin")
        adapter_config["password"] = config.get("password", "quest")
        adapter_config["database"] = config.get("database", "qdb")

        adapter_config["connect_timeout"] = config.get("connect_timeout", 10)
        adapter_config["use_tls"] = config.get("use_tls", False)

        adapter_config["loading_method"] = config.get("loading_method", "rest")
        adapter_config["partition_by"] = config.get("partition_by")
        adapter_config["parquet_chunk_rows"] = config.get("parquet_chunk_rows", 200_000)

        adapter_config["force_recreate"] = config.get("force", False)

        for key in [
            "tuning_config",
            "tuning_enabled",
            "unified_tuning_configuration",
            "tuning_source",
            "tuning_source_file",
            "verbose_enabled",
            "very_verbose",
            "capture_plans",
            "show_query_plans",
            "enable_validation",
        ]:
            if key in config:
                adapter_config[key] = config[key]

        return cls(**adapter_config)

    def __init__(self, **config):
        super().__init__(**config)

        if psycopg is None:
            available, missing = check_platform_dependencies("questdb", packages=["psycopg"])
            if not available:
                error_msg = get_dependency_error_message("questdb", missing)
                raise ImportError(error_msg)

        self._dialect = QUESTDB_DIALECT

        self.host = config.get("host", "localhost")
        self.pg_port = config.get("pg_port", 8812)
        self.http_port = config.get("http_port", 9000)
        self.ilp_port = config.get("ilp_port", 9009)
        self.ilp_host = config.get("ilp_host", config.get("host", "localhost"))
        self.database = config.get("database", "qdb")
        self.username = config.get("username", "admin")
        self.password = config.get("password", "quest")

        self.connect_timeout = config.get("connect_timeout", 10)
        self.use_tls = config.get("use_tls", False)

        self.loading_method = config.get("loading_method", "rest")
        self.partition_by = config.get("partition_by")
        self.parquet_chunk_rows = int(config.get("parquet_chunk_rows", 200_000))

        self.skip_database_management = True

    def _get_connection_params(self) -> dict[str, Any]:
        params = {
            "host": self.host,
            "port": self.pg_port,
            "dbname": self.database,
            "user": self.username,
            "connect_timeout": self.connect_timeout,
        }

        if self.password:
            params["password"] = self.password

        return {k: v for k, v in params.items() if v is not None}

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("QuestDB connection")

        self.handle_existing_database(**connection_config)

        params = self._get_connection_params()
        conn = psycopg.connect(**params)

        conn.autocommit = True

        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()

        self.log_operation_complete(
            "QuestDB connection",
            details=f"Connected to {self.host}:{self.pg_port}",
        )
        return conn

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        del connection
        params = self._get_connection_params()
        conn = psycopg.connect(**params)
        try:
            conn.autocommit = True
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            finally:
                cursor.close()
            if benchmark_type is not None:
                self.configure_for_benchmark(conn, benchmark_type)
            return conn
        except Exception:
            conn.close()
            raise

    def check_benchmark_tables_exist(self, **connection_config) -> bool | None:
        if psycopg is None:
            msg = "psycopg is required for QuestDB but is not installed"
            raise ImportError(msg)

        if self.force_recreate:
            self.log_verbose("Force recreate enabled - will recreate schema and reload data")
            return False

        try:
            params = self._get_connection_params()
            test_conn = psycopg.connect(**params)
            test_conn.autocommit = True

            try:
                benchmark = getattr(self, "benchmark", None) or getattr(self, "benchmark_instance", None)
                if benchmark is None:
                    self.log_verbose("Benchmark not available - treating as fresh database")
                    return False

                expected_tables = self._get_expected_tables(benchmark)
                if not expected_tables:
                    self.log_verbose("Benchmark has no tables - treating as fresh database")
                    return False
                expected_tables = set(expected_tables)

                with test_conn.cursor() as cursor:
                    cursor.execute("SELECT table_name FROM tables()")
                    existing_tables = {str(row[0]).lower() for row in cursor.fetchall()}

                missing_tables = expected_tables - existing_tables
                if missing_tables:
                    self.log_verbose(
                        f"Expected benchmark tables not found: {', '.join(sorted(missing_tables))} "
                        "- treating as fresh database (will create schema)"
                    )
                    return False

                empty_tables = []
                with test_conn.cursor() as row_cursor:
                    for tname in sorted(expected_tables):
                        if not self._validate_identifier(tname.lower()):
                            continue
                        row_cursor.execute(f'SELECT 1 FROM "{tname}" LIMIT 1')
                        if row_cursor.fetchone() is None:
                            empty_tables.append(tname)

                if empty_tables:
                    self.log_verbose(
                        f"Tables exist but are empty: {', '.join(empty_tables)} "
                        "- treating as fresh database (will reload data)"
                    )
                    return False

                self.log_verbose(
                    f"Found all {len(expected_tables)} expected benchmark tables with data - "
                    "attempting to reuse existing database"
                )
                return True

            finally:
                test_conn.close()

        except (psycopg.Error, OSError) as e:
            self.logger.debug(f"Error checking existing tables: {e}")
            self.log_verbose("Unable to verify existing tables - treating as fresh database")
            return False

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        self.log_operation_start("Schema creation", f"benchmark: {benchmark.__class__.__name__}")

        schema_sql = self._create_schema_with_tuning(benchmark, source_dialect=QUESTDB_DIALECT)

        self.log_very_verbose(f"Executing schema creation script ({len(schema_sql)} characters)")

        cursor = connection.cursor()
        critical_failures = []
        try:
            if not self.is_dry_run and not getattr(self, "database_was_reused", False):
                benchmark_tables = getattr(benchmark, "tables", None)
                if isinstance(benchmark_tables, dict):
                    droppable = [
                        name
                        for name in benchmark_tables
                        if isinstance(name, str) and self._validate_identifier(name.lower())
                    ]
                    if droppable:
                        self.log_notice(
                            f"Pre-dropping {len(droppable)} benchmark table(s) before schema creation: "
                            f"{', '.join(droppable)}"
                        )
                    for table_name in droppable:
                        try:
                            self.log_notice(f'Dropping table if it exists: "{table_name}"')
                            cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                        except Exception as drop_err:
                            self.logger.warning(f"Pre-create DROP TABLE {table_name} skipped: {drop_err}")

            statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
            for stmt in statements:
                stmt_upper = stmt.upper()

                if "ALTER" in stmt_upper and "FOREIGN KEY" in stmt_upper:
                    self.log_verbose("Skipping ALTER TABLE foreign key constraint (unsupported by QuestDB)")
                    continue

                if "CREATE TABLE" in stmt_upper:
                    if "FOREIGN KEY" in stmt_upper or "REFERENCES" in stmt_upper:
                        self.log_verbose("Stripping foreign key constraints from CREATE TABLE (unsupported by QuestDB)")
                    if "PRIMARY KEY" in stmt_upper:
                        self.log_verbose("Stripping primary key constraints from CREATE TABLE (unsupported by QuestDB)")
                    to_run = translate_ddl(
                        stmt,
                        QUESTDB_DIALECT,
                        strip_primary_keys=True,
                        strip_foreign_keys=True,
                        type_map=QUESTDB_TYPE_MAP,
                        drop_type_parameters=QUESTDB_PARAMETERLESS_TYPES,
                        default_layout=self._questdb_table_layout(stmt),
                    )
                elif "DROP TABLE" in stmt_upper:
                    to_run = [self._adapt_drop_table(stmt)]
                else:
                    to_run = [stmt]

                for statement in to_run:
                    try:
                        cursor.execute(statement)
                    except Exception as e:
                        if statement.strip().upper().startswith("CREATE TABLE"):
                            critical_failures.append((statement[:80], str(e)))
                        self.logger.warning(f"Schema statement failed: {e}")
        finally:
            cursor.close()

        if critical_failures:
            failed_summary = "; ".join(f"{s}: {err}" for s, err in critical_failures)
            raise RuntimeError(f"{len(critical_failures)} critical CREATE TABLE statement(s) failed: {failed_summary}")

        duration = elapsed_seconds(start_time)
        self.log_operation_complete("Schema creation", duration, "Schema and tables created")
        return duration

    def _questdb_table_layout(self, stmt: str) -> dict[str, Any]:
        table_name = self._extract_table_name(stmt)
        if not table_name:
            return {}
        table_name_lower = table_name.lower()
        designated = TPCH_TIMESTAMP_COLUMNS.get(table_name_lower)
        if designated and not re.search(rf"\b{re.escape(designated)}\b", stmt, re.IGNORECASE):
            designated = None
        return {
            "symbol_columns": TPCH_SYMBOL_COLUMNS.get(table_name_lower, []),
            "timestamp_columns": TPCH_DATE_COLUMNS.get(table_name_lower, []),
            "designated_timestamp": designated,
            "time_partition": self._get_partition_for_table(table_name_lower) if designated else None,
        }

    def _extract_table_name(self, stmt: str) -> str | None:
        match = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?", stmt, re.IGNORECASE)
        return match.group(1) if match else None

    def _get_partition_for_table(self, table_name_lower: str) -> str:
        if self.partition_by is not None:
            return self.partition_by
        return TPCH_PARTITION_DEFAULTS.get(table_name_lower, "NONE")

    def _adapt_drop_table(self, stmt: str) -> str:
        stmt_upper = stmt.upper().strip()
        if "DROP TABLE" in stmt_upper and "IF EXISTS" not in stmt_upper:
            stmt = re.sub(r"(?i)(DROP\s+TABLE)\s+", r"\1 IF EXISTS ", stmt, count=1)
        return stmt

    def load_data(
        self,
        benchmark,
        connection: Any,
        data_dir: Path,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = mono_time()
        table_stats = {}

        method = self.loading_method
        self.log_operation_start("Data loading", f"source: {data_dir}, method: {method}")

        resolver = DataSourceResolver(
            platform_name=self.platform_name,
            table_mode=self.table_mode,
            platform_config=self.platform_config,
            requested_format=self.requested_table_format,
        )
        data_source = resolver.resolve(benchmark, data_dir)
        if not data_source or not data_source.tables:
            self.logger.warning("No data files found. Ensure benchmark.generate_data() was called first.")
            loading_time = elapsed_seconds(start_time)
            self.log_operation_complete("Data loading", loading_time, "Loaded 0 total rows")
            return {}, loading_time, None

        for table_name, table_path in data_source.tables.items():
            table_name_lower = table_name.lower()

            if not self._validate_identifier(table_name_lower):
                self.logger.warning(f"Skipping table with invalid identifier: {table_name}")
                table_stats[table_name_lower] = 0
                continue

            data_files = [f for f in normalize_table_paths(table_path) if f.exists()]
            if not data_files:
                self.logger.warning(f"Data file(s) not found for table: {table_name}")
                table_stats[table_name_lower] = 0
                continue

            total_rows = 0
            for data_file in data_files:
                try:
                    dialect = resolve_csv_dialect(data_source, table_name_lower, data_file, benchmark)
                    if method == "ilp":
                        rows = self._load_table_via_ilp(table_name_lower, data_file, dialect)
                        source = "ILP"
                    elif get_data_extension(data_file) == ".parquet":
                        rows = self._load_parquet_via_chunked_csv(table_name_lower, data_file)
                        source = "REST/parquet-csv"
                    else:
                        rows = self._load_table_via_rest_api(table_name_lower, data_file, dialect)
                        source = "REST"
                except Exception as e:
                    self.logger.error(f"Failed to load {table_name_lower} chunk {data_file.name}: {e}")
                    continue
                total_rows += rows
                self.log_verbose(f"Loaded {rows:,} rows into {table_name_lower} from {data_file.name} (via {source})")

            table_stats[table_name_lower] = total_rows

        loading_time = elapsed_seconds(start_time)

        total_rows = sum(table_stats.values())
        self.log_operation_complete("Data loading", loading_time, f"Loaded {total_rows:,} total rows")

        self._populate_transactional_staging_tables(benchmark, connection)

        return table_stats, loading_time, None

    def _populate_transactional_staging_tables(self, benchmark: Any, connection: Any) -> None:
        staging_tables = getattr(benchmark, "_staging_tables", None)
        if not isinstance(staging_tables, dict) or not callable(getattr(benchmark, "_populate_staging_table", None)):
            return

        staging_source: dict[str, str] = {
            "txn_orders": "orders",
            "txn_lineitem": "lineitem",
            "txn_customer": "customer",
        }

        for staging_name, source_name in staging_source.items():
            if staging_name not in staging_tables:
                continue
            try:
                result = connection.execute(f"SELECT COUNT(*) FROM {staging_name}").fetchone()
                if result and result[0] > 0:
                    self.log_verbose(f"{staging_name} already populated ({result[0]:,} rows), skipping")
                    continue
            except Exception:
                pass
            try:
                self.log_verbose(f"Populating {staging_name} from {source_name}...")
                benchmark._populate_staging_table(connection, staging_name, source_name)
                count_result = connection.execute(f"SELECT COUNT(*) FROM {staging_name}").fetchone()
                count = count_result[0] if count_result else 0
                self.log_verbose(f"Populated {staging_name} ({count:,} rows)")
            except Exception as e:
                self.logger.warning(f"Failed to populate staging table {staging_name}: {e}")

    def _load_table_via_rest_api(self, table_name: str, data_file: Path, dialect: CsvDialect) -> int:
        import requests

        url = f"{'https' if self.use_tls else 'http'}://{self.host}:{self.http_port}/imp"

        params = {
            "name": table_name,
            "overwrite": "false",
            "durable": "true",
            "delimiter": dialect.delimiter,
        }

        strip_trailing_delim = get_data_extension(data_file) in (".tbl", ".dat")
        with prepare_local_load_file(
            data_file, dialect=dialect, strip_trailing_delim=strip_trailing_delim
        ) as load_path:
            with open(load_path, "rb") as upload_stream:
                files = {"data": (f"{table_name}.csv", upload_stream, "text/csv")}
                response = requests.post(url, params=params, files=files, timeout=300)
        response.raise_for_status()

        match = re.search(r"Rows imported\s*\|\s*(\d+)", response.text)
        if match:
            return int(match.group(1))

        return self._count_table_rows_via_http(table_name)

    def _load_parquet_via_chunked_csv(self, table_name: str, data_file: Path) -> int:
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ImportError(
                "pyarrow is required to load parquet files into QuestDB. Install it with: uv add pyarrow"
            ) from exc

        import io

        import requests

        url = f"{'https' if self.use_tls else 'http'}://{self.host}:{self.http_port}/imp"
        total_rows = 0

        pf = pq.ParquetFile(data_file)
        for batch in pf.iter_batches(batch_size=self.parquet_chunk_rows):
            csv_buf = io.BytesIO()
            batch.to_pandas().to_csv(csv_buf, index=False, header=True)
            csv_buf.seek(0)

            params = {
                "name": table_name,
                "overwrite": "false",
                "durable": "true",
                "delimiter": ",",
                "forceHeader": "true",
            }
            files = {"data": (f"{table_name}.csv", csv_buf, "text/csv")}
            response = requests.post(url, params=params, files=files, timeout=600)
            response.raise_for_status()

            match = re.search(r"Rows imported\s*\|\s*(\d+)", response.text)
            chunk_rows = int(match.group(1)) if match else len(batch)
            total_rows += chunk_rows
            self.log_verbose(f"Loaded chunk of {chunk_rows:,} rows into {table_name} (parquet → CSV)")

        return total_rows

    def _load_table_via_ilp(self, table_name: str, data_file: Path, dialect: CsvDialect) -> int:
        rows_sent = 0

        column_names = self._get_table_columns(table_name)
        if not column_names:
            raise RuntimeError(f"Cannot determine column names for table '{table_name}' (needed for ILP)")

        ts_col = TPCH_TIMESTAMP_COLUMNS.get(table_name.lower())

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        try:
            sock.connect((self.ilp_host, self.ilp_port))

            strip_trailing_delim = get_data_extension(data_file) in (".tbl", ".dat")
            with prepare_local_load_file(
                data_file, dialect=dialect, strip_trailing_delim=strip_trailing_delim
            ) as load_path:
                with open(load_path, encoding="utf-8") as stream:
                    reader = csv.reader(stream, delimiter=dialect.delimiter)
                    batch: list[str] = []
                    for row in reader:
                        if len(row) != len(column_names):
                            continue
                        line = self._row_to_ilp_line(table_name, column_names, row, ts_col)
                        if line:
                            batch.append(line)
                            rows_sent += 1

                        if len(batch) >= 1000:
                            sock.sendall(("\n".join(batch) + "\n").encode("utf-8"))
                            batch = []

                    if batch:
                        sock.sendall(("\n".join(batch) + "\n").encode("utf-8"))
        finally:
            sock.close()

        return rows_sent

    def _get_table_columns(self, table_name: str) -> list[str]:
        import requests

        if not self._validate_identifier(table_name):
            return []

        url = f"{'https' if self.use_tls else 'http'}://{self.host}:{self.http_port}/exec"
        params = {"query": f'SHOW COLUMNS FROM "{table_name}"'}

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            result = response.json()
            dataset = result.get("dataset", [])
            return [row[0] for row in dataset if row]
        except Exception:
            return []

    @staticmethod
    def _row_to_ilp_line(
        measurement: str,
        column_names: list[str],
        values: list[str],
        ts_column: str | None,
    ) -> str | None:
        fields: list[str] = []
        timestamp_ns: str | None = None

        for col_name, val in zip(column_names, values):
            val = val.strip()
            if not val:
                continue

            if ts_column and col_name == ts_column:
                timestamp_ns = _date_to_epoch_ns(val)
                continue

            escaped = _ilp_escape_field(col_name, val)
            if escaped:
                fields.append(escaped)

        if not fields:
            return None

        line = f"{_ilp_escape_measurement(measurement)} {','.join(fields)}"
        if timestamp_ns:
            line += f" {timestamp_ns}"
        return line

    def _count_table_rows_via_http(self, table_name: str) -> int:
        import requests

        if not self._validate_identifier(table_name):
            return 0

        url = f"{'https' if self.use_tls else 'http'}://{self.host}:{self.http_port}/exec"
        params = {"query": f'SELECT count() FROM "{table_name}"'}

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            result = response.json()
            dataset = result.get("dataset", [])
            if dataset and len(dataset) > 0:
                return int(dataset[0][0])
        except Exception:
            pass
        return 0

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self.log_verbose(f"Configuring QuestDB for {benchmark_type} benchmark")

        cursor = connection.cursor()
        try:
            if benchmark_type in ("olap", "tpch", "tpcds"):
                try:
                    cursor.execute("SET cairo.sql.parallel.filter.enabled = true")
                except Exception as e:
                    self.logger.debug(f"Could not set parallel filter: {e}")

                try:
                    cursor.execute("SET cairo.page.frame.max.rows = 1000000")
                except Exception as e:
                    self.logger.debug(f"Could not set page frame max rows: {e}")

            elif benchmark_type == "timeseries":
                try:
                    cursor.execute("SET cairo.sql.parallel.filter.enabled = true")
                except Exception as e:
                    self.logger.debug(f"Could not set parallel filter: {e}")
        finally:
            cursor.close()

    def apply_platform_optimizations(
        self,
        platform_config: PlatformOptimizationConfiguration,
        connection: Any,
    ) -> None:
        self.log_verbose("Applying QuestDB platform optimizations")

        cursor = connection.cursor()
        try:
            try:
                cursor.execute("SET cairo.sql.parallel.filter.enabled = true")
            except Exception as e:
                self.logger.debug(f"Could not set parallel filter: {e}")

            try:
                cursor.execute("SET cairo.sql.jit.mode = on")
            except Exception as e:
                self.logger.debug(f"Could not set JIT mode: {e}")

            try:
                cursor.execute("SET cairo.page.frame.max.rows = 1000000")
            except Exception as e:
                self.logger.debug(f"Could not set page frame max rows: {e}")
        finally:
            cursor.close()

    def apply_constraint_configuration(
        self,
        primary_key_config: PrimaryKeyConfiguration,
        foreign_key_config: ForeignKeyConfiguration,
        connection: Any,
    ) -> None:
        pass

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
        result = execute_sql_query(
            connection,
            rewrite_query(query),
            query_id,
            log_verbose=self.log_verbose,
            build_query_result_with_validation=self._build_query_result_with_validation,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )

        self._merge_plan_capture_into_result(result, connection, query, query_id)

        return result

    def get_query_plan(
        self,
        connection: Any,
        query: str,
        explain_options: dict[str, Any] | None = None,
    ) -> str | None:
        _owns_cursor = callable(getattr(connection, "cursor", None))
        cursor = connection.cursor() if _owns_cursor else connection

        explain_query = f"EXPLAIN {query}"

        try:
            cursor.execute(explain_query)
            plan_rows = cursor.fetchall()
            if _owns_cursor:
                cursor.close()

            return "\n".join(str(row[0]) for row in plan_rows)

        except Exception as e:
            if _owns_cursor:
                cursor.close()
            self.logger.warning(f"Failed to get query plan: {e}")
            return None

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.questdb import QuestDBQueryPlanParser

        return QuestDBQueryPlanParser()

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "questdb",
            "platform_name": "QuestDB",
            "host": self.host,
            "pg_port": self.pg_port,
            "http_port": self.http_port,
            "dialect": QUESTDB_DIALECT,
            "configuration": {
                "database": self.database,
                "loading_method": self.loading_method,
                "partition_by": self.partition_by,
                "ilp_port": self.ilp_port,
            },
        }

        if connection:
            try:
                cursor = connection.cursor()

                cursor.execute("SELECT build")
                version_row = cursor.fetchone()
                if version_row:
                    platform_info["version"] = str(version_row[0])

                cursor.close()
            except Exception as e:
                self.logger.debug(f"Could not get QuestDB version: {e}")
                platform_info["version"] = "unknown"

        return platform_info

    def check_database_exists(self, **connection_config) -> bool:
        try:
            params = self._get_connection_params()
            conn = psycopg.connect(**params)
            conn.autocommit = True
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            conn.close()
            return True
        except Exception:
            return False

    def get_database_path(self, **connection_config) -> str | None:
        return None

    def table_exists(self, connection: Any, table_name: str) -> bool:
        if not self._validate_identifier(table_name):
            return False

        try:
            cursor = connection.cursor()
            cursor.execute("SELECT table_name FROM tables() WHERE table_name = %s", (table_name,))
            result = cursor.fetchone()
            cursor.close()
            return result is not None
        except Exception:
            return False

    def _get_existing_tables(self, connection: Any) -> list[str]:
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT table_name FROM tables()")
            rows = cursor.fetchall()
            cursor.close()
            return [row[0].lower() for row in rows]
        except Exception as e:
            self.logger.debug(f"Failed to get existing tables: {e}")
            return []

    def drop_table(self, connection: Any, table_name: str) -> None:
        if not self._validate_identifier(table_name):
            self.logger.warning(f"Invalid table identifier: {table_name}")
            return

        try:
            cursor = connection.cursor()
            self.log_notice(f'Dropping table if it exists: "{table_name}"')
            cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            cursor.close()
        except Exception as e:
            self.logger.warning(f"Failed to drop table {table_name}: {e}")


def _ilp_escape_measurement(measurement: str) -> str:
    return measurement.replace(",", r"\,").replace(" ", r"\ ").replace("=", r"\=")


def _ilp_escape_tag_key(key: str) -> str:
    return key.replace(",", r"\,").replace("=", r"\=").replace(" ", r"\ ")


def _ilp_escape_field(col_name: str, value: str) -> str | None:
    if not value:
        return None

    escaped_key = _ilp_escape_tag_key(col_name)

    try:
        int_val = int(value)
        return f"{escaped_key}={int_val}i"
    except ValueError:
        pass

    try:
        float_val = float(value)
        return f"{escaped_key}={float_val}"
    except ValueError:
        pass

    escaped_val = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{escaped_key}="{escaped_val}"'


def _build_questdb_config(
    platform: str,
    options: dict[str, Any],
    overrides: dict[str, Any],
    info: Any,
) -> Any:
    import os

    from benchbox.core.schemas import DatabaseConfig

    merged: dict[str, Any] = {}
    merged.update(options)
    merged.update(overrides)

    name = info.display_name if info else "QuestDB"
    driver_package = info.driver_package if info else "psycopg"

    config_dict: dict[str, Any] = {
        "type": "questdb",
        "name": name,
        "options": merged,
        "driver_package": driver_package,
        "driver_version": overrides.get("driver_version") or options.get("driver_version"),
        "driver_auto_install": bool(overrides.get("driver_auto_install", options.get("driver_auto_install", False))),
        "host": merged.get("host") or os.environ.get("QUESTDB_HOST", "localhost"),
        "pg_port": int(merged.get("pg_port") or os.environ.get("QUESTDB_PG_PORT", "8812")),
        "http_port": int(merged.get("http_port") or os.environ.get("QUESTDB_HTTP_PORT", "9000")),
        "ilp_port": int(merged.get("ilp_port") or os.environ.get("QUESTDB_ILP_PORT", "9009")),
        "username": merged.get("username") or os.environ.get("QUESTDB_USER", "admin"),
        "password": merged.get("password") or os.environ.get("QUESTDB_PASSWORD", "quest"),
        "database": merged.get("database") or os.environ.get("QUESTDB_DATABASE", "qdb"),
        "loading_method": merged.get("loading_method", "rest"),
    }
    return DatabaseConfig(**config_dict)


def _date_to_epoch_ns(date_str: str) -> str | None:
    from datetime import datetime, timezone

    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
            epoch_s = int(dt.timestamp())
            return str(epoch_s * 1_000_000_000)
        except ValueError:
            continue
    return None
