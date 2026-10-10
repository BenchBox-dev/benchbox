# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

try:
    import pyarrow as pa

    PYARROW_AVAILABLE = True
except ImportError:
    pa = None
    PYARROW_AVAILABLE = False

logger = logging.getLogger(__name__)


class MaintenanceOperationType(Enum):
    INSERT = "insert"
    DELETE = "delete"

    UPDATE = "update"
    MERGE = "merge"

    BULK_INSERT = "bulk_insert"
    BULK_DELETE = "bulk_delete"

    OPTIMIZE = "optimize"
    VACUUM = "vacuum"


class TransactionIsolation(Enum):
    NONE = "none"
    READ_COMMITTED = "read_committed"
    REPEATABLE_READ = "repeatable_read"
    SERIALIZABLE = "serializable"
    SNAPSHOT = "snapshot"


@dataclass
class DataFrameMaintenanceCapabilities:
    platform_name: str
    supports_insert: bool = True
    supports_delete: bool = False
    supports_update: bool = False
    supports_merge: bool = False
    supports_transactions: bool = False
    transaction_isolation: TransactionIsolation = TransactionIsolation.NONE
    supports_partitioned_delete: bool = False
    supports_row_level_delete: bool = False
    supports_time_travel: bool = False
    supports_optimize: bool = False
    supports_vacuum: bool = False
    max_batch_size: int = 100000
    notes: str = ""
    accepts_sql_predicates: bool = True

    def supports_operation(self, operation: MaintenanceOperationType) -> bool:
        mapping = {
            MaintenanceOperationType.INSERT: self.supports_insert,
            MaintenanceOperationType.DELETE: self.supports_delete or self.supports_partitioned_delete,
            MaintenanceOperationType.UPDATE: self.supports_update,
            MaintenanceOperationType.MERGE: self.supports_merge,
            MaintenanceOperationType.BULK_INSERT: self.supports_insert,
            MaintenanceOperationType.BULK_DELETE: self.supports_partitioned_delete,
            MaintenanceOperationType.OPTIMIZE: self.supports_optimize,
            MaintenanceOperationType.VACUUM: self.supports_vacuum,
        }
        return mapping.get(operation, False)

    def validate_tpc_compliance(self) -> tuple[bool, list[str]]:
        issues = []

        if not self.supports_insert:
            issues.append("INSERT required for TPC-H RF1")
        if not (self.supports_delete or self.supports_partitioned_delete):
            issues.append("DELETE required for TPC-H RF2")

        if not self.supports_update:
            issues.append("UPDATE required for TPC-DS dimension updates (DM3)")

        return len(issues) == 0, issues


DELTA_LAKE_CAPABILITIES = DataFrameMaintenanceCapabilities(
    platform_name="delta-lake",
    supports_insert=True,
    supports_delete=True,
    supports_update=True,
    supports_merge=True,
    supports_transactions=True,
    transaction_isolation=TransactionIsolation.SNAPSHOT,
    supports_partitioned_delete=True,
    supports_row_level_delete=True,
    supports_time_travel=True,
    supports_optimize=True,
    supports_vacuum=True,
    max_batch_size=1000000,
    notes="Full ACID compliance via Delta Lake protocol",
)

ICEBERG_CAPABILITIES = DataFrameMaintenanceCapabilities(
    platform_name="iceberg",
    supports_insert=True,
    supports_delete=True,
    supports_update=True,
    supports_merge=True,
    supports_transactions=True,
    transaction_isolation=TransactionIsolation.SNAPSHOT,
    supports_partitioned_delete=True,
    supports_row_level_delete=True,
    supports_time_travel=True,
    supports_optimize=False,
    supports_vacuum=True,
    max_batch_size=1000000,
    notes="Full ACID compliance via Apache Iceberg",
    accepts_sql_predicates=False,
)

HUDI_CAPABILITIES = DataFrameMaintenanceCapabilities(
    platform_name="hudi",
    supports_insert=True,
    supports_delete=True,
    supports_update=True,
    supports_merge=True,
    supports_transactions=True,
    transaction_isolation=TransactionIsolation.SNAPSHOT,
    supports_partitioned_delete=True,
    supports_row_level_delete=True,
    supports_time_travel=True,
    supports_optimize=True,
    supports_vacuum=True,
    max_batch_size=1000000,
    notes=(
        "Full ACID compliance via Apache Hudi. Requires PySpark with hudi-spark-bundle. "
        "All maintenance operations use Spark SQL (no pure Python library like delta-rs/pyiceberg)."
    ),
)

PARQUET_CAPABILITIES = DataFrameMaintenanceCapabilities(
    platform_name="parquet",
    supports_insert=True,
    supports_delete=False,
    supports_update=False,
    supports_merge=False,
    supports_transactions=False,
    transaction_isolation=TransactionIsolation.NONE,
    supports_partitioned_delete=True,
    supports_row_level_delete=False,
    supports_time_travel=False,
    max_batch_size=10000000,
    notes="File-based operations only. Updates require read-filter-write pattern.",
)

