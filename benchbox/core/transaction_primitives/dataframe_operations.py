# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from benchbox.core.dataframe.maintenance_interface import (
    DataFrameMaintenanceCapabilities,
    TransactionIsolation,
    get_maintenance_operations_for_platform,
)
from benchbox.core.dataframe_manager_factory import get_dataframe_manager, get_spark_maintenance_operations
from benchbox.utils.iceberg_layout import is_iceberg_directory

logger = logging.getLogger(__name__)


class TransactionOperationType(Enum):
    ATOMIC_INSERT = "atomic_insert"
    ATOMIC_UPDATE = "atomic_update"
    ATOMIC_DELETE = "atomic_delete"
    ATOMIC_MERGE = "atomic_merge"

    ROLLBACK_TO_VERSION = "rollback_to_version"
    ROLLBACK_TO_TIMESTAMP = "rollback_to_timestamp"

    TIME_TRAVEL_QUERY = "time_travel_query"
    VERSION_COMPARE = "version_compare"

    CONCURRENT_WRITE = "concurrent_write"
    CONFLICT_RESOLUTION = "conflict_resolution"

    SNAPSHOT_ISOLATION = "snapshot_isolation"
    READ_YOUR_WRITES = "read_your_writes"


@dataclass
class DataFrameTransactionCapabilities:
    platform_name: str
    supports_transactions: bool = False
    supports_rollback: bool = False
    supports_time_travel: bool = False
    supports_concurrent_writes: bool = False
    transaction_isolation: TransactionIsolation = TransactionIsolation.NONE
    table_format: str = "none"
    notes: str = ""

    def supports_operation(self, operation: TransactionOperationType) -> bool:
        mapping = {
            TransactionOperationType.ATOMIC_INSERT: self.supports_transactions,
            TransactionOperationType.ATOMIC_UPDATE: self.supports_transactions,
            TransactionOperationType.ATOMIC_DELETE: self.supports_transactions,
            TransactionOperationType.ATOMIC_MERGE: self.supports_transactions,
            TransactionOperationType.ROLLBACK_TO_VERSION: self.supports_rollback,
            TransactionOperationType.ROLLBACK_TO_TIMESTAMP: self.supports_rollback,
            TransactionOperationType.TIME_TRAVEL_QUERY: self.supports_time_travel,
            TransactionOperationType.VERSION_COMPARE: self.supports_time_travel,
            TransactionOperationType.CONCURRENT_WRITE: self.supports_concurrent_writes,
            TransactionOperationType.CONFLICT_RESOLUTION: self.supports_concurrent_writes,
            TransactionOperationType.SNAPSHOT_ISOLATION: (self.transaction_isolation == TransactionIsolation.SNAPSHOT),
            TransactionOperationType.READ_YOUR_WRITES: self.supports_transactions,
        }
        return mapping.get(operation, False)

    def get_unsupported_operations(self) -> list[TransactionOperationType]:
        return [op for op in TransactionOperationType if not self.supports_operation(op)]


def _transaction_capabilities(
    platform_name: str, table_format: str, notes: str, *, supports_acid: bool
) -> DataFrameTransactionCapabilities:
    return DataFrameTransactionCapabilities(
        platform_name,
        supports_acid,
        supports_acid,
        supports_acid,
        supports_acid,
        TransactionIsolation.SNAPSHOT if supports_acid else TransactionIsolation.NONE,
        table_format,
        notes,
    )


DELTA_LAKE_TRANSACTION_CAPABILITIES = _transaction_capabilities(
    "delta-lake",
    "delta",
    "Full ACID via Delta Lake. RESTORE for rollback, version queries for time travel.",
    supports_acid=True,
)
PYSPARK_DELTA_TRANSACTION_CAPABILITIES = _transaction_capabilities(
    "pyspark-delta", "delta", "Full ACID via delta-spark. Uses DeltaTable API for transactions.", supports_acid=True
)
ICEBERG_TRANSACTION_CAPABILITIES = _transaction_capabilities(
    "iceberg", "iceberg", "Full ACID via Apache Iceberg. Snapshot-based transactions.", supports_acid=True
)
POLARS_TRANSACTION_CAPABILITIES = _transaction_capabilities(
    "polars-df",
    "parquet",
    "No transaction support. Use Delta Lake or Iceberg for ACID operations.",
    supports_acid=False,
)
PANDAS_TRANSACTION_CAPABILITIES = _transaction_capabilities(
    "pandas-df",
    "parquet",
    "No transaction support. Use Delta Lake or Iceberg for ACID operations.",
    supports_acid=False,
)


