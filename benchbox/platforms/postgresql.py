# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.platforms.base.ddl_helpers import strip_foreign_keys
from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        ForeignKeyConfiguration,
        PlatformOptimizationConfiguration,
        PrimaryKeyConfiguration,
        TableTuning,
        TuningColumn,
        UnifiedTuningConfiguration,
    )

from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from ..utils.file_format import get_data_extension
from .base import DriverIsolationCapability, PlatformAdapter, PsycopgConnectionMixin
from .base.config_utils import (
    POSTGRES_FAMILY_BASE_OPTIONS,
    POSTGRES_FAMILY_PLATFORM_FIELDS,
    make_platform_config_builder,
)
from .base.data_loading import (
    CsvDialect,
    DataSourceResolver,
    normalize_table_paths,
    prepare_local_load_file,
    resolve_csv_dialect,
)

POSTGRES_DIALECT = "postgres"

try:
    import psycopg
except ImportError:
    psycopg = None


class _PostgresCopySink:
    closed = False

    def __init__(self, copy: Any) -> None:
        self._copy = copy

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        payload = data.to_pybytes() if hasattr(data, "to_pybytes") else data
        self._copy.write(payload)
        return len(payload)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _write_parquet_to_copy(data_file: Path, copy: Any, *, include_header: bool = True) -> None:
    try:
        import pyarrow.csv as arrow_csv
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pyarrow is required to load Parquet files into PostgreSQL-family adapters") from exc

    write_options = arrow_csv.WriteOptions(include_header=include_header, quoting_style="all_valid")
    parquet_file = pq.ParquetFile(data_file)
    with arrow_csv.CSVWriter(_PostgresCopySink(copy), parquet_file.schema_arrow, write_options=write_options) as writer:
        for batch in parquet_file.iter_batches():
            writer.write_batch(batch)


def _postgres_copy_sql(
    qualified_table: str,
    dialect: CsvDialect,
    *,
    force_csv: bool = False,
) -> str:
    escaped_delim = dialect.delimiter.replace("'", "''")
    if dialect.null_marker is not None and not force_csv and dialect.quote is None:
        escaped_null = dialect.null_marker.replace("'", "''")
        return (
            f"COPY {qualified_table} FROM STDIN WITH (FORMAT text, DELIMITER '{escaped_delim}', NULL '{escaped_null}')"
        )

    header_clause = ", HEADER true" if dialect.has_header else ""
    null_marker = dialect.null_marker if dialect.null_marker is not None else "__BENCHBOX_NO_NULL__"
    escaped_null = null_marker.replace("'", "''")
    return (
        f"COPY {qualified_table} FROM STDIN"
        f" WITH (FORMAT csv, DELIMITER '{escaped_delim}', NULL '{escaped_null}'{header_clause})"
    )


def _add_postgres_compatible_arguments(
    parser: Any,
    *,
    prefix: str,
    platform_label: str,
    include_timescale_toggle: bool = False,
) -> None:
    option_prefix = prefix.strip("-")

    parser.add_argument(
        f"--{option_prefix}-host",
        dest="host",
        default="localhost",
        help=f"{platform_label} server hostname",
    )
    parser.add_argument(
        f"--{option_prefix}-port",
        dest="port",
        type=int,
        default=5432,
        help=f"{platform_label} server port",
    )
    parser.add_argument(
        f"--{option_prefix}-database",
        dest="database",
        help=f"{platform_label} database name (auto-generated if not specified)",
    )
    parser.add_argument(
        f"--{option_prefix}-username",
        dest="username",
        default="postgres",
        help=f"{platform_label} username",
    )
    parser.add_argument(
        f"--{option_prefix}-password",
        dest="password",
        help=f"{platform_label} password",
    )
    parser.add_argument(
        f"--{option_prefix}-schema",
        dest="schema",
        default="public",
        help=f"{platform_label} schema name",
    )
    parser.add_argument(
        f"--{option_prefix}-work-mem",
        dest="work_mem",
        default="256MB",
        help=f"{platform_label} work_mem setting for queries",
    )
    parser.add_argument(
        f"--{option_prefix}-maintenance-work-mem",
        dest="maintenance_work_mem",
        default="512MB",
        help=f"{platform_label} maintenance_work_mem for VACUUM/CREATE INDEX",
    )
    if include_timescale_toggle:
        parser.add_argument(
            f"--{option_prefix}-enable-timescale",
            dest="enable_timescale",
            action="store_true",
            help="Enable TimescaleDB extensions if available",
        )