POLARS_CAPABILITIES = DataFrameMaintenanceCapabilities(
    platform_name="polars",
    supports_insert=True,
    supports_delete=True,
    supports_update=True,
    supports_merge=True,
    supports_transactions=False,
    transaction_isolation=TransactionIsolation.NONE,
    supports_partitioned_delete=True,
    supports_row_level_delete=True,
    supports_time_travel=False,
    max_batch_size=10000000,
    notes="Full TPC compliance via read-modify-write. RAM-limited; use Delta Lake/Iceberg for large datasets.",
)


@dataclass
class MaintenanceResult:
    operation_type: MaintenanceOperationType
    success: bool
    start_time: float
    end_time: float
    duration: float
    rows_affected: int
    error_message: str | None = None
    transaction_id: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def failure(
        cls,
        operation_type: MaintenanceOperationType,
        error_message: str,
        start_time: float | None = None,
    ) -> MaintenanceResult:
        now = time.time()
        return cls(
            operation_type=operation_type,
            success=False,
            start_time=start_time or now,
            end_time=now,
            duration=0.0 if start_time is None else (now - start_time),
            rows_affected=0,
            error_message=error_message,
        )


@runtime_checkable
class DataFrameMaintenanceOperations(Protocol):
    def get_capabilities(self) -> DataFrameMaintenanceCapabilities: ...

    def insert_rows(
        self,
        table_path: Path | str,
        dataframe: Any,
        partition_columns: list[str] | None = None,
        mode: str = "append",
    ) -> MaintenanceResult: ...

    def delete_rows(
        self,
        table_path: Path | str,
        condition: str | Any,
    ) -> MaintenanceResult: ...

    def update_rows(
        self,
        table_path: Path | str,
        condition: str | Any,
        updates: dict[str, Any],
    ) -> MaintenanceResult: ...

    def merge_rows(
        self,
        table_path: Path | str,
        source_dataframe: Any,
        merge_condition: str | Any,
        when_matched: dict[str, Any] | None = None,
        when_not_matched: dict[str, Any] | None = None,
    ) -> MaintenanceResult: ...


@runtime_checkable
class TableFormatOptimizationOperations(Protocol):
    def optimize_table(
        self,
        table_path: Path | str,
        *,
        strategy: str = "compact",
        columns: list[str] | None = None,
        partition_filter: Any | None = None,
    ) -> MaintenanceResult: ...

    def vacuum_table(
        self,
        table_path: Path | str,
        *,
        retention_hours: int | None = None,
        dry_run: bool = True,
        enforce_retention: bool = True,
    ) -> MaintenanceResult: ...


