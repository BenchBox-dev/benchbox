# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
from dataclasses import dataclass, replace
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

try:
    import psycopg
except ImportError:
    psycopg = None

CITUS_EXTENSION = "citus"
CITUS_COLUMNAR_EXTENSION = "citus_columnar"


@dataclass(frozen=True)
class _Placement:
    kind: str
    column: str | None = None
    source: str = "tuning_file"
    columnar: bool = False
    distribution_label: str | None = None
    reference_label: str | None = None
    columnar_label: str | None = None


class CitusAdapter(PostgreSQLAdapter):
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
        self._placements: dict[str, _Placement] = {}
        self._partitioned_tables: set[str] = set()

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
        self._reset_placement_state(benchmark)
        if self._placement_requested():
            self._apply_placements(benchmark, connection, reused=False)
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
            "WHERE table_schema = current_schema() AND table_name = %s AND lower(column_name) = lower(%s)",
            (table_name, column),
        )
        return cursor.fetchone() is not None

    def _reset_placement_state(self, benchmark: Any) -> None:
        self._partitioned_tables = set()
        self._placements = self._placement_plan(benchmark)

    def _placement_requested(self) -> bool:
        return any(placement.kind != "local" or placement.columnar for placement in self._placements.values())

    def _tuning_entry(self, table_name: str) -> tuple[str, Any] | None:
        config = self.get_effective_tuning_configuration() if self.tuning_enabled else None
        for key, tuning in (getattr(config, "table_tunings", None) or {}).items():
            if str(key).lower() == table_name.lower():
                return str(key), tuning
        return None

    def _placement_plan(self, benchmark: Any) -> dict[str, _Placement]:
        plan: dict[str, _Placement] = {}
        for table_name in self._benchmark_table_names(benchmark):
            entry = self._tuning_entry(table_name)
            key, tuning = entry if entry is not None else (table_name, None)
            columnar = bool(tuning is not None and tuning.columnar)
            columnar_label = f"columnar:{key}" if columnar else None
            ordered = sorted(tuning.distribution or [], key=lambda column: column.order) if tuning is not None else []
            distribution_label = (
                f"distribution:{key} ({', '.join(column.name for column in ordered)})" if ordered else None
            )
            if tuning is not None and tuning.reference:
                self._drop_placement_intent(
                    distribution_label, "a reference table is replicated and cannot also be hash-distributed"
                )
                plan[table_name.lower()] = _Placement(
                    "reference",
                    columnar=columnar,
                    reference_label=f"reference_table:{key}",
                    columnar_label=columnar_label,
                )
            elif len(ordered) > 1:
                self._drop_placement_intent(
                    distribution_label, f"Citus hash-distributes on a single column; {len(ordered)} were requested"
                )
                plan[table_name.lower()] = _Placement("local", columnar=columnar, columnar_label=columnar_label)
            elif ordered:
                plan[table_name.lower()] = _Placement(
                    "distributed",
                    ordered[0].name,
                    columnar=columnar,
                    distribution_label=distribution_label,
                    columnar_label=columnar_label,
                )
            elif self.distribution_column:
                plan[table_name.lower()] = _Placement(
                    "distributed",
                    self._validated_distribution_column(),
                    source="cli_flag",
                    columnar=columnar,
                    columnar_label=columnar_label,
                )
            else:
                plan[table_name.lower()] = _Placement("local", columnar=columnar, columnar_label=columnar_label)
        return plan

    @staticmethod
    def _quoted(table_name: str) -> str:
        return '"' + table_name.replace('"', '""') + '"'

    @staticmethod
    def _literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _drop_placement_intent(self, label: str | None, reason: str) -> None:
        ledger = getattr(self, "_applied_tuning_ledger", None)
        if ledger is not None and label is not None:
            ledger.record_dropped(label, reason)

    def _existing_placement(self, cursor: Any, quoted_table: str) -> tuple[str, str | None] | None:
        rows = self._execute_catalog_query(
            cursor,
            "SELECT partmethod::text, column_to_column_name(logicalrelid, partkey)::text "
            "FROM pg_dist_partition WHERE logicalrelid = %s::regclass",
            (quoted_table,),
        )
        if not rows:
            return None
        return str(rows[0][0]), (str(rows[0][1]) if rows[0][1] is not None else None)

    def _verify_placement(self, cursor: Any, quoted_table: str, method: str, column: str | None) -> str:
        rows = self._execute_catalog_query(
            cursor,
            "SELECT partmethod::text, column_to_column_name(logicalrelid, partkey)::text, colocationid::bigint "
            "FROM pg_dist_partition WHERE logicalrelid = %s::regclass",
            (quoted_table,),
        )
        if not rows:
            raise ValueError("table is not registered in pg_dist_partition")
        observed_method, observed_column, colocation = rows[0]
        if observed_method != method or (column is not None and str(observed_column).lower() != column.lower()):
            raise ValueError(
                f"expected partmethod {method} on {column}, observed partmethod {observed_method} on {observed_column}"
            )
        return f"pg_dist_partition readback: partmethod={observed_method} partkey={observed_column} colocationid={colocation}"

    def _verify_columnar(self, cursor: Any, quoted_table: str) -> str:
        rows = self._execute_catalog_query(
            cursor,
            "SELECT a.amname::text FROM pg_class c JOIN pg_am a ON a.oid = c.relam WHERE c.oid = %s::regclass",
            (quoted_table,),
        )
        if not rows or rows[0][0] != "columnar":
            raise ValueError(f"expected access method columnar, observed {rows[0][0] if rows else None}")
        return "pg_am readback: access method columnar"

    def _is_partitioned(self, cursor: Any, quoted_table: str) -> bool:
        rows = self._execute_catalog_query(
            cursor, "SELECT relkind::text FROM pg_class WHERE oid = %s::regclass", (quoted_table,)
        )
        return bool(rows) and rows[0][0] == "p"

    def _place(
        self,
        cursor: Any,
        connection: Any,
        table_name: str,
        function: str,
        arguments: tuple[str, ...],
        label: str | None,
        check: Any,
        strict: bool,
    ) -> bool:
        from benchbox.core.tuning.applied_ledger import PHASE_DDL, STATEMENT_FAILED

        statement = f"SELECT {function}({', '.join(self._literal(argument) for argument in arguments)})"
        ledger = getattr(self, "_applied_tuning_ledger", None)
        try:
            cursor.execute(statement)
            connection.commit()
        except Exception as exc:
            connection.rollback()
            if strict:
                raise
            self.logger.warning(f"Citus placement not applied for {table_name}: {exc}")
            if ledger is not None and label is not None:
                ledger.record(statement, PHASE_DDL, status=STATEMENT_FAILED, table=table_name, error=exc)
                ledger.record_dropped(label, f"{function} failed: {exc}")
            return False
        if strict:
            return True
        if ledger is not None and label is not None:
            ledger.record(statement, PHASE_DDL, table=table_name, mechanism=f"citus_{function}")
        try:
            reason = check()
        except Exception as exc:
            connection.rollback()
            self.logger.warning(f"Citus placement not verified for {table_name}: {exc}")
            if ledger is not None and label is not None:
                ledger.record_dropped(label, f"catalog verification failed: {exc}")
            return False
        if ledger is not None and label is not None:
            index = ledger.executed_statement_index(lambda recorded: recorded.statement == statement)
            if index is None:
                ledger.record_dropped(label, "catalog readback matched but no executed statement was recorded")
                return False
            ledger.record_satisfied(label, index, reason)
        return True

    def _prepare_columnar(self, cursor: Any, connection: Any) -> str | None:
        if not any(placement.columnar for placement in self._placements.values()):
            return None
        try:
            cursor.execute(f"CREATE EXTENSION IF NOT EXISTS {CITUS_COLUMNAR_EXTENSION}")
            connection.commit()
        except Exception as exc:
            connection.rollback()
            return f"{CITUS_COLUMNAR_EXTENSION} extension is not available: {exc}"
        return None

    def _apply_placements(self, benchmark: Any, connection: Any, reused: bool) -> None:
        cursor = connection.cursor()
        try:
            columnar_error = self._prepare_columnar(cursor, connection)
            for table_name in self._benchmark_table_names(benchmark):
                self._apply_table_placement(cursor, connection, table_name, columnar_error, reused)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def _already_placed(self, table_name: str, placement: _Placement, existing: tuple[str, str | None] | None) -> bool:
        if existing is None:
            return False
        method, column = existing
        if placement.kind == "reference" and method == "n":
            return True
        if (
            placement.kind == "distributed"
            and method == "h"
            and column is not None
            and column.lower() == (placement.column or "").lower()
        ):
            self.logger.info(f"Citus table {table_name} already distributed on column {column}")
            return True
        wanted = "a reference table" if placement.kind == "reference" else f"distribution on {placement.column!r}"
        raise RuntimeError(
            f"Citus table {table_name} is already placed as partmethod {method} on {column!r}, "
            f"but {wanted} was requested; recreate the database or rerun with the matching placement."
        )

    def _apply_table_placement(
        self, cursor: Any, connection: Any, table_name: str, columnar_error: str | None, reused: bool
    ) -> None:
        key = table_name.lower()
        placement = self._placements[key]
        quoted = self._quoted(table_name)
        strict = placement.source == "cli_flag"
        if placement.kind != "local":
            existing = self._existing_placement(cursor, quoted) if reused else None
            if not self._already_placed(table_name, placement, existing):
                placed = self._create_placement(cursor, connection, table_name, quoted, placement, strict)
                if not placed:
                    self._placements[key] = replace(placement, kind="local", column=None)
        if self._is_partitioned(cursor, quoted):
            self._partitioned_tables.add(key)
        if placement.columnar:
            self._apply_columnar(cursor, connection, table_name, quoted, reused, columnar_error)

    def _create_placement(
        self, cursor: Any, connection: Any, table_name: str, quoted: str, placement: _Placement, strict: bool
    ) -> bool:
        if placement.kind == "reference":
            return self._place(
                cursor,
                connection,
                table_name,
                "create_reference_table",
                (quoted,),
                placement.reference_label,
                lambda: self._verify_placement(cursor, quoted, "n", None),
                False,
            )
        column = placement.column or ""
        if not self._table_has_column(cursor, table_name, column):
            if strict:
                self.logger.warning(f"Table {table_name} lacks distribution column {column}; left coordinator-local")
            else:
                self._drop_placement_intent(placement.distribution_label, f"table lacks distribution column {column}")
            return False
        placed = self._place(
            cursor,
            connection,
            table_name,
            "create_distributed_table",
            (quoted, column),
            placement.distribution_label,
            lambda: self._verify_placement(cursor, quoted, "h", column),
            strict,
        )
        if placed and strict:
            self.logger.info(f"Distributed Citus table {table_name} on column {column}")
        return placed

    def _apply_columnar(
        self, cursor: Any, connection: Any, table_name: str, quoted: str, reused: bool, columnar_error: str | None
    ) -> None:
        key = table_name.lower()
        placement = self._placements[key]
        reason = columnar_error
        if reason is None and key in self._partitioned_tables:
            reason = "Citus cannot change the access method of a partitioned table"
        if reason is None and reused and self._is_columnar(cursor, quoted):
            return
        if reason is None:
            columnar = self._place(
                cursor,
                connection,
                table_name,
                "alter_table_set_access_method",
                (quoted, "columnar"),
                placement.columnar_label,
                lambda: self._verify_columnar(cursor, quoted),
                False,
            )
            if columnar:
                return
        else:
            self._drop_placement_intent(placement.columnar_label, reason)
        self._placements[key] = replace(placement, columnar=False)

    def _is_columnar(self, cursor: Any, quoted: str) -> bool:
        try:
            self._verify_columnar(cursor, quoted)
        except ValueError:
            return False
        return True

    def _setup_reused_database_phases(self, benchmark: Any, connection: Any) -> tuple:
        phases = super()._setup_reused_database_phases(benchmark, connection)
        self._reset_placement_state(benchmark)
        if self._placement_requested():
            self._apply_placements(benchmark, connection, reused=True)
        return phases

    def _cluster_skip_reason(self, table_name: str) -> str | None:
        placement = self._placements.get(table_name.lower())
        if placement is None:
            return None
        if placement.columnar:
            return "CLUSTER is not supported on columnar tables"
        if placement.kind == "distributed" and table_name.lower() in self._partitioned_tables:
            return "Citus does not propagate CLUSTER on a partitioned distributed table to its shards"
        return None

    def apply_post_load_tunings(self, table_name: str, effective_config: Any, connection: Any) -> bool:
        if self.postgres_layout_statements(table_name, self.table_tuning_for(effective_config, table_name)):
            reason = self._cluster_skip_reason(table_name)
            if reason is not None:
                ledger = getattr(self, "_applied_tuning_ledger", None)
                if ledger is not None:
                    self._drop_cluster_intents(ledger, table_name, effective_config, reason)
                return False
        return super().apply_post_load_tunings(table_name, effective_config, connection)

    def _verify_cluster_beyond_shell(self, cursor: Any, table_name: str) -> None:
        placement = self._placements.get(table_name.lower())
        if placement is None or placement.kind == "local":
            return
        rows = self._execute_catalog_query(
            cursor,
            "SELECT count(*) FILTER (WHERE success AND result = '1')::bigint, count(*)::bigint "
            "FROM run_command_on_shards(%s::regclass, "
            "$cmd$SELECT count(*) FROM pg_index i WHERE i.indrelid = '%%s'::regclass AND i.indisclustered$cmd$)",
            (self._quoted(table_name),),
        )
        clustered, shards = rows[0]
        if shards == 0 or clustered != shards:
            raise ValueError(f"CLUSTER reached {clustered} of {shards} shards of {table_name}")

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

    _supported_tuning_type_names = (
        "PARTITIONING",
        "CLUSTERING",
        "SORTING",
        "DISTRIBUTION",
        "PRIMARY_KEYS",
        "FOREIGN_KEYS",
    )


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
