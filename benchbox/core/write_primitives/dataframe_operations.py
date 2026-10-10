# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.dataframe.maintenance_interface import (
    DataFrameMaintenanceCapabilities,
    MaintenanceResult,
    get_maintenance_operations_for_platform,
)
from benchbox.core.dataframe_manager_factory import get_dataframe_manager
from benchbox.platforms.polars_compat import collect_frame

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class WriteOperationType(Enum):
    INSERT = "insert"
    BULK_LOAD = "bulk_load"

    UPDATE = "update"
    DELETE = "delete"
    MERGE = "merge"

    TRANSACTION = "transaction"

    AGGREGATE_PERSIST = "aggregate_persist"
    AGGREGATE_MERGE = "aggregate_merge"


@dataclass
class DataFrameWriteCapabilities:
    platform_name: str
    maintenance_caps: DataFrameMaintenanceCapabilities | None = None
    supports_bulk_load: bool = True
    supports_compression: bool = True
    supported_compressions: list[str] = field(default_factory=lambda: ["zstd", "snappy", "gzip", "lz4"])
    supports_partitioning: bool = False
    supports_sorting: bool = True
    supports_aggregate_persist: bool = False
    supports_aggregate_merge: bool = False
    notes: str = ""

    def supports_operation(self, operation: WriteOperationType) -> bool:
        if operation == WriteOperationType.BULK_LOAD:
            return self.supports_bulk_load
        if operation == WriteOperationType.TRANSACTION:
            return self.maintenance_caps.supports_transactions if self.maintenance_caps else False

        if operation == WriteOperationType.AGGREGATE_PERSIST:
            return self.supports_aggregate_persist
        if operation == WriteOperationType.AGGREGATE_MERGE:
            return self.supports_aggregate_merge

        if self.maintenance_caps is None:
            return False

        mapping = {
            WriteOperationType.INSERT: self.maintenance_caps.supports_insert,
            WriteOperationType.UPDATE: self.maintenance_caps.supports_update,
            WriteOperationType.DELETE: self.maintenance_caps.supports_delete
            or self.maintenance_caps.supports_partitioned_delete,
            WriteOperationType.MERGE: self.maintenance_caps.supports_merge,
        }
        return mapping.get(operation, False)

    def get_unsupported_operations(self) -> list[WriteOperationType]:
        return [op for op in WriteOperationType if not self.supports_operation(op)]


POLARS_WRITE_CAPABILITIES = DataFrameWriteCapabilities(
    platform_name="polars-df",
    maintenance_caps=None,
    supports_bulk_load=True,
    supports_compression=True,
    supported_compressions=["zstd", "snappy", "gzip", "lz4"],
    supports_partitioning=True,
    supports_sorting=True,
    notes="Full operation support via read-modify-write. RAM-limited for large datasets.",
)

PANDAS_WRITE_CAPABILITIES = DataFrameWriteCapabilities(
    platform_name="pandas-df",
    maintenance_caps=None,
    supports_bulk_load=True,
    supports_compression=True,
    supported_compressions=["snappy", "gzip", "brotli"],
    supports_partitioning=False,
    supports_sorting=True,
    notes="File-level operations only. Use Polars or PySpark for row-level operations.",
)

PYSPARK_WRITE_CAPABILITIES = DataFrameWriteCapabilities(
    platform_name="pyspark-df",
    maintenance_caps=None,
    supports_bulk_load=True,
    supports_compression=True,
    supported_compressions=["zstd", "snappy", "gzip", "lz4"],
    supports_partitioning=True,
    supports_sorting=True,
    supports_aggregate_persist=True,
    supports_aggregate_merge=True,
    notes="Row-level operations require Delta Lake or Iceberg table format.",
)


