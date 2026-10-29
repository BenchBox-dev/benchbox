# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from benchbox.platforms.databricks.adapter import DatabricksAdapter, _normalize_table_format
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.dependencies import get_package_install_message

if TYPE_CHECKING:
    from pyspark.sql import DataFrame as SparkDataFrame, SparkSession

logger = logging.getLogger(__name__)


try:
    from databricks.connect import DatabricksSession

    DATABRICKS_CONNECT_AVAILABLE = True
    _databricks_connect_error: str | None = None
except ImportError:
    DATABRICKS_CONNECT_AVAILABLE = False
    DatabricksSession = None
    _databricks_connect_error = None
except Exception as exc:
    DATABRICKS_CONNECT_AVAILABLE = False
    DatabricksSession = None
    _databricks_connect_error = str(exc)

try:
    from pyspark.sql import (
        DataFrame as SparkDataFrame,
        SparkSession,
        functions as F,
    )

    PYSPARK_AVAILABLE = True
except ImportError:
    PYSPARK_AVAILABLE = False
    SparkDataFrame = Any
    SparkSession = Any
    F = None


class DatabricksDataFrameAdapter(DatabricksAdapter):
    plan_capture_phase_eligible = True

    def __init__(self, **config: Any) -> None:
        self.cluster_id = config.pop("compute_resource", None) or config.pop("cluster_id", None)
        self.execution_mode = config.pop("execution_mode", "dataframe")

        if _normalize_table_format(config) == "hudi":
            raise ValueError(
                "DatabricksDataFrameAdapter does not support table_format='hudi': "
                "the DataFrame write path has no Hudi handling. Use the SQL adapter "
                "for Hudi DDL, or table_format='delta' for DataFrame mode."
            )

        super().__init__(**config)

        if self.execution_mode == "dataframe" and not DATABRICKS_CONNECT_AVAILABLE:
            reason = (
                f"Databricks Connect import failed ({_databricks_connect_error})"
                if _databricks_connect_error
                else "Databricks Connect not installed"
            )
            logger.warning(
                "%s. DataFrame mode requires: uv add databricks-connect. Falling back to SQL mode.",
                reason,
            )
            self.execution_mode = "sql"

        self._spark: SparkSession | None = None
        self._spark_initialized = False

    @property
    def platform_name(self) -> str:
        mode_suffix = "-df" if self.execution_mode == "dataframe" else ""
        return f"Databricks{mode_suffix}"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> DatabricksDataFrameAdapter:
        from benchbox.core.compute_resource import normalize_compute_options

        config = normalize_compute_options("databricks-df", config)
        adapter_config = dict(config)
        adapter_config["execution_mode"] = config.get("execution_mode", "dataframe")
        adapter_config["cluster_id"] = config.get("compute_resource") or config.get("cluster_id")

        return cls(**adapter_config)

    def _get_or_create_spark_session(self) -> SparkSession:
        if self._spark is not None:
            return self._spark

        if not DATABRICKS_CONNECT_AVAILABLE:
            raise ImportError(
                get_package_install_message("databricks-connect", "Databricks Connect required for DataFrame mode.")
            )

        try:
            builder = DatabricksSession.builder

            if self.server_hostname:
                host = f"https://{self.server_hostname}"
                builder = builder.host(host)

            if self.access_token:
                builder = builder.token(self.access_token)

            if self.cluster_id:
                builder = builder.clusterId(self.cluster_id)

            self._spark = builder.getOrCreate()
            self._spark_initialized = True

            self.log_verbose(f"Databricks Connect session created: {self.server_hostname}")

            return self._spark

        except Exception as e:
            raise RuntimeError(f"Failed to create Databricks Connect session: {e}") from e

    @property
    def spark(self) -> SparkSession:
        return self._get_or_create_spark_session()

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        info = super().get_platform_info(connection)

        info["execution_mode"] = self.execution_mode
        info["cluster_id"] = self.cluster_id
        info["databricks_connect_available"] = DATABRICKS_CONNECT_AVAILABLE

        if self.execution_mode == "dataframe" and self._spark_initialized:
            try:
                info["spark_version"] = self._spark.version if self._spark else None
            except Exception:
                info["spark_version"] = None

        return info

    def execute_dataframe_query(
        self,
        connection: Any,
        query_builder: Callable[[SparkSession, dict[str, SparkDataFrame]], SparkDataFrame],
        query_id: str,
        tables: dict[str, str] | None = None,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        start_time = mono_time()
        self.log_verbose(f"Executing DataFrame query {query_id}")

        try:
            spark = self.spark

            spark.catalog.setCurrentCatalog(self.catalog)
            spark.catalog.setCurrentDatabase(self.schema)
            self.log_very_verbose(f"Set Spark context: {self.catalog}.{self.schema}")

            table_registry: dict[str, SparkDataFrame] = {}
            if tables:
                for table_name, _table_path in tables.items():
                    table_registry[table_name] = spark.table(table_name)
                    self.log_very_verbose(f"Registered table: {table_name}")

            result_df = query_builder(spark, table_registry)

            result = result_df.collect()
            execution_time = elapsed_seconds(start_time)
            actual_row_count = len(result)

            self.log_verbose(f"Query {query_id} completed: {actual_row_count} rows in {execution_time:.3f}s")

            validation_result = None
            if validate_row_count and benchmark_type:
                from benchbox.core.validation.query_validation import QueryValidator

                validator = QueryValidator()
                validation_result = validator.validate_query_result(
                    benchmark_type=benchmark_type,
                    query_id=query_id,
                    actual_row_count=actual_row_count,
                    scale_factor=scale_factor,
                    stream_id=stream_id,
                )

                if validation_result.warning_message:
                    self.log_verbose(f"Row count validation: {validation_result.warning_message}")
                elif not validation_result.is_valid:
                    self.log_verbose(f"Row count validation FAILED: {validation_result.error_message}")

            result_dict = self._build_query_result_with_validation(
                query_id=query_id,
                execution_time=execution_time,
                actual_row_count=actual_row_count,
                first_row=tuple(result[0]) if result else None,
                validation_result=validation_result,
                materialized_rows=result,
            )

            result_dict["execution_mode"] = "dataframe"
            result_dict["resource_usage"] = {
                "execution_time_seconds": execution_time,
            }

        except Exception as e:
            execution_time = elapsed_seconds(start_time)
            return {
                "query_id": query_id,
                "status": "FAILED",
                "execution_time_seconds": execution_time,
                "rows_returned": 0,
                "error": str(e),
                "error_type": type(e).__name__,
                "execution_mode": "dataframe",
            }

        self._merge_dataframe_plan_into_result(result_dict, result_df, query_id)
        return result_dict

    def _merge_dataframe_plan_into_result(self, result: dict[str, Any], dataframe: Any, query_id: str) -> None:
        if not self.capture_plans:
            return
        query_plan, capture_time_ms = self.capture_spark_dataframe_plan(dataframe, query_id)
        if query_plan:
            result["query_plan"] = query_plan
            result["plan_fingerprint"] = query_plan.plan_fingerprint
            if self.normalize_plan_literals:
                result["plan_fingerprint_normalized"] = query_plan.normalized_fingerprint
        if capture_time_ms is not None:
            result["plan_capture_time_ms"] = capture_time_ms

    def execute_query(
        self,
        connection: Any,
        query: str | Callable[[SparkSession, dict[str, SparkDataFrame]], SparkDataFrame],
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        if callable(query):
            return self.execute_dataframe_query(
                connection=connection,
                query_builder=query,
                query_id=query_id,
                benchmark_type=benchmark_type,
                scale_factor=scale_factor,
                validate_row_count=validate_row_count,
                stream_id=stream_id,
            )

        return super().execute_query(
            connection=connection,
            query=query,
            query_id=query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )

    def close_connection(self, connection: Any) -> None:
        super().close_connection(connection)

        if self._spark is not None and self._spark_initialized:
            try:
                self._spark.stop()
                self.log_verbose("Databricks Connect session stopped")
            except Exception as e:
                self.logger.warning(f"Error stopping Spark session: {e}")
            finally:
                self._spark = None
                self._spark_initialized = False

    def col(self, name: str) -> Any:
        if not PYSPARK_AVAILABLE:
            raise ImportError("PySpark required for DataFrame expressions")
        return F.col(name)

    def lit(self, value: Any) -> Any:
        if not PYSPARK_AVAILABLE:
            raise ImportError("PySpark required for DataFrame expressions")
        return F.lit(value)

    def sum_col(self, column: str) -> Any:
        if not PYSPARK_AVAILABLE:
            raise ImportError("PySpark required for DataFrame expressions")
        return F.sum(F.col(column))

    def avg_col(self, column: str) -> Any:
        if not PYSPARK_AVAILABLE:
            raise ImportError("PySpark required for DataFrame expressions")
        return F.avg(F.col(column))

    def count_col(self, column: str | None = None) -> Any:
        if not PYSPARK_AVAILABLE:
            raise ImportError("PySpark required for DataFrame expressions")
        if column:
            return F.count(F.col(column))
        return F.count(F.lit(1))

    def min_col(self, column: str) -> Any:
        if not PYSPARK_AVAILABLE:
            raise ImportError("PySpark required for DataFrame expressions")
        return F.min(F.col(column))

    def max_col(self, column: str) -> Any:
        if not PYSPARK_AVAILABLE:
            raise ImportError("PySpark required for DataFrame expressions")
        return F.max(F.col(column))


__all__ = ["DatabricksDataFrameAdapter", "DATABRICKS_CONNECT_AVAILABLE"]
