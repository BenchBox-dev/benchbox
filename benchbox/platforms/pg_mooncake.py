# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .base.config_utils import (
    POSTGRES_FAMILY_BASE_OPTIONS,
    POSTGRES_FAMILY_PLATFORM_FIELDS,
    make_platform_config_builder,
)
from .postgresql import POSTGRES_DIALECT, PostgreSQLAdapter

logger = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg import sql as psycopg_sql
except ImportError:
    psycopg = None
    psycopg_sql = None


class PgMooncakeAdapter(PostgreSQLAdapter):
    execution_partitioning_supported = False
    post_load_index_cluster_supported = False
    supports_refresh_functions = False
    plan_capture_phase_eligible = True

    @property
    def platform_name(self) -> str:
        return "pg_mooncake"

    def get_target_dialect(self) -> str:
        return POSTGRES_DIALECT

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> PgMooncakeAdapter:
        adapter_config = {}

        adapter_config["host"] = config.get("host", "localhost")
        adapter_config["port"] = config.get("port", 5432)
        adapter_config["username"] = config.get("username", "postgres")
        adapter_config["password"] = config.get("password")
        adapter_config["schema"] = config.get("schema", "public")
        adapter_config["sslmode"] = config.get("sslmode", "prefer")

        if config.get("database"):
            adapter_config["database"] = config["database"]
        elif config.get("benchmark") and config.get("scale_factor") is not None:
            from benchbox.utils.scale_factor import format_benchmark_name

            benchmark_name = format_benchmark_name(config["benchmark"], config["scale_factor"])
            adapter_config["database"] = f"benchbox_{benchmark_name}".lower().replace("-", "_")
        else:
            adapter_config["database"] = "benchbox"

        adapter_config["admin_database"] = config.get("admin_database", "postgres")

        adapter_config["work_mem"] = config.get("work_mem", "256MB")
        adapter_config["maintenance_work_mem"] = config.get("maintenance_work_mem", "512MB")
        adapter_config["effective_cache_size"] = config.get("effective_cache_size", "1GB")
        adapter_config["max_parallel_workers_per_gather"] = config.get("max_parallel_workers_per_gather", 2)

        adapter_config["connect_timeout"] = config.get("connect_timeout", 10)
        adapter_config["statement_timeout"] = config.get("statement_timeout", 0)

        adapter_config["storage_mode"] = config.get("storage_mode", "local")
        adapter_config["mooncake_bucket"] = config.get("mooncake_bucket")

        adapter_config["force_recreate"] = config.get("force", False)

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

        self.storage_mode = config.get("storage_mode", "local")

        valid_storage_modes = {"local", "s3"}
        if self.storage_mode not in valid_storage_modes:
            raise ValueError(
                f"Invalid pg_mooncake storage mode '{self.storage_mode}'. "
                f"Valid modes: {', '.join(sorted(valid_storage_modes))}"
            )

        self.mooncake_bucket = config.get("mooncake_bucket") or os.environ.get("MOONCAKE_S3_BUCKET")

        if self.storage_mode == "s3" and not self.mooncake_bucket:
            raise ValueError(
                "S3 storage mode requires bucket configuration.\n"
                "Provide via --platform-option mooncake_bucket=s3://bucket/path or "
                "set MOONCAKE_S3_BUCKET environment variable."
            )

    def create_connection(self, **connection_config) -> Any:
        conn = super().create_connection(**connection_config)

        cursor = conn.cursor()
        try:
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_mooncake'")
            result = cursor.fetchone()
            if result:
                self.logger.info(f"pg_mooncake extension version: {result[0]}")
            else:
                self.logger.info("pg_mooncake extension not found, attempting to create...")
                cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_mooncake CASCADE")
                conn.commit()
                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_mooncake'")
                result = cursor.fetchone()
                if result:
                    self.logger.info(f"Created pg_mooncake extension version: {result[0]}")
                else:
                    raise RuntimeError(
                        "pg_mooncake extension is not available on this PostgreSQL server. "
                        "Install pg_mooncake (https://github.com/Mooncake-Labs/pg_mooncake) or use "
                        "the 'postgresql' or 'duckdb' platform instead."
                    )

            self._apply_mooncake_session_gucs(cursor)

            conn.commit()

        except RuntimeError:
            cursor.close()
            raise
        except Exception as e:
            self.logger.error(f"Failed to configure pg_mooncake extension: {e}")
            cursor.close()
            raise RuntimeError(f"pg_mooncake configuration failed: {e}") from e
        finally:
            if not cursor.closed:
                cursor.close()

        return conn

    def _apply_mooncake_session_gucs(self, cursor: Any) -> None:
        if self.storage_mode == "s3" and self.mooncake_bucket:
            cursor.execute(
                psycopg_sql.SQL("SET mooncake.default_bucket = {}").format(psycopg_sql.Literal(self.mooncake_bucket))
            )
            self.logger.info(f"Set mooncake.default_bucket = {self.mooncake_bucket}")

    def _apply_stream_session_state(self, connection: Any) -> None:
        cursor = connection.cursor()
        try:
            self._apply_mooncake_session_gucs(cursor)
        finally:
            cursor.close()

    def _transform_create_statement(self, stmt: str) -> str:
        return stmt

    def _add_columnstore_access_method(self, ddl_statement: str) -> str:
        stripped = ddl_statement.strip()
        upper = stripped.upper()

        if not upper.startswith("CREATE TABLE"):
            return ddl_statement

        if "USING MOONCAKE" in upper:
            return ddl_statement

        if stripped.endswith(";"):
            return stripped[:-1] + " USING mooncake;"
        return stripped + " USING mooncake"

    def load_data(
        self,
        benchmark,
        connection: Any,
        data_dir: str | os.PathLike,
    ) -> tuple[dict[str, int], float, None]:
        table_stats, loading_time, extra = super().load_data(benchmark, connection, data_dir)
        loaded_tables = [table for table, rows in table_stats.items() if rows > 0]
        if loaded_tables:
            table_stats = self._promote_loaded_tables_to_mooncake(connection, loaded_tables, table_stats)
        return table_stats, loading_time, extra

    def _promote_loaded_tables_to_mooncake(
        self,
        connection: Any,
        table_names: list[str],
        table_stats: dict[str, int],
    ) -> dict[str, int]:
        if not self._validate_identifier(self.schema):
            raise ValueError(f"Invalid pg_mooncake schema identifier: {self.schema}")

        cursor = connection.cursor()
        updated_stats = dict(table_stats)
        try:
            for table_name in table_names:
                table_name_lower = table_name.lower()
                if not self._validate_identifier(table_name_lower):
                    self.logger.warning(f"Skipping mooncake mirror for invalid table identifier: {table_name}")
                    continue

                staging_table = self._mooncake_staging_table_name(table_name_lower)
                qualified_table = self._qualified_identifier(table_name_lower)
                source_name = self._mooncake_table_reference(staging_table)
                target_name = self._mooncake_table_reference(table_name_lower)

                cursor.execute(f'ALTER TABLE {qualified_table} RENAME TO "{staging_table}"')
                cursor.execute("CALL mooncake.create_table(%s, %s)", (target_name, source_name))
                cursor.execute(f"SELECT COUNT(*) FROM {qualified_table}")
                updated_stats[table_name_lower] = cursor.fetchone()[0]

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
        return updated_stats

    def _mooncake_staging_table_name(self, table_name: str) -> str:
        suffix_budget = 63 - len("__bb_moon_src_")
        return f"__bb_moon_src_{table_name[:suffix_budget]}"

    def _qualified_identifier(self, table_name: str) -> str:
        if self.schema != "public":
            return f'"{self.schema}"."{table_name}"'
        return f'"{table_name}"'

    def _mooncake_table_reference(self, table_name: str) -> str:
        if self.schema != "public":
            return f"{self.schema}.{table_name}"
        return table_name

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
        self._close_mooncake_query_transaction(connection, action="commit", phase="before query")
        result = super().execute_query(
            connection,
            query,
            query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )
        if self._is_duckdb_inside_function_error(result):
            self._close_mooncake_query_transaction(connection, action="rollback", phase="after DuckDB function error")
            self.log_verbose(f"Retrying pg_mooncake query {query_id} after closing transaction")
            result = super().execute_query(
                connection,
                query,
                query_id,
                benchmark_type=benchmark_type,
                scale_factor=scale_factor,
                validate_row_count=validate_row_count,
                stream_id=stream_id,
            )

        self._close_mooncake_query_transaction(connection, action="commit", phase="after query")
        return result

    def _close_mooncake_query_transaction(self, connection: Any, *, action: str, phase: str) -> None:
        closer = getattr(connection, action, None)
        if not callable(closer):
            return
        try:
            closer()
        except Exception as exc:
            self.logger.debug("Failed to %s pg_mooncake transaction %s: %s", action, phase, exc)

    def _is_duckdb_inside_function_error(self, result: dict[str, Any]) -> bool:
        if result.get("status") != "FAILED":
            return False
        return "DuckDB execution is not supported inside functions" in str(result.get("error", ""))

    def _get_existing_tables(self, connection: Any) -> list[str]:
        cursor = connection.cursor()
        should_commit = False
        try:
            cursor.execute(
                """
                SELECT c.relname
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r'
                  AND n.nspname = %s
                ORDER BY c.relname
                """,
                (self.schema,),
            )
            tables = [row[0].lower() for row in cursor.fetchall()]
            should_commit = True
            return tables
        except Exception as e:
            self.logger.warning(f"Failed to get pg_mooncake tables: {e}")
            connection.rollback()
            return []
        finally:
            cursor.close()
            if should_commit:
                try:
                    connection.commit()
                except Exception as e:
                    self.logger.debug(f"Failed to close pg_mooncake catalog transaction: {e}")

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = super().get_platform_info(connection)

        platform_info["platform_type"] = "pg_mooncake"
        platform_info["platform_name"] = "pg_mooncake"

        platform_info["configuration"]["storage_mode"] = self.storage_mode
        if self.mooncake_bucket:
            platform_info["configuration"]["mooncake_bucket"] = self.mooncake_bucket

        if connection:
            try:
                cursor = connection.cursor()

                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_mooncake'")
                result = cursor.fetchone()
                if result:
                    platform_info["pg_mooncake_version"] = result[0]

                cursor.close()
            except Exception as e:
                self.logger.debug(f"Error getting pg_mooncake info: {e}")

        return platform_info

    def run_migration_phase(
        self,
        connection: Any,
        table_names: list[str] | None = None,
    ) -> Any:
        from benchbox.core.results.models import MigrationPhase, MigrationTableStats

        cursor = connection.cursor()
        try:
            if table_names is None:
                cursor.execute(
                    """
                    SELECT c.relname
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relkind = 'r'
                      AND n.nspname = %s
                      AND c.relam = (SELECT oid FROM pg_catalog.pg_am WHERE amname = 'heap')
                    ORDER BY c.relname
                    """,
                    (self.schema,),
                )
                table_names = [row[0] for row in cursor.fetchall()]

            if not table_names:
                return None

            def _get_storage_bytes(table: str) -> int:
                qualified = psycopg_sql.SQL("{}.{}").format(
                    psycopg_sql.Identifier(self.schema),
                    psycopg_sql.Identifier(table),
                )
                cursor.execute(
                    psycopg_sql.SQL("SELECT pg_catalog.pg_total_relation_size({}::regclass)").format(qualified),
                )
                row = cursor.fetchone()
                return int(row[0]) if row and row[0] is not None else 0

            per_table: dict[str, MigrationTableStats] = {}
            phase_start = time.monotonic()
            tables_migrated = 0
            tables_failed = 0
            total_before = 0
            total_after = 0

            for table in table_names:
                before_bytes = _get_storage_bytes(table)
                total_before += before_bytes
                t0 = time.monotonic()
                try:
                    cursor.execute(
                        psycopg_sql.SQL("ALTER TABLE {}.{} SET ACCESS METHOD columnar").format(
                            psycopg_sql.Identifier(self.schema),
                            psycopg_sql.Identifier(table),
                        )
                    )
                    connection.commit()
                    elapsed_ms = round((time.monotonic() - t0) * 1000)
                    after_bytes = _get_storage_bytes(table)
                    total_after += after_bytes
                    per_table[table] = MigrationTableStats(
                        duration_ms=elapsed_ms,
                        status="completed",
                        storage_before_bytes=before_bytes,
                        storage_after_bytes=after_bytes,
                        storage_delta_bytes=after_bytes - before_bytes,
                    )
                    tables_migrated += 1
                    self.logger.info(f"Migrated {table}: {before_bytes} -> {after_bytes} bytes ({elapsed_ms} ms)")
                except Exception as exc:
                    connection.rollback()
                    elapsed_ms = round((time.monotonic() - t0) * 1000)
                    per_table[table] = MigrationTableStats(
                        duration_ms=elapsed_ms,
                        status="failed",
                        storage_before_bytes=before_bytes,
                        storage_after_bytes=before_bytes,
                        storage_delta_bytes=0,
                        error_message=str(exc),
                    )
                    tables_failed += 1
                    self.logger.warning(f"Migration failed for {table}: {exc}")

            phase_ms = round((time.monotonic() - phase_start) * 1000)
            status = "completed" if tables_failed == 0 else "partial" if tables_migrated > 0 else "failed"

            return MigrationPhase(
                duration_ms=phase_ms,
                status=status,
                tables_migrated=tables_migrated,
                tables_failed=tables_failed,
                storage_before_bytes=total_before,
                storage_after_bytes=total_after,
                storage_delta_bytes=total_after - total_before,
                per_table_stats=per_table,
            )
        finally:
            cursor.close()

    def supports_tuning_type(self, tuning_type: Any) -> bool:
        return False


_build_pg_mooncake_config = make_platform_config_builder(
    "pg-mooncake",
    __name__,
    "pg_mooncake",
    "psycopg",
    POSTGRES_FAMILY_PLATFORM_FIELDS + ("storage_mode", "mooncake_bucket"),
    base_options={
        **POSTGRES_FAMILY_BASE_OPTIONS,
        "storage_mode": "local",
    },
)