@dataclass
class DataFrameWriteResult:
    operation_type: WriteOperationType
    success: bool
    start_time: float
    end_time: float
    duration_ms: float
    rows_affected: int
    bytes_written: int | None = None
    compression: str | None = None
    file_count: int | None = None
    error_message: str | None = None
    validation_passed: bool = True
    validation_results: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_maintenance_result(
        cls,
        maintenance_result: MaintenanceResult,
        operation_type: WriteOperationType,
        **extra_fields: Any,
    ) -> DataFrameWriteResult:
        return cls(
            operation_type=operation_type,
            success=maintenance_result.success,
            start_time=maintenance_result.start_time,
            end_time=maintenance_result.end_time,
            duration_ms=maintenance_result.duration * 1000,
            rows_affected=maintenance_result.rows_affected,
            error_message=maintenance_result.error_message,
            metrics=maintenance_result.metrics,
            **extra_fields,
        )

    @classmethod
    def failure(
        cls,
        operation_type: WriteOperationType,
        error_message: str,
        start_time: float | None = None,
    ) -> DataFrameWriteResult:
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


class DataFrameWriteOperationsManager:
    def __init__(self, platform_name: str, spark_session: Any = None, execution_engine: str = "default") -> None:
        self.platform_name = platform_name.lower()
        self.spark_session = spark_session
        self.execution_engine = execution_engine
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        self._maintenance_ops = self._get_maintenance_ops()
        self._capabilities = self._build_capabilities()

    def _get_maintenance_ops(self) -> Any:
        if "pyspark" in self.platform_name or "spark" in self.platform_name:
            if self.spark_session is not None:
                try:
                    from benchbox.platforms.dataframe.pyspark_maintenance import (
                        get_pyspark_maintenance_operations,
                    )

                    return get_pyspark_maintenance_operations(
                        spark_session=self.spark_session,
                        prefer_delta=True,
                    )
                except ImportError:
                    self.logger.debug("PySpark maintenance module not available")
                    return None
            else:
                self.logger.debug("No SparkSession provided for PySpark platform")
                return None

        return get_maintenance_operations_for_platform(self.platform_name, self.execution_engine)

    def _build_capabilities(self) -> DataFrameWriteCapabilities:
        maintenance_caps = None
        if self._maintenance_ops is not None:
            maintenance_caps = self._maintenance_ops.get_capabilities()

        if "polars" in self.platform_name:
            caps = DataFrameWriteCapabilities(
                platform_name=self.platform_name,
                maintenance_caps=maintenance_caps,
                supports_bulk_load=True,
                supports_compression=True,
                supported_compressions=["zstd", "snappy", "gzip", "lz4"],
                supports_partitioning=True,
                supports_sorting=True,
                notes="Full operation support via read-modify-write.",
            )
        elif "pandas" in self.platform_name:
            caps = DataFrameWriteCapabilities(
                platform_name=self.platform_name,
                maintenance_caps=maintenance_caps,
                supports_bulk_load=True,
                supports_compression=True,
                supported_compressions=["snappy", "gzip", "brotli"],
                supports_partitioning=False,
                supports_sorting=True,
                notes="File-level operations only.",
            )
        elif "pyspark" in self.platform_name or "spark" in self.platform_name:
            caps = DataFrameWriteCapabilities(
                platform_name=self.platform_name,
                maintenance_caps=maintenance_caps,
                supports_bulk_load=True,
                supports_compression=True,
                supported_compressions=["zstd", "snappy", "gzip", "lz4"],
                supports_partitioning=True,
                supports_sorting=True,
                supports_aggregate_persist=True,
                supports_aggregate_merge=True,
                notes="Row-level operations require Delta Lake table format.",
            )
        else:
            caps = DataFrameWriteCapabilities(
                platform_name=self.platform_name,
                maintenance_caps=maintenance_caps,
                supports_bulk_load=True,
                supports_compression=True,
            )

        return caps

    def get_capabilities(self) -> DataFrameWriteCapabilities:
        return self._capabilities

    def supports_operation(self, operation: WriteOperationType) -> bool:
        return self._capabilities.supports_operation(operation)

    def get_unsupported_message(self, operation: WriteOperationType) -> str:
        if operation in (WriteOperationType.UPDATE, WriteOperationType.DELETE, WriteOperationType.MERGE):
            return (
                f"{self.platform_name} does not support {operation.value} operations in the current configuration.\n"
                f"Alternatives:\n"
                f"  - Use polars-df (supports row-level operations via read-modify-write)\n"
                f"  - Use pyspark-df with Delta Lake table format\n"
                f"  - Use file-level INSERT/BULK_LOAD operations instead"
            )
        if operation == WriteOperationType.TRANSACTION:
            return (
                f"{self.platform_name} does not support explicit transactions.\n"
                f"Use Delta Lake or Iceberg for ACID transaction support."
            )
        if operation in (WriteOperationType.AGGREGATE_PERSIST, WriteOperationType.AGGREGATE_MERGE):
            return (
                f"{self.platform_name} does not support DataFrame-layer "
                f"{operation.value} operations.\n"
                f"Aggregate-state ops require an engine with sketch APIs at the "
                f"DataFrame layer (e.g. PySpark `hll_sketch_agg`). "
                f"Use the SQL surface or skip this op."
            )
        return f"{self.platform_name} does not support {operation.value} operations."

    def execute_insert(
        self,
        table_path: Path | str,
        dataframe: Any,
        partition_columns: list[str] | None = None,
        mode: str = "append",
    ) -> DataFrameWriteResult:
        if not self.supports_operation(WriteOperationType.INSERT):
            return DataFrameWriteResult.failure(
                WriteOperationType.INSERT,
                self.get_unsupported_message(WriteOperationType.INSERT),
            )

        if self._maintenance_ops is None:
            return DataFrameWriteResult.failure(
                WriteOperationType.INSERT,
                f"Maintenance operations not available for {self.platform_name}",
            )

        result = self._maintenance_ops.insert_rows(
            table_path=table_path,
            dataframe=dataframe,
            partition_columns=partition_columns,
            mode=mode,
        )

        return DataFrameWriteResult.from_maintenance_result(
            result,
            WriteOperationType.INSERT,
        )

    def execute_update(
        self,
        table_path: Path | str,
        condition: str,
        updates: dict[str, Any],
    ) -> DataFrameWriteResult:
        if not self.supports_operation(WriteOperationType.UPDATE):
            return DataFrameWriteResult.failure(
                WriteOperationType.UPDATE,
                self.get_unsupported_message(WriteOperationType.UPDATE),
            )

        if self._maintenance_ops is None:
            return DataFrameWriteResult.failure(
                WriteOperationType.UPDATE,
                f"Maintenance operations not available for {self.platform_name}",
            )

        result = self._maintenance_ops.update_rows(
            table_path=table_path,
            condition=condition,
            updates=updates,
        )

        return DataFrameWriteResult.from_maintenance_result(
            result,
            WriteOperationType.UPDATE,
        )

    def execute_delete(
        self,
        table_path: Path | str,
        condition: str,
    ) -> DataFrameWriteResult:
        if not self.supports_operation(WriteOperationType.DELETE):
            return DataFrameWriteResult.failure(
                WriteOperationType.DELETE,
                self.get_unsupported_message(WriteOperationType.DELETE),
            )

        if self._maintenance_ops is None:
            return DataFrameWriteResult.failure(
                WriteOperationType.DELETE,
                f"Maintenance operations not available for {self.platform_name}",
            )

        result = self._maintenance_ops.delete_rows(
            table_path=table_path,
            condition=condition,
        )

        return DataFrameWriteResult.from_maintenance_result(
            result,
            WriteOperationType.DELETE,
        )

    def execute_merge(
        self,
        table_path: Path | str,
        source_dataframe: Any,
        merge_condition: str,
        when_matched: dict[str, Any] | None = None,
        when_not_matched: dict[str, Any] | None = None,
    ) -> DataFrameWriteResult:
        if not self.supports_operation(WriteOperationType.MERGE):
            return DataFrameWriteResult.failure(
                WriteOperationType.MERGE,
                self.get_unsupported_message(WriteOperationType.MERGE),
            )

        if self._maintenance_ops is None:
            return DataFrameWriteResult.failure(
                WriteOperationType.MERGE,
                f"Maintenance operations not available for {self.platform_name}",
            )

        result = self._maintenance_ops.merge_rows(
            table_path=table_path,
            source_dataframe=source_dataframe,
            merge_condition=merge_condition,
            when_matched=when_matched,
            when_not_matched=when_not_matched,
        )

        return DataFrameWriteResult.from_maintenance_result(
            result,
            WriteOperationType.MERGE,
        )

    def execute_bulk_load(
        self,
        source_path: Path | str,
        target_path: Path | str,
        source_format: str = "parquet",
        target_format: str = "parquet",
        compression: str | None = "zstd",
        partition_columns: list[str] | None = None,
        sort_columns: list[str] | None = None,
    ) -> DataFrameWriteResult:
        if not self.supports_operation(WriteOperationType.BULK_LOAD):
            return DataFrameWriteResult.failure(
                WriteOperationType.BULK_LOAD,
                self.get_unsupported_message(WriteOperationType.BULK_LOAD),
            )

        start_time = time.time()
        source_path = Path(source_path)
        target_path = Path(target_path)

        try:
            if "polars" in self.platform_name:
                rows, bytes_written, file_count = self._bulk_load_polars(
                    source_path,
                    target_path,
                    source_format,
                    target_format,
                    compression,
                    partition_columns,
                    sort_columns,
                )
            elif "pandas" in self.platform_name:
                rows, bytes_written, file_count = self._bulk_load_pandas(
                    source_path,
                    target_path,
                    source_format,
                    target_format,
                    compression,
                    sort_columns,
                )
            elif "pyspark" in self.platform_name or "spark" in self.platform_name:
                rows, bytes_written, file_count = self._bulk_load_pyspark(
                    source_path,
                    target_path,
                    source_format,
                    target_format,
                    compression,
                    partition_columns,
                    sort_columns,
                )
            else:
                return DataFrameWriteResult.failure(
                    WriteOperationType.BULK_LOAD,
                    f"BULK_LOAD not implemented for {self.platform_name}",
                    start_time,
                )

            end_time = time.time()
            return DataFrameWriteResult(
                operation_type=WriteOperationType.BULK_LOAD,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration_ms=(end_time - start_time) * 1000,
                rows_affected=rows,
                bytes_written=bytes_written,
                compression=compression,
                file_count=file_count,
            )

        except Exception as e:
            self.logger.error(f"BULK_LOAD failed: {e}")
            return DataFrameWriteResult.failure(
                WriteOperationType.BULK_LOAD,
                str(e),
                start_time,
            )

    def _bulk_load_polars(
        self,
        source_path: Path,
        target_path: Path,
        source_format: str,
        target_format: str,
        compression: str | None,
        partition_columns: list[str] | None,
        sort_columns: list[str] | None,
    ) -> tuple[int, int | None, int]:
        try:
            import polars as pl
        except ImportError as e:
            raise ImportError("Polars is required for polars-df bulk load") from e

        if source_format == "parquet":
            df = collect_frame(pl.scan_parquet(source_path), self.execution_engine)
        elif source_format == "csv":
            df = collect_frame(pl.scan_csv(source_path), self.execution_engine)
        elif source_format == "json":
            df = pl.read_json(source_path)
        else:
            raise ValueError(f"Unsupported source format: {source_format}")

        row_count = df.height

        if sort_columns:
            df = df.sort(sort_columns)

        target_path.mkdir(parents=True, exist_ok=True)

        if partition_columns:
            for partition_vals, partition_df in df.group_by(partition_columns):
                if isinstance(partition_vals, tuple):
                    parts = zip(partition_columns, partition_vals)
                else:
                    parts = [(partition_columns[0], partition_vals)]

                partition_path = target_path
                for col, val in parts:
                    partition_path = partition_path / f"{col}={val}"

                partition_path.mkdir(parents=True, exist_ok=True)
                partition_df.write_parquet(
                    partition_path / "part-00000.parquet",
                    compression=compression or "uncompressed",
                )
            file_count = len(list(target_path.rglob("*.parquet")))
        else:
            output_file = target_path / "part-00000.parquet"
            df.write_parquet(
                output_file,
                compression=compression or "uncompressed",
            )
            file_count = 1

        bytes_written = sum(f.stat().st_size for f in target_path.rglob("*.parquet"))

        return row_count, bytes_written, file_count

    def _bulk_load_pandas(
        self,
        source_path: Path,
        target_path: Path,
        source_format: str,
        target_format: str,
        compression: str | None,
        sort_columns: list[str] | None,
    ) -> tuple[int, int | None, int]:
        try:
            import pandas as pd
        except ImportError as e:
            raise ImportError("Pandas is required for pandas-df bulk load") from e

        if source_format == "parquet":
            df = pd.read_parquet(source_path)
        elif source_format == "csv":
            df = pd.read_csv(source_path)
        elif source_format == "json":
            df = pd.read_json(source_path)
        else:
            raise ValueError(f"Unsupported source format: {source_format}")

        row_count = len(df)

        if sort_columns:
            df = df.sort_values(sort_columns)

        target_path.mkdir(parents=True, exist_ok=True)
        output_file = target_path / "part-00000.parquet"
        df.to_parquet(
            output_file,
            compression=compression or "snappy",
            index=False,
        )

        bytes_written = output_file.stat().st_size

        return row_count, bytes_written, 1

    def _bulk_load_pyspark(
        self,
        source_path: Path,
        target_path: Path,
        source_format: str,
        target_format: str,
        compression: str | None,
        partition_columns: list[str] | None,
        sort_columns: list[str] | None,
    ) -> tuple[int, int | None, int]:
        if self.spark_session is None:
            raise ValueError(
                "SparkSession is required for PySpark bulk load. "
                "Pass spark_session to DataFrameWriteOperationsManager or use get_pyspark_write_manager()."
            )

        spark = self.spark_session
        source_str = str(source_path)
        target_str = str(target_path)

        reader = spark.read.format(source_format)

        if source_format == "csv":
            reader = reader.option("header", "true").option("inferSchema", "true")

        df = reader.load(source_str)
        row_count = df.count()

        if row_count == 0:
            self.logger.info("No rows to load")
            return 0, 0, 0

        if sort_columns:
            df = df.orderBy(*sort_columns)

        writer = df.write.mode("overwrite")

        if partition_columns:
            writer = writer.partitionBy(*partition_columns)

        if compression:
            writer = writer.option("compression", compression)

        if target_format == "delta":
            writer.format("delta").save(target_str)
        else:
            writer.parquet(target_str)

        target_path.mkdir(parents=True, exist_ok=True)
        parquet_files = list(target_path.rglob("*.parquet"))
        file_count = len(parquet_files)
        bytes_written = sum(f.stat().st_size for f in parquet_files)

        return row_count, bytes_written, file_count

    def execute_aggregate_persist(
        self,
        target_path: Path | str,
        state_builder: Any,
        compression: str | None = "zstd",
    ) -> DataFrameWriteResult:
        if not self.supports_operation(WriteOperationType.AGGREGATE_PERSIST):
            return DataFrameWriteResult.failure(
                WriteOperationType.AGGREGATE_PERSIST,
                self.get_unsupported_message(WriteOperationType.AGGREGATE_PERSIST),
            )

        start_time = time.time()
        target_path = Path(target_path)
        target_existed_before = target_path.exists()
        try:
            state_df = state_builder()
            target_path.mkdir(parents=True, exist_ok=True)
            rows, bytes_written, file_count = self._persist_dataframe_to_parquet(state_df, target_path, compression)
            end_time = time.time()
            storage_check = self.validate_persisted_storage_size(
                target_path,
                rows_affected=rows,
                bytes_written=bytes_written,
                file_count=file_count,
            )
            return DataFrameWriteResult(
                operation_type=WriteOperationType.AGGREGATE_PERSIST,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration_ms=(end_time - start_time) * 1000,
                rows_affected=rows,
                bytes_written=bytes_written,
                compression=compression,
                file_count=file_count,
                validation_passed=bool(storage_check["passed"]),
                validation_results=[storage_check],
            )
        except Exception as e:
            self.logger.error(f"AGGREGATE_PERSIST failed: {e}")
            if not target_existed_before and target_path.exists():
                try:
                    if not any(target_path.iterdir()):
                        target_path.rmdir()
                except OSError:
                    pass
            return DataFrameWriteResult.failure(
                WriteOperationType.AGGREGATE_PERSIST,
                str(e),
                start_time,
            )

    def execute_aggregate_merge(
        self,
        source_path: Path | str,
        merge_extract: Any,
    ) -> DataFrameWriteResult:
        if not self.supports_operation(WriteOperationType.AGGREGATE_MERGE):
            return DataFrameWriteResult.failure(
                WriteOperationType.AGGREGATE_MERGE,
                self.get_unsupported_message(WriteOperationType.AGGREGATE_MERGE),
            )

        start_time = time.time()
        source_path = Path(source_path)
        try:
            value = merge_extract(source_path)
            end_time = time.time()
            return DataFrameWriteResult(
                operation_type=WriteOperationType.AGGREGATE_MERGE,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration_ms=(end_time - start_time) * 1000,
                rows_affected=1,
                metrics={"aggregate_value": value},
            )
        except Exception as e:
            self.logger.error(f"AGGREGATE_MERGE failed: {e}")
            return DataFrameWriteResult.failure(
                WriteOperationType.AGGREGATE_MERGE,
                str(e),
                start_time,
            )

    def _persist_dataframe_to_parquet(
        self,
        state_df: Any,
        target_path: Path,
        compression: str | None,
    ) -> tuple[int, int, int]:
        if "pyspark" in self.platform_name or "spark" in self.platform_name:
            row_count = state_df.count()
            (
                state_df.write.mode("overwrite")
                .option("compression", compression or "uncompressed")
                .parquet(str(target_path))
            )
        elif "polars" in self.platform_name:
            row_count = state_df.height
            state_df.write_parquet(
                target_path / "part-00000.parquet",
                compression=compression or "uncompressed",
            )
        else:
            raise RuntimeError(f"Aggregate-state persistence not implemented for platform '{self.platform_name}'")

        parquet_files = list(target_path.rglob("*.parquet"))
        file_count = len(parquet_files)
        bytes_written = sum(f.stat().st_size for f in parquet_files)
        return row_count, bytes_written, file_count

    @staticmethod
    def validate_persisted_storage_size(
        target_path: Path | str,
        *,
        rows_affected: int,
        bytes_written: int | None,
        file_count: int | None,
    ) -> dict[str, Any]:
        target = Path(target_path)
        parquet_files = list(target.rglob("*.parquet")) if target.exists() else []
        on_disk_bytes = sum(f.stat().st_size for f in parquet_files)
        on_disk_files = len(parquet_files)
        reasons: list[str] = []
        if bytes_written is None or bytes_written < 0:
            reasons.append(f"reported bytes_written is {bytes_written}")
        elif bytes_written != on_disk_bytes:
            reasons.append(f"reported bytes_written={bytes_written} != on-disk bytes={on_disk_bytes}")
        if file_count is None or file_count < 0:
            reasons.append(f"reported file_count is {file_count}")
        elif file_count != on_disk_files:
            reasons.append(f"reported file_count={file_count} != on-disk files={on_disk_files}")
        if rows_affected > 0:
            if (bytes_written or 0) <= 0:
                reasons.append(f"non-empty persist ({rows_affected} rows) reported no bytes")
            if (file_count or 0) < 1:
                reasons.append(f"non-empty persist ({rows_affected} rows) reported no files")
        return {
            "check": "storage_size",
            "passed": not reasons,
            "expected_bytes": on_disk_bytes,
            "actual_bytes": bytes_written,
            "expected_files": on_disk_files,
            "actual_files": file_count,
            "reason": "; ".join(reasons) if reasons else None,
        }


