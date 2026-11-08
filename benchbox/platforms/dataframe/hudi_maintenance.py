# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from benchbox.platforms.dataframe.pyspark_maintenance import (
    create_spark_dataframe_from_arrow,
    create_spark_dataframe_from_pandas,
)
from benchbox.utils.clock import mono_time

try:
    from pyspark.sql import SparkSession

    PYSPARK_AVAILABLE = True
except ImportError:
    SparkSession = None
    PYSPARK_AVAILABLE = False

from benchbox.core.dataframe.maintenance_interface import (
    HUDI_CAPABILITIES,
    BaseDataFrameMaintenanceOperations,
    DataFrameMaintenanceCapabilities,
    MaintenanceOperationType,
    MaintenanceResult,
)

logger = logging.getLogger(__name__)

HUDI_VERSION = "1.2.1"
HUDI_BUNDLES_BY_SPARK: dict[tuple[int, int], str] = {
    (3, 5): f"org.apache.hudi:hudi-spark3.5-bundle_2.12:{HUDI_VERSION}",
    (4, 0): f"org.apache.hudi:hudi-spark4.0-bundle_2.13:{HUDI_VERSION}",
    (4, 1): f"org.apache.hudi:hudi-spark4.1-bundle_2.13:{HUDI_VERSION}",
}


def hudi_bundle_for_spark(spark_version: str) -> str | None:
    parts = spark_version.split(".")
    try:
        return HUDI_BUNDLES_BY_SPARK.get((int(parts[0]), int(parts[1])))
    except (IndexError, ValueError):
        return None


def validate_hudi_spark_version(spark_session: Any) -> None:
    version = getattr(spark_session, "version", None)
    if not isinstance(version, str) or hudi_bundle_for_spark(version) is not None:
        return
    parts = version.split(".")
    missing = f"hudi-spark{parts[0]}.{parts[1]}-bundle" if len(parts) > 1 else "a matching hudi-spark bundle"
    supported = ", ".join(
        f"Spark {major}.{minor} with {bundle}" for (major, minor), bundle in HUDI_BUNDLES_BY_SPARK.items()
    )
    raise RuntimeError(
        f"Hudi {HUDI_VERSION} publishes no {missing} for Spark {version}, so it has no bundle that works on Spark {version}. Supported pairs: {supported}. "
        "Use one of these Spark versions for Hudi maintenance."
    )


