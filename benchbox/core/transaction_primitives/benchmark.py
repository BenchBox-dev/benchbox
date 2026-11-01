# Copyright 2026 Joe Harris / BenchBox Project

# This implementation is derived from TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

import datetime
import importlib
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from benchbox.core.connection import DatabaseConnection
from benchbox.core.primitives_benchmark_utils import (
    build_tpch_staging_tables_sql,
    failed_platform_error,
    fetch_count_probe,
    quote_identifier_for_dialect,
    replace_table_sql,
    replaces_tables_in_place,
    summarize_validation_failures,
)
from benchbox.core.transaction_primitives.generator import TransactionPrimitivesDataGenerator
from benchbox.core.transaction_primitives.operations import TransactionOperationsManager
from benchbox.core.transaction_primitives.schema import STAGING_TABLES, get_all_staging_tables_sql, get_create_table_sql
from benchbox.core.transactional.benchmark_base import TransactionalBenchmarkBase
from benchbox.sql_compat.rules.execution_filter.cloud_transaction_primitives import (
    BIGQUERY_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
    SNOWFLAKE_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)
from benchbox.sql_compat.rules.execution_filter.databricks_transaction_primitives import (
    DATABRICKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)
from benchbox.sql_compat.rules.execution_filter.doris_transaction_primitives import (
    DORIS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)
from benchbox.sql_compat.rules.execution_filter.duckdb_transaction_primitives import (
    DUCKDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)
from benchbox.sql_compat.rules.execution_filter.pg_duckdb_transaction_primitives import (
    PG_DUCKDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)
from benchbox.sql_compat.rules.execution_filter.questdb_transaction_primitives import (
    QUESTDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)
from benchbox.sql_compat.rules.execution_filter.sqlite_transaction_primitives import (
    SQLITE_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)