class BaseDataFrameMaintenanceOperations(ABC):
    def __init__(self) -> None:
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._capabilities: DataFrameMaintenanceCapabilities | None = None

    def _convert_to_arrow(self, dataframe: Any) -> Any:
        if not PYARROW_AVAILABLE:
            raise ImportError(
                "PyArrow is not installed. Install with: pip install pyarrow\n"
                "PyArrow is required for maintenance operations."
            )

        if isinstance(dataframe, pa.Table):
            return dataframe

        if hasattr(dataframe, "to_arrow"):
            if hasattr(dataframe, "collect"):
                dataframe = dataframe.collect()
            return dataframe.to_arrow()

        if hasattr(dataframe, "to_parquet") and hasattr(dataframe, "columns"):
            return pa.Table.from_pandas(dataframe)

        raise TypeError(
            f"Unsupported DataFrame type: {type(dataframe)}. "
            f"Expected Polars DataFrame, Pandas DataFrame, or PyArrow Table."
        )

    @abstractmethod
    def _get_capabilities(self) -> DataFrameMaintenanceCapabilities: ...

    def get_capabilities(self) -> DataFrameMaintenanceCapabilities:
        if self._capabilities is None:
            self._capabilities = self._get_capabilities()
        return self._capabilities

    def _check_capability(self, operation: MaintenanceOperationType) -> None:
        caps = self.get_capabilities()
        if not caps.supports_operation(operation):
            raise NotImplementedError(
                f"{caps.platform_name} does not support {operation.value} operations. "
                f"Consider using Delta Lake or Iceberg for full maintenance support."
            )

    @abstractmethod
    def _do_insert(
        self,
        table_path: Path | str,
        dataframe: Any,
        partition_columns: list[str] | None,
        mode: str,
    ) -> int: ...

    def insert_rows(
        self,
        table_path: Path | str,
        dataframe: Any,
        partition_columns: list[str] | None = None,
        mode: str = "append",
    ) -> MaintenanceResult:
        start_time = time.time()
        operation = MaintenanceOperationType.INSERT

        try:
            self._check_capability(operation)
            rows_affected = self._do_insert(table_path, dataframe, partition_columns, mode)

            end_time = time.time()
            return MaintenanceResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                rows_affected=rows_affected,
            )

        except NotImplementedError:
            raise
        except Exception as e:
            self.logger.error(f"INSERT failed: {e}")
            return MaintenanceResult.failure(operation, str(e), start_time)

    @abstractmethod
    def _do_delete(
        self,
        table_path: Path | str,
        condition: str | Any,
    ) -> int: ...

    def delete_rows(
        self,
        table_path: Path | str,
        condition: str | Any,
    ) -> MaintenanceResult:
        start_time = time.time()
        operation = MaintenanceOperationType.DELETE

        try:
            self._check_capability(operation)
            rows_affected = self._do_delete(table_path, condition)

            end_time = time.time()
            return MaintenanceResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                rows_affected=rows_affected,
            )

        except NotImplementedError:
            raise
        except Exception as e:
            self.logger.error(f"DELETE failed: {e}")
            return MaintenanceResult.failure(operation, str(e), start_time)

    def _do_update(
        self,
        table_path: Path | str,
        condition: str | Any,
        updates: dict[str, Any],
    ) -> int:
        raise NotImplementedError("UPDATE not implemented for this platform")

    def update_rows(
        self,
        table_path: Path | str,
        condition: str | Any,
        updates: dict[str, Any],
    ) -> MaintenanceResult:
        start_time = time.time()
        operation = MaintenanceOperationType.UPDATE

        try:
            self._check_capability(operation)
            rows_affected = self._do_update(table_path, condition, updates)

            end_time = time.time()
            return MaintenanceResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                rows_affected=rows_affected,
            )

        except NotImplementedError:
            raise
        except Exception as e:
            self.logger.error(f"UPDATE failed: {e}")
            return MaintenanceResult.failure(operation, str(e), start_time)

    def _do_merge(
        self,
        table_path: Path | str,
        source_dataframe: Any,
        merge_condition: str | Any,
        when_matched: dict[str, Any] | None,
        when_not_matched: dict[str, Any] | None,
    ) -> int:
        raise NotImplementedError("MERGE not implemented for this platform")

    def merge_rows(
        self,
        table_path: Path | str,
        source_dataframe: Any,
        merge_condition: str | Any,
        when_matched: dict[str, Any] | None = None,
        when_not_matched: dict[str, Any] | None = None,
    ) -> MaintenanceResult:
        start_time = time.time()
        operation = MaintenanceOperationType.MERGE

        try:
            self._check_capability(operation)
            rows_affected = self._do_merge(
                table_path,
                source_dataframe,
                merge_condition,
                when_matched,
                when_not_matched,
            )

            end_time = time.time()
            return MaintenanceResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                rows_affected=rows_affected,
            )

        except NotImplementedError:
            raise
        except Exception as e:
            self.logger.error(f"MERGE failed: {e}")
            return MaintenanceResult.failure(operation, str(e), start_time)


def get_maintenance_operations_for_platform(
    platform_name: str, execution_engine: str = "default"
) -> DataFrameMaintenanceOperations | None:
    platform_lower = platform_name.lower()

    if platform_lower in ("polars-df", "polars"):
        try:
            from benchbox.platforms.dataframe.polars_maintenance import (
                get_polars_maintenance_operations,
            )

            return get_polars_maintenance_operations(execution_engine=execution_engine)
        except ImportError:
            logger.debug("Polars maintenance not available (polars not installed)")
            return None

    if platform_lower in ("delta-lake", "delta", "deltalake"):
        try:
            from benchbox.platforms.dataframe.delta_lake_maintenance import (
                get_delta_lake_maintenance_operations,
            )

            return get_delta_lake_maintenance_operations()
        except ImportError:
            logger.debug("Delta Lake maintenance not available (deltalake not installed)")
            return None

    if platform_lower in ("iceberg", "apache-iceberg", "pyiceberg"):
        try:
            from benchbox.platforms.dataframe.iceberg_maintenance import (
                get_iceberg_maintenance_operations,
            )

            return get_iceberg_maintenance_operations()
        except ImportError:
            logger.debug("Iceberg maintenance not available (pyiceberg not installed)")
            return None

    if platform_lower in ("ducklake", "duck-lake", "duckdb-lake"):
        try:
            from benchbox.platforms.dataframe.ducklake_maintenance import (
                get_ducklake_maintenance_operations,
            )

            return get_ducklake_maintenance_operations()
        except ImportError:
            logger.debug("DuckLake maintenance not available (duckdb not installed)")
            return None

    if platform_lower in ("hudi", "apache-hudi"):
        logger.debug(
            "Hudi maintenance requires SparkSession. Use get_hudi_maintenance_operations(spark_session=spark) directly."
        )
        return None

    if platform_lower in ("pyspark-df", "pyspark", "spark"):
        logger.debug(
            "PySpark maintenance requires SparkSession. "
            "Use get_pyspark_maintenance_operations(spark_session=spark) directly."
        )
        return None

    logger.debug(f"No maintenance operations implementation for platform: {platform_name}")
    return None
