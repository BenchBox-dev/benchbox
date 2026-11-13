# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.platforms.pyspark import (
    PYSPARK_AVAILABLE,
    PYSPARK_VERSION,
    SparkSessionManager,
)

if PYSPARK_AVAILABLE:
    from pyspark.sql import DataFrame, SparkSession, functions as spark_functions
    from pyspark.sql.column import Column
    from pyspark.sql.types import StringType, StructField, StructType
    from pyspark.sql.window import Window

    F = spark_functions
else:
    DataFrame = Any
    SparkSession = Any
    Column = Any
    F = None
    Window = Any
    StringType = Any
    StructField = Any
    StructType = Any

from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.core.results.timer_scope import TIMING_METHOD_LEGACY
from benchbox.platforms.dataframe.expression_family import (
    ExpressionFamilyAdapter,
)
from benchbox.platforms.dataframe.shared_loading import resolve_empty_string_restore_columns
from benchbox.utils.file_format import TRAILING_DUMMY_COLUMN, has_trailing_delimiter

if TYPE_CHECKING:
    from pyspark.sql.window import WindowSpec

logger = logging.getLogger(__name__)

if PYSPARK_AVAILABLE:
    PySparkDF = DataFrame
    PySparkLazyDF = DataFrame
    PySparkExpr = Column
else:
    PySparkDF = Any
    PySparkLazyDF = Any
    PySparkExpr = Any


