# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    from pyspark.sql import SparkSession

    PYSPARK_AVAILABLE = True
except ImportError:
    SparkSession = None
    PYSPARK_AVAILABLE = False

try:
    from delta.tables import DeltaTable

    DELTA_SPARK_AVAILABLE = True
except ImportError:
    DeltaTable = None
    DELTA_SPARK_AVAILABLE = False

from benchbox.core.dataframe.maintenance_interface import (
    BaseDataFrameMaintenanceOperations,
    DataFrameMaintenanceCapabilities,
    TransactionIsolation,
)

logger = logging.getLogger(__name__)

_ARROW_CONVERSION_KEY = "spark.sql.execution.arrow.pyspark.enabled"


def _supports_direct_arrow_ingest() -> bool:
    try:
        import pyspark

        return int(str(pyspark.__version__).split(".")[0]) >= 4
    except (ImportError, ValueError):
        return False


def _is_arrow_table(value: Any) -> bool:
    try:
        import pyarrow as pa
    except ImportError:
        return False
    return isinstance(value, pa.Table)


def create_spark_dataframe_from_pandas(spark: Any, pandas_df: Any) -> Any:
    previous = spark.conf.get(_ARROW_CONVERSION_KEY, None)
    spark.conf.set(_ARROW_CONVERSION_KEY, "true")
    try:
        return spark.createDataFrame(pandas_df)
    finally:
        if previous is None:
            spark.conf.unset(_ARROW_CONVERSION_KEY)
        else:
            spark.conf.set(_ARROW_CONVERSION_KEY, previous)


def create_spark_dataframe_from_arrow(spark: Any, arrow_table: Any) -> Any:
    if _supports_direct_arrow_ingest():
        return spark.createDataFrame(arrow_table)
    return create_spark_dataframe_from_pandas(spark, arrow_table.to_pandas())


PYSPARK_DELTA_CAPABILITIES = DataFrameMaintenanceCapabilities(
    platform_name="pyspark-delta",
    supports_insert=True,
    supports_delete=True,
    supports_update=True,
    supports_merge=True,
    supports_transactions=True,
    transaction_isolation=TransactionIsolation.SNAPSHOT,
    supports_partitioned_delete=True,
    supports_row_level_delete=True,
    supports_time_travel=True,
    max_batch_size=10000000,
    notes="Full ACID compliance via Delta Lake (delta-spark)",
)

PYSPARK_PARQUET_CAPABILITIES = DataFrameMaintenanceCapabilities(
    platform_name="pyspark-parquet",
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
    notes="File-level operations only. Use Delta Lake for row-level operations.",
)