def get_dataframe_write_manager(
    platform_name: str,
    spark_session: Any = None,
    execution_engine: str = "default",
) -> DataFrameWriteOperationsManager | None:
    return get_dataframe_manager(
        platform_name,
        manager_class=DataFrameWriteOperationsManager,
        supported_platforms=("polars-df", "polars", "pandas-df", "pandas", "pyspark-df", "pyspark"),
        logger=logger,
        manager_label="write",
        spark_session=spark_session,
        execution_engine=execution_engine,
    )


def pyspark_supports_approx_top_k(spark_session: Any) -> bool:
    if spark_session is None:
        return False
    try:
        version = str(spark_session.version)
    except Exception:
        return False
    parts = version.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return False
    if (major, minor) < (4, 1):
        return False
    try:
        from pyspark.sql import functions as F  # noqa: N812

        return hasattr(F, "approx_top_k_accumulate")
    except ImportError:
        return False


def make_pyspark_hll_persist_builder(
    spark_session: Any,
    source_path: Path | str,
    group_cols: list[str],
    value_col: str,
    sketch_alias: str = "sketch",
) -> Any:
    source_str = str(source_path)

    def builder() -> Any:
        from pyspark.sql import functions as F  # noqa: N812

        return (
            spark_session.read.parquet(source_str)
            .groupBy(*group_cols)
            .agg(F.hll_sketch_agg(value_col).alias(sketch_alias))
        )

    return builder