def _build_postgres_connection_kwargs(config: dict[str, Any], *, default_port: int = 5432) -> dict[str, Any]:
    from benchbox.utils.scale_factor import format_benchmark_name

    result: dict[str, Any] = {
        "host": config.get("host", "localhost"),
        "port": config.get("port", default_port),
        "username": config.get("username", "postgres"),
        "password": config.get("password"),
        "schema": config.get("schema", "public"),
        "sslmode": config.get("sslmode", "prefer"),
        "admin_database": config.get("admin_database", "postgres"),
        "work_mem": config.get("work_mem", "256MB"),
        "maintenance_work_mem": config.get("maintenance_work_mem", "512MB"),
        "effective_cache_size": config.get("effective_cache_size", "1GB"),
        "max_parallel_workers_per_gather": config.get("max_parallel_workers_per_gather", 2),
        "connect_timeout": config.get("connect_timeout", 10),
        "statement_timeout": config.get("statement_timeout", 0),
        "force_recreate": config.get("force", False),
    }

    if config.get("database"):
        result["database"] = config["database"]
    elif config.get("benchmark") and config.get("scale_factor") is not None:
        benchmark_name = format_benchmark_name(config["benchmark"], config["scale_factor"])
        result["database"] = f"benchbox_{benchmark_name}".lower().replace("-", "_")
    else:
        result["database"] = "benchbox"

    for key in (
        "tuning_config",
        "tuning_enabled",
        "unified_tuning_configuration",
        "tuning_source",
        "tuning_source_file",
        "verbose_enabled",
        "very_verbose",
    ):
        if key in config:
            result[key] = config[key]

    return result


def ensure_postgres_extension(
    conn: Any,
    logger: Any,
    extension: str,
    install_url: str,
    *,
    cascade: bool = False,
) -> str:
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT extversion FROM pg_extension WHERE extname = '{extension}'")
        result = cursor.fetchone()
        if result:
            logger.info(f"{extension} extension version: {result[0]}")
            conn.commit()
            return str(result[0])

        logger.info(f"{extension} extension not found, attempting to create...")
        cascade_sql = " CASCADE" if cascade else ""
        cursor.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}{cascade_sql}")
        conn.commit()
        cursor.execute(f"SELECT extversion FROM pg_extension WHERE extname = '{extension}'")
        result = cursor.fetchone()
        if result:
            logger.info(f"Created {extension} extension version: {result[0]}")
            conn.commit()
            return str(result[0])

        raise RuntimeError(
            f"{extension} extension is not available on this PostgreSQL server. "
            f"Install it ({install_url}) or use the 'postgresql' platform instead."
        )
    except RuntimeError:
        cursor.close()
        raise
    except Exception as e:
        logger.error(f"Failed to configure {extension} extension: {e}")
        cursor.close()
        raise RuntimeError(f"{extension} configuration failed: {e}") from e
    finally:
        if not cursor.closed:
            cursor.close()


PARTITIONING_WITH_KEYS_REASON = (
    "PostgreSQL requires a primary or unique key on a partitioned table to include the partition column, "
    "so partitioning is skipped while primary or unique keys are requested"
)
_CREATE_TABLE_NAME_RE = re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<table>[^\s(]+)", re.IGNORECASE)
_BENCHMARK_DATE_YEARS = {"tpch": (1992, 1999), "ssb": (1992, 1999), "tpcds": (1998, 2004)}


def _benchmark_date_years(benchmark: Any) -> tuple[int, int] | None:
    name = str(getattr(benchmark, "_name", "") or type(benchmark).__name__).lower()
    key = re.sub(r"[^a-z]", "", name).removesuffix("benchmark")
    return _BENCHMARK_DATE_YEARS.get(key)