class PySparkMaintenanceOperations(BaseDataFrameMaintenanceOperations):
    def __init__(
        self,
        spark_session: Any,
        working_dir: str | Path | None = None,
        prefer_delta: bool = True,
    ) -> None:
        super().__init__()

        if not PYSPARK_AVAILABLE:
            raise ImportError(
                "PySpark is not installed. Install with: pip install pyspark\n"
                "For Delta Lake support, also install: pip install delta-spark"
            )

        if spark_session is None:
            raise ValueError(
                "spark_session is required. Create one with:\n"
                "  spark = SparkSession.builder.appName('benchbox').getOrCreate()"
            )

        self.spark = spark_session
        self.working_dir = Path(working_dir) if working_dir else None
        self.prefer_delta = prefer_delta and DELTA_SPARK_AVAILABLE
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        if prefer_delta and not DELTA_SPARK_AVAILABLE:
            self.logger.warning(
                "Delta Lake (delta-spark) not available. "
                "Row-level operations (UPDATE/DELETE/MERGE) will not be supported. "
                "Install with: pip install delta-spark"
            )

    def _get_capabilities(self) -> DataFrameMaintenanceCapabilities:
        if self.prefer_delta and DELTA_SPARK_AVAILABLE:
            return PYSPARK_DELTA_CAPABILITIES
        return PYSPARK_PARQUET_CAPABILITIES

    def is_delta_table(self, table_path: str | Path) -> bool:
        if not DELTA_SPARK_AVAILABLE:
            return False

        path = Path(table_path)
        delta_log = path / "_delta_log"
        return delta_log.exists() and delta_log.is_dir()

    def _convert_to_spark_df(self, dataframe: Any) -> Any:
        from pyspark.sql import DataFrame as SparkDataFrame

        if isinstance(dataframe, SparkDataFrame):
            return dataframe

        if _is_arrow_table(dataframe):
            return create_spark_dataframe_from_arrow(self.spark, dataframe)

        if hasattr(dataframe, "to_arrow"):
            return create_spark_dataframe_from_arrow(self.spark, dataframe.to_arrow())

        if hasattr(dataframe, "to_dict") and hasattr(dataframe, "columns"):
            return create_spark_dataframe_from_pandas(self.spark, dataframe)

        if hasattr(dataframe, "to_pandas"):
            return create_spark_dataframe_from_pandas(self.spark, dataframe.to_pandas())

        if isinstance(dataframe, list):
            return self.spark.createDataFrame(dataframe)

        raise TypeError(
            f"Unsupported DataFrame type: {type(dataframe)}. "
            f"Expected Spark DataFrame, Pandas DataFrame, Polars DataFrame, or list."
        )

    def _to_spark_column(self, value: Any) -> Any:
        from pyspark.sql import functions as spark_functions

        if not isinstance(value, str):
            return spark_functions.lit(value)

        if value.startswith("col:"):
            return spark_functions.col(value[4:])
        if value.startswith("expr:"):
            return spark_functions.expr(value[5:])
        if value.startswith("lit:"):
            return spark_functions.lit(value[4:])

        if value.startswith("source.") or value.startswith("target."):
            return spark_functions.col(value)

        return spark_functions.expr(value)

    def _do_insert(
        self,
        table_path: Path | str,
        dataframe: Any,
        partition_columns: list[str] | None,
        mode: str,
    ) -> int:
        table_path = str(table_path)

        spark_df = self._convert_to_spark_df(dataframe)
        row_count = spark_df.count()

        if row_count == 0:
            self.logger.info("No rows to insert")
            return 0

        spark_mode = "append" if mode == "append" else "overwrite"

        is_delta = self.is_delta_table(table_path)
        write_format = "delta" if is_delta else "parquet"

        writer = spark_df.write.mode(spark_mode)

        if partition_columns:
            writer = writer.partitionBy(*partition_columns)

        if write_format == "delta":
            writer.format("delta").save(table_path)
        else:
            writer.parquet(table_path)

        self.logger.info(f"Inserted {row_count} rows to {table_path} (format: {write_format})")
        return row_count

    def _do_delete(
        self,
        table_path: Path | str,
        condition: str | Any,
    ) -> int:
        table_path = str(table_path)

        if not DELTA_SPARK_AVAILABLE:
            raise NotImplementedError("DELETE requires Delta Lake (delta-spark). Install with: pip install delta-spark")

        if not self.is_delta_table(table_path):
            raise NotImplementedError(
                f"DELETE requires Delta Lake table format. "
                f"Table at {table_path} is not a Delta table. "
                f"Convert with: df.write.format('delta').save(path)"
            )

        dt = DeltaTable.forPath(self.spark, table_path)

        rows_before = dt.toDF().count()

        dt.delete(condition=str(condition))

        rows_after = dt.toDF().count()
        rows_deleted = rows_before - rows_after

        self.logger.info(f"Deleted {rows_deleted} rows from {table_path}")
        return rows_deleted

    def _do_update(
        self,
        table_path: Path | str,
        condition: str | Any,
        updates: dict[str, Any],
    ) -> int:
        table_path = str(table_path)

        if not DELTA_SPARK_AVAILABLE:
            raise NotImplementedError("UPDATE requires Delta Lake (delta-spark). Install with: pip install delta-spark")

        if not self.is_delta_table(table_path):
            raise NotImplementedError(
                f"UPDATE requires Delta Lake table format. "
                f"Table at {table_path} is not a Delta table. "
                f"Convert with: df.write.format('delta').save(path)"
            )

        dt = DeltaTable.forPath(self.spark, table_path)

        matching_count = dt.toDF().filter(condition).count()

        if matching_count == 0:
            self.logger.info("No rows match update condition")
            return 0

        update_set = {col: self._to_spark_column(value) for col, value in updates.items()}

        dt.update(condition=condition, set=update_set)

        self.logger.info(f"Updated {matching_count} rows in {table_path}")
        return matching_count

    def _do_merge(
        self,
        table_path: Path | str,
        source_dataframe: Any,
        merge_condition: str | Any,
        when_matched: dict[str, Any] | None,
        when_not_matched: dict[str, Any] | None,
    ) -> int:
        table_path = str(table_path)

        if not DELTA_SPARK_AVAILABLE:
            raise NotImplementedError("MERGE requires Delta Lake (delta-spark). Install with: pip install delta-spark")

        if not self.is_delta_table(table_path):
            raise NotImplementedError(
                f"MERGE requires Delta Lake table format. "
                f"Table at {table_path} is not a Delta table. "
                f"Convert with: df.write.format('delta').save(path)"
            )

        source_df = self._convert_to_spark_df(source_dataframe)
        source_count = source_df.count()

        dt = DeltaTable.forPath(self.spark, table_path)

        merge_builder = dt.alias("target").merge(source_df.alias("source"), merge_condition)

        if when_matched:
            update_exprs = {col: self._to_spark_column(expr) for col, expr in when_matched.items()}
            merge_builder = merge_builder.whenMatchedUpdate(set=update_exprs)

        if when_not_matched:
            insert_exprs = {col: self._to_spark_column(expr) for col, expr in when_not_matched.items()}
            merge_builder = merge_builder.whenNotMatchedInsert(values=insert_exprs)

        merge_builder.execute()

        self.logger.info(f"Merged from {source_count} source rows into {table_path}")
        return source_count

    def execute_bulk_load(
        self,
        source_path: str | Path,
        target_path: str | Path,
        source_format: str = "parquet",
        target_format: str = "parquet",
        compression: str | None = "zstd",
        partition_columns: list[str] | None = None,
        sort_columns: list[str] | None = None,
    ) -> int:
        source_path = str(source_path)
        target_path = str(target_path)

        reader = self.spark.read.format(source_format)

        if source_format == "csv":
            reader = reader.option("header", "true").option("inferSchema", "true")

        df = reader.load(source_path)
        row_count = df.count()

        if row_count == 0:
            self.logger.info("No rows to load")
            return 0

        if sort_columns:
            df = df.orderBy(*sort_columns)

        writer = df.write.mode("overwrite")

        if partition_columns:
            writer = writer.partitionBy(*partition_columns)

        if compression:
            writer = writer.option("compression", compression)

        if target_format == "delta":
            writer.format("delta").save(target_path)
        else:
            writer.parquet(target_path)

        self.logger.info(f"Bulk loaded {row_count} rows to {target_path}")
        return row_count


def get_pyspark_maintenance_operations(
    spark_session: Any = None,
    working_dir: str | Path | None = None,
    prefer_delta: bool = True,
) -> PySparkMaintenanceOperations | None:
    if not PYSPARK_AVAILABLE:
        logger.debug("PySpark not available, cannot create maintenance operations")
        return None

    if spark_session is None:
        logger.debug("No SparkSession provided, cannot create maintenance operations")
        return None

    return PySparkMaintenanceOperations(
        spark_session=spark_session,
        working_dir=working_dir,
        prefer_delta=prefer_delta,
    )


__all__ = [
    "PySparkMaintenanceOperations",
    "get_pyspark_maintenance_operations",
    "PYSPARK_DELTA_CAPABILITIES",
    "PYSPARK_PARQUET_CAPABILITIES",
    "PYSPARK_AVAILABLE",
    "DELTA_SPARK_AVAILABLE",
]
