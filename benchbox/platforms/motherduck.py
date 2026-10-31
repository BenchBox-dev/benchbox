# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from benchbox.core.config_inheritance import (
    resolve_dialect_for_query_translation,
)
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.dependencies import get_package_install_message

try:
    import duckdb
except ImportError:
    duckdb = None

from .base import DriverIsolationCapability, PlatformAdapter
from .base.config_utils import make_platform_config_builder
from .duckdb import _build_duckdb_ctas_sort_sql, _create_duckdb_external_views

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import TuningColumn

logger = logging.getLogger(__name__)

_MOTHERDUCK_TOKEN_RE = re.compile(r"(motherduck_token=)[^&\s,;)]*", flags=re.IGNORECASE)


def _redact_motherduck_token(message: str, token: str | None = None) -> str:
    redacted = message.replace(token, "****") if token else message
    return _MOTHERDUCK_TOKEN_RE.sub(r"\1****", redacted)


_build_motherduck_config = make_platform_config_builder(
    "motherduck",
    __name__,
    "MotherDuck",
    "duckdb",
    [
        "database",
        "memory_limit",
        "token_env_var",
    ],
)


class MotherDuckAdapter(PlatformAdapter):
    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    supports_external_tables = True
    plan_capture_phase_eligible = True

    def __init__(self, **config):
        super().__init__(**config)

        if duckdb is None:
            raise ImportError(
                "MotherDuck requires the duckdb package.\n"
                + get_package_install_message("duckdb", "")
                + "\n\nNote: MotherDuck support requires DuckDB >= 0.9.0"
            )

        self._dialect = "duckdb"

        self.token = config.get("token") or os.environ.get("MOTHERDUCK_TOKEN")
        if not self.token:
            raise ValueError(
                "MotherDuck requires authentication token.\n"
                "Set the MOTHERDUCK_TOKEN environment variable.\n"
                "\nGet your token at: https://app.motherduck.com/token-request"
            )

        self.database = config.get("database", "benchbox")
        self.memory_limit = config.get("memory_limit", "4GB")

        self.connection = None

        logger.info(f"MotherDuck adapter initialized for database: {self.database}")

    @property
    def platform_name(self) -> str:
        return "MotherDuck"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        md_group = parser.add_argument_group("MotherDuck Arguments")
        md_group.add_argument(
            "--motherduck-database",
            type=str,
            default="benchbox",
            help="MotherDuck database name (default: benchbox)",
        )
        md_group.add_argument(
            "--motherduck-token",
            type=str,
            help="MotherDuck authentication token (or use MOTHERDUCK_TOKEN env)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        adapter_config = {
            "benchmark": config.get("benchmark"),
        }

        database = config.get("motherduck_database") or config.get("database")
        if database:
            adapter_config["database"] = database
        if config.get("motherduck_token"):
            adapter_config["token"] = config["motherduck_token"]
        if config.get("memory_limit"):
            adapter_config["memory_limit"] = config["memory_limit"]

        from benchbox.platforms.base.config_utils import PLAN_FORWARD_KEYS

        for key in PLAN_FORWARD_KEYS:
            if key in config and config[key] is not None:
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

    def get_target_dialect(self) -> str:
        return resolve_dialect_for_query_translation("motherduck")

    def create_connection(self, connection_config: Optional[dict[str, Any]] = None):
        self.fail_closed_on_force_recreate(
            platform_label="MotherDuck",
            database=self.database,
            manual_hint="Delete the database manually via the MotherDuck UI",
        )
        if self.connection is not None:
            return self.connection

        connection_string = f"md:{self.database}?motherduck_token={self.token}"

        try:
            logger.info(f"Connecting to MotherDuck database: {self.database}")
            self.connection = duckdb.connect(connection_string)

            self.connection.execute(f"SET memory_limit = '{self.memory_limit}'")

            result = self.connection.execute("SELECT 1 AS test").fetchone()
            if result and result[0] == 1:
                logger.info("MotherDuck connection successful")
            else:
                raise ConnectionError("MotherDuck connection test failed")

            return self.connection

        except Exception as e:
            safe_error = _redact_motherduck_token(str(e), self.token)
            logger.error(f"Failed to connect to MotherDuck: {safe_error}")
            raise ConnectionError(
                f"Failed to connect to MotherDuck: {safe_error}\nCheck your MOTHERDUCK_TOKEN and network connection."
            ) from None

    def close_connection(self, connection=None):
        conn = connection or self.connection
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.warning(f"Error closing MotherDuck connection: {e}")
        if connection is None:
            self.connection = None

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        from benchbox.platforms.base.result_capture import is_dml_query

        analyze = self.analyze_plans
        if analyze and is_dml_query(query):
            analyze = False
        explain_options = "ANALYZE, FORMAT JSON" if analyze else "FORMAT JSON"
        try:
            rows = (connection or self.connection).execute(f"EXPLAIN ({explain_options}) {query}").fetchall()
            parts = [str(row[1]) for row in rows]
            return "\n".join(parts) if parts else None
        except Exception as e:
            logger.debug(f"Failed to get MotherDuck query plan: {e}")
            return None

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser

        return DuckDBQueryPlanParser()

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
        conn = connection or self.connection
        if conn is None:
            conn = self.create_connection()

        start_time = mono_time()
        try:
            result = conn.execute(query)
            rows = result.fetchall()
            execution_time = elapsed_seconds(start_time)
            logger.debug(f"Query {query_id} completed in {execution_time:.3f}s, returned {len(rows)} rows")
            result_dict = {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "SUCCESS",
                "execution_time_seconds": execution_time,
                "rows_returned": len(rows),
                "first_row": rows[0] if rows else None,
                "error": None,
            }
        except Exception as e:
            execution_time = elapsed_seconds(start_time)
            logger.error(f"Query {query_id} failed after {execution_time:.2f}s: {e}")
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "FAILED",
                "execution_time_seconds": execution_time,
                "rows_returned": 0,
                "error": str(e),
                "error_type": type(e).__name__,
            }

        if not self.capture_plans:
            self.display_query_plan_if_enabled(conn, query, query_id)

        self._merge_plan_capture_into_result(result_dict, conn, query, query_id)

        return result_dict

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        info: dict[str, Any] = {
            "platform_type": "motherduck",
            "platform_name": self.platform_name,
            "platform_version": "unknown",
            "connection_mode": "remote",
            "configuration": {
                "database": self.database,
            },
            "client_library_version": getattr(duckdb, "__version__", None) if duckdb else None,
        }

        conn = connection or self.connection
        if conn:
            try:
                result = conn.execute("SELECT version()").fetchone()
                if result:
                    info["platform_version"] = result[0]
                    info["engine_version"] = result[0]
                    info["engine_version_source"] = "sql_query"
            except Exception:
                logger.debug("Failed to probe MotherDuck engine version via SELECT version()", exc_info=True)

        return info

    def get_platform_metadata(self) -> dict[str, Any]:
        metadata = {
            "platform_type": "motherduck",
            "platform_name": self.platform_name,
            "dialect": self._dialect,
            "database": self.database,
            "inherits_from": "duckdb",
        }

        if self.connection:
            try:
                result = self.connection.execute("SELECT version()").fetchone()
                if result:
                    metadata["duckdb_version"] = result[0]
            except Exception:
                pass

        return metadata

    def test_connection(self) -> bool:
        try:
            conn = self.create_connection()
            result = conn.execute("SELECT 1").fetchone()
            return result is not None and result[0] == 1
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

    def create_schema(self, benchmark, connection) -> float:
        start_time = time.perf_counter()

        schema_sql = benchmark.get_create_tables_sql(dialect="duckdb")

        for statement in schema_sql.split(";"):
            statement = statement.strip()
            if statement:
                connection.execute(statement)

        return time.perf_counter() - start_time

    def load_data(self, benchmark, connection, data_dir: Path) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = time.perf_counter()
        row_counts: dict[str, int] = {}
        effective_tuning = self.unified_tuning_configuration if self.tuning_enabled else None

        for table_file in data_dir.glob("*.parquet"):
            table_name = table_file.stem

            logger.info(f"Loading {table_name} from {table_file}")
            connection.execute(f"""
                INSERT INTO {table_name}
                SELECT * FROM read_parquet('{table_file}')
            """)

            result = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            row_counts[table_name] = result[0] if result else 0

            if effective_tuning:
                self.apply_ctas_sort(table_name, effective_tuning, connection)
                self.run_post_load_tunings(table_name, effective_tuning, connection)

        load_time = time.perf_counter() - start_time
        return row_counts, load_time, None

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        return _create_duckdb_external_views(self, benchmark, connection, data_dir)

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[TuningColumn]) -> str | None:
        return _build_duckdb_ctas_sort_sql(table_name, sort_columns)

    def apply_platform_optimizations(self, platform_config, connection) -> None:
        pass

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection) -> None:
        pass

    def configure_for_benchmark(self, connection, benchmark_type: str) -> None:
        try:
            connection.execute(f"SET memory_limit = '{self.memory_limit}'")
        except Exception as e:
            logger.warning(f"Could not set memory_limit: {e}")


__all__ = ["MotherDuckAdapter"]
