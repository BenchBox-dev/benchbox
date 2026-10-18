# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import re
from typing import Any

from .base.config_utils import (
    POSTGRES_FAMILY_BASE_OPTIONS,
    POSTGRES_FAMILY_PLATFORM_FIELDS,
    make_platform_config_builder,
)
from .postgresql import (
    POSTGRES_DIALECT,
    PostgreSQLAdapter,
    _build_postgres_connection_kwargs,
    ensure_postgres_extension,
)

logger = logging.getLogger(__name__)

try:
    import psycopg
except ImportError:
    psycopg = None

CITUS_EXTENSION = "citus"


class CitusAdapter(PostgreSQLAdapter):
    execution_partitioning_supported = False
    post_load_index_cluster_supported = False
    supports_refresh_functions = False
    plan_capture_phase_eligible = True

    @property
    def platform_name(self) -> str:
        return "citus"

    def get_target_dialect(self) -> str:
        return POSTGRES_DIALECT

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        try:
            parser.add_argument(
                "--citus-host",
                dest="host",
                default="localhost",
                help="Citus coordinator hostname (with citus extension installed)",
            )
            parser.add_argument(
                "--citus-port",
                dest="port",
                type=int,
                default=5432,
                help="Citus coordinator port",
            )
            parser.add_argument(
                "--citus-database",
                dest="database",
                help="PostgreSQL database name (auto-generated if not specified)",
            )
            parser.add_argument(
                "--citus-username",
                dest="username",
                default="postgres",
                help="PostgreSQL username",
            )
            parser.add_argument(
                "--citus-password",
                dest="password",
                help="PostgreSQL password",
            )
            parser.add_argument(
                "--citus-schema",
                dest="schema",
                default="public",
                help="PostgreSQL schema name",
            )
            parser.add_argument(
                "--citus-distribution-column",
                dest="distribution_column",
                default=None,
                help="Distribute every benchmark table on this column "
                "(e.g. l_orderkey for TPC-H). Unset keeps coordinator-local tables.",
            )
        except Exception:
            pass

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> CitusAdapter:
        adapter_config = _build_postgres_connection_kwargs(config)
        adapter_config["distribution_column"] = config.get("distribution_column")
        return cls(**adapter_config)

    def __init__(self, **config):
        super().__init__(**config)
        self.distribution_column: str | None = config.get("distribution_column")

    def create_connection(self, **connection_config) -> Any:
        conn = super().create_connection(**connection_config)
        ensure_postgres_extension(
            conn, self.logger, CITUS_EXTENSION, "https://github.com/citusdata/citus", cascade=True
        )
        return conn

    def _apply_stream_session_state(self, connection: Any) -> None:
        super()._apply_stream_session_state(connection)

    def create_schema(self, benchmark, connection: Any) -> float:
        elapsed = super().create_schema(benchmark, connection)
        if self.distribution_column:
            self._distribute_benchmark_tables(benchmark, connection)
        return elapsed

    def _benchmark_table_names(self, benchmark: Any) -> list[str]:
        get_tables = getattr(benchmark, "_get_active_tables", None)
        if callable(get_tables):
            try:
                names = get_tables()
                if names:
                    return [str(name) for name in names]
            except Exception:
                pass
        schema_getter = getattr(benchmark, "get_schema", None)
        if callable(schema_getter):
            try:
                schema = schema_getter()
                if isinstance(schema, dict):
                    return list(schema.keys())
            except Exception:
                pass
        tables = getattr(benchmark, "tables", None)
        if isinstance(tables, dict):
            return list(tables.keys())
        return []

    def _validated_distribution_column(self) -> str:
        column = self.distribution_column
        assert column is not None
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", column):
            raise ValueError(f"Invalid Citus distribution column {column!r}: must be a plain SQL identifier.")
        return column

    @staticmethod
    def _table_has_column(cursor: Any, table_name: str, column: str) -> bool:
        cursor.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
            (table_name, column),
        )
        return cursor.fetchone() is not None

    @staticmethod
    def _distributed_column(cursor: Any, table_name: str) -> str | None:
        cursor.execute("SELECT partkey FROM pg_dist_partition WHERE logicalrelid = %s::regclass", (table_name,))
        row = cursor.fetchone()
        if not row:
            return None
        return row[0] or None

    def _distribute_single_table(self, cursor: Any, connection: Any, table_name: str, column: str) -> None:
        quoted_table = '"' + table_name.replace('"', '""') + '"'
        cursor.execute(f"SELECT create_distributed_table('{quoted_table}', '{column}')")
        connection.commit()
        self.logger.info(f"Distributed Citus table {table_name} on column {column}")

    def _distribute_benchmark_tables(self, benchmark: Any, connection: Any) -> None:
        column = self._validated_distribution_column()
        cursor = connection.cursor()
        try:
            for table_name in self._benchmark_table_names(benchmark):
                if not self._table_has_column(cursor, table_name, column):
                    self.logger.warning(
                        f"Table {table_name} lacks distribution column {column}; left coordinator-local"
                    )
                    continue
                self._distribute_single_table(cursor, connection, table_name, column)
        finally:
            cursor.close()

    def _ensure_distribution_on_reused_database(self, benchmark: Any, connection: Any) -> None:
        column = self._validated_distribution_column()
        cursor = connection.cursor()
        try:
            for table_name in self._benchmark_table_names(benchmark):
                existing = self._distributed_column(cursor, table_name)
                if existing == column:
                    self.logger.info(f"Citus table {table_name} already distributed on column {column}")
                    continue
                if existing is not None:
                    raise RuntimeError(
                        f"Citus table {table_name} is already distributed on {existing!r}, "
                        f"but distribution_column={column!r} was requested; recreate the database "
                        "or rerun with the matching column."
                    )
                if not self._table_has_column(cursor, table_name, column):
                    self.logger.warning(
                        f"Table {table_name} lacks distribution column {column}; left coordinator-local"
                    )
                    continue
                self._distribute_single_table(cursor, connection, table_name, column)
        finally:
            cursor.close()

    def _setup_reused_database_phases(self, benchmark: Any, connection: Any) -> tuple:
        phases = super()._setup_reused_database_phases(benchmark, connection)
        if self.distribution_column:
            self._ensure_distribution_on_reused_database(benchmark, connection)
        return phases

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = super().get_platform_info(connection)

        platform_info["platform_type"] = "citus"
        platform_info["platform_name"] = "citus"

        platform_info["configuration"]["distribution_column"] = self.distribution_column

        if connection:
            try:
                cursor = connection.cursor()

                cursor.execute(f"SELECT extversion FROM pg_extension WHERE extname = '{CITUS_EXTENSION}'")
                result = cursor.fetchone()
                if result:
                    platform_info["citus_version"] = result[0]

                cursor.close()
            except Exception as e:
                self.logger.debug(f"Error getting citus info: {e}")

        return platform_info

    _supported_tuning_type_names = ("PARTITIONING", "CLUSTERING", "PRIMARY_KEYS", "FOREIGN_KEYS")


_build_citus_config = make_platform_config_builder(
    "citus",
    __name__,
    "citus",
    "psycopg",
    POSTGRES_FAMILY_PLATFORM_FIELDS + ("distribution_column",),
    base_options={
        **POSTGRES_FAMILY_BASE_OPTIONS,
    },
)