@dataclass
class DataFrameTransactionResult:
    operation_type: TransactionOperationType
    success: bool
    start_time: float
    end_time: float
    duration_ms: float
    rows_affected: int
    version_before: int | None = None
    version_after: int | None = None
    error_message: str | None = None
    validation_passed: bool = True
    validation_results: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def failure(
        cls,
        operation_type: TransactionOperationType,
        error_message: str,
        start_time: float | None = None,
    ) -> DataFrameTransactionResult:
        now = time.time()
        return cls(
            operation_type=operation_type,
            success=False,
            start_time=start_time or now,
            end_time=now,
            duration_ms=0.0 if start_time is None else (now - start_time) * 1000,
            rows_affected=0,
            error_message=error_message,
            validation_passed=False,
        )


class DataFrameTransactionOperationsManager:
    def __init__(self, platform_name: str, spark_session: Any = None, execution_engine: str = "default") -> None:
        self.platform_name = platform_name.lower()
        self.spark_session = spark_session
        self.execution_engine = execution_engine
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        self._maintenance_ops = self._get_maintenance_ops()

        self._capabilities = self._build_capabilities()

    def _get_maintenance_ops(self) -> Any:
        if "spark" in self.platform_name:
            return get_spark_maintenance_operations(self.spark_session, self.logger)
        return get_maintenance_operations_for_platform(self.platform_name, self.execution_engine)

    def _build_capabilities(self) -> DataFrameTransactionCapabilities:
        maintenance_caps: DataFrameMaintenanceCapabilities | None = None
        if self._maintenance_ops is not None:
            maintenance_caps = self._maintenance_ops.get_capabilities()

        if "delta" in self.platform_name:
            return DELTA_LAKE_TRANSACTION_CAPABILITIES

        if "pyspark" in self.platform_name or "spark" in self.platform_name:
            if maintenance_caps and maintenance_caps.supports_transactions:
                return PYSPARK_DELTA_TRANSACTION_CAPABILITIES
            return DataFrameTransactionCapabilities(
                platform_name=self.platform_name,
                supports_transactions=False,
                table_format="parquet",
                notes="PySpark without Delta Lake. Install delta-spark for ACID support.",
            )

        if "iceberg" in self.platform_name:
            return ICEBERG_TRANSACTION_CAPABILITIES

        if "polars" in self.platform_name:
            return POLARS_TRANSACTION_CAPABILITIES

        if "pandas" in self.platform_name:
            return PANDAS_TRANSACTION_CAPABILITIES

        if maintenance_caps and maintenance_caps.supports_transactions:
            return DataFrameTransactionCapabilities(
                platform_name=self.platform_name,
                supports_transactions=True,
                supports_rollback=maintenance_caps.supports_time_travel,
                supports_time_travel=maintenance_caps.supports_time_travel,
                supports_concurrent_writes=True,
                transaction_isolation=maintenance_caps.transaction_isolation,
                table_format="unknown",
            )

        return DataFrameTransactionCapabilities(
            platform_name=self.platform_name,
            supports_transactions=False,
            notes="Platform does not support ACID transactions.",
        )

    def get_capabilities(self) -> DataFrameTransactionCapabilities:
        return self._capabilities

    def supports_transactions(self) -> bool:
        return self._capabilities.supports_transactions

    def supports_operation(self, operation: TransactionOperationType) -> bool:
        return self._capabilities.supports_operation(operation)

    def get_unsupported_message(self) -> str:
        return (
            f"Transaction Primitives benchmark requires ACID transaction support.\n"
            f"Platform '{self.platform_name}' does not support transactions.\n"
            f"\n"
            f"Alternatives:\n"
            f"  - Use pyspark-df with Delta Lake table format:\n"
            f"    benchbox run --platform pyspark-df --benchmark transaction_primitives\n"
            f"    (Requires: pip install pyspark delta-spark)\n"
            f"\n"
            f"  - Use delta-lake platform directly:\n"
            f"    benchbox run --platform delta-lake --benchmark transaction_primitives\n"
            f"    (Requires: pip install deltalake)\n"
            f"\n"
            f"Note: {self._capabilities.notes}"
        )

    def _validate_path_safe(self, table_path: Path | str) -> tuple[Path | None, str]:
        try:
            path = Path(table_path)
            resolved = path.resolve()

            path_str = str(table_path)
            if ".." in path_str:
                return None, f"Path traversal detected in '{table_path}'. Use absolute paths."

            return resolved, ""
        except (ValueError, OSError) as e:
            return None, f"Invalid path '{table_path}': {e}"

    def validate_table_format(self, table_path: Path | str) -> tuple[bool, str]:
        resolved_path, error_msg = self._validate_path_safe(table_path)
        if resolved_path is None:
            return False, error_msg
        table_path = resolved_path

        delta_log = table_path / "_delta_log"
        if delta_log.exists() and delta_log.is_dir():
            return True, ""

        if is_iceberg_directory(table_path):
            return True, ""

        if table_path.exists():
            return False, (
                f"Table at '{table_path}' is not a Delta Lake or Iceberg table.\n"
                f"Transaction Primitives requires a transactional table format.\n"
                f"\n"
                f"To convert to Delta Lake:\n"
                f"  df.write.format('delta').mode('overwrite').save('{table_path}')\n"
                f"\n"
                f"Or to create a new Delta table:\n"
                f"  spark.sql(\"CREATE TABLE ... USING DELTA LOCATION '{table_path}'\")"
            )

        return False, f"Table path '{table_path}' does not exist."

    def get_table_version(self, table_path: Path | str) -> int | None:
        table_path = str(table_path)

        if self._capabilities.table_format == "delta":
            try:
                if "pyspark" in self.platform_name and self.spark_session:
                    from delta.tables import DeltaTable

                    dt = DeltaTable.forPath(self.spark_session, table_path)
                    history = dt.history(1).collect()
                    if history:
                        return history[0]["version"]
                else:
                    from deltalake import DeltaTable

                    dt = DeltaTable(table_path)
                    return dt.version()
            except Exception as e:
                self.logger.warning(f"Could not get table version: {e}")
                return None

        if self._capabilities.table_format == "iceberg":
            self.logger.debug("Iceberg version tracking not yet implemented")
            return None

        return None

    def _execute_atomic_operation(
        self,
        operation: TransactionOperationType,
        table_path: Path | str,
        action_name: str,
        operation_fn: Any,
        metrics_fn: Any | None = None,
    ) -> DataFrameTransactionResult:
        start_time = time.time()

        if not self.supports_operation(operation):
            return DataFrameTransactionResult.failure(operation, self.get_unsupported_message(), start_time)

        is_valid, error_msg = self.validate_table_format(table_path)
        if not is_valid:
            return DataFrameTransactionResult.failure(operation, error_msg, start_time)

        version_before = self.get_table_version(table_path)

        try:
            if self._maintenance_ops is None:
                return DataFrameTransactionResult.failure(
                    operation,
                    f"Maintenance operations not available for {self.platform_name}",
                    start_time,
                )

            result = operation_fn(self._maintenance_ops)
            version_after = self.get_table_version(table_path)

            end_time = time.time()
            total_duration_ms = (end_time - start_time) * 1000
            operation_result = self._success_result(
                operation,
                start_time,
                end_time,
                result.rows_affected,
                version_before,
                version_after,
                metrics_fn(result, total_duration_ms) if metrics_fn else {},
                result.error_message,
            )
            operation_result.success = result.success
            return operation_result

        except Exception as e:
            self.logger.error(f"{action_name} failed: {e}")
            return DataFrameTransactionResult.failure(operation, str(e), start_time)

    def execute_atomic_insert(
        self,
        table_path: Path | str,
        dataframe: Any,
        partition_columns: list[str] | None = None,
    ) -> DataFrameTransactionResult:

        def metrics(result: Any, total_duration_ms: float) -> dict[str, float]:
            write_duration_ms = result.duration * 1000
            return {
                "write_duration_ms": write_duration_ms,
                "version_check_overhead_ms": total_duration_ms - write_duration_ms,
            }

        return self._execute_atomic_operation(
            TransactionOperationType.ATOMIC_INSERT,
            table_path,
            "Atomic INSERT",
            lambda ops: ops.insert_rows(
                table_path=table_path,
                dataframe=dataframe,
                partition_columns=partition_columns,
                mode="append",
            ),
            metrics,
        )

    def execute_atomic_update(
        self,
        table_path: Path | str,
        condition: str,
        updates: dict[str, Any],
    ) -> DataFrameTransactionResult:
        return self._execute_atomic_operation(
            TransactionOperationType.ATOMIC_UPDATE,
            table_path,
            "Atomic UPDATE",
            lambda ops: ops.update_rows(
                table_path=table_path,
                condition=condition,
                updates=updates,
            ),
        )

    def execute_atomic_delete(
        self,
        table_path: Path | str,
        condition: str,
    ) -> DataFrameTransactionResult:
        return self._execute_atomic_operation(
            TransactionOperationType.ATOMIC_DELETE,
            table_path,
            "Atomic DELETE",
            lambda ops: ops.delete_rows(
                table_path=table_path,
                condition=condition,
            ),
        )

    def execute_atomic_merge(
        self,
        table_path: Path | str,
        source_dataframe: Any,
        merge_condition: str,
        when_matched: dict[str, Any] | None = None,
        when_not_matched: dict[str, Any] | None = None,
    ) -> DataFrameTransactionResult:
        return self._execute_atomic_operation(
            TransactionOperationType.ATOMIC_MERGE,
            table_path,
            "Atomic MERGE",
            lambda ops: ops.merge_rows(
                table_path=table_path,
                source_dataframe=source_dataframe,
                merge_condition=merge_condition,
                when_matched=when_matched,
                when_not_matched=when_not_matched,
            ),
        )

    def _restore_delta_table(
        self, table_path: Path | str, *, version: int | None = None, timestamp: str | None = None
    ) -> None:
        table_path_str = str(table_path)
        if "pyspark" in self.platform_name and self.spark_session:
            from delta.tables import DeltaTable

            dt = DeltaTable.forPath(self.spark_session, table_path_str)
            if version is not None:
                dt.restoreToVersion(version)
            else:
                dt.restoreToTimestamp(timestamp)
            return

        from deltalake import DeltaTable

        dt = DeltaTable(table_path_str)
        if version is not None:
            dt.restore(version)
            return

        from datetime import datetime

        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if isinstance(timestamp, str) else timestamp
        dt.restore(datetime_target=ts)

    @staticmethod
    def _parse_timestamp(timestamp: str | None) -> Any:
        if timestamp is None:
            return None
        from datetime import datetime

        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

    @staticmethod
    def _arrow_table_from_dataframe(dataframe: Any) -> Any:
        import pyarrow as pa

        return pa.Table.from_pandas(dataframe.toPandas() if hasattr(dataframe, "toPandas") else dataframe)

    @staticmethod
    def _write_row_count(dataframe: Any) -> int:
        return dataframe.count() if hasattr(dataframe, "count") else len(dataframe)

    def _read_delta_snapshot(
        self, table_path: str, *, version: int | None = None, timestamp: str | None = None
    ) -> tuple[int, list[str]]:
        if "pyspark" in self.platform_name and self.spark_session:
            reader = self.spark_session.read.format("delta")
            if version is not None:
                reader = reader.option("versionAsOf", version)
            elif timestamp is not None:
                reader = reader.option("timestampAsOf", timestamp)
            df = reader.load(table_path)
            return df.count(), [f.name for f in df.schema.fields]

        from deltalake import DeltaTable

        kwargs = {"version": version} if version is not None else {"datetime_target": self._parse_timestamp(timestamp)}
        arrow_table = DeltaTable(table_path, **kwargs).to_pyarrow_table()
        return arrow_table.num_rows, arrow_table.schema.names

    def _write_transaction_dataframe(
        self, table_path: str, dataframe: Any, mode: str = "append", *, count_rows: bool = False
    ) -> int | None:
        if self._capabilities.table_format == "delta" and self.spark_session:
            rows_written = self._write_row_count(dataframe) if count_rows else None
            dataframe.write.format("delta").mode(mode).save(table_path)
            return rows_written
        if self._capabilities.table_format == "iceberg" and self.spark_session:
            rows_written = self._write_row_count(dataframe) if count_rows else None
            writer = dataframe.writeTo(table_path)
            writer.overwritePartitions() if mode == "overwrite" else writer.append()
            return rows_written

        from deltalake.writer import write_deltalake

        arrow_table = self._arrow_table_from_dataframe(dataframe)
        write_deltalake(table_path, arrow_table, mode=mode)
        return arrow_table.num_rows if count_rows else None

    def _read_transaction_table_count(self, table_path: str) -> int:
        if self._capabilities.table_format in {"delta", "iceberg"} and self.spark_session:
            return self.spark_session.read.format(self._capabilities.table_format).load(table_path).count()

        from deltalake import DeltaTable

        return DeltaTable(table_path).to_pyarrow_table().num_rows

    def _snapshot_read_counts(
        self, table_path: str, current_version: int | None, query_fn: Any | None
    ) -> tuple[int, int, bool]:
        if self._capabilities.table_format in {"delta", "iceberg"} and self.spark_session:
            reader = self.spark_session.read.format(self._capabilities.table_format)
            if self._capabilities.table_format == "delta" and current_version is not None:
                reader = reader.option("versionAsOf", current_version)
            df = query_fn(reader.load(table_path)) if query_fn is not None else reader.load(table_path)
            count1 = df.count()
            count2 = df.count()
            return count1, count2, count1 == count2

        from deltalake import DeltaTable

        count = DeltaTable(table_path, version=current_version).to_pyarrow_table().num_rows
        return count, count, True

    @staticmethod
    def _success_result(
        operation: TransactionOperationType,
        start_time: float,
        end_time: float,
        rows_affected: int,
        version_before: int | None,
        version_after: int | None,
        metrics: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> DataFrameTransactionResult:
        return DataFrameTransactionResult(
            operation_type=operation,
            success=True,
            start_time=start_time,
            end_time=end_time,
            duration_ms=(end_time - start_time) * 1000,
            rows_affected=rows_affected,
            version_before=version_before,
            version_after=version_after,
            error_message=error_message,
            metrics=metrics or {},
        )

    def _execute_rollback(
        self,
        operation: TransactionOperationType,
        table_path: Path | str,
        target_label: str,
        metrics: dict[str, Any],
        restore_fn: Any,
        unsupported_message: str,
    ) -> DataFrameTransactionResult:
        start_time = time.time()

        if not self.supports_operation(operation):
            return DataFrameTransactionResult.failure(operation, unsupported_message, start_time)

        is_valid, error_msg = self.validate_table_format(table_path)
        if not is_valid:
            return DataFrameTransactionResult.failure(operation, error_msg, start_time)

        version_before = self.get_table_version(table_path)

        try:
            if self._capabilities.table_format == "delta":
                restore_fn()
                version_after = self.get_table_version(table_path)
                end_time = time.time()
                return self._success_result(operation, start_time, end_time, 0, version_before, version_after, metrics)

            return DataFrameTransactionResult.failure(
                operation,
                f"Rollback not implemented for table format: {self._capabilities.table_format}",
                start_time,
            )

        except Exception as e:
            self.logger.error(f"Rollback to {target_label} failed: {e}")
            return DataFrameTransactionResult.failure(operation, str(e), start_time)

    def execute_rollback_to_version(
        self,
        table_path: Path | str,
        version: int,
    ) -> DataFrameTransactionResult:
        return self._execute_rollback(
            TransactionOperationType.ROLLBACK_TO_VERSION,
            table_path,
            f"version {version}",
            {"target_version": version},
            lambda: self._restore_delta_table(table_path, version=version),
            f"Rollback not supported on {self.platform_name}. "
            f"Use Delta Lake or Iceberg for RESTORE/rollback operations.",
        )

    def execute_rollback_to_timestamp(
        self,
        table_path: Path | str,
        timestamp: str,
    ) -> DataFrameTransactionResult:
        return self._execute_rollback(
            TransactionOperationType.ROLLBACK_TO_TIMESTAMP,
            table_path,
            f"timestamp {timestamp}",
            {"target_timestamp": timestamp},
            lambda: self._restore_delta_table(table_path, timestamp=timestamp),
            f"Rollback not supported on {self.platform_name}.",
        )

    def execute_time_travel_query(
        self,
        table_path: Path | str,
        version: int | None = None,
        timestamp: str | None = None,
    ) -> DataFrameTransactionResult:
        start_time = time.time()
        operation = TransactionOperationType.TIME_TRAVEL_QUERY

        if not self.supports_operation(operation):
            return DataFrameTransactionResult.failure(
                operation,
                f"Time travel not supported on {self.platform_name}. Use Delta Lake or Iceberg for historical queries.",
                start_time,
            )

        if version is None and timestamp is None:
            return DataFrameTransactionResult.failure(
                operation,
                "Either version or timestamp must be provided for time travel query.",
                start_time,
            )

        is_valid, error_msg = self.validate_table_format(table_path)
        if not is_valid:
            return DataFrameTransactionResult.failure(operation, error_msg, start_time)

        current_version = self.get_table_version(table_path)

        try:
            if self._capabilities.table_format == "delta":
                row_count, _ = self._read_delta_snapshot(str(table_path), version=version, timestamp=timestamp)
                end_time = time.time()
                return self._success_result(
                    operation,
                    start_time,
                    end_time,
                    row_count,
                    current_version,
                    current_version,
                    {
                        "query_version": version,
                        "query_timestamp": timestamp,
                        "row_count": row_count,
                    },
                )

            return DataFrameTransactionResult.failure(
                operation,
                f"Time travel not implemented for table format: {self._capabilities.table_format}",
                start_time,
            )

        except Exception as e:
            self.logger.error(f"Time travel query failed: {e}")
            return DataFrameTransactionResult.failure(operation, str(e), start_time)

    def execute_version_compare(
        self,
        table_path: Path | str,
        version1: int,
        version2: int,
    ) -> DataFrameTransactionResult:
        start_time = time.time()
        operation = TransactionOperationType.VERSION_COMPARE

        if not self.supports_operation(operation):
            return DataFrameTransactionResult.failure(
                operation,
                f"Version compare not supported on {self.platform_name}. "
                f"Use Delta Lake or Iceberg for time travel operations.",
                start_time,
            )

        is_valid, error_msg = self.validate_table_format(table_path)
        if not is_valid:
            return DataFrameTransactionResult.failure(operation, error_msg, start_time)

        current_version = self.get_table_version(table_path)

        try:
            if self._capabilities.table_format == "delta":
                count1, schema1 = self._read_delta_snapshot(str(table_path), version=version1)
                count2, schema2 = self._read_delta_snapshot(str(table_path), version=version2)
                row_diff = count2 - count1
                schema_added = [c for c in schema2 if c not in schema1]
                schema_removed = [c for c in schema1 if c not in schema2]

                end_time = time.time()
                return self._success_result(
                    operation,
                    start_time,
                    end_time,
                    abs(row_diff),
                    current_version,
                    current_version,
                    {
                        "version1": version1,
                        "version2": version2,
                        "row_count_v1": count1,
                        "row_count_v2": count2,
                        "row_difference": row_diff,
                        "columns_added": schema_added,
                        "columns_removed": schema_removed,
                        "schema_changed": len(schema_added) > 0 or len(schema_removed) > 0,
                    },
                )

            return DataFrameTransactionResult.failure(
                operation,
                f"Version compare not implemented for table format: {self._capabilities.table_format}",
                start_time,
            )

        except Exception as e:
            self.logger.error(f"Version compare failed: {e}")
            return DataFrameTransactionResult.failure(operation, str(e), start_time)

    def execute_concurrent_write(
        self,
        table_path: Path | str,
        dataframes: list[Any],
    ) -> DataFrameTransactionResult:
        start_time = time.time()
        operation = TransactionOperationType.CONCURRENT_WRITE

        if not self.supports_operation(operation):
            return DataFrameTransactionResult.failure(
                operation,
                f"Concurrent write not supported on platform '{self.platform_name}' "
                f"(table_format={self._capabilities.table_format})",
                start_time,
            )

        table_path_str = str(table_path)

        try:
            version_before = self.get_table_version(table_path_str)
            writes_succeeded = 0
            writes_conflicted = 0

            for i, df in enumerate(dataframes):
                try:
                    self._write_transaction_dataframe(table_path_str, df)
                    writes_succeeded += 1
                except Exception as write_err:
                    self.logger.warning(f"Concurrent write {i} conflicted: {write_err}")
                    writes_conflicted += 1

            version_after = self.get_table_version(table_path_str)
            end_time = time.time()

            return self._success_result(
                operation,
                start_time,
                end_time,
                writes_succeeded,
                version_before,
                version_after,
                {
                    "writes_attempted": len(dataframes),
                    "writes_succeeded": writes_succeeded,
                    "writes_conflicted": writes_conflicted,
                },
            )

        except Exception as e:
            self.logger.error(f"Concurrent write failed: {e}")
            return DataFrameTransactionResult.failure(operation, str(e), start_time)

    def execute_conflict_resolution(
        self,
        table_path: Path | str,
        dataframe: Any,
        resolution_strategy: str = "retry",
    ) -> DataFrameTransactionResult:
        start_time = time.time()
        operation = TransactionOperationType.CONFLICT_RESOLUTION

        if not self.supports_operation(operation):
            return DataFrameTransactionResult.failure(
                operation,
                f"Conflict resolution not supported on platform '{self.platform_name}' "
                f"(table_format={self._capabilities.table_format})",
                start_time,
            )

        table_path_str = str(table_path)

        try:
            version_before = self.get_table_version(table_path_str)
            retry_count = 0
            max_retries = 3 if resolution_strategy == "retry" else 1

            last_error: str | None = None
            succeeded = False

            for attempt in range(max_retries):
                try:
                    write_mode = "overwrite" if resolution_strategy == "overwrite" else "append"
                    self._write_transaction_dataframe(table_path_str, dataframe, mode=write_mode)
                    succeeded = True
                    break
                except Exception as e:
                    last_error = str(e)
                    retry_count += 1
                    if resolution_strategy == "fail":
                        break

            version_after = self.get_table_version(table_path_str)
            end_time = time.time()

            if succeeded:
                return self._success_result(
                    operation,
                    start_time,
                    end_time,
                    1,
                    version_before,
                    version_after,
                    {
                        "resolution_strategy": resolution_strategy,
                        "retry_count": retry_count,
                    },
                )
            return DataFrameTransactionResult.failure(
                operation,
                f"Conflict resolution failed after {retry_count} attempts: {last_error}",
                start_time,
            )

        except Exception as e:
            self.logger.error(f"Conflict resolution failed: {e}")
            return DataFrameTransactionResult.failure(operation, str(e), start_time)

    def execute_snapshot_isolation(
        self,
        table_path: Path | str,
        query_fn: Any | None = None,
    ) -> DataFrameTransactionResult:
        start_time = time.time()
        operation = TransactionOperationType.SNAPSHOT_ISOLATION

        if not self.supports_operation(operation):
            return DataFrameTransactionResult.failure(
                operation,
                f"Snapshot isolation not supported on platform '{self.platform_name}' "
                f"(isolation={self._capabilities.transaction_isolation})",
                start_time,
            )

        table_path_str = str(table_path)

        try:
            current_version = self.get_table_version(table_path_str)
            count1, count2, consistent = self._snapshot_read_counts(table_path_str, current_version, query_fn)
            end_time = time.time()
            return self._success_result(
                operation,
                start_time,
                end_time,
                count1,
                current_version,
                current_version,
                {
                    "snapshot_consistent": consistent,
                    "row_count_read1": count1,
                    "row_count_read2": count2,
                    "pinned_version": current_version,
                },
            )

        except Exception as e:
            self.logger.error(f"Snapshot isolation check failed: {e}")
            return DataFrameTransactionResult.failure(operation, str(e), start_time)

    def execute_read_your_writes(
        self,
        table_path: Path | str,
        dataframe: Any,
    ) -> DataFrameTransactionResult:
        start_time = time.time()
        operation = TransactionOperationType.READ_YOUR_WRITES

        if not self.supports_operation(operation):
            return DataFrameTransactionResult.failure(
                operation,
                f"Read-your-writes not supported on platform '{self.platform_name}' "
                f"(supports_transactions={self._capabilities.supports_transactions})",
                start_time,
            )

        table_path_str = str(table_path)

        try:
            version_before = self.get_table_version(table_path_str)
            rows_written = self._write_transaction_dataframe(table_path_str, dataframe, count_rows=True)
            if rows_written is None:
                raise RuntimeError("Unable to determine rows written for read-your-writes validation")

            version_after = self.get_table_version(table_path_str)
            rows_read_back = self._read_transaction_table_count(table_path_str)

            end_time = time.time()
            reads_own_writes = rows_read_back >= rows_written

            return self._success_result(
                operation,
                start_time,
                end_time,
                rows_written,
                version_before,
                version_after,
                {
                    "rows_written": rows_written,
                    "rows_read_back": rows_read_back,
                    "reads_own_writes": reads_own_writes,
                },
            )

        except Exception as e:
            self.logger.error(f"Read-your-writes check failed: {e}")
            return DataFrameTransactionResult.failure(operation, str(e), start_time)


def get_dataframe_transaction_manager(
    platform_name: str,
    spark_session: Any = None,
    execution_engine: str = "default",
) -> DataFrameTransactionOperationsManager | None:
    return get_dataframe_manager(
        platform_name,
        manager_class=DataFrameTransactionOperationsManager,
        supported_platforms=(
            "polars-df",
            "polars",
            "pandas-df",
            "pandas",
            "pyspark-df",
            "pyspark",
            "delta-lake",
            "delta",
            "iceberg",
        ),
        logger=logger,
        manager_label="transaction",
        spark_session=spark_session,
        execution_engine=execution_engine,
    )


def validate_transaction_primitives_platform(platform_name: str) -> tuple[bool, str]:
    platform_lower = platform_name.lower()

    supported_patterns = ("delta", "iceberg", "pyspark")

    if any(p in platform_lower for p in supported_patterns):
        return True, ""

    unsupported_patterns = ("polars", "pandas", "duckdb", "sqlite", "datafusion")

    if any(p in platform_lower for p in unsupported_patterns):
        return False, (
            f"Transaction Primitives benchmark requires ACID transaction support.\n"
            f"Platform '{platform_name}' does not support DataFrame transactions.\n"
            f"\n"
            f"Supported platforms:\n"
            f"  - pyspark-df (with Delta Lake table format)\n"
            f"  - delta-lake (standalone Delta Lake)\n"
            f"  - iceberg (Apache Iceberg tables)\n"
            f"\n"
            f"Example:\n"
            f"  benchbox run --platform pyspark-df --benchmark transaction_primitives\n"
        )

    return True, ""


__all__ = [
    "TransactionOperationType",
    "DataFrameTransactionCapabilities",
    "DataFrameTransactionResult",
    "DataFrameTransactionOperationsManager",
    "get_dataframe_transaction_manager",
    "validate_transaction_primitives_platform",
    "DELTA_LAKE_TRANSACTION_CAPABILITIES",
    "PYSPARK_DELTA_TRANSACTION_CAPABILITIES",
    "ICEBERG_TRANSACTION_CAPABILITIES",
    "POLARS_TRANSACTION_CAPABILITIES",
    "PANDAS_TRANSACTION_CAPABILITIES",
]
