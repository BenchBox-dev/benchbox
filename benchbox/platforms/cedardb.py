# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any

from .base.config_utils import (
    POSTGRES_CONNECTION_PLATFORM_FIELDS,
    POSTGRES_FAMILY_BASE_OPTIONS,
    make_platform_config_builder,
)
from .postgresql import (
    POSTGRES_DIALECT,
    PostgreSQLAdapter,
    _add_postgres_compatible_arguments,
    _build_postgres_connection_kwargs,
)

CEDARDB_DEFAULT_PORT = 5432


class CedarDBAdapter(PostgreSQLAdapter):
    execution_partitioning_supported = False
    plan_capture_phase_eligible = True

    @property
    def platform_name(self) -> str:
        return "CedarDB"

    def get_target_dialect(self) -> str:
        return POSTGRES_DIALECT

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        try:
            _add_postgres_compatible_arguments(
                parser,
                prefix="cedardb",
                platform_label="CedarDB",
            )
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("Failed to register CedarDB CLI arguments: %s", e)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> CedarDBAdapter:
        adapter_config = _build_postgres_connection_kwargs(config, default_port=CEDARDB_DEFAULT_PORT)
        return cls(**adapter_config)

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = super().get_platform_info(connection)

        platform_info["platform_type"] = "cedardb"
        platform_info["platform_name"] = "CedarDB"

        if connection:
            try:
                cursor = connection.cursor()

                cursor.execute("SELECT version()")
                result = cursor.fetchone()
                if result:
                    platform_info["cedardb_version_string"] = result[0]

                cursor.close()
            except Exception as e:
                self.logger.debug(f"Error getting CedarDB version info: {e}")

        return platform_info

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        import psycopg

        cursor = connection.cursor()
        guc_statements = []
        if benchmark_type == "olap":
            guc_statements = [
                "SET enable_seqscan = on",
                "SET enable_hashjoin = on",
                "SET enable_mergejoin = on",
                "SET random_page_cost = 1.1",
                "SET cpu_tuple_cost = 0.01",
            ]
        elif benchmark_type == "oltp":
            guc_statements = [
                "SET synchronous_commit = on",
                "SET random_page_cost = 4.0",
            ]

        for stmt in guc_statements:
            try:
                cursor.execute(stmt)
                connection.commit()
            except (psycopg.Error, Exception):
                connection.rollback()
                self.logger.debug(f"Skipping unsupported GUC in CedarDB: {stmt}")
        cursor.close()

    _strip_tpc_trailing_pipe: bool = False

    def _copy_options_for_tpc(self, delimiter: str) -> str:
        return f"FORMAT csv, DELIMITER '{delimiter}', NULL ''"

    def drop_database(
        self,
        schema: str | None = None,
        catalog: str | None = None,
        database: str | None = None,
        **_kwargs,
    ) -> None:
        import psycopg

        db_name = database or self.database
        if not self._validate_identifier(db_name):
            raise ValueError(f"Invalid database identifier: {db_name}")

        try:
            admin_params = self._get_connection_params(database=self.admin_database)
            admin_conn = psycopg.connect(**admin_params)
            admin_conn.autocommit = True
            admin_cursor = admin_conn.cursor()
            admin_cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if not admin_cursor.fetchone():
                admin_cursor.close()
                admin_conn.close()
                return
            admin_cursor.close()
            admin_conn.close()

            db_params = self._get_connection_params(database=db_name)
            db_conn = psycopg.connect(**db_params)
            db_conn.autocommit = True
            db_cursor = db_conn.cursor()

            db_cursor.execute(
                """
                SELECT schema_name FROM information_schema.schemata
                WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                  AND schema_name NOT LIKE 'pg_temp%'
                  AND schema_name NOT LIKE 'pg_toast%'
                """
            )
            schemas = [row[0] for row in db_cursor.fetchall()]

            for s in schemas:
                db_cursor.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = %s AND table_type = 'BASE TABLE'
                    """,
                    (s,),
                )
                tables = [row[0] for row in db_cursor.fetchall()]
                for t in tables:
                    self.log_notice(f'Dropping table if it exists: "{s}"."{t}"')
                    db_cursor.execute(f'DROP TABLE IF EXISTS "{s}"."{t}"')

                db_cursor.execute(f'DROP SCHEMA IF EXISTS "{s}"')
                self.logger.info(f'Dropped schema (if existed): "{s}"')

            db_cursor.close()
            db_conn.close()

            admin_conn2 = psycopg.connect(**admin_params)
            admin_conn2.autocommit = True
            admin_cursor2 = admin_conn2.cursor()
            admin_cursor2.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
            admin_cursor2.close()
            admin_conn2.close()
            self.logger.info(f"Dropped database: {db_name}")
        except Exception as e:
            self.logger.warning(f"Failed to drop database {db_name}: {e}")
            raise

    _supported_tuning_type_names = ("PRIMARY_KEYS", "FOREIGN_KEYS")


_build_cedardb_config = make_platform_config_builder(
    "cedardb",
    __name__,
    "CedarDB",
    "psycopg",
    POSTGRES_CONNECTION_PLATFORM_FIELDS,
    base_options={"schema": "public"},
    field_defaults={**POSTGRES_FAMILY_BASE_OPTIONS, "port": CEDARDB_DEFAULT_PORT},
    consume_explicit_options=True,
)
