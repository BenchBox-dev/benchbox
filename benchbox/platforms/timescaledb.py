# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from benchbox.core.deployment import deployment_class, select_adapter_deployment
from benchbox.utils.clock import elapsed_seconds, mono_time

from .base import DriverIsolationCapability
from .base.config_utils import (
    POSTGRES_FAMILY_BASE_OPTIONS,
    POSTGRES_FAMILY_PLATFORM_FIELDS,
    make_platform_config_builder,
)
from .postgresql import (
    POSTGRES_DIALECT,
    PostgreSQLAdapter,
    _add_postgres_compatible_arguments,
    _build_postgres_connection_kwargs,
)

logger = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg import sql as psql
except ImportError:
    psycopg = None
    psql = None


DEFAULT_DEPLOYMENT = "self-hosted"

_INTERVAL_PATTERN = re.compile(
    r"^\s*\d+\s+"
    r"(microseconds?|milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?|years?)"
    r"\s*$",
    re.IGNORECASE,
)


class TimescaleDBAdapter(PostgreSQLAdapter):
    execution_partitioning_supported = False
    post_load_index_cluster_supported = False
    supports_refresh_functions = False
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

    @property
    def platform_name(self) -> str:
        return "TimescaleDB"

    def get_target_dialect(self) -> str:
        return POSTGRES_DIALECT

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        try:
            _add_postgres_compatible_arguments(
                parser,
                prefix="timescale",
                platform_label="TimescaleDB",
            )
            parser.add_argument(
                "--timescale-chunk-interval",
                dest="chunk_interval",
                default="1 day",
                help="Chunk time interval for hypertables (e.g., '1 day', '1 week')",
            )
            parser.add_argument(
                "--timescale-compression",
                dest="compression_enabled",
                action="store_true",
                help="Enable compression on hypertables",
            )
            parser.add_argument(
                "--timescale-compression-after",
                dest="compression_after",
                default="7 days",
                help="Compress chunks older than this interval (e.g., '7 days')",
            )
        except Exception:
            pass

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> TimescaleDBAdapter:
        adapter_config = _build_postgres_connection_kwargs(config)
        adapter_config["chunk_interval"] = config.get("chunk_interval", "1 day")
        adapter_config["compression_enabled"] = config.get("compression_enabled", False)
        adapter_config["compression_after"] = config.get("compression_after", "7 days")
        nested = config.get("options")
        deployment_mode = config.get("deployment_mode") or (
            nested.get("deployment_mode") if isinstance(nested, dict) else None
        )
        if deployment_mode is not None:
            adapter_config["deployment_mode"] = deployment_mode
        return cls(**adapter_config)

    def __init__(self, **config):
        self.deployment_mode = select_adapter_deployment("timescaledb", config, DEFAULT_DEPLOYMENT)
        self.deployment_selected = self.deployment_mode
        self.deployment_selected_class = deployment_class("timescaledb", self.deployment_mode)

        if self.deployment_mode == "cloud":
            self._configure_cloud_mode(config)

        config["enable_timescale"] = True
        super().__init__(**config)

        self.chunk_interval = self._validate_interval(config.get("chunk_interval", "1 day"), "chunk_interval")
        self.compression_enabled = config.get("compression_enabled", False)
        self.compression_after = self._validate_interval(config.get("compression_after", "7 days"), "compression_after")

        self._hypertables: set[str] = set()

        if self.deployment_mode == "cloud":
            self.skip_database_management = config.get("skip_database_management", True)
            logger.info(f"TigerData adapter initialized for host: {config.get('host')}")

    def _configure_cloud_mode(self, config: dict) -> None:
        service_url = (
            config.get("service_url")
            or os.environ.get("TIGERDATA_SERVICE_URL")
            or os.environ.get("TIMESCALE_SERVICE_URL")
        )
        if service_url:
            self._parse_service_url(config, service_url)
            return

        config["host"] = config.get("host") or os.environ.get("TIGERDATA_HOST") or os.environ.get("TIMESCALE_HOST")
        config["password"] = (
            config.get("password")
            or os.environ.get("TIGERDATA_PASSWORD")
            or os.environ.get("TIMESCALE_PASSWORD")
            or os.environ.get("PGPASSWORD")
        )
        config["username"] = (
            config.get("username")
            or os.environ.get("TIGERDATA_USER")
            or os.environ.get("TIMESCALE_USER")
            or "tsdbadmin"
        )
        config["port"] = config.get("port") or int(
            os.environ.get("TIGERDATA_PORT") or os.environ.get("TIMESCALE_PORT") or os.environ.get("PGPORT", "5432")
        )
        config["database"] = (
            config.get("database")
            or os.environ.get("TIGERDATA_DATABASE")
            or os.environ.get("TIMESCALE_DATABASE")
            or os.environ.get("PGDATABASE", "tsdb")
        )

        if not config.get("host"):
            raise ValueError(
                "TigerData requires host configuration.\n"
                "Provide via --platform-option host=<hostname> or "
                "TIGERDATA_HOST environment variable "
                "(TIMESCALE_HOST supported as fallback), or use "
                "TIGERDATA_SERVICE_URL (TIMESCALE_SERVICE_URL fallback).\n"
                "Example: abc123.rc8ft3nbrw.tsdb.cloud.timescale.com"
            )
        if not config.get("password"):
            raise ValueError(
                "TigerData requires password authentication.\n"
                "Provide via --platform-option password=<password> or "
                "TIGERDATA_PASSWORD/PGPASSWORD environment variable "
                "(TIMESCALE_PASSWORD supported as fallback)."
            )

        config["sslmode"] = config.get("sslmode", "require")

        config["force_recreate"] = False
        config["skip_database_management"] = True

    def _parse_service_url(self, config: dict, service_url: str) -> None:
        import urllib.parse

        try:
            parsed = urllib.parse.urlparse(service_url)

            if parsed.scheme not in ("postgres", "postgresql"):
                raise ValueError(f"Unsupported scheme '{parsed.scheme}'. Expected 'postgres://' or 'postgresql://'.")

            config["host"] = parsed.hostname
            config["port"] = parsed.port or 5432
            config["username"] = parsed.username or "tsdbadmin"
            config["password"] = urllib.parse.unquote(parsed.password) if parsed.password else None
            config["database"] = parsed.path.lstrip("/") or "tsdb"

            query_params = urllib.parse.parse_qs(parsed.query)
            if "sslmode" in query_params:
                config["sslmode"] = query_params["sslmode"][0]
            else:
                config["sslmode"] = "require"

            logger.debug(
                f"Parsed service URL: host={config['host']}, port={config['port']}, database={config['database']}"
            )

        except Exception as e:
            raise ValueError(f"Invalid TIGERDATA_SERVICE_URL (or TIMESCALE_SERVICE_URL) format: {e}") from e

    @staticmethod
    def _validate_interval(value: str, param_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{param_name} must be a string, got {type(value).__name__}")

        value = value.strip()
        if not _INTERVAL_PATTERN.match(value):
            raise ValueError(
                f"Invalid {param_name} format: '{value}'. "
                f"Expected format: '<number> <unit>' where unit is one of: "
                f"microsecond(s), millisecond(s), second(s), minute(s), hour(s), "
                f"day(s), week(s), month(s), year(s). Examples: '1 day', '7 days', '2 weeks'."
            )
        return value

    def create_connection(self, **connection_config) -> Any:
        conn = super().create_connection(**connection_config)

        cursor = conn.cursor()
        try:
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
            result = cursor.fetchone()
            if result:
                self.logger.info(f"TimescaleDB extension version: {result[0]}")
            else:
                self.logger.info("TimescaleDB extension not found, attempting to create...")
                cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
                conn.commit()
                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
                result = cursor.fetchone()
                if result:
                    self.logger.info(f"Created TimescaleDB extension version: {result[0]}")
                else:
                    self.logger.warning(
                        "TimescaleDB extension not available. "
                        "Install TimescaleDB or use the postgresql platform instead."
                    )
        except Exception as e:
            self.logger.warning(f"Could not verify TimescaleDB extension: {e}")
        finally:
            cursor.close()

        return conn

    def _apply_stream_session_state(self, connection: Any) -> None:
        super()._apply_stream_session_state(connection)

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        self.log_operation_start("Schema creation", f"benchmark: {benchmark.__class__.__name__}")

        schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="standard")

        self.log_very_verbose(f"Executing schema creation script ({len(schema_sql)} characters)")

        cursor = connection.cursor()

        statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
        tables_created = []

        for stmt in statements:
            try:
                cursor.execute(stmt)
                stmt_upper = stmt.upper()
                if "CREATE TABLE" in stmt_upper:
                    match = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)", stmt, re.IGNORECASE)
                    if match:
                        table_name = match.group(1).strip('"').lower()
                        tables_created.append(table_name)
            except Exception as e:
                connection.rollback()
                self.logger.warning(f"Schema statement failed: {e}")

        connection.commit()

        self._convert_to_hypertables(connection, tables_created, benchmark)

        cursor.close()

        duration = elapsed_seconds(start_time)
        self.log_operation_complete(
            "Schema creation",
            duration,
            f"Schema and tables created, {len(self._hypertables)} hypertables",
        )
        return duration

    def _convert_to_hypertables(
        self,
        connection: Any,
        tables: list[str],
        benchmark,
    ) -> None:
        cursor = connection.cursor()
        effective_config = self.get_effective_tuning_configuration() if self.tuning_enabled else None
        from benchbox.core.tuning.interface import TuningType as _LayoutTuningType

        layout_types = (
            _LayoutTuningType.PARTITIONING,
            _LayoutTuningType.SORTING,
            _LayoutTuningType.CLUSTERING,
            _LayoutTuningType.DISTRIBUTION,
        )
        tuned_tables = set()
        if effective_config:
            for table_name in tables:
                table_tuning = self.table_tuning_for(effective_config, table_name)
                if table_tuning is None:
                    continue
                if not any(table_tuning.get_columns_by_type(kind) for kind in layout_types):
                    continue
                tuned_tables.add(table_name)
                self._apply_native_table_tuning(connection, table_name, table_tuning)

        time_column_tables = self._get_tables_with_time_column(connection, tables)

        for table_name in (table for table in time_column_tables if table not in tuned_tables):
            try:
                if self.schema != "public":
                    table_identifier = psql.Identifier(self.schema, table_name)
                else:
                    table_identifier = psql.Identifier(table_name)

                cursor.execute(
                    """
                    SELECT 1 FROM timescaledb_information.hypertables
                    WHERE hypertable_name = %s
                    """,
                    (table_name,),
                )
                if cursor.fetchone():
                    self.logger.debug(f"Table {table_name} is already a hypertable")
                    self._hypertables.add(table_name)
                    continue

                self.log_verbose(f"Converting {table_name} to hypertable with chunk_interval={self.chunk_interval}")
                cursor.execute(
                    psql.SQL(
                        """
                        SELECT create_hypertable(
                            {}::regclass,
                            'time',
                            chunk_time_interval => INTERVAL {},
                            if_not_exists => TRUE,
                            migrate_data => TRUE
                        )
                        """
                    ).format(
                        psql.Literal(table_identifier.as_string(connection)),
                        psql.Literal(self.chunk_interval),
                    )
                )
                connection.commit()
                self._hypertables.add(table_name)
                self.logger.info(f"Created hypertable: {table_name}")

                if self.compression_enabled:
                    self._add_compression_policy(connection, table_name)

            except Exception as e:
                self.logger.warning(f"Failed to convert {table_name} to hypertable: {e}")
                connection.rollback()

        cursor.close()

    def _apply_native_table_tuning(self, connection: Any, table_name: str, table_tuning: Any) -> None:
        from benchbox.core.tuning.applied_ledger import PHASE_DDL, STATEMENT_FAILED
        from benchbox.core.tuning.generators.timescaledb import TimescaleDBDDLGenerator
        from benchbox.core.tuning.interface import TuningType

        ledger = self._applied_tuning_ledger
        layout_types = (TuningType.PARTITIONING, TuningType.SORTING, TuningType.CLUSTERING, TuningType.DISTRIBUTION)
        requested = {kind: table_tuning.get_columns_by_type(kind) for kind in layout_types}
        if not any(requested.values()):
            return
        partition_columns = requested[TuningType.PARTITIONING]
        cursor = connection.cursor()
        try:
            if len(partition_columns) != 1:
                raise ValueError("TimescaleDB native tuning requires exactly one date or timestamp partition column")
            time_column = partition_columns[0].name
            cursor.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
                (self.schema, table_name, time_column),
            )
            row = cursor.fetchone()
            if not row or row[0] not in ("date", "timestamp without time zone", "timestamp with time zone"):
                raise ValueError(f"Partition column {time_column} must have a date or timestamp type")
            if isinstance(self.compression_enabled, str):
                compression_opt_in = self.compression_enabled.strip().lower() in ("true", "1", "yes", "on")
            else:
                compression_opt_in = bool(self.compression_enabled)
            compression = compression_opt_in or any(
                requested[kind] for kind in (TuningType.SORTING, TuningType.CLUSTERING, TuningType.DISTRIBUTION)
            )
            generator = TimescaleDBDDLGenerator(
                default_chunk_interval=psql.SQL("INTERVAL {}")
                .format(psql.Literal(self.chunk_interval))
                .as_string(connection),
                enable_compression=compression,
                compression_after=psql.SQL("INTERVAL {}")
                .format(psql.Literal(self.compression_after))
                .as_string(connection),
            )
            clauses = generator.generate_tuning_clauses(table_tuning)
            qualified_name = psql.Identifier(self.schema, table_name).as_string(connection)
            for template in clauses.post_create_statements:
                statement = template.replace(
                    "'{table_name}'", psql.Literal(qualified_name).as_string(connection)
                ).replace("{table_name}", qualified_name)
                try:
                    cursor.execute(statement)
                    connection.commit()
                except Exception as exc:
                    connection.rollback()
                    if ledger is not None:
                        ledger.record(statement, PHASE_DDL, status=STATEMENT_FAILED, table=table_name, error=exc)
                    raise
                if ledger is not None:
                    ledger.record(statement, PHASE_DDL, table=table_name, mechanism="timescaledb_native_layout")
            self._verify_native_table_tuning(cursor, table_name, requested, ledger)
            self._hypertables.add(table_name)
        except Exception as exc:
            connection.rollback()
            if ledger is not None:
                for kind, columns in requested.items():
                    if columns:
                        ledger.record_dropped(f"{kind.value}:{table_name}", str(exc))
            self.logger.warning("Native tuning not applied for %s: %s", table_name, exc)
        finally:
            cursor.close()

    def _verify_native_table_tuning(self, cursor: Any, table_name: str, requested: dict, ledger: Any) -> None:
        from benchbox.core.tuning.interface import TuningType

        cursor.execute(
            "SELECT column_name FROM timescaledb_information.dimensions "
            "WHERE hypertable_schema = %s AND hypertable_name = %s AND dimension_type = 'Time'",
            (self.schema, table_name),
        )
        time_columns = [row[0] for row in cursor.fetchall()]
        expected_time = [column.name for column in requested[TuningType.PARTITIONING]]
        if time_columns != expected_time:
            raise ValueError(f"Hypertable catalog mismatch: expected {expected_time}, observed {time_columns}")
        cursor.execute(
            "SELECT attname, segmentby_column_index, orderby_column_index, orderby_asc, orderby_nullsfirst "
            "FROM timescaledb_information.compression_settings "
            "WHERE hypertable_schema = %s AND hypertable_name = %s",
            (self.schema, table_name),
        )
        settings = cursor.fetchall()
        for kind, columns in requested.items():
            if not columns:
                continue
            ordered = sorted(columns, key=lambda column: column.order)
            if kind == TuningType.PARTITIONING:
                observed = time_columns
            elif kind == TuningType.SORTING:
                observed = [(row[0], row[3], row[4]) for row in sorted(settings, key=lambda row: row[2] or 0) if row[2]]
                expected = [
                    (
                        column.name,
                        column.sort_order == "ASC",
                        column.nulls_position == "FIRST"
                        or (column.nulls_position == "DEFAULT" and column.sort_order == "DESC"),
                    )
                    for column in ordered
                ]
                if observed[: len(expected)] != expected:
                    raise ValueError(f"Compression ordering catalog mismatch: expected {expected}, observed {observed}")
            else:
                observed = [row[0] for row in sorted(settings, key=lambda row: row[1] or 0) if row[1]]
                if not all(column.name in observed for column in ordered):
                    raise ValueError(f"Compression segmentation catalog mismatch: observed {observed}")
            if ledger is not None:
                footprint = "create_hypertable" if kind == TuningType.PARTITIONING else "timescaledb.compress"
                statement_index = ledger.executed_statement_index(
                    lambda statement: statement.table == table_name and footprint in statement.statement
                )
                if statement_index is None:
                    raise ValueError(f"No executed native layout statement for {kind.value}:{table_name}")
                ledger.record_satisfied(
                    f"{kind.value}:{table_name}",
                    statement_index,
                    f"timescaledb_information catalog readback: {observed!r}",
                )

    def _get_tables_with_time_column(self, connection: Any, tables: list[str]) -> list[str]:
        if not tables:
            return []

        cursor = connection.cursor()
        time_tables = []

        for table_name in tables:
            try:
                cursor.execute(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = %s
                      AND table_name = %s
                      AND column_name = 'time'
                    """,
                    (self.schema, table_name),
                )
                if cursor.fetchone():
                    time_tables.append(table_name)
            except Exception as e:
                self.logger.debug(f"Error checking time column for {table_name}: {e}")

        cursor.close()
        return time_tables

    def _add_compression_policy(self, connection: Any, table_name: str) -> None:
        cursor = connection.cursor()

        try:
            if self.schema != "public":
                table_identifier = psql.Identifier(self.schema, table_name)
            else:
                table_identifier = psql.Identifier(table_name)

            cursor.execute(
                psql.SQL(
                    """
                    ALTER TABLE {} SET (
                        timescaledb.compress,
                        timescaledb.compress_segmentby = ''
                    )
                    """
                ).format(table_identifier)
            )

            cursor.execute(
                psql.SQL(
                    """
                    SELECT add_compression_policy(
                        {}::regclass,
                        INTERVAL {},
                        if_not_exists => TRUE
                    )
                    """
                ).format(
                    psql.Literal(table_identifier.as_string(connection)),
                    psql.Literal(self.compression_after),
                )
            )
            connection.commit()
            self.log_verbose(f"Added compression policy to {table_name}: compress after {self.compression_after}")

        except Exception as e:
            self.logger.warning(f"Failed to add compression policy to {table_name}: {e}")
            connection.rollback()
        finally:
            cursor.close()

    def load_data(
        self,
        benchmark,
        connection: Any,
        data_dir: Path,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        table_stats, loading_time, extra_info = super().load_data(benchmark, connection, data_dir)

        cursor = connection.cursor()
        for table_name in self._hypertables:
            try:
                if self.schema != "public":
                    table_identifier = psql.Identifier(self.schema, table_name)
                else:
                    table_identifier = psql.Identifier(table_name)
                cursor.execute(psql.SQL("ANALYZE {}").format(table_identifier))
                connection.commit()
            except Exception as e:
                self.logger.debug(f"ANALYZE failed for hypertable {table_name}: {e}")
        cursor.close()

        return table_stats, loading_time, extra_info

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = super().get_platform_info(connection)

        platform_info["platform_type"] = "timescaledb"
        platform_info["platform_name"] = "TimescaleDB"

        platform_info["configuration"]["chunk_interval"] = self.chunk_interval
        platform_info["configuration"]["compression_enabled"] = self.compression_enabled
        if self.compression_enabled:
            platform_info["configuration"]["compression_after"] = self.compression_after

        if connection:
            try:
                cursor = connection.cursor()

                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
                result = cursor.fetchone()
                if result:
                    platform_info["timescaledb_version"] = result[0]

                cursor.execute("SELECT COUNT(*) FROM timescaledb_information.hypertables")
                result = cursor.fetchone()
                if result:
                    platform_info["configuration"]["hypertable_count"] = result[0]

                cursor.execute("SELECT COUNT(*) FROM timescaledb_information.chunks")
                result = cursor.fetchone()
                if result:
                    platform_info["configuration"]["chunk_count"] = result[0]

                cursor.close()

            except Exception as e:
                self.logger.debug(f"Error getting TimescaleDB info: {e}")

        return platform_info

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        super().configure_for_benchmark(connection, benchmark_type)

        cursor = connection.cursor()

        try:
            if benchmark_type == "olap" or benchmark_type == "timeseries":
                cursor.execute("SET timescaledb.enable_chunk_skipping = on")

            connection.commit()
        except Exception as e:
            self.logger.debug(f"Could not set TimescaleDB optimizations: {e}")
        finally:
            cursor.close()

    def supports_tuning_type(self, tuning_type: Any) -> bool:
        try:
            from benchbox.core.tuning.interface import TuningType

            supported = {
                TuningType.PARTITIONING: True,
                TuningType.SORTING: True,
                TuningType.DISTRIBUTION: True,
                TuningType.CLUSTERING: True,
                TuningType.PRIMARY_KEYS: True,
                TuningType.FOREIGN_KEYS: True,
                TuningType.AUTO_COMPACT: True,
            }
            return supported.get(tuning_type, False)
        except ImportError:
            return False


_build_timescaledb_config = make_platform_config_builder(
    "timescaledb",
    __name__,
    "TimescaleDB",
    "psycopg",
    POSTGRES_FAMILY_PLATFORM_FIELDS + ("chunk_interval", "compression_enabled", "compression_after"),
    base_options={
        **POSTGRES_FAMILY_BASE_OPTIONS,
        "chunk_interval": "1 day",
        "compression_enabled": False,
        "compression_after": "7 days",
    },
)