class PostgreSQLAdapter(PsycopgConnectionMixin, PlatformAdapter):
    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    plan_capture_phase_eligible = True
    supports_refresh_functions = True
    default_service_port = 5432

    @property
    def platform_name(self) -> str:
        return "PostgreSQL"

    def get_target_dialect(self) -> str:
        return POSTGRES_DIALECT

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        try:
            _add_postgres_compatible_arguments(
                parser,
                prefix="postgres",
                platform_label="PostgreSQL",
                include_timescale_toggle=True,
            )
        except Exception:
            pass

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> PostgreSQLAdapter:
        adapter_config = _build_postgres_connection_kwargs(config)
        adapter_config["enable_timescale"] = config.get("enable_timescale", False)
        return cls(**adapter_config)

    def __init__(self, **config):
        super().__init__(**config)

        if psycopg is None:
            available, missing = check_platform_dependencies("postgresql")
            if not available:
                error_msg = get_dependency_error_message("postgresql", missing)
                raise ImportError(error_msg)

        self._dialect = POSTGRES_DIALECT

        self.host = config.get("host", "localhost")
        self.port = config.get("port", 5432)
        self.database = config.get("database", "benchbox")
        self.username = config.get("username", "postgres")
        self.password = config.get("password")
        self.schema = config.get("schema", "public")
        self.sslmode = config.get("sslmode", "prefer")

        self.admin_database = config.get("admin_database", "postgres")

        self.connect_timeout = config.get("connect_timeout", 10)
        self.statement_timeout = config.get("statement_timeout", 0)

        self.work_mem = config.get("work_mem", "256MB")
        self.maintenance_work_mem = config.get("maintenance_work_mem", "512MB")
        self.effective_cache_size = config.get("effective_cache_size", "1GB")
        self.max_parallel_workers_per_gather = config.get("max_parallel_workers_per_gather", 2)

        self.enable_timescale = config.get("enable_timescale", False)

    def _get_connection_params(self, database: str | None = None) -> dict[str, Any]:
        params = {
            "host": self.host,
            "port": self.port,
            "dbname": database or self.database,
            "user": self.username,
            "connect_timeout": self.connect_timeout,
            "options": f"-c statement_timeout={self.statement_timeout}" if self.statement_timeout else None,
        }

        if self.password:
            params["password"] = self.password

        if self.sslmode:
            params["sslmode"] = self.sslmode

        return {k: v for k, v in params.items() if v is not None}

    def check_server_database_exists(
        self,
        schema: str | None = None,
        catalog: str | None = None,
        database: str | None = None,
        **_: object,
    ) -> bool:
        db_name = database or self.database

        try:
            params = self._get_connection_params(database=self.admin_database)
            conn = psycopg.connect(**params)
            conn.autocommit = True
            cursor = conn.cursor()

            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (db_name,),
            )
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            return result is not None

        except Exception as e:
            self.logger.debug(f"Failed to check database existence: {e}")
            return False

    def drop_database(
        self,
        schema: str | None = None,
        catalog: str | None = None,
        database: str | None = None,
        **_: object,
    ) -> None:
        db_name = database or self.database

        if not self._validate_identifier(db_name):
            raise ValueError(f"Invalid database identifier: {db_name}")

        try:
            params = self._get_connection_params(database=self.admin_database)
            conn = psycopg.connect(**params)
            conn.autocommit = True
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (db_name,),
            )

            cursor.execute(f'DROP DATABASE IF EXISTS "{db_name}"')

            cursor.close()
            conn.close()
            self.logger.info(f"Dropped database: {db_name}")

        except Exception as e:
            self.logger.warning(f"Failed to drop database {db_name}: {e}")
            raise

    def _create_database(self) -> None:
        if not self._validate_identifier(self.database):
            raise ValueError(f"Invalid database identifier: {self.database}")

        try:
            params = self._get_connection_params(database=self.admin_database)
            conn = psycopg.connect(**params)
            conn.autocommit = True
            cursor = conn.cursor()

            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (self.database,),
            )
            if not cursor.fetchone():
                cursor.execute(f'CREATE DATABASE "{self.database}"')
                self.logger.info(f"Created database: {self.database}")

            cursor.close()
            conn.close()

        except Exception as e:
            self.logger.error(f"Failed to create database: {e}")
            raise

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("PostgreSQL connection")

        self.handle_existing_database(**connection_config)

        if not self.check_server_database_exists():
            self._create_database()

        params = self._get_connection_params()
        conn = psycopg.connect(**params)

        cursor = conn.cursor()
        settings_applied = []

        guc_statements = [
            (f"SET work_mem = '{self.work_mem}'", f"work_mem={self.work_mem}"),
            (
                f"SET maintenance_work_mem = '{self.maintenance_work_mem}'",
                f"maintenance_work_mem={self.maintenance_work_mem}",
            ),
            (
                f"SET effective_cache_size = '{self.effective_cache_size}'",
                f"effective_cache_size={self.effective_cache_size}",
            ),
            (
                f"SET max_parallel_workers_per_gather = {self.max_parallel_workers_per_gather}",
                f"max_parallel_workers_per_gather={self.max_parallel_workers_per_gather}",
            ),
        ]
        for sql, label in guc_statements:
            try:
                cursor.execute(sql)
                settings_applied.append(label)
            except Exception as e:
                conn.rollback()
                self.logger.debug(f"Could not set {label}: {e}")

        if self.schema != "public" and self._validate_identifier(self.schema):
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            cursor.execute(f'SET search_path TO "{self.schema}", public')
            settings_applied.append(f"schema={self.schema}")

        conn.commit()
        cursor.close()

        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()

        self.log_operation_complete("PostgreSQL connection", details=f"Applied: {', '.join(settings_applied)}")
        return conn

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        del connection
        params = self._get_connection_params()
        conn = psycopg.connect(**params)

        cursor = None
        try:
            cursor = conn.cursor()
            guc_statements = [
                f"SET work_mem = '{self.work_mem}'",
                f"SET maintenance_work_mem = '{self.maintenance_work_mem}'",
                f"SET effective_cache_size = '{self.effective_cache_size}'",
                f"SET max_parallel_workers_per_gather = {self.max_parallel_workers_per_gather}",
            ]
            for sql in guc_statements:
                try:
                    cursor.execute(sql)
                except Exception as e:
                    conn.rollback()
                    self.logger.debug(f"Could not apply stream session setting ({sql}): {e}")

            if self.schema != "public" and self._validate_identifier(self.schema):
                cursor.execute(f'SET search_path TO "{self.schema}", public')

            self._apply_stream_session_state(conn)

            if benchmark_type is not None:
                self.configure_for_benchmark(conn, benchmark_type)

            conn.commit()
            return conn
        except Exception:
            conn.close()
            raise
        finally:
            if cursor is not None:
                cursor.close()

    def _apply_stream_session_state(self, connection: Any) -> None:
        del connection

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        self.log_operation_start("Schema creation", f"benchmark: {benchmark.__class__.__name__}")

        schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="standard")

        self.log_very_verbose(f"Executing schema creation script ({len(schema_sql)} characters)")

        cursor = connection.cursor()

        statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
        for raw_stmt in statements:
            stmt, partition_children = self.partitioned_create_statements(raw_stmt, benchmark)
            try:
                cursor.execute(self._transform_create_statement(stmt))
            except Exception as e:
                connection.rollback()
                self.logger.warning(f"Schema statement failed: {e}")
                if re.search(r"\bCREATE\s+TABLE\b", stmt, re.IGNORECASE) and "FOREIGN" in stmt.upper():
                    stripped = strip_foreign_keys(stmt)
                    if stripped != stmt:
                        try:
                            cursor.execute(self._transform_create_statement(stripped))
                        except Exception as e2:
                            connection.rollback()
                            self.logger.error(f"Schema statement failed even without FK constraints: {e2}")
                            raise
                    else:
                        raise
                else:
                    raise
            for child in partition_children:
                cursor.execute(child)

        connection.commit()
        cursor.close()

        duration = elapsed_seconds(start_time)
        self.log_operation_complete("Schema creation", duration, "Schema and tables created")
        return duration

    execution_partitioning_supported = True

    def partitioned_create_statements(self, statement: str, benchmark: Any) -> tuple[str, list[str]]:
        match = _CREATE_TABLE_NAME_RE.match(statement)
        config = self.get_effective_tuning_configuration() if self.tuning_enabled else None
        if match is None or config is None or not self.execution_partitioning_supported:
            return statement, []
        table_name = match.group("table").split(".")[-1].strip('"').lower()
        table_tuning = self.table_tuning_for(config, table_name)
        partition_columns = list(getattr(table_tuning, "partitioning", None) or [])
        if not partition_columns:
            return statement, []
        if config.primary_keys.enabled or config.unique_constraints.enabled:
            self._record_partitioning_skipped(table_tuning, partition_columns)
            return statement, []
        from benchbox.core.tuning.ddl_generator import get_ddl_generator
        from benchbox.core.tuning.interface import TableTuning as RuntimeTableTuning

        generator = get_ddl_generator("postgresql")
        layout_only = RuntimeTableTuning(table_name=table_tuning.table_name, partitioning=partition_columns)
        clauses = generator.generate_tuning_clauses(layout_only)
        children = generator.generate_partition_children(
            table_name,
            [],
            clauses,
            table_tuning=layout_only,
            schema=self.schema if self.schema != "public" else None,
            year_range=_benchmark_date_years(benchmark),
        )
        return f"{statement}\n{clauses.partition_by}", children

    def _record_partitioning_skipped(self, table_tuning: TableTuning, partition_columns: list[Any]) -> None:
        ledger = getattr(self, "_applied_tuning_ledger", None)
        if ledger is None:
            return
        columns = ", ".join(column.name for column in sorted(partition_columns, key=lambda column: column.order))
        ledger.record_dropped(f"partitioning:{table_tuning.table_name} ({columns})", PARTITIONING_WITH_KEYS_REASON)

    def _transform_create_statement(self, stmt: str) -> str:
        return stmt

    @staticmethod
    def _group_files_into_copy_sessions(
        data_source: Any,
        table_name: str,
        data_files: list[Path],
        benchmark: Any,
    ) -> list[tuple[CsvDialect, bool, list[tuple[Path, bool]]]]:
        groups: dict[tuple[Any, ...], tuple[CsvDialect, bool, list[tuple[Path, bool]]]] = {}
        order: list[tuple[Any, ...]] = []
        for data_file in data_files:
            extension = get_data_extension(data_file)
            if extension == ".parquet":
                dialect = CsvDialect(
                    delimiter=",",
                    has_header=True,
                    null_marker="",
                    normalize_booleans=False,
                    quote='"',
                )
                key: tuple[Any, ...] = ("parquet",)
                force_csv = True
                strip_trailing = False
            else:
                dialect = resolve_csv_dialect(data_source, table_name, data_file, benchmark)
                force_csv = extension == ".csv" and dialect.has_header
                key = (
                    "text",
                    dialect.delimiter,
                    dialect.null_marker,
                    dialect.has_header,
                    dialect.quote,
                    force_csv,
                )
                strip_trailing = extension == ".tbl"
            if key not in groups:
                groups[key] = (dialect, force_csv, [])
                order.append(key)
            groups[key][2].append((data_file, strip_trailing))
        return [groups[key] for key in order]

    def load_data(
        self,
        benchmark,
        connection: Any,
        data_dir: Path,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = mono_time()
        table_stats = {}

        self.log_operation_start("Data loading", f"source: {data_dir}")
        effective_tuning = self.unified_tuning_configuration if self.tuning_enabled else None

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

        cursor = connection.cursor()

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

            qualified_table = (
                f'"{self.schema}"."{table_name_lower}"' if self.schema != "public" else f'"{table_name_lower}"'
            )

            load_failed = False
            sessions = self._group_files_into_copy_sessions(data_source, table_name, data_files, benchmark)
            for dialect, force_csv, session_files in sessions:
                is_parquet = bool(session_files) and get_data_extension(session_files[0][0]) == ".parquet"
                try:
                    copy_sql = _postgres_copy_sql(qualified_table, dialect, force_csv=force_csv)
                    with cursor.copy(copy_sql) as copy:
                        last_file_index = len(session_files) - 1
                        for index, (data_file, strip_trailing) in enumerate(session_files):
                            skip_header = dialect.has_header and index > 0
                            if is_parquet:
                                _write_parquet_to_copy(data_file, copy, include_header=not skip_header)
                            else:
                                with prepare_local_load_file(
                                    data_file,
                                    dialect=dialect,
                                    strip_trailing_delim=strip_trailing,
                                ) as load_path:
                                    with open(load_path, encoding="utf-8") as f:
                                        if skip_header:
                                            f.readline()
                                        wrote_any = False
                                        ends_with_newline = True
                                        while chunk := f.read(65536):
                                            copy.write(chunk)
                                            wrote_any = True
                                            ends_with_newline = chunk.endswith("\n")
                                        if wrote_any and index < last_file_index and not ends_with_newline:
                                            copy.write("\n")
                    connection.commit()
                except Exception as e:
                    failed_names = ", ".join(data_file.name for data_file, _ in session_files)
                    self.logger.error(f"Failed to load {table_name_lower} chunk {failed_names}: {e}")
                    connection.rollback()
                    load_failed = True
                    break

            if load_failed:
                table_stats[table_name_lower] = 0
                continue

            try:
                cursor.execute(f"SELECT COUNT(*) FROM {qualified_table}")
                row_count = cursor.fetchone()[0]
                table_stats[table_name_lower] = row_count

                if effective_tuning:
                    self.apply_ctas_sort(table_name_lower, effective_tuning, connection)
                    self.run_post_load_tunings(table_name_lower, effective_tuning, connection)

                self.log_verbose(f"Loaded {row_count:,} rows into {table_name_lower}")
            except Exception as e:
                self.logger.error(f"Failed to count rows for {table_name_lower}: {e}")
                connection.rollback()
                table_stats[table_name_lower] = 0

        cursor.close()
        loading_time = elapsed_seconds(start_time)

        total_rows = sum(table_stats.values())
        self.log_operation_complete("Data loading", loading_time, f"Loaded {total_rows:,} total rows")

        return table_stats, loading_time, None

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[TuningColumn]) -> list[str] | None:
        return None

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        cursor = connection.cursor()

        if benchmark_type == "olap":
            cursor.execute("SET enable_seqscan = on")
            cursor.execute("SET enable_hashjoin = on")
            cursor.execute("SET enable_mergejoin = on")
            cursor.execute("SET random_page_cost = 1.1")
            cursor.execute("SET cpu_tuple_cost = 0.01")
        elif benchmark_type == "oltp":
            cursor.execute("SET synchronous_commit = on")
            cursor.execute("SET random_page_cost = 4.0")

        connection.commit()
        cursor.close()

    def get_query_plan(
        self,
        connection: Any,
        query: str,
        explain_options: dict[str, Any] | None = None,
    ) -> str | None:
        from benchbox.platforms.base.result_capture import is_dml_query

        if callable(getattr(connection, "cursor", None)):
            cursor = connection.cursor()
        else:
            cursor = connection.connection.cursor()
        _owns_cursor = True

        if self.analyze_plans and not is_dml_query(query):
            options = ["ANALYZE", "BUFFERS", "FORMAT JSON"]
        else:
            options = ["FORMAT JSON"]
        if explain_options:
            if explain_options.get("verbose"):
                options.append("VERBOSE")

        options_str = ", ".join(options)
        explain_query = f"EXPLAIN ({options_str}) {query}"

        try:
            cursor.execute(explain_query)
            plan_rows = cursor.fetchall()
            if _owns_cursor:
                cursor.close()
            raw = plan_rows[0][0] if plan_rows else None
            if raw is not None and not isinstance(raw, str):
                raw = json.dumps(raw)
            return raw

        except Exception as e:
            if _owns_cursor:
                cursor.close()
            self.logger.debug(f"Failed to get query plan: {e}")
            return None

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.postgresql import PostgreSQLQueryPlanParser

        return PostgreSQLQueryPlanParser()

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
            connection=connection,
            query=query,
            query_id=query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )

    def analyze_table(self, connection: Any, table_name: str) -> None:
        if not self._validate_identifier(table_name):
            self.logger.warning(f"Invalid table identifier: {table_name}")
            return

        cursor = connection.cursor()
        qualified_table = f'"{self.schema}"."{table_name}"' if self.schema != "public" else f'"{table_name}"'

        try:
            cursor.execute(f"ANALYZE {qualified_table}")
            connection.commit()
        finally:
            cursor.close()

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "postgresql",
            "platform_name": "PostgreSQL",
            "host": self.host,
            "port": self.port,
            "dialect": POSTGRES_DIALECT,
            "configuration": {
                "database": self.database,
                "schema": self.schema,
                "work_mem": self.work_mem,
                "maintenance_work_mem": self.maintenance_work_mem,
                "max_parallel_workers_per_gather": self.max_parallel_workers_per_gather,
            },
        }

        if connection:
            try:
                cursor = connection.cursor()

                cursor.execute("SELECT version()")
                version_row = cursor.fetchone()
                if version_row:
                    platform_info["platform_version"] = version_row[0].split()[1] if version_row[0] else None

                cursor.execute(
                    """
                    SELECT extname, extversion
                    FROM pg_extension
                    WHERE extname = 'timescaledb'
                    """
                )
                timescale = cursor.fetchone()
                if timescale:
                    platform_info["configuration"]["timescaledb_version"] = timescale[1]

                cursor.execute(
                    "SELECT pg_size_pretty(pg_database_size(%s))",
                    (self.database,),
                )
                size_row = cursor.fetchone()
                if size_row:
                    platform_info["configuration"]["database_size"] = size_row[0]

                cursor.close()

            except Exception as e:
                self.logger.debug(f"Error getting platform info: {e}")

        if psycopg:
            platform_info["client_library_version"] = psycopg.__version__

        return platform_info

    def test_connection(self) -> bool:
        try:
            params = self._get_connection_params()
            conn = psycopg.connect(**params)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            self.logger.debug(f"Connection test failed: {e}")
            return False

    post_load_index_cluster_supported = True

    def postgres_layout_statements(self, table_name: str, table_tuning: TableTuning | None) -> list[str]:
        if table_tuning is None or not self.post_load_index_cluster_supported:
            return []
        from benchbox.core.tuning.ddl_generator import get_ddl_generator
        from benchbox.core.tuning.interface import TableTuning as RuntimeTableTuning

        generator = get_ddl_generator("postgresql")
        layout_only = RuntimeTableTuning(
            table_name=table_tuning.table_name,
            clustering=table_tuning.clustering,
            sorting=table_tuning.sorting,
        )
        clauses = generator.generate_tuning_clauses(layout_only)
        schema = self.schema if self.schema != "public" else None
        return generator.get_post_load_statements(table_name.lower(), clauses, schema=schema)

    def apply_post_load_tunings(self, table_name: str, effective_config: Any, connection: Any) -> bool:
        statements = self.postgres_layout_statements(table_name, self.table_tuning_for(effective_config, table_name))
        if not statements:
            return False
        cursor = connection.cursor()
        try:
            for statement in statements:
                cursor.execute(statement)
            connection.commit()
            self.logger.info(f"Clustered {table_name} on its tuned index")
        except Exception as exc:
            connection.rollback()
            self.logger.warning(f"Failed to cluster {table_name}: {exc}")
            self.note_post_load_maintenance_failure()
        finally:
            cursor.close()
        return True

    def apply_ctas_sort(self, table_name: str, tuning_config: Any, connection: Any) -> bool:
        if self.postgres_layout_statements(table_name, self.table_tuning_for(tuning_config, table_name)):
            return False
        return super().apply_ctas_sort(table_name, tuning_config, connection)

    def apply_table_tunings(self, table_tuning: TableTuning, connection: Any) -> None:
        pass

    def generate_tuning_clause(self, table_tuning: TableTuning | None) -> str:
        if not table_tuning:
            return ""
        return ""

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        pass

    def apply_platform_optimizations(
        self,
        platform_config: PlatformOptimizationConfiguration,
        connection: Any,
    ) -> None:
        pass

    def apply_constraint_configuration(
        self,
        primary_key_config: PrimaryKeyConfiguration,
        foreign_key_config: ForeignKeyConfiguration,
        connection: Any,
    ) -> None:
        pass

    def validate_platform_capabilities(self, benchmark_type: str):
        errors = []
        warnings = []

        if psycopg is None:
            errors.append("psycopg library not available - install with 'pip install psycopg[binary]'")
        else:
            try:
                version = psycopg.__version__
                version_parts = version.split(".")
                major = int(version_parts[0])
                if major < 3:
                    warnings.append(
                        f"psycopg version {version} is older - consider upgrading to 3.1+ for better performance"
                    )
            except (AttributeError, ValueError, IndexError):
                warnings.append("Could not determine psycopg version")

        if hasattr(self, "work_mem") and self.work_mem:
            try:
                work_mem_str = str(self.work_mem).upper()
                if work_mem_str.endswith("GB"):
                    memory_mb = float(work_mem_str[:-2]) * 1024
                elif work_mem_str.endswith("MB"):
                    memory_mb = float(work_mem_str[:-2])
                elif work_mem_str.endswith("KB"):
                    memory_mb = float(work_mem_str[:-2]) / 1024
                else:
                    memory_mb = float(work_mem_str) / (1024 * 1024)

                if memory_mb < 64:
                    warnings.append(f"work_mem ({self.work_mem}) may be too low for complex analytical queries")
            except (ValueError, TypeError):
                warnings.append(f"Could not parse work_mem setting: {self.work_mem}")

        platform_info = {
            "platform": self.platform_name,
            "benchmark_type": benchmark_type,
            "dry_run_mode": self.dry_run_mode,
            "psycopg_available": psycopg is not None,
            "host": getattr(self, "host", None),
            "port": getattr(self, "port", None),
            "database": getattr(self, "database", None),
            "schema": getattr(self, "schema", None),
            "work_mem": getattr(self, "work_mem", None),
        }

        if psycopg:
            platform_info["psycopg_version"] = getattr(psycopg, "__version__", "unknown")

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

    def validate_connection_health(self, connection: Any):
        errors = []
        warnings = []
        connection_info = {}

        try:
            cursor = connection.cursor()

            cursor.execute("SELECT 1 as test_value")
            result = cursor.fetchone()
            if result[0] != 1:
                errors.append("Basic query execution test failed")
            else:
                connection_info["basic_query_test"] = "passed"

            try:
                cursor.execute("SELECT version()")
                version_result = cursor.fetchone()
                if version_result:
                    connection_info["server_version"] = version_result[0]
                    version_match = re.search(r"PostgreSQL (\d+)", version_result[0])
                    if version_match:
                        major_version = int(version_match.group(1))
                        if major_version < 12:
                            warnings.append(f"PostgreSQL {major_version} is older than recommended minimum (12)")
            except Exception:
                warnings.append("Could not query PostgreSQL version")

            try:
                cursor.execute("SHOW work_mem")
                work_mem_result = cursor.fetchone()
                if work_mem_result:
                    connection_info["work_mem_setting"] = work_mem_result[0]
            except Exception:
                warnings.append("Could not query work_mem setting")

            try:
                cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'timescaledb'")
                timescale_result = cursor.fetchone()
                connection_info["timescaledb_available"] = timescale_result is not None
            except Exception:
                connection_info["timescaledb_available"] = False

            cursor.close()

        except Exception as e:
            errors.append(f"Connection health check failed: {str(e)}")

        try:
            from benchbox.core.validation import ValidationResult

            return ValidationResult(
                is_valid=len(errors) == 0,
                errors=errors,
                warnings=warnings,
                details={
                    "platform": self.platform_name,
                    "connection_type": type(connection).__name__,
                    **connection_info,
                },
            )
        except ImportError:
            return None

    _supported_tuning_type_names = ("PARTITIONING", "CLUSTERING", "PRIMARY_KEYS", "FOREIGN_KEYS")


_build_postgresql_config = make_platform_config_builder(
    "postgresql",
    __name__,
    "PostgreSQL",
    "psycopg",
    POSTGRES_FAMILY_PLATFORM_FIELDS + ("enable_timescale", "statement_timeout"),
    base_options={"schema": "public"},
    field_defaults={**POSTGRES_FAMILY_BASE_OPTIONS, "enable_timescale": False, "statement_timeout": 0},
)
