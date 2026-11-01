# Copyright 2026 Joe Harris / BenchBox Project

# This implementation is derived from TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

import importlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from benchbox.core.write_primitives.dataframe_operations import (
        DataFrameWriteCapabilities,
        DataFrameWriteOperationsManager,
    )

from benchbox.core.connection import DatabaseConnection
from benchbox.core.primitives_benchmark_utils import (
    build_tpch_staging_tables_sql,
    failed_platform_error,
    fetch_count_probe,
    quote_identifier_for_dialect,
    replace_table_sql,
    replaces_tables_in_place,
    summarize_validation_failures,
    table_exists,
)
from benchbox.core.query_catalog_base import TranslatableQueryMixin
from benchbox.core.tpch.schema import TABLES_BY_NAME as TPCH_TABLES_BY_NAME
from benchbox.core.transactional.benchmark_base import TransactionalBenchmarkBase
from benchbox.core.write_primitives.generator import WritePrimitivesDataGenerator
from benchbox.core.write_primitives.operations import WriteOperationsManager
from benchbox.core.write_primitives.schema import (
    STAGING_TABLES,
    TABLES,
    get_all_staging_tables_sql,
    get_create_table_sql,
)
from benchbox.sql_compat.rules.execution_filter.doris_write_primitives import doris_write_primitive_skip_reason
from benchbox.sql_compat.rules.execution_filter.duckdb_write_primitives import (
    duckdb_write_primitive_skip_reason,
)
from benchbox.sql_compat.rules.execution_filter.postgres_write_primitives import (
    POSTGRES_WRITE_PRIMITIVES_CATEGORY_SKIPS,
    POSTGRES_WRITE_PRIMITIVES_OPERATION_SKIPS,
)
from benchbox.sql_compat.rules.execution_filter.questdb_write_primitives import questdb_write_primitive_skip_reason
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.cloud_storage import normalize_output_dir
from benchbox.utils.dialect_utils import split_sql_statements
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path

_POSTGRES_OPERATION_SKIP_DIALECTS = frozenset({"postgres", "postgresql"})

_OPTIONAL_WHEN_SOURCE_MISSING = {
    "delete_ops_supplier": "GDPR deletion operations will not be available.",
    "scd2_ops_dim_customer": "SCD Type 2 dimension operations will not be available.",
    "scd2_ops_stage_customer": "SCD Type 2 dimension operations will not be available.",
}


def _pk_lock_bypass_required(dialect: str) -> bool:
    importlib.import_module("benchbox.sql_compat.rules.schema_emit.pk_capability")
    from benchbox.sql_compat.actions import CompatAction
    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.registry import REGISTRY

    ctx = CompatibilityContext(
        platform=dialect.lower(),
        platform_version=None,
        benchmark="write_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect=dialect,
    )
    registry_decision = REGISTRY.resolve(ctx)

    if registry_decision is not None:
        return registry_decision.action != CompatAction.NATIVE
    return False


@dataclass
class OperationResult:
    operation_id: str
    success: bool
    write_duration_ms: float
    rows_affected: int
    validation_duration_ms: float
    validation_passed: bool
    validation_results: list[dict[str, Any]]
    cleanup_duration_ms: float
    cleanup_success: bool
    status: str = "SUCCESS"
    error: Optional[str] = None
    cleanup_warning: Optional[str] = None
    skip_reason: Optional[str] = None
    executed_sql: Optional[str] = None


BOUNDS_BASE_SCALE_FACTOR = 0.01


def _scaled_value_bounds(val_query: Any, scale_factor: float) -> tuple[float, float]:
    lo = float(val_query.expected_value_min)
    hi = float(val_query.expected_value_max)
    if getattr(val_query, "bounds_scale_with_sf", False):
        factor = float(scale_factor) / BOUNDS_BASE_SCALE_FACTOR
        return lo * factor, hi * factor
    return lo, hi


def _check_validation_query(
    val_query: Any,
    actual_rows: int,
    val_result: list | None = None,
    scale_factor: float = BOUNDS_BASE_SCALE_FACTOR,
) -> bool:
    expected_rows = val_query.expected_rows
    if expected_rows is not None:
        return actual_rows == expected_rows
    if val_query.expected_rows_min is not None or val_query.expected_rows_max is not None:
        min_val = val_query.expected_rows_min if val_query.expected_rows_min is not None else 0
        max_val = val_query.expected_rows_max if val_query.expected_rows_max is not None else float("inf")
        return min_val <= actual_rows <= max_val
    if (
        getattr(val_query, "expected_value_min", None) is not None
        and getattr(val_query, "expected_value_max", None) is not None
    ):
        if not val_result:
            return False
        lo, hi = _scaled_value_bounds(val_query, scale_factor)
        for row in val_result:
            if not row or row[0] is None:
                return False
            try:
                scalar = float(row[0])
            except (TypeError, ValueError):
                return False
            if not (lo <= scalar <= hi):
                return False
        return True
    return True


def _resolve_validation_sql(
    val_query: Any,
    platform_key: str | None,
    fallback_key: str | None = None,
) -> tuple[str | None, str | None]:
    found, override = TransactionalBenchmarkBase._lookup_platform_override(
        getattr(val_query, "platform_overrides", None), platform_key, fallback_key
    )
    if not found:
        return val_query.sql, None
    if override is None:
        return None, (
            f"Validation '{val_query.id}' explicitly skipped on platform '{platform_key}' "
            "via null platform_overrides entry"
        )
    return override, None


_SINGLE_STATEMENT_SETUP_DIALECTS = frozenset({"sqlite", "databricks", "questdb", "presto", "trino"})
_NO_TRUNCATE_DIALECTS = frozenset({"sqlite"})
_SUMMED_ROWCOUNT_DIALECTS = frozenset({"sqlite"})