def make_pyspark_hll_merge_extract(
    spark_session: Any,
    sketch_col: str = "sketch",
) -> Any:

    def merge_extract(state_path: Path) -> float:
        from pyspark.sql import functions as F  # noqa: N812

        state = spark_session.read.parquet(str(state_path))
        row = state.agg(F.hll_sketch_estimate(F.hll_union_agg(sketch_col))).collect()[0]
        return float(row[0])

    return merge_extract


def make_pyspark_topk_persist_builder(
    spark_session: Any,
    source_path: Path | str,
    group_cols: list[str],
    value_col: str,
    sketch_alias: str = "sketch",
) -> Any:
    source_str = str(source_path)

    def builder() -> Any:
        from pyspark.sql import functions as F  # noqa: N812

        return (
            spark_session.read.parquet(source_str)
            .groupBy(*group_cols)
            .agg(F.approx_top_k_accumulate(value_col).alias(sketch_alias))
        )

    return builder


def make_pyspark_topk_merge_extract(
    spark_session: Any,
    sketch_col: str = "sketch",
) -> Any:

    def merge_extract(state_path: Path) -> float:
        from pyspark.sql import functions as F  # noqa: N812

        state = spark_session.read.parquet(str(state_path))
        row = state.agg(F.size(F.approx_top_k_estimate(F.approx_top_k_combine(sketch_col)))).collect()[0]
        return float(row[0])

    return merge_extract


__all__ = [
    "WriteOperationType",
    "DataFrameWriteCapabilities",
    "DataFrameWriteResult",
    "DataFrameWriteOperationsManager",
    "get_dataframe_write_manager",
    "make_pyspark_hll_persist_builder",
    "make_pyspark_hll_merge_extract",
    "make_pyspark_topk_persist_builder",
    "make_pyspark_topk_merge_extract",
    "pyspark_supports_approx_top_k",
    "POLARS_WRITE_CAPABILITIES",
    "PANDAS_WRITE_CAPABILITIES",
    "PYSPARK_WRITE_CAPABILITIES",
]