class HudiMaintenanceOperations(BaseDataFrameMaintenanceOperations):
    def __init__(
        self,
        spark_session: Any,
        record_key: str | None = None,
        precombine_field: str | None = None,
        table_type: str = "COPY_ON_WRITE",
        working_dir: str | Path | None = None,
    ) -> None:
        super().__init__()

        if not PYSPARK_AVAILABLE:
            raise ImportError(
                "PySpark is not installed. Install with: pip install pyspark\n"
                "For Hudi support, also configure Spark with:\n"
                f"  spark.jars.packages={HUDI_BUNDLES_BY_SPARK[(3, 5)]}"
            )

        if spark_session is None:
            raise ValueError(
                "spark_session is required. Create one with:\n"
                "  spark = SparkSession.builder \\\n"
                "      .appName('benchbox') \\\n"
                f"      .config('spark.jars.packages', '{HUDI_BUNDLES_BY_SPARK[(3, 5)]}') \\\n"
                "      .getOrCreate()"
            )

        validate_hudi_spark_version(spark_session)

        self.spark = spark_session
        self.record_key = record_key
        self.precombine_field = precombine_field
        self.table_type = table_type
        self.working_dir = Path(working_dir) if working_dir else None
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def _get_capabilities(self) -> DataFrameMaintenanceCapabilities:
        return HUDI_CAPABILITIES

    def _convert_to_spark_df(self, dataframe: Any) -> Any:
        from pyspark.sql import DataFrame as SparkDataFrame

        if isinstance(dataframe, SparkDataFrame):
            return dataframe

        try:
            import pyarrow as pa

            if isinstance(dataframe, pa.Table):
                return create_spark_dataframe_from_arrow(self.spark, dataframe)
        except ImportError:
            pass

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
            f"Expected Spark DataFrame, Pandas DataFrame, Polars DataFrame, PyArrow Table, or list."
        )

    def _normalize_table_identifier(self, table_path: str | Path) -> str:
        path_str = str(table_path)

        if "." in path_str and "/" not in path_str and "\\" not in path_str:
            return path_str

        if path_str.startswith("/") or path_str.startswith("s3://") or path_str.startswith("gs://"):
            return f"`hudi`.`{path_str}`"

        return path_str

    def _do_insert(
        self,
        table_path: Path | str,
        dataframe: Any,
        partition_columns: list[str] | None,
        mode: str,
    ) -> int:
        table_id = self._normalize_table_identifier(str(table_path))

        spark_df = self._convert_to_spark_df(dataframe)
        row_count = spark_df.count()

        if row_count == 0:
            self.logger.info("No rows to insert")
            return 0

        temp_view = f"_hudi_insert_source_{id(spark_df)}"
        spark_df.createOrReplaceTempView(temp_view)

        try:
            if mode == "overwrite":
                self.spark.sql(f"INSERT OVERWRITE TABLE {table_id} SELECT * FROM {temp_view}")
            else:
                self.spark.sql(f"INSERT INTO {table_id} SELECT * FROM {temp_view}")

            self.logger.info(f"Inserted {row_count} rows to Hudi table {table_id}")
            return row_count

        finally:
            self.spark.catalog.dropTempView(temp_view)

    def _do_delete(
        self,
        table_path: Path | str,
        condition: str | Any,
    ) -> int:
        table_id = self._normalize_table_identifier(str(table_path))

        count_before = self.spark.sql(f"SELECT COUNT(*) as cnt FROM {table_id}").collect()[0]["cnt"]

        self.spark.sql(f"DELETE FROM {table_id} WHERE {condition}")

        count_after = self.spark.sql(f"SELECT COUNT(*) as cnt FROM {table_id}").collect()[0]["cnt"]

        rows_deleted = count_before - count_after
        self.logger.info(f"Deleted {rows_deleted} rows from Hudi table {table_id}")
        return rows_deleted

    def _do_update(
        self,
        table_path: Path | str,
        condition: str | Any,
        updates: dict[str, Any],
    ) -> int:
        table_id = self._normalize_table_identifier(str(table_path))

        matching_count = self.spark.sql(f"SELECT COUNT(*) as cnt FROM {table_id} WHERE {condition}").collect()[0]["cnt"]

        if matching_count == 0:
            self.logger.info("No rows match update condition")
            return 0

        set_clauses = ", ".join([f"{col} = {val}" for col, val in updates.items()])

        self.spark.sql(f"UPDATE {table_id} SET {set_clauses} WHERE {condition}")

        self.logger.info(f"Updated {matching_count} rows in Hudi table {table_id}")
        return matching_count

    def _do_merge(
        self,
        table_path: Path | str,
        source_dataframe: Any,
        merge_condition: str | Any,
        when_matched: dict[str, Any] | None,
        when_not_matched: dict[str, Any] | None,
    ) -> int:
        table_id = self._normalize_table_identifier(str(table_path))

        source_df = self._convert_to_spark_df(source_dataframe)
        source_count = source_df.count()

        source_view = f"_hudi_merge_source_{id(source_df)}"
        source_df.createOrReplaceTempView(source_view)

        try:
            merge_sql = f"MERGE INTO {table_id} AS target\n"
            merge_sql += f"USING {source_view} AS source\n"
            merge_sql += f"ON {merge_condition}\n"

            if when_matched:
                set_clauses = ", ".join([f"target.{col} = {val}" for col, val in when_matched.items()])
                merge_sql += f"WHEN MATCHED THEN UPDATE SET {set_clauses}\n"

            if when_not_matched:
                columns = ", ".join(when_not_matched.keys())
                values = ", ".join([str(v) for v in when_not_matched.values()])
                merge_sql += f"WHEN NOT MATCHED THEN INSERT ({columns}) VALUES ({values})"

            self.spark.sql(merge_sql)

            self.logger.info(f"Merged {source_count} source rows into Hudi table {table_id}")
            return source_count

        finally:
            self.spark.catalog.dropTempView(source_view)

    def _call_target(self, table_path: Path | str, *, allow_path: bool = True) -> str:
        value = str(table_path).replace("'", "''")
        if "/" in value or "\\" in value:
            if not allow_path:
                raise ValueError(
                    "Hudi run_clean path form is unverified; pass a catalog table "
                    f"name instead of a path, got {table_path!r}."
                )
            return f"path => '{value}'"
        return f"table => '{value}'"

    def _run_procedure(self, operation: MaintenanceOperationType, sql: str, start_time: float) -> MaintenanceResult:
        try:
            result_frame = self.spark.sql(sql)
            rows = result_frame.collect() if hasattr(result_frame, "collect") else []
            row_dicts = [dict(row.asDict()) if hasattr(row, "asDict") else dict(row) for row in rows]
            deleted = 0
            for row_dict in row_dicts:
                for key, value in row_dict.items():
                    if "deleted" in str(key).lower() and isinstance(value, int):
                        deleted += value
            end_time = mono_time()
            self.logger.info(f"Hudi procedure succeeded: {sql}")
            return MaintenanceResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                rows_affected=deleted,
                metrics={"statement": sql, "result_rows": row_dicts},
            )
        except Exception as e:
            self.logger.error(f"Hudi procedure failed: {e}")
            end_time = mono_time()
            return MaintenanceResult(
                operation_type=operation,
                success=False,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                rows_affected=0,
                error_message=str(e),
            )

    def optimize_table(
        self,
        table_path: Path | str,
        *,
        strategy: str = "compact",
        columns: list[str] | None = None,
        partition_filter: Any | None = None,
    ) -> MaintenanceResult:
        if columns:
            self.logger.warning("Hudi optimize ignores columns; procedure takes no column arguments.")
        if partition_filter is not None:
            self.logger.warning("Hudi optimize ignores partition_filter; procedure takes no partition arguments.")
        operation = MaintenanceOperationType.OPTIMIZE
        start_time = mono_time()
        try:
            self._check_capability(operation)
            normalized = strategy.lower()
            target = self._call_target(table_path)
            if normalized == "compact":
                sql = f"CALL run_compaction(op => 'run', {target})"
            elif normalized == "cluster":
                sql = f"CALL run_clustering({target})"
            else:
                raise NotImplementedError(f"Unknown Hudi optimize strategy '{strategy}'. Use 'compact' or 'cluster'.")
            return self._run_procedure(operation, sql, start_time)
        except NotImplementedError:
            raise
        except Exception as e:
            self.logger.error(f"OPTIMIZE failed: {e}")
            end_time = mono_time()
            return MaintenanceResult(
                operation_type=operation,
                success=False,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                rows_affected=0,
                error_message=str(e),
            )

    def vacuum_table(
        self,
        table_path: Path | str,
        *,
        retention_hours: int | None = None,
        dry_run: bool = True,
        enforce_retention: bool = True,
    ) -> MaintenanceResult:
        if not enforce_retention:
            self.logger.warning("Hudi run_clean ignores enforce_retention=False; cleaner always enforces retention.")
        operation = MaintenanceOperationType.VACUUM
        start_time = mono_time()
        try:
            self._check_capability(operation)
            if dry_run:
                raise NotImplementedError("Hudi run_clean has no dry-run mode. Re-run with dry_run=False to clean.")
            target = self._call_target(table_path, allow_path=False)
            if retention_hours is not None:
                sql = (
                    f"CALL run_clean({target}, clean_policy => 'KEEP_LATEST_BY_HOURS', "
                    f"hours_retained => {int(retention_hours)})"
                )
            else:
                sql = f"CALL run_clean({target})"
            return self._run_procedure(operation, sql, start_time)
        except (NotImplementedError, ValueError):
            raise
        except Exception as e:
            self.logger.error(f"VACUUM failed: {e}")
            end_time = mono_time()
            return MaintenanceResult(
                operation_type=operation,
                success=False,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                rows_affected=0,
                error_message=str(e),
            )


def get_hudi_maintenance_operations(
    spark_session: Any = None,
    record_key: str | None = None,
    precombine_field: str | None = None,
    table_type: str = "COPY_ON_WRITE",
    working_dir: str | Path | None = None,
) -> HudiMaintenanceOperations | None:
    if not PYSPARK_AVAILABLE:
        logger.debug("Hudi maintenance not available (pyspark not installed)")
        return None

    if spark_session is None:
        logger.debug("No SparkSession provided, cannot create Hudi maintenance operations")
        return None

    return HudiMaintenanceOperations(
        spark_session=spark_session,
        record_key=record_key,
        precombine_field=precombine_field,
        table_type=table_type,
        working_dir=working_dir,
    )


__all__ = [
    "HudiMaintenanceOperations",
    "get_hudi_maintenance_operations",
    "PYSPARK_AVAILABLE",
]
