# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from benchbox.core.deployment import DEPLOYMENT_SELECTOR_OPTION, deployment_class, select_adapter_deployment
from benchbox.core.execution_engine import (
    MIXED,
    ExecutionEngineReceipt,
    ObservationNotRecognizedError,
    QueryObservationTarget,
    resolve_requested,
    supported_execution_engines,
)
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE

from .base.config_utils import (
    POSTGRES_FAMILY_BASE_OPTIONS,
    POSTGRES_FAMILY_PLATFORM_FIELDS,
    make_platform_config_builder,
)
from .postgresql import POSTGRES_DIALECT, PostgreSQLAdapter, _build_postgres_connection_kwargs

logger = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg import sql as psycopg_sql
except ImportError:
    psycopg = None
    psycopg_sql = None


_DUCKDB_SCAN_NODE = "Custom Scan (DuckDBScan)"
_POSTGRES_PLAN_NODE = re.compile(r"\(cost=\S+ rows=\d+ width=\d+\)")


_TRANSACTION_IDLE = 0
_TRANSACTION_INTRANS = 2
_TRANSACTION_INERROR = 3
_OBSERVATION_SAVEPOINT = "benchbox_engine_observation"


def _transaction_status(connection: Any) -> int | None:
    try:
        return int(connection.info.transaction_status)
    except (AttributeError, TypeError, ValueError):
        return None


def classify_explain_plan(plan_text: str) -> str | None:
    lines = [line for line in plan_text.splitlines() if line.strip()]
    if not lines:
        return None
    if lines[0].lstrip().startswith(_DUCKDB_SCAN_NODE):
        return "duckdb"
    if _DUCKDB_SCAN_NODE in plan_text:
        return MIXED
    return "postgres" if _POSTGRES_PLAN_NODE.search(lines[0]) else None