class WritePrimitivesBenchmark(TranslatableQueryMixin, TransactionalBenchmarkBase["OperationResult"]):
    _source_dialect = "duckdb"
    _native_source_passthrough = True
    _benchmark_label = "Write Primitives"
    _staging_tables = STAGING_TABLES
    DATAFRAME_WORKLOAD_USES_PLATFORM_ENGINE = False

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        **config: Any,
    ):
        config = dict(config)
        quiet = config.pop("quiet", False)

        super().__init__(scale_factor, quiet=quiet, **config)

        self._name = "Write Primitives Benchmark"
        self._version = "2.0"
        self._description = "Write Primitives benchmark - Testing fundamental write operations using TPC-H schema"

        if output_dir is None:
            output_dir = get_benchmark_runs_datagen_path("tpch", scale_factor)

        self.output_dir = normalize_output_dir(output_dir)

        self.operations_manager = WriteOperationsManager()
        self.data_generator = WritePrimitivesDataGenerator(scale_factor, self.output_dir, **config)

        self.tables: dict[str, Path] = {}

        self._setup_dialect: str = "standard"
        self._manifest_verified_connection: Any = None
        self._dataframe_loaded_table_counts: dict[str, int] | None = None

    def _acquire_setup_lock(
        self, connection: DatabaseConnection, timeout_seconds: int = 300, dialect: str = "standard"
    ) -> bool:
        if _pk_lock_bypass_required(dialect):
            return True

        try:
            lock_res = connection.execute("""
                CREATE TABLE IF NOT EXISTS write_primitives_setup_lock (
                    lock_name VARCHAR(255) PRIMARY KEY,
                    holder_info VARCHAR(1000),
                    acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            if (err := failed_platform_error(lock_res)) is not None:
                self.log_verbose(f"Warning: Could not create lock table: {err}")
                return False
        except Exception as e:
            self.log_verbose(f"Warning: Could not create lock table: {e}")
            return False

        lock_name = "staging_table_setup"
        start_time = mono_time()

        while elapsed_seconds(start_time) < timeout_seconds:
            try:
                import os

                holder_info = f"pid:{os.getpid()},time:{time.time()}"

                escaped_lock_name = lock_name.replace("'", "''")
                escaped_holder_info = holder_info.replace("'", "''")

                ins_res = connection.execute(
                    f"INSERT INTO write_primitives_setup_lock (lock_name, holder_info) "
                    f"VALUES ('{escaped_lock_name}', '{escaped_holder_info}')"
                )
                if (err := failed_platform_error(ins_res)) is not None:
                    error_msg = err.lower()
                    if "unique" in error_msg or "duplicate" in error_msg or "constraint" in error_msg:
                        time.sleep(0.5)
                        continue
                    else:
                        self.log_verbose(f"Unexpected error acquiring lock: {err}")
                        return False
                self.log_verbose(f"Acquired setup lock (waited {elapsed_seconds(start_time):.1f}s)")
                return True
            except Exception as e:
                error_msg = str(e).lower()
                if "unique" in error_msg or "duplicate" in error_msg or "constraint" in error_msg:
                    time.sleep(0.5)
                else:
                    self.log_verbose(f"Unexpected error acquiring lock: {e}")
                    return False

        try:
            escaped_lock_name = lock_name.replace("'", "''")
            result = connection.execute(
                f"SELECT acquired_at FROM write_primitives_setup_lock WHERE lock_name = '{escaped_lock_name}'"
            ).fetchone()
            if result:
                self.log_verbose(f"Setup lock timeout after {timeout_seconds}s (lock held since {result[0]})")
        except Exception:
            pass

        return False

    def _release_setup_lock(self, connection: DatabaseConnection, dialect: str = "standard") -> None:
        if _pk_lock_bypass_required(dialect):
            return

        try:
            lock_name = "staging_table_setup"
            escaped_lock_name = lock_name.replace("'", "''")
            connection.execute(f"DELETE FROM write_primitives_setup_lock WHERE lock_name = '{escaped_lock_name}'")
            self.log_verbose("Released setup lock")
        except Exception as e:
            self.log_verbose(f"Warning: Could not release setup lock: {e}")

    def _quote_identifier(self, identifier: str) -> str:
        if self._setup_dialect == "starrocks":
            escaped = identifier.replace("`", "``")
            return f"`{escaped}`"
        return quote_identifier_for_dialect(identifier, self._setup_dialect)

    def _dialect_base_write_sql(self, operation: Any, platform_key: str | None) -> str:
        sql = operation.write_sql
        if not platform_key:
            return sql
        if platform_key.lower() != "questdb" and self._rewrite_transactional_sql_for_platform(sql, platform_key) != sql:
            return sql
        return self.translate_query_text(sql, platform_key)

    def _get_effective_write_sql(
        self,
        operation: Any,
        platform_key: str | None = None,
        sql_override: str | None = None,
        connection: DatabaseConnection | None = None,
        platform_fallback_key: str | None = None,
    ) -> tuple[str | None, str | None]:
        if getattr(operation, "aggregate_state", None) is not None:
            platform_label = platform_key or "SQL"
            return None, (
                f"Operation '{operation.id}' is DataFrame aggregate-state only and is unsupported on "
                f"platform '{platform_label}'."
            )

        if (platform_key or "").lower() in _POSTGRES_OPERATION_SKIP_DIALECTS:
            category = getattr(operation, "category", "")
            if category in POSTGRES_WRITE_PRIMITIVES_CATEGORY_SKIPS:
                return None, (
                    f"Operation '{operation.id}' is skipped on PostgreSQL-family platforms: "
                    f"{POSTGRES_WRITE_PRIMITIVES_CATEGORY_SKIPS[category]}"
                )
            if operation.id in POSTGRES_WRITE_PRIMITIVES_OPERATION_SKIPS:
                return None, (
                    f"Operation '{operation.id}' is skipped on PostgreSQL-family platforms: "
                    f"{POSTGRES_WRITE_PRIMITIVES_OPERATION_SKIPS[operation.id]}"
                )

        if "doris" in {(platform_key or "").lower(), (platform_fallback_key or "").lower()}:
            doris_skip_reason = doris_write_primitive_skip_reason(operation)
            if doris_skip_reason is not None:
                return None, f"Operation '{operation.id}' is skipped on Doris: {doris_skip_reason}"

        if "questdb" in {(platform_key or "").lower(), (platform_fallback_key or "").lower()}:
            questdb_skip_reason = questdb_write_primitive_skip_reason(operation)
            if questdb_skip_reason is not None:
                return None, f"Operation '{operation.id}' is skipped on QuestDB: {questdb_skip_reason}"

        platform_override_sql: str | None = None
        found_override, override = self._lookup_platform_override(
            getattr(operation, "platform_overrides", None), platform_key, platform_fallback_key
        )
        if found_override:
            if override is None:
                return None, f"Operation '{operation.id}' is unsupported on platform '{platform_key}'."
            platform_override_sql = override

        if (missing_file := self._check_bulk_load_file_dependencies(operation)) is not None:
            return None, (
                f"Operation '{operation.id}' is skipped because required bulk-load files are missing: {missing_file}"
            )

        if sql_override is not None:
            effective_sql = sql_override
        elif platform_override_sql is not None:
            effective_sql = platform_override_sql
        else:
            effective_sql = self._dialect_base_write_sql(operation, platform_key or platform_fallback_key)

        if (platform_key or "").lower() == "duckdb" or (platform_fallback_key or "").lower() == "duckdb":
            duckdb_skip_reason = duckdb_write_primitive_skip_reason(operation, effective_sql)
            if duckdb_skip_reason is not None:
                return None, (f"Operation '{operation.id}' is skipped on DuckDB: {duckdb_skip_reason}")

        if (empty_reason := self._check_staging_table_population(operation.write_sql, connection)) is not None:
            return None, f"Operation '{operation.id}' is skipped because {empty_reason}"

        return effective_sql, None

    def _check_staging_table_population(self, write_sql: str, connection: DatabaseConnection | None) -> str | None:
        if connection is None:
            return None
        for table_name, capability_note in _OPTIONAL_WHEN_SOURCE_MISSING.items():
            if not re.search(rf"\b{re.escape(table_name)}\b", write_sql):
                continue
            try:
                quoted = self._quote_identifier(table_name)
                row_count = fetch_count_probe(connection, f"SELECT COUNT(*) FROM {quoted}")
            except Exception:
                row_count = 0
            if row_count == 0:
                return (
                    f"staging table '{table_name}' is empty (source data was unavailable at setup): {capability_note}"
                )
        return None

    def _check_bulk_load_file_dependencies(self, operation: Any) -> str | None:
        dependencies = list(getattr(operation, "file_dependencies", []))
        if not dependencies:
            return None

        files_dir = getattr(self.data_generator, "files_dir", None)
        if files_dir is None:
            return ", ".join(dependencies)

        missing: list[str] = []
        for filename in dependencies:
            try:
                if "*" in filename:
                    matches = list(files_dir.glob(filename))
                    if not matches:
                        missing.append(filename)
                else:
                    dependency_path = files_dir / filename
                    if not dependency_path.exists():
                        missing.append(filename)
            except Exception:
                missing.append(filename)

        if not missing:
            return None

        return ", ".join(missing)

    def _table_exists(self, connection: DatabaseConnection, table_name: str) -> bool:
        return table_exists(connection, table_name, self.log_verbose, getattr(self, "_setup_dialect", None))

    def _scd2_row_hash_expr(self, acctbal_expr: str) -> str:
        dialect = getattr(self, "_setup_dialect", "standard").lower()
        if dialect == "singlestore":
            return f"CONCAT(c_name, '|', c_address, '|', CAST({acctbal_expr} AS CHAR), '|', c_mktsegment)"
        text_type = "STRING" if dialect in {"bigquery", "databricks"} else "VARCHAR"
        return f"c_name || '|' || c_address || '|' || CAST({acctbal_expr} AS {text_type}) || '|' || c_mktsegment"

    def _integer_cast_type(self) -> str:
        return "SIGNED" if self._setup_dialect.lower() == "singlestore" else "INTEGER"

    def _date_literal(self, value: str) -> str:
        if self._setup_dialect.lower() == "sqlite":
            return f"DATE('{value}')"
        if self._setup_dialect.lower() == "singlestore":
            return f"CAST('{value}' AS DATE)"
        return f"DATE '{value}'"

    def _get_population_sql(self, table_name: str, source_table: str) -> str:
        quoted_table = self._quote_identifier(table_name)
        quoted_source = self._quote_identifier(source_table)
        integer_type = self._integer_cast_type()

        if table_name == "merge_ops_target":
            return (
                f"INSERT INTO {quoted_table} SELECT * FROM {quoted_source} "
                f"WHERE o_orderkey <= (SELECT CAST(MAX(o_orderkey) * 0.5 AS {integer_type}) FROM {quoted_source})"
            )
        elif table_name == "merge_ops_source":
            return (
                f"INSERT INTO {quoted_table} SELECT * FROM {quoted_source} "
                f"WHERE o_orderkey > (SELECT CAST(MAX(o_orderkey) * 0.5 AS {integer_type}) FROM {quoted_source})"
            )
        elif table_name == "merge_ops_lineitem_target":
            return (
                f"INSERT INTO {quoted_table} SELECT * FROM {quoted_source} "
                f"WHERE l_orderkey <= (SELECT CAST(MAX(l_orderkey) * 0.5 AS {integer_type}) FROM {quoted_source})"
            )
        elif table_name == "scd2_ops_dim_customer":
            fingerprint = self._scd2_row_hash_expr("c_acctbal")
            valid_from = self._date_literal("1990-01-01")
            valid_to = self._date_literal("9999-12-31")
            return (
                f"INSERT INTO {quoted_table} "
                f"SELECT c_custkey AS sk, c_custkey, c_name, c_address, c_acctbal, c_mktsegment, "
                f"{fingerprint} AS row_hash, true AS is_current, "
                f"{valid_from} AS valid_from, {valid_to} AS valid_to "
                f"FROM {quoted_source}"
            )
        elif table_name == "scd2_ops_stage_customer":
            fp_changed = self._scd2_row_hash_expr("c_acctbal + 100")
            fp_same = self._scd2_row_hash_expr("c_acctbal")
            effective_changed = self._date_literal("2026-01-01")
            effective_unchanged = self._date_literal("2026-01-02")
            effective_new = self._date_literal("2026-01-03")
            if self._setup_dialect.lower() == "questdb":
                new_key_expr = "c_custkey + max_key.max_custkey"
                new_source = (
                    f"{quoted_source} CROSS JOIN (SELECT MAX(c_custkey) AS max_custkey FROM {quoted_source}) max_key"
                )
            else:
                new_key_expr = f"c_custkey + (SELECT MAX(c_custkey) FROM {quoted_source})"
                new_source = quoted_source
            return (
                f"INSERT INTO {quoted_table} "
                f"SELECT c_custkey, c_name, c_address, c_acctbal + 100, c_mktsegment, "
                f"{fp_changed} AS row_hash, {effective_changed} AS effective_ts, 'changed' AS change_type "
                f"FROM {quoted_source} WHERE c_custkey BETWEEN 1 AND 20;\n"
                f"INSERT INTO {quoted_table} "
                f"SELECT c_custkey, c_name, c_address, c_acctbal, c_mktsegment, "
                f"{fp_same} AS row_hash, {effective_unchanged} AS effective_ts, 'unchanged' AS change_type "
                f"FROM {quoted_source} WHERE c_custkey BETWEEN 21 AND 40;\n"
                f"INSERT INTO {quoted_table} "
                f"SELECT {new_key_expr}, "
                f"c_name, c_address, c_acctbal, c_mktsegment, "
                f"{fp_same} AS row_hash, {effective_new} AS effective_ts, 'new' AS change_type "
                f"FROM {new_source} WHERE c_custkey BETWEEN 1 AND 20"
            )
        elif table_name == "ddl_truncate_target":
            return f"INSERT INTO {quoted_table} SELECT o_orderkey, o_custkey, o_orderdate FROM {quoted_source}"
        else:
            return f"INSERT INTO {quoted_table} SELECT * FROM {quoted_source}"

    def _execute_population_sql(self, connection: DatabaseConnection, sql: str) -> None:
        if self._setup_dialect.lower() in _SINGLE_STATEMENT_SETUP_DIALECTS:
            for statement in sql.split(";"):
                if statement.strip():
                    stmt_res = connection.execute(statement)
                    if (err := failed_platform_error(stmt_res)) is not None:
                        raise RuntimeError(f"Population SQL statement failed: {err}")
            return
        res = connection.execute(sql)
        if (err := failed_platform_error(res)) is not None:
            raise RuntimeError(f"Population SQL failed: {err}")

    def _populate_staging_tables(self, connection: DatabaseConnection, tables: dict[str, str]) -> dict[str, int]:
        status: dict[str, int] = {}

        for table_name, source_table in tables.items():
            quoted_table = self._quote_identifier(table_name)

            try:
                current_count = fetch_count_probe(connection, f"SELECT COUNT(*) FROM {quoted_table}")
            except Exception:
                current_count = 0

            if current_count == 0:
                try:
                    quoted_source = self._quote_identifier(source_table)
                    source_count = fetch_count_probe(connection, f"SELECT COUNT(*) FROM {quoted_source}")
                except Exception as e:
                    if table_name in _OPTIONAL_WHEN_SOURCE_MISSING:
                        self.log_verbose(
                            f"Skipping {table_name} population - source table '{source_table}' does not exist. "
                            f"{_OPTIONAL_WHEN_SOURCE_MISSING[table_name]}"
                        )
                        status[table_name] = 0
                        continue
                    else:
                        raise RuntimeError(
                            f"Cannot validate source table '{source_table}' before populating '{table_name}': {e}"
                        ) from e

                if source_count == 0:
                    if table_name in ["update_ops_orders", "delete_ops_orders", "delete_ops_lineitem"]:
                        raise RuntimeError(
                            f"Source table '{source_table}' is empty (0 rows). "
                            f"Cannot populate staging table '{table_name}'. "
                            f"Please ensure TPC-H data is loaded before running setup()."
                        )
                    else:
                        self.log_verbose(f"Skipping {table_name} population - source table '{source_table}' is empty.")
                        status[table_name] = 0
                        continue

                self.log_verbose(f"Populating {table_name} from {source_table} ({source_count} rows)...")
                populate_sql = self._get_population_sql(table_name, source_table)
                self._execute_population_sql(connection, populate_sql)

                status[table_name] = fetch_count_probe(connection, f"SELECT COUNT(*) FROM {quoted_table}")
                self.log_verbose(f"Populated {table_name} with {status[table_name]} rows")
            else:
                status[table_name] = current_count
                self.log_verbose(f"Table {table_name} already populated ({current_count} rows)")

        return status

    def setup(self, connection: DatabaseConnection, force: bool = False, dialect: str = "standard") -> dict[str, Any]:
        self._setup_dialect = dialect
        self._manifest_verified_connection = None
        self.log_verbose("Setting up Write Primitives benchmark...")

        required_tables = ["orders", "lineitem"]
        for table in required_tables:
            try:
                probe_res = connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
                if (err := failed_platform_error(probe_res)) is not None:
                    raise RuntimeError(f"Source table check failed: {err}")
            except Exception as e:
                raise RuntimeError(
                    f"Required TPC-H table '{table}' not found. "
                    f"Please load TPC-H data first using generate_data() and loading the files. "
                    f"Error: {e}"
                ) from e

        if not self._acquire_setup_lock(connection, timeout_seconds=300, dialect=dialect):
            raise RuntimeError(
                "Could not acquire setup lock after 5 minutes. "
                "Another process may be running setup, or a previous setup crashed. "
                "Check write_primitives_setup_lock table for stale locks."
            )

        try:
            rebuild = force or not self._staging_manifest_matches(connection, required_tables)

            replace_in_place = rebuild and replaces_tables_in_place(dialect)
            if rebuild:
                reason = "force mode" if force else "stale/absent staging manifest"
                self._drop_legacy_staging_manifests(connection)
                if replace_in_place:
                    self._invalidate_staging_manifest(connection)
                for table_name in [] if replace_in_place else STAGING_TABLES:
                    try:
                        quoted = self._quote_identifier(table_name)
                        connection.execute(f"DROP TABLE IF EXISTS {quoted}")
                        self.log_verbose(f"Dropped existing {table_name} ({reason})")
                    except Exception as e:
                        self.log_verbose(f"Warning: Could not drop {table_name}: {e}")

            created_tables = []
            for table_name in STAGING_TABLES:
                table_existed = self._table_exists(connection, table_name)
                create_sql = (
                    replace_table_sql(get_create_table_sql(table_name, dialect=dialect))
                    if replace_in_place
                    else get_create_table_sql(table_name, dialect=dialect, if_not_exists=True)
                )
                try:
                    create_res = connection.execute(create_sql)
                    if (err := failed_platform_error(create_res)) is not None:
                        raise RuntimeError(f"Failed to create {table_name}: {err}")
                    if not table_existed:
                        created_tables.append(table_name)
                        self.log_verbose(f"Created {table_name}")
                    else:
                        self.log_verbose(f"Table {table_name} already exists")
                except Exception as e:
                    raise RuntimeError(f"Failed to create {table_name}: {e}") from e

            table_population_map = {
                "update_ops_orders": "orders",
                "delete_ops_orders": "orders",
                "delete_ops_lineitem": "lineitem",
                "delete_ops_supplier": "supplier",
                "merge_ops_target": "orders",
                "merge_ops_source": "orders",
                "merge_ops_lineitem_target": "lineitem",
                "scd2_ops_dim_customer": "customer",
                "scd2_ops_stage_customer": "customer",
                "ddl_truncate_target": "orders",
            }

            population_status = self._populate_staging_tables(connection, table_population_map)

            status: dict[str, int] = dict(population_status)
            for table_name in STAGING_TABLES:
                if table_name not in status:
                    try:
                        quoted = self._quote_identifier(table_name)
                        status[table_name] = fetch_count_probe(connection, f"SELECT COUNT(*) FROM {quoted}")
                    except Exception:
                        status[table_name] = 0

            self._write_staging_manifest(connection, required_tables)

            self.log_verbose(f"Setup complete: {status}")

            return {
                "success": True,
                "tables_created": created_tables,
                "table_row_counts": status,
            }
        finally:
            self._release_setup_lock(connection, dialect=dialect)

    def teardown(self, connection: DatabaseConnection) -> None:
        self.log_verbose("Tearing down Write Primitives benchmark...")
        self._manifest_verified_connection = None

        for table_name in STAGING_TABLES:
            try:
                quoted = self._quote_identifier(table_name)
                connection.execute(f"DROP TABLE IF EXISTS {quoted}")
                self.log_verbose(f"Dropped {table_name}")
            except Exception as e:
                self.log_verbose(f"Warning: Could not drop {table_name}: {e}")

        self.log_verbose("Teardown complete")

    def cleanup_auxiliary_files(self) -> None:
        import shutil

        aux_dir = self.data_generator.files_dir
        if aux_dir.exists():
            try:
                shutil.rmtree(aux_dir)
                self.log_verbose(f"Removed auxiliary files directory: {aux_dir}")
            except Exception as e:
                self.log_verbose(f"Warning: Could not remove auxiliary files: {e}")

    def load_data(self, connection: DatabaseConnection, **kwargs: Any) -> dict[str, Any]:
        return self.setup(connection, force=False, dialect=kwargs.get("dialect", "standard"))

    def reset(self, connection: DatabaseConnection) -> None:
        self.log_verbose("Resetting Write Primitives staging tables...")

        reset_map = {
            "update_ops_orders": "orders",
            "delete_ops_orders": "orders",
            "delete_ops_lineitem": "lineitem",
            "delete_ops_supplier": "supplier",
            "merge_ops_target": "orders",
            "merge_ops_source": "orders",
            "merge_ops_lineitem_target": "lineitem",
            "scd2_ops_dim_customer": "customer",
            "scd2_ops_stage_customer": "customer",
            "ddl_truncate_target": "orders",
        }

        clear_verb = "DELETE FROM" if self._setup_dialect.lower() in _NO_TRUNCATE_DIALECTS else "TRUNCATE TABLE"
        for table_name in reset_map:
            try:
                quoted = self._quote_identifier(table_name)
                connection.execute(f"{clear_verb} {quoted}")
                self.log_verbose(f"Truncated {table_name}")
            except Exception as e:
                self.log_verbose(f"Warning: Could not truncate {table_name}: {e}")

        self._populate_staging_tables(connection, reset_map)
        self.log_verbose("Reset complete")

    def is_setup(self, connection: DatabaseConnection) -> bool:
        try:
            required_tables = [
                "update_ops_orders",
                "delete_ops_orders",
                "delete_ops_lineitem",
                "merge_ops_target",
                "merge_ops_source",
                "merge_ops_lineitem_target",
                "ddl_truncate_target",
            ]

            for table_name in required_tables:
                if not self._table_exists(connection, table_name):
                    return False
                quoted = self._quote_identifier(table_name)
                probe = connection.execute(f"SELECT 1 FROM {quoted} LIMIT 1")
                if failed_platform_error(probe) is not None or probe.fetchone() is None:
                    return False
            if self._manifest_verified_connection is connection:
                return True
            if self._staging_manifest_matches(connection, ["orders", "lineitem"]):
                self._manifest_verified_connection = connection
                return True
            return False
        except Exception:
            return False

    def _replace_placeholders(self, sql: str) -> str:
        if "{file_path}" in sql:
            if self.output_dir:
                file_path = str(self.output_dir / "write_primitives_auxiliary")
            else:
                file_path = ""

            file_path = file_path.replace("'", "''")

            if re.search(r"[^\w\s/\\\.\-:]", file_path.replace("''", "'")):
                self.log_verbose(f"Warning: File path contains unusual characters: {file_path}")

            sql = sql.replace("{file_path}", file_path)
        return sql

    def get_schema(self, dialect: str = "standard") -> dict[str, dict]:
        normalized: dict[str, dict[str, Any]] = {}

        loadable_table_names = {
            "region",
            "nation",
            "customer",
            "supplier",
            "part",
            "partsupp",
            "orders",
            "lineitem",
            *STAGING_TABLES.keys(),
        }

        for table_name, table_def in TABLES.items():
            if table_name not in loadable_table_names:
                continue

            if isinstance(table_def, dict) and "columns" in table_def:
                normalized[table_name] = table_def
                continue

            if hasattr(table_def, "columns"):
                columns = []
                for col in getattr(table_def, "columns", []):
                    col_type = col.get_sql_type() if hasattr(col, "get_sql_type") else "VARCHAR"
                    columns.append(
                        {
                            "name": col.name,
                            "type": col_type,
                            "nullable": getattr(col, "nullable", False),
                            "primary_key": getattr(col, "primary_key", False),
                        }
                    )

                normalized[table_name] = {
                    "name": getattr(table_def, "name", table_name),
                    "columns": columns,
                }

        return normalized

    def get_create_tables_sql(self, dialect: str = "standard", tuning_config=None) -> str:
        return build_tpch_staging_tables_sql(
            dialect=dialect,
            tuning_config=tuning_config,
            staging_heading="Write Primitives Staging Tables",
            get_staging_tables_sql=get_all_staging_tables_sql,
        )

    def get_query(self, query_id: Union[int, str], **kwargs: Any) -> str:
        operation = self.operations_manager.get_operation(str(query_id))
        if operation.aggregate_state is not None:
            raise ValueError(f"Operation '{operation.id}' is DataFrame aggregate-state only and is not exposed as SQL")
        return operation.write_sql

    def get_all_operations(self) -> dict[str, Any]:
        return {
            op_id: operation
            for op_id, operation in self.operations_manager.get_all_operations().items()
            if operation.aggregate_state is None and operation.category.lower() != "sketch"
        }

    def get_operation_categories(self) -> list[str]:
        return sorted({operation.category for operation in self.get_all_operations().values()})

    def get_operations_by_category(self, category: str) -> dict[str, Any]:
        normalized = category.lower()
        return {
            op_id: operation
            for op_id, operation in self.get_all_operations().items()
            if operation.category.lower() == normalized
        }

    def get_benchmark_info(self) -> dict[str, Any]:
        return {
            "name": self._name,
            "version": self._version,
            "description": self._description,
            "scale_factor": self.scale_factor,
            "total_operations": len(self.get_all_operations()),
            "categories": self.get_operation_categories(),
            "tables": list(self._staging_tables.keys()),
            "data_source": "tpch",
        }

    def get_queries(self, dialect: Optional[str] = None) -> dict[str, str]:
        operations = self.operations_manager.get_all_operations()
        queries: dict[str, str] = {}
        for op_id, op in operations.items():
            if op.aggregate_state is not None or op.category.lower() == "sketch":
                continue
            found, override = self._lookup_platform_override(getattr(op, "platform_overrides", None), dialect)
            if found and isinstance(override, str):
                queries[op_id] = override
            else:
                queries[op_id] = self._dialect_base_write_sql(op, dialect)
        return queries

    def get_queries_by_category(self, category: str) -> dict[str, str]:
        operations = self.operations_manager.get_operations_by_category(category)
        return {
            op_id: op.write_sql
            for op_id, op in operations.items()
            if op.aggregate_state is None and op.category.lower() != "sketch"
        }

    def execute_operation(
        self,
        operation_id: str,
        connection: DatabaseConnection,
        **kwargs: Any,
    ) -> OperationResult:
        operation, platform_key, fallback_key, sql_override = self._prepare_operation(
            operation_id, connection, **kwargs
        )

        try:
            effective_sql, skip_reason = self._get_effective_write_sql(
                operation,
                platform_key=platform_key,
                sql_override=sql_override,
                connection=connection,
                platform_fallback_key=fallback_key,
            )
            if skip_reason is not None:
                self.log_verbose(f"Skipping operation {operation_id}: {skip_reason}")
                return OperationResult(
                    operation_id=operation_id,
                    success=True,
                    write_duration_ms=0.0,
                    rows_affected=0,
                    validation_duration_ms=0.0,
                    validation_passed=True,
                    validation_results=[],
                    cleanup_duration_ms=0.0,
                    cleanup_success=True,
                    status="SKIPPED",
                    skip_reason=skip_reason,
                )

            self.log_verbose(f"Executing write operation: {operation_id}")
            if effective_sql is None:
                raise RuntimeError(f"No executable SQL resolved for operation '{operation_id}'")
            effective_sql = self._rewrite_transactional_sql_for_platform(effective_sql, platform_key)
            write_sql = self._replace_placeholders(effective_sql)
            summed_statements = (
                split_sql_statements(write_sql, "sqlite")
                if operation.category == "merge" and (platform_key or "").lower() in _SUMMED_ROWCOUNT_DIALECTS
                else None
            )
            write_start = time.perf_counter()
            if summed_statements is not None:
                write_result = self._execute_summing_rowcounts(connection, summed_statements)
            else:
                write_result = connection.execute(write_sql)
            write_duration_ms = (time.perf_counter() - write_start) * 1000
            if (write_error := failed_platform_error(write_result)) is not None:
                raise RuntimeError(f"Write SQL failed on platform: {write_error}")

            rows_affected = self._extract_rows_affected(write_result, operation_id)

            validation_passed, validation_results, validation_duration_ms = self._run_operation_validation(
                operation, connection, operation_id, platform_key=platform_key, platform_fallback_key=fallback_key
            )

            cleanup_success, cleanup_warning, cleanup_duration_ms = self._run_operation_cleanup(
                operation, connection, operation_id, platform_key=platform_key, platform_fallback_key=fallback_key
            )

            return OperationResult(
                operation_id=operation_id,
                success=validation_passed,
                write_duration_ms=write_duration_ms,
                rows_affected=rows_affected,
                validation_duration_ms=validation_duration_ms,
                validation_passed=validation_passed,
                validation_results=validation_results,
                cleanup_duration_ms=cleanup_duration_ms,
                cleanup_success=cleanup_success,
                status="SUCCESS" if validation_passed else "VALIDATION_FAILED",
                error=None if validation_passed else summarize_validation_failures(validation_results),
                cleanup_warning=cleanup_warning,
                executed_sql=write_sql,
            )

        except Exception as e:
            self._rollback_connection_after_error(connection)
            error_msg = f"Operation {operation_id} failed: {str(e)}"
            self.log_verbose(error_msg)

            return OperationResult(
                operation_id=operation_id,
                success=False,
                write_duration_ms=0.0,
                rows_affected=0,
                validation_duration_ms=0.0,
                validation_passed=False,
                validation_results=[],
                cleanup_duration_ms=0.0,
                cleanup_success=False,
                status="FAILED",
                error=error_msg,
                cleanup_warning="Write operation failed during execution. Run reset() to ensure clean state.",
            )

    @staticmethod
    def _execute_summing_rowcounts(connection: DatabaseConnection, statements: list[str]) -> SimpleNamespace:
        total = 0
        for statement in statements:
            cursor = connection.execute(statement)
            if (error := failed_platform_error(cursor)) is not None:
                return SimpleNamespace(rowcount=-1, platform_result={"status": "FAILED", "error": error})
            if cursor.description is not None:
                cursor.fetchall()
            total += max(cursor.rowcount, 0)
        return SimpleNamespace(rowcount=total)

    def _extract_rows_affected(self, write_result: Any, operation_id: str) -> int:
        rows_affected = getattr(write_result, "rowcount", None)
        if rows_affected is None:
            self.log_verbose(f"Warning: Platform doesn't support rowcount for {operation_id}")
            return -1
        if rows_affected == -1:
            self.log_verbose(f"Note: rowcount not applicable for {operation_id}")
        return rows_affected

    def _run_operation_validation(
        self,
        operation: Any,
        connection: DatabaseConnection,
        operation_id: str,
        platform_key: str | None = None,
        platform_fallback_key: str | None = None,
    ) -> tuple[bool, list[dict], float]:
        self.log_verbose(f"Validating operation: {operation_id}")
        validation_start = time.perf_counter()
        validation_results = []
        validation_passed = True

        for val_query in operation.validation_queries:
            effective_sql, skip_reason = _resolve_validation_sql(val_query, platform_key, platform_fallback_key)

            if skip_reason is not None:
                self.log_verbose(f"Skipping validation '{val_query.id}' for {operation_id}: {skip_reason}")
                validation_results.append(
                    {
                        "query_id": val_query.id,
                        "sql": val_query.sql,
                        "expected_rows": val_query.expected_rows,
                        "actual_rows": 0,
                        "passed": True,
                        "skipped": True,
                        "skip_reason": skip_reason,
                        "sample": [],
                    }
                )
                continue

            val_sql = self._replace_placeholders(effective_sql)
            val_cursor = connection.execute(val_sql)
            if (val_error := failed_platform_error(val_cursor)) is not None:
                validation_passed = False
                validation_results.append(
                    {
                        "query_id": val_query.id,
                        "sql": effective_sql,
                        "expected_rows": val_query.expected_rows,
                        "actual_rows": 0,
                        "passed": False,
                        "error": val_error,
                        "sample": [],
                    }
                )
                continue
            val_result = val_cursor.fetchall()
            actual_rows = len(val_result)
            passed = _check_validation_query(val_query, actual_rows, val_result, self.scale_factor)
            validation_passed = validation_passed and passed

            validation_results.append(
                {
                    "query_id": val_query.id,
                    "sql": effective_sql,
                    "expected_rows": val_query.expected_rows,
                    "actual_rows": actual_rows,
                    "passed": passed,
                    "sample": val_result[:5] if val_result else [],
                }
            )

        validation_duration_ms = (time.perf_counter() - validation_start) * 1000
        return validation_passed, validation_results, validation_duration_ms

    def _run_operation_cleanup(
        self,
        operation: Any,
        connection: DatabaseConnection,
        operation_id: str,
        platform_key: str | None = None,
        platform_fallback_key: str | None = None,
    ) -> tuple[bool, str | None, float]:
        self.log_verbose(f"Cleaning up operation: {operation_id}")
        cleanup_start = time.perf_counter()
        cleanup_success = True
        cleanup_warning = None

        cleanup_sql = operation.cleanup_sql
        found_override, override = self._lookup_platform_override(
            getattr(operation, "cleanup_platform_overrides", None), platform_key, platform_fallback_key
        )
        if found_override:
            cleanup_sql = override

        if cleanup_sql:
            try:
                self._execute_cleanup_sql(connection, cleanup_sql, platform_key or platform_fallback_key)
                self.log_verbose(f"Executed cleanup SQL for {operation_id}")
            except Exception as e:
                cleanup_error = str(e)
                self.log_verbose(f"Cleanup SQL failed for {operation_id}: {cleanup_error}")
                cleanup_success = False
                cleanup_warning = (
                    f"Write operation '{operation_id}' cleanup failed. "
                    f"Database may be in modified state. Run reset() to restore staging tables. "
                    f"Error: {cleanup_error}"
                )
                self.log_verbose(f"WARNING: {cleanup_warning}")

        cleanup_duration_ms = (time.perf_counter() - cleanup_start) * 1000
        return cleanup_success, cleanup_warning, cleanup_duration_ms

    def supports_dataframe_mode(self) -> bool:
        return True

    def skip_dataframe_data_loading(self) -> bool:
        return True

    def dataframe_loaded_tables(self, *, ctx: Any, adapter: Any) -> tuple[dict[str, int], list[str]] | None:
        _ = (ctx, adapter)
        if self._dataframe_loaded_table_counts is None:
            return None
        return dict(self._dataframe_loaded_table_counts), list(TPCH_TABLES_BY_NAME)

    def get_dataframe_operations(
        self, platform_name: str, *, execution_engine: str | None = None
    ) -> "DataFrameWriteOperationsManager | None":
        from benchbox.core.write_primitives.dataframe_operations import (
            get_dataframe_write_manager,
        )

        selected_engine = execution_engine
        if selected_engine is None:
            selected_engine = getattr(self, "execution_engine", None)
        if selected_engine is None:
            selected_engine = "default"
        return get_dataframe_write_manager(platform_name, execution_engine=selected_engine)

    def get_dataframe_capabilities(self, platform_name: str) -> "DataFrameWriteCapabilities | None":
        manager = self.get_dataframe_operations(platform_name)
        if manager is None:
            return None
        return manager.get_capabilities()

    def execute_dataframe_workload(
        self,
        *,
        ctx: Any,
        adapter: Any,
        benchmark_config: Any,
        query_filter: set[str] | None = None,
        monitor: Any | None = None,
        run_options: Any | None = None,
    ) -> list[dict[str, Any]]:
        _ = (ctx, monitor, run_options)
        config_options = getattr(benchmark_config, "options", {}) or {}
        warmup_iterations = int(config_options.get("power_warmup_iterations", 1) or 1)
        measurement_iterations = int(config_options.get("power_iterations", 3) or 3)

        try:
            results: list[dict[str, Any]] = []

            for _warmup_idx in range(max(warmup_iterations, 0)):
                warmup_rows = self._execute_dataframe_sql_parity_workload(query_filter=query_filter, adapter=adapter)
                for row in warmup_rows:
                    warmup_row = dict(row)
                    warmup_row["run_type"] = "warmup"
                    warmup_row["iteration"] = 0
                    warmup_row["stream_id"] = 0
                    results.append(warmup_row)

            for measurement_idx in range(1, measurement_iterations + 1):
                measurement_rows = self._execute_dataframe_sql_parity_workload(
                    query_filter=query_filter, adapter=adapter
                )
                for row in measurement_rows:
                    measurement_row = dict(row)
                    measurement_row["run_type"] = "measurement"
                    measurement_row["iteration"] = measurement_idx
                    measurement_row["stream_id"] = 0
                    results.append(measurement_row)

            return results
        except Exception as e:
            return [
                {
                    "query_id": "WR_PARITY_EXECUTION",
                    "status": "FAILED",
                    "execution_time_seconds": 0.0,
                    "rows_returned": 0,
                    "error": f"DataFrame SQL-parity execution failed: {e}",
                }
            ]

    def _execute_dataframe_sql_parity_workload(
        self,
        *,
        query_filter: set[str] | None = None,
        adapter: Any = None,
    ) -> list[dict[str, Any]]:
        from benchbox.platforms.duckdb import DuckDBAdapter

        data_dir = Path(self.output_dir)
        parity_db_path = data_dir / "_write_primitives_df_parity.duckdb"

        all_op_ids = self._select_dataframe_operation_ids(query_filter=query_filter)
        operations = self.operations_manager.get_all_operations()
        agg_state_op_ids = [op_id for op_id in all_op_ids if operations[op_id].aggregate_state is not None]
        sql_parity_op_ids = [op_id for op_id in all_op_ids if operations[op_id].aggregate_state is None]

        results: list[dict[str, Any]] = []

        for op_id in agg_state_op_ids:
            results.append(self._execute_aggregate_state_op(op_id, adapter=adapter))

        if not sql_parity_op_ids:
            return results

        parity_adapter = DuckDBAdapter(database_path=str(parity_db_path))
        connection = parity_adapter.create_connection(database_path=str(parity_db_path), force_recreate=True)
        try:
            parity_adapter.create_schema(self, connection)
            parity_adapter.load_data(self, connection, data_dir)
            self._dataframe_loaded_table_counts = {
                name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                for name in TPCH_TABLES_BY_NAME
            }

            for op_id in sql_parity_op_ids:
                op_result = self.execute_operation(op_id, connection, platform_key="duckdb")
                status = "SUCCESS" if op_result.success and op_result.validation_passed else "FAILED"
                error = op_result.error
                if status == "FAILED" and not error and not op_result.validation_passed:
                    error = f"Validation failed for operation '{op_id}'"

                results.append(
                    {
                        "query_id": op_id,
                        "status": status,
                        "execution_time_seconds": op_result.write_duration_ms / 1000.0,
                        "rows_returned": max(0, op_result.rows_affected),
                        **({"error": error} if error else {}),
                    }
                )
            return results
        finally:
            try:
                parity_adapter.close_connection(connection)
            finally:
                try:
                    parity_db_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _execute_aggregate_state_op(self, op_id: str, *, adapter: Any) -> dict[str, Any]:
        import shutil

        op = self.operations_manager.get_operation(op_id)
        spec = op.aggregate_state
        if spec is None:
            return {
                "query_id": op_id,
                "status": "FAILED",
                "execution_time_seconds": 0.0,
                "rows_returned": 0,
                "error": f"Operation '{op_id}' has no aggregate_state spec",
            }

        platform_name = self._resolve_platform_name(adapter)
        if platform_name not in spec.supported_platforms:
            return {
                "query_id": op_id,
                "status": "SKIPPED",
                "execution_time_seconds": 0.0,
                "rows_returned": 0,
                "error": (
                    f"Aggregate-state op '{op_id}' not supported on platform "
                    f"'{platform_name}' (supports: {','.join(spec.supported_platforms) or 'none'})"
                ),
            }

        try:
            from benchbox.core.write_primitives.dataframe_operations import (
                get_dataframe_write_manager,
                make_pyspark_hll_merge_extract,
                make_pyspark_hll_persist_builder,
                make_pyspark_topk_merge_extract,
                make_pyspark_topk_persist_builder,
                pyspark_supports_approx_top_k,
            )

            spark = self._extract_spark_session(adapter)
            if spark is None:
                return {
                    "query_id": op_id,
                    "status": "FAILED",
                    "execution_time_seconds": 0.0,
                    "rows_returned": 0,
                    "error": f"Adapter for '{platform_name}' did not expose a SparkSession for op '{op_id}'",
                }

            if spec.sketch_type == "topk" and not pyspark_supports_approx_top_k(spark):
                return {
                    "query_id": op_id,
                    "status": "SKIPPED",
                    "execution_time_seconds": 0.0,
                    "rows_returned": 0,
                    "error": (
                        f"Top-K aggregate-state op '{op_id}' requires Spark 4.1+; "
                        f"runtime version {getattr(spark, 'version', 'unknown')}"
                    ),
                }

            output_dir = Path(self.output_dir)
            source_path = self._resolve_aggregate_source_path(spec.source_table, adapter, output_dir, spark)
            target_path = output_dir / spec.target_subdir

            if target_path.exists():
                shutil.rmtree(target_path, ignore_errors=True)

            manager = get_dataframe_write_manager("pyspark-df", spark_session=spark)
            if manager is None:
                return {
                    "query_id": op_id,
                    "status": "FAILED",
                    "execution_time_seconds": 0.0,
                    "rows_returned": 0,
                    "error": f"No DataFrameWriteOperationsManager for pyspark-df (op '{op_id}')",
                }

            if spec.sketch_type == "hll":
                state_builder = make_pyspark_hll_persist_builder(
                    spark_session=spark,
                    source_path=source_path,
                    group_cols=spec.group_cols,
                    value_col=spec.value_col,
                    sketch_alias=spec.sketch_alias,
                )
                merge_extract = make_pyspark_hll_merge_extract(spark_session=spark, sketch_col=spec.sketch_alias)
            else:
                state_builder = make_pyspark_topk_persist_builder(
                    spark_session=spark,
                    source_path=source_path,
                    group_cols=spec.group_cols,
                    value_col=spec.value_col,
                    sketch_alias=spec.sketch_alias,
                )
                merge_extract = make_pyspark_topk_merge_extract(spark_session=spark, sketch_col=spec.sketch_alias)

            t0 = mono_time()
            persist = manager.execute_aggregate_persist(target_path, state_builder)
            if not persist.success:
                return {
                    "query_id": op_id,
                    "status": "FAILED",
                    "execution_time_seconds": elapsed_seconds(t0),
                    "rows_returned": 0,
                    "error": f"AGGREGATE_PERSIST failed: {persist.error_message or 'unknown error'}",
                }
            if not persist.validation_passed:
                details = (
                    "; ".join(
                        str(entry.get("reason") or entry.get("check", "storage_size"))
                        for entry in persist.validation_results
                        if isinstance(entry, dict) and not entry.get("passed", True)
                    )
                    or "storage validation failed"
                )
                return {
                    "query_id": op_id,
                    "status": "VALIDATION_FAILED",
                    "execution_time_seconds": elapsed_seconds(t0),
                    "rows_returned": int(persist.rows_affected),
                    "error": f"AGGREGATE_PERSIST storage validation failed: {details}",
                }
            merge = manager.execute_aggregate_merge(target_path, merge_extract)
            elapsed = elapsed_seconds(t0)
            if not merge.success:
                return {
                    "query_id": op_id,
                    "status": "FAILED",
                    "execution_time_seconds": elapsed,
                    "rows_returned": int(persist.rows_affected),
                    "error": f"AGGREGATE_MERGE failed: {merge.error_message or 'unknown error'}",
                }

            aggregate_value = float(merge.metrics.get("aggregate_value", 0.0))
            validation_pass, validation_error = self._validate_aggregate_value(op, aggregate_value, self.scale_factor)
            if not validation_pass:
                return {
                    "query_id": op_id,
                    "status": "FAILED",
                    "execution_time_seconds": elapsed,
                    "rows_returned": int(persist.rows_affected),
                    "error": validation_error,
                }

            shutil.rmtree(target_path, ignore_errors=True)

            return {
                "query_id": op_id,
                "status": "SUCCESS",
                "execution_time_seconds": elapsed,
                "rows_returned": int(persist.rows_affected),
                "aggregate_value": aggregate_value,
            }
        except Exception as exc:
            return {
                "query_id": op_id,
                "status": "FAILED",
                "execution_time_seconds": 0.0,
                "rows_returned": 0,
                "error": f"Aggregate-state op '{op_id}' raised: {exc}",
            }

    @staticmethod
    def _resolve_platform_name(adapter: Any) -> str:
        if adapter is None:
            return ""
        name = getattr(adapter, "platform_name", None) or getattr(adapter, "name", None) or ""
        return str(name).strip().lower().replace("-df", "").replace("_df", "")

    @staticmethod
    def _extract_spark_session(adapter: Any) -> Any:
        for attr in ("spark", "_spark", "spark_session", "session"):
            value = getattr(adapter, attr, None)
            if value is not None:
                return value
        return None

    def _resolve_aggregate_source_path(self, source_table: str, adapter: Any, output_dir: Path, spark: Any) -> Path:
        cache_dir = output_dir / "_aggregate_state_sources" / source_table
        if cache_dir.exists() and any(cache_dir.iterdir()):
            return cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        from benchbox.utils.scale_factor import format_scale_factor

        scale = float(getattr(self, "scale_factor", 0.01) or 0.01)
        scale_label = f"tpch_{format_scale_factor(scale)}"
        candidate_roots = [
            output_dir,
            output_dir.parent / "datagen" / scale_label,
            output_dir / "datagen" / scale_label,
            Path.cwd() / "benchmark_runs" / "datagen" / scale_label,
        ]
        tbl_files = self._find_aggregate_tbl_sources(source_table, candidate_roots)
        if not tbl_files:
            raise RuntimeError(
                f"Could not locate {source_table}.tbl fixture for scale {scale} "
                f"under any of {[str(r) for r in candidate_roots]}"
            )

        import duckdb

        from benchbox.platforms.base.data_loading import escape_sql_string_literal

        column_specs = self._tbl_column_specs(source_table)
        column_list = ", ".join(f"'{name}': '{type_}'" for name, type_ in column_specs)
        escaped_paths = [escape_sql_string_literal(str(path)) for path in tbl_files]
        path_list = "[" + ", ".join(f"'{path}'" for path in escaped_paths) + "]"
        target_file = cache_dir / "data.parquet"
        conn = duckdb.connect()
        try:
            conn.execute(
                f"COPY (SELECT * FROM read_csv({path_list}, delim='|', "
                f"columns={{{column_list}}}, header=false)) "
                f"TO '{target_file}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            conn.close()

        return cache_dir

    @staticmethod
    def _find_aggregate_tbl_sources(source_table: str, candidate_roots: list[Path]) -> list[Path]:
        patterns = (
            f"{source_table}.tbl",
            f"{source_table}.tbl.[0-9]*",
            f"{source_table}.tbl.gz",
            f"{source_table}.tbl.[0-9]*.gz",
            f"{source_table}.tbl.zst",
            f"{source_table}.tbl.[0-9]*.zst",
        )
        for root in candidate_roots:
            if not root.exists():
                continue
            files = sorted({path for pattern in patterns for path in root.glob(pattern) if path.is_file()})
            if files:
                return files
        return []

    @staticmethod
    def _tbl_column_specs(source_table: str) -> list[tuple[str, str]]:
        if source_table == "lineitem":
            return [
                ("l_orderkey", "BIGINT"),
                ("l_partkey", "BIGINT"),
                ("l_suppkey", "BIGINT"),
                ("l_linenumber", "INTEGER"),
                ("l_quantity", "DOUBLE"),
                ("l_extendedprice", "DOUBLE"),
                ("l_discount", "DOUBLE"),
                ("l_tax", "DOUBLE"),
                ("l_returnflag", "VARCHAR"),
                ("l_linestatus", "VARCHAR"),
                ("l_shipdate", "DATE"),
                ("l_commitdate", "DATE"),
                ("l_receiptdate", "DATE"),
                ("l_shipinstruct", "VARCHAR"),
                ("l_shipmode", "VARCHAR"),
                ("l_comment", "VARCHAR"),
            ]
        raise NotImplementedError(
            f"Aggregate-state source table '{source_table}' has no TBL column spec; "
            f"add one to WritePrimitivesBenchmark._tbl_column_specs."
        )

    @staticmethod
    def _validate_aggregate_value(
        op: Any, value: float, scale_factor: float = BOUNDS_BASE_SCALE_FACTOR
    ) -> tuple[bool, str | None]:
        if not op.validation_queries:
            return True, None
        vq = op.validation_queries[0]
        if vq.expected_value_min is None or vq.expected_value_max is None:
            return True, None
        lo, hi = _scaled_value_bounds(vq, scale_factor)
        if value < lo or value > hi:
            return False, (
                f"Aggregate scalar {value:.4f} outside bounds [{lo}, {hi}] for op '{op.id}' validation '{vq.id}'"
            )
        return True, None

    def _select_dataframe_operation_ids(self, query_filter: set[str] | None = None) -> list[str]:
        operation_ids = list(self.operations_manager.get_all_operations().keys())
        if not query_filter:
            return operation_ids

        normalized_filter = {str(query_id).strip().upper() for query_id in query_filter}
        return [
            op_id
            for op_id in operation_ids
            if op_id.upper() in normalized_filter or f"Q{op_id}".upper() in normalized_filter
        ]


__all__ = ["WritePrimitivesBenchmark", "OperationResult"]