class PySparkDataFrameAdapter(ExpressionFamilyAdapter[PySparkDF, PySparkLazyDF, PySparkExpr]):
    timing_method = TIMING_METHOD_LEGACY

    def __init__(
        self,
        working_dir: str | Path | None = None,
        verbose: bool = False,
        very_verbose: bool = False,
        tuning_config: DataFrameTuningConfiguration | None = None,
        master: str = "local[*]",
        app_name: str = "BenchBox-TPC-H",
        driver_memory: str = "4g",
        executor_memory: str | None = None,
        shuffle_partitions: int | None = None,
        enable_aqe: bool = True,
        allow_third_party_extensions: bool = False,
        **spark_config: Any,
    ) -> None:
        if not PYSPARK_AVAILABLE:
            raise ImportError("PySpark not installed. Install with: pip install pyspark pyarrow")

        super().__init__(
            working_dir=working_dir,
            verbose=verbose,
            very_verbose=very_verbose,
            tuning_config=tuning_config,
        )

        self._master = master
        self._app_name = app_name
        self._driver_memory = driver_memory
        self._executor_memory = executor_memory
        self._shuffle_partitions = shuffle_partitions or os.cpu_count() or 8
        self._enable_aqe = enable_aqe
        self.allow_third_party_extensions = bool(allow_third_party_extensions)
        self._spark_config = spark_config

        self._spark: SparkSession | None = None
        self._session_claimed = False

        self._validate_and_apply_tuning()

    def _apply_tuning(self) -> None:
        config = self._tuning_config

        if config.parallelism.thread_count is not None:
            self._master = f"local[{config.parallelism.thread_count}]"
            self._shuffle_partitions = config.parallelism.thread_count
            self._log_verbose(f"Set master={self._master}, shuffle_partitions={self._shuffle_partitions}")

        if config.memory.memory_limit is not None:
            self._driver_memory = config.memory.memory_limit
            self._log_verbose(f"Set driver_memory={self._driver_memory}")

        if config.execution.streaming_mode:
            self._log_verbose("Note: streaming_mode not applicable to PySpark batch DataFrames")

    @property
    def platform_name(self) -> str:
        return "PySpark"

    def _get_or_create_session(self) -> SparkSession:
        if self._spark is None:
            self._spark = SparkSessionManager.get_or_create(
                master=self._master,
                app_name=self._app_name,
                driver_memory=self._driver_memory,
                executor_memory=self._executor_memory,
                shuffle_partitions=self._shuffle_partitions,
                enable_aqe=self._enable_aqe,
                extra_configs=self._spark_config,
                verbose=self.verbose,
            )
            self._session_claimed = True

            if self.verbose and self._spark is not None:
                master = self._spark.sparkContext.master
                self._log_verbose(f"SparkSession acquired: master={master}")

        return self._spark

    @property
    def spark(self) -> SparkSession:
        return self._get_or_create_session()

    def close(self) -> None:
        if self._session_claimed:
            SparkSessionManager.release()
            self._session_claimed = False
            if self.verbose:
                self._log_verbose("SparkSession reference released")
        self._spark = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

    def __enter__(self) -> PySparkDataFrameAdapter:
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        self.close()

    def _ensure_spark(self) -> None:
        _ = self.spark

    def col(self, name: str) -> PySparkExpr:
        self._ensure_spark()
        return F.col(name)

    def lit(self, value: Any) -> PySparkExpr:
        self._ensure_spark()
        return F.lit(value)

    def date_sub(self, column: PySparkExpr, days: int) -> PySparkExpr:
        self._ensure_spark()
        return F.date_sub(column, days)

    def date_add(self, column: PySparkExpr, days: int) -> PySparkExpr:
        self._ensure_spark()
        return F.date_add(column, days)

    def cast_date(self, column: PySparkExpr) -> PySparkExpr:
        return column.cast("date")

    def cast_string(self, column: PySparkExpr) -> PySparkExpr:
        return column.cast("string")

    def sum(self, column: str) -> PySparkExpr:
        self._ensure_spark()
        return F.sum(F.col(column))

    def mean(self, column: str) -> PySparkExpr:
        self._ensure_spark()
        return F.avg(F.col(column))

    def count(self, column: str | None = None) -> PySparkExpr:
        self._ensure_spark()
        if column:
            return F.count(F.col(column))
        return F.count(F.lit(1))

    def min(self, column: str) -> PySparkExpr:
        self._ensure_spark()
        return F.min(F.col(column))

    def max(self, column: str) -> PySparkExpr:
        self._ensure_spark()
        return F.max(F.col(column))

    def when(self, condition: PySparkExpr) -> Any:
        self._ensure_spark()
        return F.when(condition, True)

    def concat_str(self, *columns: str, separator: str = "") -> PySparkExpr:
        self._ensure_spark()
        if separator:
            return F.concat_ws(separator, *[F.col(c) for c in columns])
        return F.concat(*[F.col(c) for c in columns])

    def read_csv(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        has_header: bool = True,
        column_names: list[str] | None = None,
        null_marker: str | None = None,
        string_columns: list[str] | None = None,
        temporal_columns: dict[str, str] | None = None,
    ) -> PySparkLazyDF:
        path_str = str(path)

        reader = self.spark.read.option("delimiter", delimiter).option("header", str(has_header).lower())

        if null_marker is not None and column_names and has_trailing_delimiter(path, delimiter, column_names):
            extended_names = column_names + [TRAILING_DUMMY_COLUMN]
            schema = self._build_schema(extended_names)
            df = reader.schema(schema).csv(path_str)
            df = df.drop(TRAILING_DUMMY_COLUMN)
        elif column_names:
            schema = self._build_schema(column_names)
            df = reader.schema(schema).csv(path_str)
        else:
            df = reader.option("inferSchema", "true").csv(path_str)

        present = resolve_empty_string_restore_columns(string_columns, null_marker, df.columns)
        if present:
            df = df.fillna("", subset=present)

        return df

    def _build_schema(self, column_names: list[str]) -> StructType:
        return StructType([StructField(name, StringType(), nullable=True) for name in column_names])

    def read_parquet(self, path: Path) -> PySparkLazyDF:
        return self.spark.read.parquet(str(path))

    def collect(self, df: PySparkLazyDF) -> PySparkDF:
        _ = df.count()
        return df

    def get_row_count(self, df: PySparkLazyDF | PySparkDF) -> int:
        return df.count()

    def scalar(self, df: PySparkDF, column: str | None = None) -> Any:
        rows = df.limit(2).collect()

        if len(rows) == 0:
            raise ValueError("Cannot extract scalar from empty DataFrame")
        if len(rows) > 1:
            raise ValueError("Expected exactly one row, got multiple")

        first_row = rows[0]

        if column is not None:
            return first_row[column]

        return first_row[0]

    def scalar_to_df(self, data: dict[str, Any]) -> PySparkDF:
        self._ensure_spark()
        from pyspark.sql import Row

        row = Row(**data)
        return self._spark.createDataFrame([row])

    def _build_window_spec(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> WindowSpec:
        self._ensure_spark()
        window = Window.partitionBy(*[F.col(c) for c in partition_by]) if partition_by else Window.partitionBy()

        order_cols = []
        for col_name, ascending in order_by:
            col_expr = F.col(col_name)
            if ascending:
                order_cols.append(col_expr.asc())
            else:
                order_cols.append(col_expr.desc())

        if order_cols:
            window = window.orderBy(*order_cols)

        return window

    def _build_aggregate_window_spec(
        self,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> WindowSpec:
        self._ensure_spark()
        window = Window.partitionBy(*[F.col(c) for c in partition_by]) if partition_by else Window.partitionBy()

        if order_by:
            order_cols = []
            for col_name, ascending in order_by:
                col_expr = F.col(col_name)
                order_cols.append(col_expr.asc() if ascending else col_expr.desc())
            window = window.orderBy(*order_cols)
            window = window.rowsBetween(Window.unboundedPreceding, Window.currentRow)

        return window

    def _over_ordered_window(
        self,
        window_fn: Callable[[], PySparkExpr],
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PySparkExpr:
        return window_fn().over(self._build_window_spec(order_by, partition_by))

    def _offset_window(
        self,
        offset_fn: Callable[..., PySparkExpr],
        column: str,
        offset: int,
        order_by: list[tuple[str, bool]] | None,
        partition_by: list[str] | None = None,
    ) -> PySparkExpr:
        window_spec = self._build_window_spec(order_by or [(column, True)], partition_by)
        return offset_fn(F.col(column), offset).over(window_spec)

    def window_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PySparkExpr:
        return self._over_ordered_window(F.rank, order_by, partition_by)

    def window_row_number(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PySparkExpr:
        return self._over_ordered_window(F.row_number, order_by, partition_by)

    def window_dense_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PySparkExpr:
        return self._over_ordered_window(F.dense_rank, order_by, partition_by)

    def window_sum(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> PySparkExpr:
        window_spec = self._build_aggregate_window_spec(partition_by, order_by)
        return F.sum(F.col(column)).over(window_spec)

    def window_avg(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> PySparkExpr:
        window_spec = self._build_aggregate_window_spec(partition_by, order_by)
        return F.avg(F.col(column)).over(window_spec)

    def window_count(
        self,
        column: str | None = None,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> PySparkExpr:
        window_spec = self._build_aggregate_window_spec(partition_by, order_by)
        if column:
            return F.count(F.col(column)).over(window_spec)
        else:
            return F.count(F.lit(1)).over(window_spec)

    def window_min(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> PySparkExpr:
        window_spec = self._build_aggregate_window_spec(partition_by, None)
        return F.min(F.col(column)).over(window_spec)

    def window_max(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> PySparkExpr:
        window_spec = self._build_aggregate_window_spec(partition_by, None)
        return F.max(F.col(column)).over(window_spec)

    def window_lag(
        self,
        column: str,
        offset: int = 1,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> PySparkExpr:
        return self._offset_window(F.lag, column, offset, order_by, partition_by)

    def window_lead(
        self,
        column: str,
        offset: int = 1,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> PySparkExpr:
        return self._offset_window(F.lead, column, offset, order_by, partition_by)

    def window_ntile(
        self,
        n: int,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PySparkExpr:
        window_spec = self._build_window_spec(order_by, partition_by)
        return F.ntile(n).over(window_spec)

    def window_percent_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PySparkExpr:
        return self._over_ordered_window(F.percent_rank, order_by, partition_by)

    def window_cume_dist(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PySparkExpr:
        return self._over_ordered_window(F.cume_dist, order_by, partition_by)

    def union_all(self, *dataframes: PySparkLazyDF) -> PySparkLazyDF:
        if len(dataframes) == 0:
            raise ValueError("At least one DataFrame required for union")
        if len(dataframes) == 1:
            return dataframes[0]

        result = dataframes[0]
        for df in dataframes[1:]:
            result = result.union(df)
        return result

    def rename_columns(self, df: PySparkLazyDF, mapping: dict[str, str]) -> PySparkLazyDF:
        result = df
        for old_name, new_name in mapping.items():
            if old_name in df.columns:
                result = result.withColumnRenamed(old_name, new_name)
        return result

    def _concat_dataframes(self, dfs: list[PySparkLazyDF]) -> PySparkLazyDF:
        if len(dfs) == 1:
            return dfs[0]
        return self.union_all(*dfs)

    def _get_first_row(self, df: PySparkDF) -> tuple | None:
        rows = df.take(1)
        if not rows:
            return None
        return tuple(rows[0])

    def get_platform_info(self) -> dict[str, Any]:
        info = {
            "platform": self.platform_name,
            "family": self.family,
            "master": self._master,
            "driver_memory": self._driver_memory,
            "shuffle_partitions": self._shuffle_partitions,
            "aqe_enabled": self._enable_aqe,
            "working_dir": str(self.working_dir),
        }

        if PYSPARK_AVAILABLE:
            info["version"] = PYSPARK_VERSION

        self._enrich_with_extension_options(info)
        return info

    def get_tuning_summary(self) -> dict[str, Any]:
        base_summary = super().get_tuning_summary()
        base_summary.update(
            {
                "master": self._master,
                "driver_memory": self._driver_memory,
                "executor_memory": self._executor_memory,
                "shuffle_partitions": self._shuffle_partitions,
                "aqe_enabled": self._enable_aqe,
                "spark_version": PYSPARK_VERSION,
            }
        )
        return base_summary

    def explain(self, df: PySparkLazyDF, mode: str = "extended") -> str:
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        try:
            df.explain(mode=mode)
        finally:
            sys.stdout = old_stdout

        return buffer.getvalue()

    def get_query_plan(self, df: PySparkLazyDF) -> dict[str, str]:
        return {
            "logical": self.explain(df, "simple"),
            "physical": self.explain(df, "extended"),
        }

    def sql(self, query: str) -> PySparkLazyDF:
        return self.spark.sql(query)

    def register_table(self, name: str, df: PySparkLazyDF) -> None:
        df.createOrReplaceTempView(name)

    def to_pandas(self, df: PySparkLazyDF) -> Any:
        return df.toPandas()

    def to_polars(self, df: PySparkLazyDF) -> Any:
        try:
            import polars as pl
        except ImportError as e:
            raise ImportError("Polars not installed. Install with: pip install polars") from e

        try:
            if hasattr(df, "toArrow"):
                arrow_table = df.toArrow()
                return pl.from_arrow(arrow_table)
        except Exception:
            pass

        pandas_df = df.toPandas()
        return pl.from_pandas(pandas_df)