class PgDuckDBAdapter(PostgreSQLAdapter):
    OBSERVED_ENGINE_SOURCE = "explain"
    OBSERVATION_AT_RUN_END = True
    RUN_END_OBSERVATION_USES_CONNECTION = True
    execution_partitioning_supported = True
    post_load_index_cluster_supported = True
    supports_refresh_functions = False
    plan_capture_phase_eligible = True

    @property
    def platform_name(self) -> str:
        return "pg_duckdb"

    def get_target_dialect(self) -> str:
        return POSTGRES_DIALECT

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
        result = super().execute_query(
            connection, query, query_id, benchmark_type, scale_factor, validate_row_count, stream_id
        )
        if result.get("status") == "SUCCESS":
            self.record_query_engine_observation(connection, query, query_id)
        return result

    def observe_execution_engine(self, lazy: QueryObservationTarget) -> str | None:
        connection = lazy.connection
        status = _transaction_status(connection)
        if status == _TRANSACTION_INERROR:
            raise RuntimeError("the connection's transaction is aborted")
        cursor = connection.cursor()
        savepoint = status == _TRANSACTION_INTRANS
        try:
            if savepoint:
                cursor.execute(f"SAVEPOINT {_OBSERVATION_SAVEPOINT}")
            try:
                cursor.execute(f"EXPLAIN {lazy.query}")
                plan_text = "\n".join(str(row[0]) for row in cursor.fetchall())
            except Exception:
                if savepoint:
                    cursor.execute(f"ROLLBACK TO SAVEPOINT {_OBSERVATION_SAVEPOINT}")
                raise
            if savepoint:
                cursor.execute(f"RELEASE SAVEPOINT {_OBSERVATION_SAVEPOINT}")
        finally:
            cursor.close()
            if status == _TRANSACTION_IDLE and _transaction_status(connection) != _TRANSACTION_IDLE:
                connection.rollback()
        observed = classify_explain_plan(plan_text)
        if observed is None:
            raise ObservationNotRecognizedError("EXPLAIN output not recognized")
        return observed

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        try:
            parser.add_argument(
                "--pgduckdb-host",
                dest="host",
                default="localhost",
                help="PostgreSQL server hostname (with pg_duckdb installed)",
            )
            parser.add_argument(
                "--pgduckdb-port",
                dest="port",
                type=int,
                default=5432,
                help="PostgreSQL server port",
            )
            parser.add_argument(
                "--pgduckdb-database",
                dest="database",
                help="PostgreSQL database name (auto-generated if not specified)",
            )
            parser.add_argument(
                "--pgduckdb-username",
                dest="username",
                default="postgres",
                help="PostgreSQL username",
            )
            parser.add_argument(
                "--pgduckdb-password",
                dest="password",
                help="PostgreSQL password",
            )
            parser.add_argument(
                "--pgduckdb-schema",
                dest="schema",
                default="public",
                help="PostgreSQL schema name",
            )
            parser.add_argument(
                "--pgduckdb-threads",
                dest="postgres_scan_threads",
                type=int,
                default=0,
                help="Threads for PostgreSQL table scanning (0 = auto)",
            )
        except Exception:
            pass

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> PgDuckDBAdapter:
        adapter_config = _build_postgres_connection_kwargs(config)
        if config.get(DEPLOYMENT_SELECTOR_OPTION) is not None:
            adapter_config[DEPLOYMENT_SELECTOR_OPTION] = config[DEPLOYMENT_SELECTOR_OPTION]
        adapter_config["execution_engine"] = config.get("execution_engine") or DEFAULT_EXECUTION_ENGINE
        adapter_config["postgres_scan_threads"] = config.get("postgres_scan_threads", 0)
        adapter_config["compare_native"] = config.get("compare_native", False)
        adapter_config["duckdb_db_path"] = config.get("duckdb_db_path")
        if config.get("motherduck_token"):
            adapter_config["motherduck_token"] = config["motherduck_token"]
        return cls(**adapter_config)

    def __init__(self, **config):
        deployment_mode = select_adapter_deployment("pg-duckdb", config, "self-hosted")
        self.deployment_mode = deployment_mode.lower()
        self.deployment_selected = self.deployment_mode
        self.deployment_selected_class = deployment_class("pg-duckdb", self.deployment_mode)

        valid_modes = {"self-hosted", "motherduck"}
        if self.deployment_mode not in valid_modes:
            raise ValueError(
                f"Invalid pg_duckdb deployment mode '{self.deployment_mode}'. "
                f"Valid modes: {', '.join(sorted(valid_modes))}"
            )

        if self.deployment_mode == "motherduck":
            self._configure_motherduck_mode(config)

        super().__init__(**config)

        requested = config.get("execution_engine") or DEFAULT_EXECUTION_ENGINE
        self._engine_receipt = self.resolve_execution_engine(requested)
        self.force_execution = self._engine_receipt.applied == "duckdb"
        self.postgres_scan_threads = config.get("postgres_scan_threads", 0)

        raw_compare = config.get("compare_native", False)
        self.compare_native: bool = (
            str(raw_compare).lower() == "true" if isinstance(raw_compare, str) else bool(raw_compare)
        )
        self.duckdb_db_path: str | None = config.get("duckdb_db_path")

        self.motherduck_token = config.get("motherduck_token") or os.environ.get("MOTHERDUCK_TOKEN")

    def resolve_execution_engine(self, requested: str) -> ExecutionEngineReceipt:
        resolved = resolve_requested("pg-duckdb", requested)
        explicit = None if resolved == DEFAULT_EXECUTION_ENGINE else resolved
        selected = explicit or "duckdb"
        engines = supported_execution_engines("pg-duckdb")
        return ExecutionEngineReceipt(
            requested=selected if explicit is not None else DEFAULT_EXECUTION_ENGINE,
            applied=selected,
            applied_class=engines[selected].engine_class,
            applied_native={"duckdb.force_execution": selected == "duckdb"},
            resolution="explicit" if explicit is not None else "platform_default",
        )

    def _configure_motherduck_mode(self, config: dict) -> None:
        token = config.get("motherduck_token") or os.environ.get("MOTHERDUCK_TOKEN")
        if not token:
            raise ValueError(
                "MotherDuck deployment mode requires authentication token.\n"
                "Set the MOTHERDUCK_TOKEN environment variable.\n"
                "It is deliberately not a --platform-option: options appear in "
                "shell history and in the process list.\n"
                "Get your token at https://app.motherduck.com/token"
            )
        config["motherduck_token"] = token

    def create_connection(self, **connection_config) -> Any:
        conn = super().create_connection(**connection_config)

        cursor = conn.cursor()
        try:
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_duckdb'")
            result = cursor.fetchone()
            if result:
                self.logger.info(f"pg_duckdb extension version: {result[0]}")
            else:
                self.logger.info("pg_duckdb extension not found, attempting to create...")
                cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_duckdb")
                conn.commit()
                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_duckdb'")
                result = cursor.fetchone()
                if result:
                    self.logger.info(f"Created pg_duckdb extension version: {result[0]}")
                else:
                    raise RuntimeError(
                        "pg_duckdb extension is not available on this PostgreSQL server. "
                        "Install pg_duckdb (https://github.com/duckdb/pg_duckdb) or use "
                        "the 'postgresql' or 'duckdb' platform instead."
                    )

            self._apply_pgduckdb_session_gucs(cursor)

            conn.commit()

        except RuntimeError:
            cursor.close()
            raise
        except Exception as e:
            self.logger.error(f"Failed to configure pg_duckdb extension: {e}")
            cursor.close()
            raise RuntimeError(f"pg_duckdb configuration failed: {e}") from e
        finally:
            if not cursor.closed:
                cursor.close()

        return conn

    def _apply_pgduckdb_session_gucs(self, cursor: Any) -> None:
        cursor.execute(f"SET duckdb.force_execution = {'true' if self.force_execution else 'false'}")
        if self.force_execution:
            self.logger.info("Enabled duckdb.force_execution for DuckDB query routing")

        if self.postgres_scan_threads > 0:
            cursor.execute(f"SET duckdb.threads_for_postgres_scan = {int(self.postgres_scan_threads)}")
            self.logger.info(f"Set duckdb.threads_for_postgres_scan = {self.postgres_scan_threads}")

        if self.deployment_mode == "motherduck" and self.motherduck_token:
            cursor.execute(
                psycopg_sql.SQL("SET duckdb.motherduck_token = {}").format(psycopg_sql.Literal(self.motherduck_token))
            )
            self.logger.info("Configured MotherDuck token for hybrid queries")

    def _apply_stream_session_state(self, connection: Any) -> None:
        cursor = connection.cursor()
        try:
            self._apply_pgduckdb_session_gucs(cursor)
        finally:
            cursor.close()

    def _execute_catalog_query(self, cursor: Any, query: str, params: tuple) -> list:
        cursor.execute("SET duckdb.force_execution = false")
        try:
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            cursor.execute(f"SET duckdb.force_execution = {'true' if self.force_execution else 'false'}")

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        if not self.tuning_enabled:
            return

        super().configure_for_benchmark(connection, benchmark_type)

        cursor = connection.cursor()
        try:
            if benchmark_type == "olap":
                cursor.execute(f"SET duckdb.force_execution = {'true' if self.force_execution else 'false'}")

            connection.commit()
        except Exception as e:
            self.logger.debug(f"Could not set pg_duckdb benchmark optimizations: {e}")
        finally:
            cursor.close()

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = super().get_platform_info(connection)

        platform_info["platform_type"] = "pg_duckdb"
        platform_info["platform_name"] = "pg_duckdb"

        platform_info["configuration"]["force_execution"] = self.force_execution
        platform_info["configuration"]["postgres_scan_threads"] = self.postgres_scan_threads
        platform_info["configuration"]["deployment_mode"] = self.deployment_mode
        platform_info["configuration"]["execution_engine"] = self.finalize_query_engine_observation(
            asdict(self._engine_receipt), connection
        )

        if connection:
            try:
                cursor = connection.cursor()

                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_duckdb'")
                result = cursor.fetchone()
                if result:
                    platform_info["pg_duckdb_version"] = result[0]

                cursor.close()
            except Exception as e:
                self.logger.debug(f"Error getting pg_duckdb info: {e}")

        return platform_info

    def run_native_comparison(
        self,
        query_results: list[dict],
        query_sql_map: dict[str, str],
        scale_factor: float,
    ) -> Any:
        if not self.compare_native:
            return None

        try:
            import duckdb as _duckdb
        except ImportError:
            self.logger.warning(
                "compare_native=true requested but duckdb package is not installed. "
                "Install duckdb to enable native comparison."
            )
            return None

        from benchbox.core.results.models import NativeComparison, NativeComparisonEntry

        pg_timings: dict[str, list[float]] = {}
        for row in query_results:
            qid = row.get("query_id") or row.get("id")
            run_type = row.get("run_type", "measurement")
            if qid and run_type != "warmup" and qid in query_sql_map:
                ms = row.get("ms") or row.get("execution_time_ms")
                if ms is not None:
                    pg_timings.setdefault(qid, []).append(float(ms))

        if not pg_timings:
            self.logger.info("run_native_comparison: no matching measurement rows found")
            return None

        db_path = self.duckdb_db_path or ":memory:"
        entries: list[NativeComparisonEntry] = []

        try:
            native_conn = _duckdb.connect(db_path, read_only=bool(self.duckdb_db_path))
        except Exception as exc:
            self.logger.warning(f"run_native_comparison: could not open DuckDB at {db_path!r}: {exc}")
            return None

        try:
            for query_id, pg_ms_list in sorted(pg_timings.items()):
                sql = query_sql_map[query_id]
                pg_mean_ms = sum(pg_ms_list) / len(pg_ms_list)
                t0 = time.monotonic()
                try:
                    native_conn.execute(sql).fetchall()
                    native_ms = (time.monotonic() - t0) * 1000
                except Exception as exc:
                    self.logger.debug(f"Native DuckDB query {query_id} failed: {exc}")
                    continue
                entries.append(
                    NativeComparisonEntry(
                        query_id=query_id,
                        pg_duckdb_ms=round(pg_mean_ms, 3),
                        duckdb_ms=round(native_ms, 3),
                        delta_ms=round(pg_mean_ms - native_ms, 3),
                    )
                )
        finally:
            native_conn.close()

        if not entries:
            return None

        deltas = [e.delta_ms for e in entries]
        return NativeComparison(
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
            scale_factor=scale_factor,
            total_queries=len(entries),
            mean_delta_ms=round(sum(deltas) / len(deltas), 3),
            max_delta_ms=round(max(deltas), 3),
            entries=entries,
        )

    _supported_tuning_type_names = ("PARTITIONING", "CLUSTERING", "SORTING", "PRIMARY_KEYS", "FOREIGN_KEYS")


_build_pg_duckdb_config = make_platform_config_builder(
    "pg-duckdb",
    __name__,
    "pg_duckdb",
    "psycopg",
    POSTGRES_FAMILY_PLATFORM_FIELDS
    + (
        "postgres_scan_threads",
        "compare_native",
        "duckdb_db_path",
    ),
    base_options={
        **POSTGRES_FAMILY_BASE_OPTIONS,
        "postgres_scan_threads": 0,
        "compare_native": False,
    },
)