from benchbox.sql_compat.rules.execution_filter.timescaledb_transaction_primitives import (
    TIMESCALEDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.cloud_storage import normalize_output_dir
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path


def _pk_lock_bypass_required(dialect: str) -> bool:
    importlib.import_module("benchbox.sql_compat.rules.schema_emit.pk_capability_txn")
    from benchbox.sql_compat.actions import CompatAction
    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.registry import REGISTRY

    ctx = CompatibilityContext(
        platform=dialect.lower(),
        platform_version=None,
        benchmark="transaction_primitives",
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
    error: Optional[str] = None
    cleanup_warning: Optional[str] = None
    status: Optional[str] = None
    skip_reason: Optional[str] = None
    executed_sql: Optional[str] = None


class TransactionPrimitivesBenchmark(TransactionalBenchmarkBase["OperationResult"]):
    _benchmark_label = "Transaction Primitives"
    _staging_tables = STAGING_TABLES

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        **config: Any,
    ):
        config = dict(config)
        quiet = config.pop("quiet", False)

        super().__init__(scale_factor, quiet=quiet, **config)

        self._name = "Transaction Primitives Benchmark"
        self._version = "1.0"
        self._description = "Transaction Primitives benchmark - Testing fundamental write operations using TPC-H schema"

        if output_dir is None:
            output_dir = get_benchmark_runs_datagen_path("tpch", scale_factor)

        self.output_dir = normalize_output_dir(output_dir)

        self.operations_manager = TransactionOperationsManager()
        self.data_generator = TransactionPrimitivesDataGenerator(scale_factor, self.output_dir, **config)

        self.tables: dict[str, Path] = {}

    def _acquire_setup_lock(
        self, connection: DatabaseConnection, timeout_seconds: int = 300, dialect: str = "standard"
    ) -> bool:
        import time

        if _pk_lock_bypass_required(dialect):
            return True

        try:
            lock_res = connection.execute("""
                CREATE TABLE IF NOT EXISTS transaction_primitives_setup_lock (
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
                    f"INSERT INTO transaction_primitives_setup_lock (lock_name, holder_info) "
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
                f"SELECT acquired_at FROM transaction_primitives_setup_lock WHERE lock_name = '{escaped_lock_name}'"
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
            connection.execute(f"DELETE FROM transaction_primitives_setup_lock WHERE lock_name = '{escaped_lock_name}'")
            self.log_verbose("Released setup lock")
        except Exception as e:
            self.log_verbose(f"Warning: Could not release setup lock: {e}")

    def _quote_identifier(self, identifier: str) -> str:
        return quote_identifier_for_dialect(identifier, self._setup_dialect)

    def _populate_staging_table(self, connection: DatabaseConnection, staging_table: str, source_table: str) -> None:
        self.log_verbose(f"Populating {staging_table} from {source_table}...")

        populate_sql = f"""
        INSERT INTO {staging_table}
        SELECT * FROM {source_table}
        """

        populate_res = connection.execute(populate_sql)
        if (err := failed_platform_error(populate_res)) is not None:
            raise RuntimeError(f"Failed to populate {staging_table} from {source_table}: {err}")
        self.log_verbose(f"{staging_table} populated successfully")

    def _ensure_staging_table_populated(
        self,
        connection: DatabaseConnection,
        table_name: str,
    ) -> int:
        if table_name not in ["txn_orders", "txn_lineitem", "txn_customer"]:
            try:
                quoted_empty = self._quote_identifier(table_name)
                return fetch_count_probe(connection, f"SELECT COUNT(*) FROM {quoted_empty}")
            except Exception:
                return 0

        source_table = "orders" if "orders" in table_name else ("lineitem" if "lineitem" in table_name else "customer")
        quoted_table = self._quote_identifier(table_name)

        try:
            current_count = fetch_count_probe(connection, f"SELECT COUNT(*) FROM {quoted_table}")
        except Exception:
            current_count = 0

        if current_count > 0:
            self.log_verbose(f"Table {table_name} already populated ({current_count} rows)")
            return current_count

        try:
            quoted_source = self._quote_identifier(source_table)
            source_count = fetch_count_probe(connection, f"SELECT COUNT(*) FROM {quoted_source}")
        except Exception as e:
            raise RuntimeError(
                f"Cannot validate source table '{source_table}' before populating '{table_name}': {e}"
            ) from e

        if source_count == 0:
            raise RuntimeError(
                f"Source table '{source_table}' is empty (0 rows). "
                f"Cannot populate staging table '{table_name}'. "
                f"Please ensure TPC-H data is loaded before running setup()."
            )

        self.log_verbose(f"Populating {table_name} from {source_table} ({source_count} rows)...")
        self._populate_staging_table(connection, table_name, source_table)
        final_count = fetch_count_probe(connection, f"SELECT COUNT(*) FROM {quoted_table}")
        self.log_verbose(f"✅ Populated {table_name} with {final_count} rows")

        if final_count != source_count:
            self.log_verbose(
                f"⚠️ Warning: Row count mismatch after population. Source: {source_count}, Destination: {final_count}"
            )
        return final_count

    def setup(self, connection: DatabaseConnection, force: bool = False, dialect: str = "standard") -> dict[str, Any]:
        self.log_verbose("Setting up Transaction Primitives benchmark...")

        self._setup_dialect = dialect

        required_tables = ["orders", "lineitem", "customer"]
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
                "Check transaction_primitives_setup_lock table for stale locks."
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
                        connection.execute(f"DROP TABLE IF EXISTS {table_name}")
                        self.log_verbose(f"Dropped existing {table_name} ({reason})")
                    except Exception as e:
                        self.log_verbose(f"Warning: Could not drop {table_name}: {e}")

            created_tables = []
            status = {}

            for table_name, table_def in STAGING_TABLES.items():
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
                        self.log_verbose(f"✅ Created {table_name}")
                    else:
                        self.log_verbose(f"Table {table_name} already exists")
                except Exception as e:
                    raise RuntimeError(f"Failed to create {table_name}: {e}") from e

                status[table_name] = self._ensure_staging_table_populated(connection, table_name)

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
        self.log_verbose("Tearing down Transaction Primitives benchmark...")

        for table_name in STAGING_TABLES:
            try:
                connection.execute(f"DROP TABLE IF EXISTS {table_name}")
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
        self.log_verbose("Resetting Transaction Primitives staging tables...")

        for table_name in ["txn_orders", "txn_lineitem", "txn_customer"]:
            try:
                connection.execute(f"TRUNCATE TABLE {table_name}")
                self.log_verbose(f"Truncated {table_name}")
            except Exception as e:
                self.log_verbose(f"Warning: Could not truncate {table_name}: {e}")

        self._populate_staging_table(connection, "txn_orders", "orders")
        self._populate_staging_table(connection, "txn_lineitem", "lineitem")
        self._populate_staging_table(connection, "txn_customer", "customer")

        self.log_verbose("Reset complete")

    def is_setup(self, connection: DatabaseConnection) -> bool:
        try:
            for table_name in ["txn_orders", "txn_lineitem", "txn_customer"]:
                if not self._table_exists(connection, table_name):
                    return False
                quoted = self._quote_identifier(table_name)
                count = fetch_count_probe(connection, f"SELECT COUNT(*) FROM {quoted}")
                if count <= 0:
                    return False
            return self._staging_manifest_matches(connection, ["orders", "lineitem", "customer"])
        except Exception:
            return False

    def _replace_placeholders(self, sql: str) -> str:
        if "{file_path}" in sql:
            if self.output_dir:
                file_path = str(self.output_dir / "transaction_primitives_auxiliary")
            else:
                file_path = ""

            file_path = file_path.replace("'", "''")

            import re

            if re.search(r"[^\w\s/\\\.\-:]", file_path.replace("''", "'")):
                self.log_verbose(f"Warning: File path contains unusual characters: {file_path}")

            sql = sql.replace("{file_path}", file_path)
        return sql

    def get_schema(self, dialect: str = "standard") -> dict[str, dict]:
        return STAGING_TABLES

    def get_create_tables_sql(self, dialect: str = "standard", tuning_config=None) -> str:
        return build_tpch_staging_tables_sql(
            dialect=dialect,
            tuning_config=tuning_config,
            staging_heading="Transaction Primitives Staging Tables",
            get_staging_tables_sql=get_all_staging_tables_sql,
        )

    _SQLITE_SERIES_RE = re.compile(
        r"SELECT\s+unnest\(\s*generate_series\(\s*(?P<lo>[^,()]+?)\s*,\s*(?P<hi>[^,()]+?)\s*\)\s*\)\s+AS\s+(?P<alias>\w+)",
        re.IGNORECASE,
    )
    _SQLITE_TYPED_DATE_RE = re.compile(r"\b(?:DATE|TIMESTAMP)\s+(?P<literal>'[^']*')", re.IGNORECASE)

    def _rewrite_transactional_sql_for_platform(self, sql: str, platform_key: Optional[str]) -> str:
        if (platform_key or "").lower() != "sqlite":
            return super()._rewrite_transactional_sql_for_platform(sql, platform_key)
        sql = self._SQLITE_SERIES_RE.sub(
            lambda m: (
                f"WITH RECURSIVE _series({m['alias']}) AS (SELECT {m['lo']} UNION ALL "
                f"SELECT {m['alias']} + 1 FROM _series WHERE {m['alias']} < {m['hi']}) "
                f"SELECT {m['alias']} FROM _series"
            ),
            sql,
        )
        return self._SQLITE_TYPED_DATE_RE.sub(r"\g<literal>", sql)

    def execute_operation(
        self,
        operation_id: str,
        connection: DatabaseConnection,
        **kwargs: Any,
    ) -> OperationResult:
        operation, platform_key, fallback_key, sql_override = self._prepare_operation(
            operation_id, connection, **kwargs
        )
        platform_name = str(kwargs.get("platform_name") or "").lower()

        try:
            platform_operation_skips = {
                "bigquery": BIGQUERY_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
                "databricks": DATABRICKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
                "apache doris": DORIS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
                "duckdb": DUCKDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
                "snowflake": SNOWFLAKE_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
                "sqlite": SQLITE_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
                "pg_duckdb": PG_DUCKDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
                "questdb": QUESTDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
                "timescaledb": TIMESCALEDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
            }
            if operation_id in platform_operation_skips.get(platform_name, {}):
                skip_details = platform_operation_skips[platform_name][operation_id]
                skip_reason = f"Operation '{operation_id}' is skipped on {platform_name}: {skip_details}"
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

            found_override, override = self._lookup_platform_override(
                getattr(operation, "platform_overrides", None), platform_key, fallback_key
            )
            if sql_override is not None:
                write_sql_raw = sql_override
            elif found_override:
                if override is None:
                    skip_reason = f"Operation '{operation_id}' is unsupported on platform '{platform_key}'."
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
                write_sql_raw = override
            else:
                write_sql_raw = operation.write_sql

            self.log_verbose(f"Executing transaction operation: {operation_id}")
            write_sql_raw = self._rewrite_transactional_sql_for_platform(write_sql_raw, platform_key)
            write_sql = self._replace_placeholders(write_sql_raw)
            write_start = time.perf_counter()
            write_result = connection.execute(write_sql)
            write_duration_ms = (time.perf_counter() - write_start) * 1000
            if (write_error := failed_platform_error(write_result)) is not None:
                raise RuntimeError(f"Transaction SQL failed on platform: {write_error}")

            rows_affected = getattr(write_result, "rowcount", None)
            if rows_affected is None:
                self.log_verbose(f"Warning: Platform doesn't support rowcount for {operation_id}")
                rows_affected = -1
            elif rows_affected == -1:
                self.log_verbose(f"Note: rowcount not applicable for {operation_id}")

            self.log_verbose(f"Validating operation: {operation_id}")
            validation_start = time.perf_counter()
            validation_results = []
            validation_passed = True

            for val_query in operation.validation_queries:
                val_sql = self._replace_placeholders(val_query.sql)
                val_cursor = connection.execute(val_sql)
                if (val_error := failed_platform_error(val_cursor)) is not None:
                    validation_passed = False
                    validation_results.append(
                        {
                            "query_id": val_query.id,
                            "sql": val_query.sql,
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
                expected_rows = val_query.expected_rows

                if expected_rows is not None:
                    passed = actual_rows == expected_rows
                    validation_passed = validation_passed and passed
                elif val_query.expected_rows_min is not None or val_query.expected_rows_max is not None:
                    min_val = val_query.expected_rows_min if val_query.expected_rows_min is not None else 0
                    max_val = val_query.expected_rows_max if val_query.expected_rows_max is not None else float("inf")
                    passed = min_val <= actual_rows <= max_val
                    validation_passed = validation_passed and passed
                else:
                    passed = True

                validation_results.append(
                    {
                        "query_id": val_query.id,
                        "sql": val_query.sql,
                        "expected_rows": expected_rows,
                        "actual_rows": actual_rows,
                        "passed": passed,
                        "sample": val_result[:5] if val_result else [],
                    }
                )

            validation_duration_ms = (time.perf_counter() - validation_start) * 1000

            self.log_verbose(f"Cleaning up operation: {operation_id}")
            cleanup_start = time.perf_counter()
            cleanup_success = True
            cleanup_warning = None

            if operation.cleanup_sql:
                try:
                    self._execute_cleanup_sql(connection, operation.cleanup_sql, platform_key or fallback_key)
                except Exception as e:
                    cleanup_error = str(e)
                    self.log_verbose(f"Cleanup SQL failed for {operation_id}: {cleanup_error}")
                    cleanup_success = False
                    cleanup_warning = (
                        f"Transaction operation '{operation_id}' cleanup failed. "
                        f"Database may be in modified state. Run reset() to restore staging tables. "
                        f"Error: {cleanup_error}"
                    )
                    self.log_verbose(f"WARNING: {cleanup_warning}")

            cleanup_duration_ms = (time.perf_counter() - cleanup_start) * 1000

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
                status=None if validation_passed else "VALIDATION_FAILED",
                error=None if validation_passed else summarize_validation_failures(validation_results),
                cleanup_warning=cleanup_warning,
                executed_sql=write_sql,
            )

        except Exception as e:
            self._rollback_connection_after_error(connection)
            error_msg = f"Operation {operation_id} failed: {str(e)}"
            self.log_verbose(error_msg)

            cleanup_warning = (
                "Transaction operation failed during execution. "
                "Partial changes may exist in database. Run reset() to ensure clean state."
            )
            self.log_verbose(f"WARNING: {cleanup_warning}")

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
                error=error_msg,
                cleanup_warning=cleanup_warning,
            )

    def supports_dataframe_mode(self, platform_name: str) -> bool:
        from benchbox.core.transaction_primitives.dataframe_operations import (
            validate_transaction_primitives_platform,
        )

        is_valid, _ = validate_transaction_primitives_platform(platform_name)
        return is_valid

    def get_dataframe_operations(
        self, platform_name: str, spark_session: Any = None, *, execution_engine: str = "default"
    ) -> Any:
        from benchbox.core.transaction_primitives.dataframe_operations import (
            get_dataframe_transaction_manager,
            validate_transaction_primitives_platform,
        )

        is_valid, error_msg = validate_transaction_primitives_platform(platform_name)
        if not is_valid:
            raise ValueError(error_msg)

        manager = get_dataframe_transaction_manager(
            platform_name, spark_session=spark_session, execution_engine=execution_engine
        )

        if manager is None:
            raise ValueError(f"Could not create DataFrame transaction manager for {platform_name}")

        return manager

    def validate_dataframe_configuration(self, platform_name: str, spark_session: Any = None) -> tuple[bool, str]:
        from benchbox.core.transaction_primitives.dataframe_operations import (
            validate_transaction_primitives_platform,
        )

        is_valid, error_msg = validate_transaction_primitives_platform(platform_name)
        if not is_valid:
            return False, error_msg

        if "pyspark" in platform_name.lower() or "spark" in platform_name.lower():
            if spark_session is None:
                return False, (
                    f"Platform '{platform_name}' requires a SparkSession for DataFrame operations.\n"
                    f"Provide spark_session when initializing the platform adapter."
                )

        return True, ""

    def skip_dataframe_data_loading(self) -> bool:
        return True

    def _create_test_orders_df(self, spark_session: Any, start_key: int, count: int) -> Any:
        rows = [
            {
                "o_orderkey": start_key + i,
                "o_custkey": 1000 + (i % 100),
                "o_orderstatus": "O",
                "o_totalprice": round(1000.00 + i * 10.5, 2),
                "o_orderdate": "1998-01-01",
                "o_orderpriority": "3-MEDIUM",
                "o_clerk": f"Clerk#{i:010d}",
                "o_shippriority": 0,
                "o_comment": f"benchbox test row {i}",
            }
            for i in range(count)
        ]
        if spark_session is not None:
            return spark_session.createDataFrame(rows)
        import pandas as pd

        return pd.DataFrame(rows)

    def _setup_transaction_table(self, spark_session: Any, table_path: Path) -> None:
        if table_path.exists():
            shutil.rmtree(str(table_path))

        initial_rows = [
            {
                "o_orderkey": i + 1,
                "o_custkey": 1000 + (i % 100),
                "o_orderstatus": "O",
                "o_totalprice": round(1000.00 + i * 10.5, 2),
                "o_orderdate": "1998-01-01",
                "o_orderpriority": "3-MEDIUM",
                "o_clerk": f"Clerk#{i:010d}",
                "o_shippriority": 0,
                "o_comment": f"benchbox initial row {i}",
            }
            for i in range(100)
        ]
        table_path_str = str(table_path)

        if spark_session is not None:
            df = spark_session.createDataFrame(initial_rows)
            df.write.format("delta").mode("overwrite").save(table_path_str)
        else:
            import pyarrow as pa
            from deltalake.writer import write_deltalake

            keys = [r["o_orderkey"] for r in initial_rows]
            table = pa.table(
                {
                    "o_orderkey": pa.array(keys, type=pa.int64()),
                    "o_custkey": pa.array([r["o_custkey"] for r in initial_rows], type=pa.int64()),
                    "o_orderstatus": pa.array([r["o_orderstatus"] for r in initial_rows], type=pa.string()),
                    "o_totalprice": pa.array([r["o_totalprice"] for r in initial_rows], type=pa.float64()),
                    "o_orderdate": pa.array([r["o_orderdate"] for r in initial_rows], type=pa.string()),
                    "o_orderpriority": pa.array([r["o_orderpriority"] for r in initial_rows], type=pa.string()),
                    "o_clerk": pa.array([r["o_clerk"] for r in initial_rows], type=pa.string()),
                    "o_shippriority": pa.array([r["o_shippriority"] for r in initial_rows], type=pa.int32()),
                    "o_comment": pa.array([r["o_comment"] for r in initial_rows], type=pa.string()),
                }
            )
            write_deltalake(table_path_str, table, mode="overwrite")

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
        from benchbox.core.transaction_primitives.dataframe_operations import (
            TransactionOperationType,
        )

        platform_name = adapter.platform_name
        spark_session = getattr(ctx, "spark_session", None)

        config_options = getattr(benchmark_config, "options", {}) or {}
        iterations = int(config_options.get("power_iterations", 1) or 1)

        manager = self.get_dataframe_operations(
            platform_name, spark_session, execution_engine=getattr(adapter, "collect_engine", "default")
        )

        output: list[dict[str, Any]] = []
        op_iteration_counts: dict[str, int] = {}

        table_dir = tempfile.mkdtemp(prefix="benchbox_txn_")
        table_path = Path(table_dir) / "orders"

        try:
            for _iteration in range(1, iterations + 1):
                self._setup_transaction_table(
                    spark_session=spark_session,
                    table_path=table_path,
                )

                pre_phase_timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

                dispatch: list[tuple[Any, Any]] = [
                    (
                        TransactionOperationType.ATOMIC_INSERT,
                        lambda: manager.execute_atomic_insert(
                            table_path,
                            self._create_test_orders_df(spark_session, start_key=8_000_001, count=20),
                        ),
                    ),
                    (
                        TransactionOperationType.ATOMIC_UPDATE,
                        lambda: manager.execute_atomic_update(
                            table_path, "o_orderkey > 8000000", {"o_orderpriority": "1-URGENT"}
                        ),
                    ),
                    (
                        TransactionOperationType.ATOMIC_DELETE,
                        lambda: manager.execute_atomic_delete(
                            table_path, "o_orderkey >= 8000001 AND o_orderkey <= 8000010"
                        ),
                    ),
                    (
                        TransactionOperationType.ATOMIC_MERGE,
                        lambda: manager.execute_atomic_merge(
                            table_path,
                            self._create_test_orders_df(spark_session, start_key=8_000_001, count=10),
                            "target.o_orderkey = source.o_orderkey",
                            when_matched={"o_orderpriority": "1-URGENT"},
                            when_not_matched=None,
                        ),
                    ),
                    (
                        TransactionOperationType.ROLLBACK_TO_VERSION,
                        lambda: manager.execute_rollback_to_version(table_path, version=0),
                    ),
                    (
                        TransactionOperationType.ROLLBACK_TO_TIMESTAMP,
                        lambda _ts=pre_phase_timestamp: manager.execute_rollback_to_timestamp(table_path, _ts),
                    ),
                    (
                        TransactionOperationType.TIME_TRAVEL_QUERY,
                        lambda: manager.execute_time_travel_query(table_path, version=0),
                    ),
                    (
                        TransactionOperationType.VERSION_COMPARE,
                        lambda: manager.execute_version_compare(table_path, version1=0, version2=1),
                    ),
                    (
                        TransactionOperationType.CONCURRENT_WRITE,
                        lambda: manager.execute_concurrent_write(
                            table_path,
                            [
                                self._create_test_orders_df(spark_session, start_key=8_000_100 + i * 100, count=5)
                                for i in range(3)
                            ],
                        ),
                    ),
                    (
                        TransactionOperationType.CONFLICT_RESOLUTION,
                        lambda: manager.execute_conflict_resolution(
                            table_path,
                            self._create_test_orders_df(spark_session, start_key=8_000_200, count=5),
                            resolution_strategy="retry",
                        ),
                    ),
                    (
                        TransactionOperationType.SNAPSHOT_ISOLATION,
                        lambda: manager.execute_snapshot_isolation(table_path),
                    ),
                    (
                        TransactionOperationType.READ_YOUR_WRITES,
                        lambda: manager.execute_read_your_writes(
                            table_path,
                            self._create_test_orders_df(spark_session, start_key=8_000_300, count=5),
                        ),
                    ),
                ]

                for op_type, op_callable in dispatch:
                    query_id: str = op_type.value

                    if query_filter and query_id.upper() not in query_filter:
                        continue

                    op_iteration_counts[query_id] = op_iteration_counts.get(query_id, 0) + 1
                    op_iter = op_iteration_counts[query_id]

                    if not manager.supports_operation(op_type):
                        output.append(
                            {
                                "query_id": query_id,
                                "status": "SKIPPED",
                                "execution_time_seconds": 0.0,
                                "rows_returned": 0,
                                "iteration": op_iter,
                                "run_type": "measurement",
                            }
                        )
                        continue

                    _t0 = time.perf_counter()
                    try:
                        result = op_callable()
                    except Exception as exc:
                        output.append(
                            {
                                "query_id": query_id,
                                "status": "FAILED",
                                "execution_time_seconds": time.perf_counter() - _t0,
                                "rows_returned": 0,
                                "iteration": op_iter,
                                "run_type": "measurement",
                                "error": str(exc),
                            }
                        )
                        continue

                    row: dict[str, Any] = {
                        "query_id": query_id,
                        "status": "SUCCESS" if result.success else "FAILED",
                        "execution_time_seconds": result.duration_ms / 1000.0,
                        "rows_returned": result.rows_affected,
                        "iteration": op_iter,
                        "run_type": "measurement",
                    }
                    if result.error_message:
                        row["error"] = result.error_message
                    output.append(row)

        finally:
            shutil.rmtree(table_dir, ignore_errors=True)

        return output


__all__ = ["TransactionPrimitivesBenchmark", "OperationResult"]
