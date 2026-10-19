# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from benchbox.core.deployment import deployment_class, select_adapter_deployment
from benchbox.core.execution_engine import (
    ExecutionEngineReceipt,
    legacy_option_engine,
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


class PgDuckDBAdapter(PostgreSQLAdapter):
    execution_partitioning_supported = True
    post_load_index_cluster_supported = True
    supports_refresh_functions = False
    plan_capture_phase_eligible = True

    @property
    def platform_name(self) -> str:
        return "pg_duckdb"

    def get_target_dialect(self) -> str:
        return POSTGRES_DIALECT

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
                "--pgduckdb-force-execution",
                dest="force_execution",
                action=argparse.BooleanOptionalAction,
                default=None,
                help="Deprecated alias for --execution-engine (duckdb or postgres)",
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
        nested_options = config.get("options") if isinstance(config.get("options"), dict) else {}
        adapter_config["force_execution"] = config.get("force_execution")
        if adapter_config["force_execution"] is None:
            adapter_config["force_execution"] = nested_options.get("force_execution")
        adapter_config["execution_engine"] = config.get("execution_engine") or DEFAULT_EXECUTION_ENGINE
        adapter_config["postgres_scan_threads"] = config.get("postgres_scan_threads", 0)
        adapter_config["compare_native"] = config.get("compare_native", False)
        adapter_config["duckdb_db_path"] = config.get("duckdb_db_path")
        adapter_config["deployment_mode"] = (
            config.get("deployment_mode") or nested_options.get("deployment_mode") or "self-hosted"
        )
        if config.get("motherduck_token"):
            adapter_config["motherduck_token"] = config["motherduck_token"]
        return cls(**adapter_config)

    def __init__(self, **config):
        deployment_mode = select_adapter_deployment("pg-duckdb", config, config.get("deployment_mode", "self-hosted"))
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

        self._legacy_force_execution = config.get("force_execution")
        if self._legacy_force_execution is None and isinstance(config.get("options"), dict):
            self._legacy_force_execution = config["options"].get("force_execution")

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
        legacy = None
        if self._legacy_force_execution is not None:
            if not isinstance(self._legacy_force_execution, bool):
                raise ValueError("pg-duckdb force_execution must be a boolean")
            legacy = legacy_option_engine("pg-duckdb", "force_execution", self._legacy_force_execution)
        if explicit is not None and legacy is not None and explicit != legacy:
            raise ValueError(
                "Conflicting execution engine sources: "
                f"explicit setting requests {explicit!r} vs legacy option force_execution requests {legacy!r}"
            )
        selected = explicit or legacy or "duckdb"
        engines = supported_execution_engines("pg-duckdb")
        if explicit is not None:
            resolution = "explicit"
        elif legacy is not None:
            resolution = "legacy_option"
        else:
            resolution = "platform_default"
        return ExecutionEngineReceipt(
            requested=selected if explicit is not None or legacy is not None else DEFAULT_EXECUTION_ENGINE,
            applied=selected,
            applied_class=engines[selected].engine_class,
            applied_native={"duckdb.force_execution": selected == "duckdb"},
            resolution=resolution,
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

    def create_schema(self, benchmark, connection: Any) -> float:
        duration = super().create_schema(benchmark, connection)
        self._verify_partitioning_catalog(connection)
        return duration

    def _verify_partitioning_catalog(self, connection: Any) -> None:
        if not self.tuning_enabled:
            return
        config = self.get_effective_tuning_configuration()
        if config is None or config.primary_keys.enabled or config.unique_constraints.enabled:
            return
        ledger = getattr(self, "_applied_tuning_ledger", None)
        cursor = connection.cursor()
        try:
            for config_name, table_tuning in (config.table_tunings or {}).items():
                columns = sorted(table_tuning.partitioning or [], key=lambda column: column.order)
                if not columns:
                    continue
                label = f"partitioning:{config_name} ({', '.join(column.name for column in columns)})"
                cursor.execute(
                    "SELECT pg_get_partkeydef(c.oid) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'p'",
                    (self.schema, str(config_name).lower()),
                )
                row = cursor.fetchone()
                if not row:
                    if ledger is not None:
                        ledger.record_dropped(label, "catalog readback: table is not a partitioned table")
                    continue
                definition = str(row[0])
                strategy = definition.split("(", 1)[0].strip().upper()
                inside = definition[definition.find("(") + 1 : definition.rfind(")")]
                observed = [part.strip().strip('"').lower() for part in inside.split(",") if part.strip()]
                expected = [column.name.lower() for column in columns]
                first_type = columns[0].type.upper()
                expected_strategy = (
                    "RANGE"
                    if ("DATE" in first_type or "TIME" in first_type)
                    else "HASH"
                    if "INT" in first_type
                    else "LIST"
                )
                if observed != expected or strategy != expected_strategy:
                    if ledger is not None:
                        ledger.record_dropped(
                            label,
                            f"catalog verification failed: expected {expected_strategy} {expected}, "
                            f"observed {definition}",
                        )
                    continue
                bare_name = str(config_name).lower()
                if ledger is not None:
                    from benchbox.core.tuning.introspection import statement_table

                    def _matches(statement: Any, _bare: str = bare_name) -> bool:
                        target = (statement_table(statement) or "").lower().strip('"').split(".")[-1]
                        return target == _bare and "partition by" in statement.statement.lower()

                    index = ledger.executed_statement_index(_matches)
                    if index is not None:
                        ledger.record_satisfied(label, index, f"pg_get_partkeydef catalog readback: {definition}")
                    else:
                        ledger.record_dropped(
                            label,
                            f"catalog readback matched ({definition}) but no executed PARTITION BY "
                            "statement was recorded",
                        )
        finally:
            cursor.close()

    def apply_post_load_tunings(self, table_name: str, effective_config: Any, connection: Any) -> bool:
        statements = self.postgres_layout_statements(table_name, self.table_tuning_for(effective_config, table_name))
        if not statements:
            return False
        from benchbox.core.tuning.applied_ledger import PHASE_POST_LOAD

        ledger = getattr(self, "_applied_tuning_ledger", None)
        cursor = connection.cursor()
        executed: list[str] = []
        index_committed = False
        cluster_committed = False
        observed: list[str] = []
        try:
            expected = self._expected_cluster_columns(effective_config, table_name)
            for statement in statements:
                if statement.lstrip().upper().startswith("CLUSTER"):
                    groups = self._index_column_groups(cursor, table_name, clustered_only=False)
                    if not groups or any(group != expected for group in groups):
                        raise ValueError(
                            f"Index catalog mismatch before CLUSTER for {table_name}: "
                            f"expected {expected}, observed {groups}"
                        )
                    connection.commit()
                    index_committed = True
                    previous_autocommit = connection.autocommit
                    try:
                        connection.autocommit = True
                        cursor.execute(statement)
                    finally:
                        connection.autocommit = previous_autocommit
                    cluster_committed = True
                else:
                    cursor.execute(statement)
                executed.append(statement)
            groups = self._index_column_groups(cursor, table_name, clustered_only=True)
            if not groups or any(group != expected for group in groups):
                raise ValueError(
                    f"Clustered index catalog mismatch for {table_name}: expected {expected}, observed {groups}"
                )
            observed = groups[0]
        except Exception as exc:
            self._record_post_load_failure(
                ledger,
                statements,
                executed,
                table_name,
                effective_config,
                exc,
                connection=connection,
                index_committed=index_committed,
                cluster_committed=cluster_committed,
            )
            raise
        finally:
            cursor.close()
        connection.commit()
        if ledger is not None:
            for statement in executed:
                ledger.record(statement, PHASE_POST_LOAD, table=table_name)
            self._record_cluster_satisfied(ledger, table_name, effective_config, observed)
        self.logger.info(f"Clustered {table_name} on its tuned index")
        return True

    def _record_post_load_failure(
        self,
        ledger: Any,
        statements: list[str],
        executed: list[str],
        table_name: str,
        effective_config: Any,
        exc: Exception,
        *,
        connection: Any,
        index_committed: bool,
        cluster_committed: bool,
    ) -> None:
        from benchbox.core.tuning.applied_ledger import PHASE_POST_LOAD, STATEMENT_FAILED
        from benchbox.core.tuning.interface import TuningType

        if ledger is None:
            if not index_committed and not cluster_committed:
                connection.rollback()
            return
        if cluster_committed:
            for statement in executed:
                ledger.record(statement, PHASE_POST_LOAD, table=table_name)
            tuning = self.table_tuning_for(effective_config, table_name)
            if tuning is not None:
                for kind, columns in (
                    (TuningType.CLUSTERING, tuning.clustering),
                    (TuningType.SORTING, tuning.sorting),
                ):
                    if columns:
                        ordered = sorted(columns, key=lambda column: column.order)
                        label = f"{kind.value}:{tuning.table_name} ({', '.join(c.name for c in ordered)})"
                        ledger.record_dropped(label, f"catalog verification failed: {exc}")
            return
        if index_committed:
            for statement in executed:
                ledger.record(statement, PHASE_POST_LOAD, table=table_name)
            for statement in statements:
                if statement not in executed:
                    ledger.record(statement, PHASE_POST_LOAD, status=STATEMENT_FAILED, table=table_name, error=exc)
            return
        connection.rollback()
        for statement in statements:
            ledger.record(statement, PHASE_POST_LOAD, status=STATEMENT_FAILED, table=table_name, error=exc)

    def _record_cluster_satisfied(
        self, ledger: Any, table_name: str, effective_config: Any, observed: list[str]
    ) -> None:
        from benchbox.core.tuning.interface import TuningType

        tuning = self.table_tuning_for(effective_config, table_name)
        index = ledger.executed_statement_index(
            lambda statement, _name=table_name: (
                statement.table == _name and "create index" in statement.statement.lower()
            )
        )
        if index is None or tuning is None:
            return
        for kind, columns in (
            (TuningType.CLUSTERING, tuning.clustering),
            (TuningType.SORTING, tuning.sorting),
        ):
            if columns:
                ordered = sorted(columns, key=lambda column: column.order)
                label = f"{kind.value}:{tuning.table_name} ({', '.join(column.name for column in ordered)})"
                ledger.record_satisfied(label, index, f"pg_index indisclustered catalog readback: {observed}")

    def _expected_cluster_columns(self, effective_config: Any, table_name: str) -> list[str]:
        tuning = self.table_tuning_for(effective_config, table_name)
        if tuning is None:
            return []
        combined = list(tuning.clustering or []) + [
            column for column in (tuning.sorting or []) if column not in (tuning.clustering or [])
        ]
        return [column.name.lower() for column in sorted(combined, key=lambda column: column.order)]

    def _index_column_groups(self, cursor: Any, table_name: str, *, clustered_only: bool) -> list[list[str]]:
        clustered_filter = "AND i.indisclustered" if clustered_only else ""
        cursor.execute(
            "WITH RECURSIVE tree AS ("
            "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s "
            "UNION ALL "
            "SELECT i.inhrelid FROM pg_inherits i JOIN tree ON tree.oid = i.inhparent"
            ") "
            "SELECT i.indrelid, k.pos, a.attname FROM pg_index i "
            "JOIN tree ON tree.oid = i.indrelid "
            "JOIN LATERAL unnest(string_to_array(i.indkey::text, ' ')::smallint[]) "
            "WITH ORDINALITY AS k(attnum, pos) ON true "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
            f"WHERE true {clustered_filter} "
            "ORDER BY i.indrelid, k.pos",
            (self.schema, table_name.lower()),
        )
        groups: dict[Any, list[tuple[int, str]]] = {}
        for indrelid, pos, attname in cursor.fetchall():
            groups.setdefault(indrelid, []).append((int(pos), str(attname).lower()))
        return [[name for _pos, name in sorted(entries)] for entries in groups.values()]

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
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
        platform_info["configuration"]["execution_engine"] = asdict(self._engine_receipt)

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

    _supported_tuning_type_names = ("PARTITIONING", "CLUSTERING", "PRIMARY_KEYS", "FOREIGN_KEYS")


_build_pg_duckdb_config = make_platform_config_builder(
    "pg-duckdb",
    __name__,
    "pg_duckdb",
    "psycopg",
    POSTGRES_FAMILY_PLATFORM_FIELDS
    + (
        "force_execution",
        "postgres_scan_threads",
        "compare_native",
        "duckdb_db_path",
        "deployment_mode",
    ),
    base_options={
        **POSTGRES_FAMILY_BASE_OPTIONS,
        "postgres_scan_threads": 0,
        "compare_native": False,
    },
)
