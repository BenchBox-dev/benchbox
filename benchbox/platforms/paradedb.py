# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
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

PARADEDB_EXTENSION = "pg_analytics"


class ParadeDBAdapter(PostgreSQLAdapter):
    execution_partitioning_supported = False
    post_load_index_cluster_supported = False
    supports_refresh_functions = False
    plan_capture_phase_eligible = True

    @property
    def platform_name(self) -> str:
        return "paradedb"

    def get_target_dialect(self) -> str:
        return POSTGRES_DIALECT

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        try:
            parser.add_argument(
                "--paradedb-host",
                dest="host",
                default="localhost",
                help="PostgreSQL server hostname (with pg_analytics installed)",
            )
            parser.add_argument(
                "--paradedb-port",
                dest="port",
                type=int,
                default=5432,
                help="PostgreSQL server port",
            )
            parser.add_argument(
                "--paradedb-database",
                dest="database",
                help="PostgreSQL database name (auto-generated if not specified)",
            )
            parser.add_argument(
                "--paradedb-username",
                dest="username",
                default="postgres",
                help="PostgreSQL username",
            )
            parser.add_argument(
                "--paradedb-password",
                dest="password",
                help="PostgreSQL password",
            )
            parser.add_argument(
                "--paradedb-schema",
                dest="schema",
                default="public",
                help="PostgreSQL schema name",
            )
        except Exception:
            pass

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ParadeDBAdapter:
        adapter_config = _build_postgres_connection_kwargs(config)
        return cls(**adapter_config)

    def __init__(self, **config):
        super().__init__(**config)

    def create_connection(self, **connection_config) -> Any:
        conn = super().create_connection(**connection_config)
        ensure_postgres_extension(conn, self.logger, PARADEDB_EXTENSION, "https://github.com/paradedb/paradedb")
        return conn

    def _apply_stream_session_state(self, connection: Any) -> None:
        super()._apply_stream_session_state(connection)

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = super().get_platform_info(connection)

        platform_info["platform_type"] = "paradedb"
        platform_info["platform_name"] = "paradedb"

        if connection:
            try:
                cursor = connection.cursor()

                cursor.execute(f"SELECT extversion FROM pg_extension WHERE extname = '{PARADEDB_EXTENSION}'")
                result = cursor.fetchone()
                if result:
                    platform_info["paradedb_version"] = result[0]

                cursor.close()
            except Exception as e:
                self.logger.debug(f"Error getting pg_analytics info: {e}")

        return platform_info

    _supported_tuning_type_names = ("PARTITIONING", "CLUSTERING", "PRIMARY_KEYS", "FOREIGN_KEYS")


_build_paradedb_config = make_platform_config_builder(
    "paradedb",
    __name__,
    "paradedb",
    "psycopg",
    POSTGRES_FAMILY_PLATFORM_FIELDS,
    base_options={
        **POSTGRES_FAMILY_BASE_OPTIONS,
    },
)
