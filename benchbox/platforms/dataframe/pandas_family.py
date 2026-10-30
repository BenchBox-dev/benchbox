# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from benchbox.core.dataframe.context import DataFrameContextImpl
from benchbox.core.dataframe.profiling import (
    MemoryTracker,
    QueryExecutionProfile,
    QueryProfileContext,
)
from benchbox.core.dataframe.query import DataFrameQuery
from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.platforms.dataframe._result_helpers import (
    build_failure_result_dict,
    build_success_result_dict,
)
from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin
from benchbox.platforms.dataframe.shared_loading import resolve_dataframe_csv_dialect
from benchbox.platforms.dataframe.tuning_mixin import TuningConfigurableMixin
from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.file_format import detect_data_format

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

DF = TypeVar("DF")


def _schema_column_types(
    benchmark: Any,
    table_name: str,
    column_names: list[str] | None,
) -> list[str | None] | None:
    if benchmark is None or not column_names:
        return None
    try:
        from benchbox.core.dataframe.schema_utils import get_benchmark_schema_columns

        schema = get_benchmark_schema_columns(benchmark)
    except Exception:
        return None
    if not schema:
        return None
    columns = schema.get(table_name)
    if columns is None:
        lowered = {key.lower(): value for key, value in schema.items()}
        columns = lowered.get(table_name.lower())
    if not columns:
        return None
    type_by_name = {column.get("name", "").lower(): column.get("type") for column in columns}
    return [type_by_name.get(name.lower()) for name in column_names]


class PandasFamilyContext(DataFrameContextImpl[DF], Generic[DF]):
    def __init__(self, adapter: PandasFamilyAdapter[DF]) -> None:
        super().__init__(platform=adapter.platform_name, family="pandas")
        self._adapter = adapter

    def get_table(self, name: str) -> UnifiedPandasFrame[DF]:
        native_df = super().get_table(name)
        return UnifiedPandasFrame(native_df, self._adapter)

    def col(self, name: str) -> str:
        return name

    def lit(self, value: Any) -> Any:
        return value

    def date_sub(self, column: Any, days: int) -> dict[str, Any]:
        return self._adapter.date_sub(column, days)

    def date_add(self, column: Any, days: int) -> dict[str, Any]:
        return self._adapter.date_add(column, days)

    def cast_date(self, column: Any) -> dict[str, Any]:
        return self._adapter.cast_date(column)

    def cast_string(self, column: Any) -> dict[str, Any]:
        return self._adapter.cast_string(column)

    def window_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._adapter.window_rank(order_by, partition_by)

    def window_row_number(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._adapter.window_row_number(order_by, partition_by)

    def window_dense_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._adapter.window_dense_rank(order_by, partition_by)

    def window_sum(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> dict[str, Any]:
        return self._adapter.window_sum(column, partition_by, order_by)

    def window_avg(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> dict[str, Any]:
        return self._adapter.window_avg(column, partition_by, order_by)

    def window_count(
        self,
        column: str | None = None,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> dict[str, Any]:
        return self._adapter.window_count(column, partition_by, order_by)

    def window_min(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._adapter.window_min(column, partition_by)

    def window_max(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._adapter.window_max(column, partition_by)

    def union_all(self, *dataframes: Any) -> Any:
        return self._adapter.union_all(*dataframes)

    def concat(self, dataframes: list[Any]) -> Any:
        from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

        unwrapped = [df._df if isinstance(df, UnifiedPandasFrame) else df for df in dataframes]
        result = self._adapter.concat(unwrapped)
        return UnifiedPandasFrame(result, self._adapter)

    def rename_columns(self, df: Any, mapping: dict[str, str]) -> Any:
        return self._adapter.rename_columns(df, mapping)

    def groupby_size(
        self,
        df: Any,
        by: str | list[str],
        name: str = "size",
    ) -> Any:
        from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

        native_df = df._df if isinstance(df, UnifiedPandasFrame) else df

        result = self._adapter.groupby_size(native_df, by, name)
        return UnifiedPandasFrame(result, self._adapter)

    def groupby_agg(
        self,
        df: Any,
        by: str | list[str],
        agg_spec: dict[str, Any],
        as_index: bool = False,
        **kwargs: Any,
    ) -> Any:
        from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

        native_df = df._df if isinstance(df, UnifiedPandasFrame) else df

        result = self._adapter.groupby_agg(native_df, by, agg_spec, as_index=as_index, **kwargs)
        return UnifiedPandasFrame(result, self._adapter)

    def scalar(self, df: Any, column: str | None = None) -> Any:
        return self._adapter.scalar(df, column)

    def scalar_to_df(self, data: dict[str, Any]) -> Any:
        from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

        native_df = self._adapter.scalar_to_df(data)
        return UnifiedPandasFrame(native_df, self._adapter)

    def to_set(self, series_or_df: Any) -> set[Any]:
        from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

        native = series_or_df._df if isinstance(series_or_df, UnifiedPandasFrame) else series_or_df

        if hasattr(native, "ndim") and native.ndim == 2:
            col = native.columns[0]
            native = native[col]

        if hasattr(native, "compute"):
            native = native.compute()

        return set(native.unique())

    def filter_gt(self, df: Any, column: str, threshold: Any) -> Any:
        from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

        native_df = df._df if isinstance(df, UnifiedPandasFrame) else df

        if hasattr(native_df, "query"):
            result = native_df.query(f"`{column}` > @_threshold", local_dict={"_threshold": threshold})
        else:
            result = native_df[native_df[column] > threshold]

        return UnifiedPandasFrame(result, self._adapter)


class PandasFamilyAdapter(BenchmarkExecutionMixin, TuningConfigurableMixin, ABC, Generic[DF]):
    def __init__(
        self,
        working_dir: str | Path | None = None,
        verbose: bool = False,
        very_verbose: bool = False,
        tuning_config: DataFrameTuningConfiguration | None = None,
    ) -> None:
        self.working_dir = Path(working_dir) if working_dir else Path.cwd()
        self.verbose = verbose
        self.very_verbose = very_verbose
        self.table_mode = "native"
        self.platform_config: dict[str, Any] = {}
        self._context: PandasFamilyContext[DF] | None = None

        self._init_tuning(tuning_config)

    @property
    @abstractmethod
    def platform_name(self) -> str:
        pass

    @property
    def family(self) -> str:
        return "pandas"

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[Any]) -> None:
        return None

    @abstractmethod
    def read_csv(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        header: int | None = 0,
        names: list[str] | None = None,
        null_marker: str | None = None,
        column_types: list[str] | None = None,
    ) -> DF:
        pass

    @abstractmethod
    def read_parquet(self, path: Path) -> DF:
        pass

    @abstractmethod
    def to_datetime(self, series: Any) -> Any:
        pass

    @abstractmethod
    def timedelta_days(self, days: int) -> timedelta:
        pass

    @abstractmethod
    def concat(self, dfs: list[DF]) -> DF:
        pass

    def concat_shards(self, dfs: list[DF]) -> DF:
        return self.concat(dfs)

    @abstractmethod
    def get_row_count(self, df: DF) -> int:
        pass

    def date_sub(self, column: str, days: int) -> dict[str, Any]:
        return {"op": "date_sub", "column": column, "days": days}

    def date_add(self, column: str, days: int) -> dict[str, Any]:
        return {"op": "date_add", "column": column, "days": days}

    def cast_date(self, column: str) -> dict[str, Any]:
        return {"op": "cast_date", "column": column}

    def cast_string(self, column: str) -> dict[str, Any]:
        return {"op": "cast_string", "column": column}

    def window_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "op": "window_rank",
            "order_by": order_by,
            "partition_by": partition_by or [],
        }

    def window_row_number(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "op": "window_row_number",
            "order_by": order_by,
            "partition_by": partition_by or [],
        }

    def window_dense_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "op": "window_dense_rank",
            "order_by": order_by,
            "partition_by": partition_by or [],
        }

    def window_sum(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> dict[str, Any]:
        return {
            "op": "window_sum",
            "column": column,
            "partition_by": partition_by or [],
            "order_by": order_by,
        }

    def window_avg(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> dict[str, Any]:
        return {
            "op": "window_avg",
            "column": column,
            "partition_by": partition_by or [],
            "order_by": order_by,
        }

    def window_count(
        self,
        column: str | None = None,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> dict[str, Any]:
        return {
            "op": "window_count",
            "column": column,
            "partition_by": partition_by or [],
            "order_by": order_by,
        }

    def window_min(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "op": "window_min",
            "column": column,
            "partition_by": partition_by or [],
        }

    def window_max(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "op": "window_max",
            "column": column,
            "partition_by": partition_by or [],
        }

    def union_all(self, *dataframes: DF) -> DF:
        if len(dataframes) == 0:
            raise ValueError("At least one DataFrame required for union")
        if len(dataframes) == 1:
            return dataframes[0]
        return self.concat(list(dataframes))

    def rename_columns(self, df: DF, mapping: dict[str, str]) -> DF:
        return df.rename(columns=mapping)

    def merge(
        self,
        left: DF,
        right: DF,
        on: str | list[str] | None = None,
        left_on: str | list[str] | None = None,
        right_on: str | list[str] | None = None,
        how: str = "inner",
    ) -> DF:
        return self._merge_frames(
            left,
            right,
            on=on,
            left_on=left_on,
            right_on=right_on,
            how=how,
        )

    def _merge_frames(
        self,
        left: DF,
        right: DF,
        *,
        on: str | list[str] | None,
        left_on: str | list[str] | None,
        right_on: str | list[str] | None,
        how: str,
    ) -> DF:
        if not hasattr(left, "merge"):
            raise TypeError(f"{self.platform_name} merge requires DataFrame-like inputs, got {type(left).__name__}")
        return left.merge(
            right,
            on=on,
            left_on=left_on,
            right_on=right_on,
            how=how,
        )

    def scalar(self, df: DF, column: str | None = None) -> Any:
        if hasattr(df, "compute"):
            df = df.compute()

        row_count = len(df)
        if row_count == 0:
            raise ValueError("Cannot extract scalar from empty DataFrame")
        if row_count > 1:
            raise ValueError(f"Expected exactly one row, got {row_count}")

        if column is not None:
            return df[column].iloc[0]

        return df.iloc[0, 0]

    def scalar_to_df(self, data: dict[str, Any]) -> DF:
        return self._create_single_row_df(data)

    def _create_single_row_df(self, data: dict[str, Any]) -> DF:
        import pandas as pd

        return pd.DataFrame({k: [v] for k, v in data.items()})

    def groupby_size(
        self,
        df: DF,
        by: str | list[str],
        name: str = "size",
    ) -> DF:
        by_list = [by] if isinstance(by, str) else list(by)
        return df.groupby(by_list).size().reset_index(name=name)

    def groupby_agg(
        self,
        df: DF,
        by: str | list[str],
        agg_spec: dict[str, Any],
        as_index: bool = False,
        **kwargs: Any,
    ) -> DF:
        is_named_agg = any(isinstance(v, tuple) for v in agg_spec.values())

        if is_named_agg:
            return df.groupby(by, as_index=as_index, **kwargs).agg(**agg_spec)
        else:
            return df.groupby(by, as_index=as_index, **kwargs).agg(agg_spec)

    def create_context(self) -> PandasFamilyContext[DF]:
        self._context = PandasFamilyContext(self)
        return self._context

    def get_context(self) -> PandasFamilyContext[DF]:
        if self._context is None:
            return self.create_context()
        return self._context

    def load_table(
        self,
        ctx: PandasFamilyContext[DF],
        table_name: str,
        file_paths: list[Path],
        column_names: list[str] | None = None,
        delimiter: str | None = None,
        format_hint: str | None = None,
        *,
        data_source: Any | None = None,
        benchmark: Any | None = None,
    ) -> int:
        if not file_paths:
            raise ValueError(f"No files provided for table '{table_name}'")

        first_file = file_paths[0]
        if format_hint == "parquet":
            format_type = "parquet"
        elif format_hint in ("tbl", "csv"):
            format_type = format_hint
        elif format_hint:
            raise ValueError(f"Unknown format_hint '{format_hint}'; expected 'parquet', 'csv', or 'tbl'")
        else:
            format_type = self._detect_format(first_file)

        self._log_verbose(f"Loading table {table_name} from {len(file_paths)} file(s), format: {format_type}")

        if format_type == "parquet":
            df = self._load_parquet_files(file_paths)
        else:
            actual_delimiter = delimiter if delimiter is not None else ("|" if format_type == "tbl" else ",")

            null_marker, has_header = resolve_dataframe_csv_dialect(
                data_source=data_source,
                table_name=table_name,
                first_file=first_file,
                benchmark=benchmark,
                format_type=format_type,
                default_has_header=format_type == "csv",
            )

            column_types = _schema_column_types(benchmark, table_name, column_names)

            df = self._load_csv_files(
                file_paths,
                delimiter=actual_delimiter,
                has_header=has_header,
                column_names=column_names,
                null_marker=null_marker,
                column_types=column_types,
            )

        ctx.register_table(table_name, df)

        row_count = self.get_row_count(df)
        self._log_verbose(f"Loaded table {table_name}: {row_count:,} rows")

        return row_count

    def load_tables_from_data_source(
        self,
        ctx: PandasFamilyContext[DF],
        data_dir: Path,
        schema_info: dict[str, dict] | None = None,
    ) -> dict[str, int]:
        from benchbox.platforms.dataframe.shared_loading import load_tables_from_data_source_impl

        return load_tables_from_data_source_impl(self, ctx, data_dir, schema_info)

    def execute_query(
        self,
        ctx: PandasFamilyContext[DF],
        query: DataFrameQuery,
        query_id: str | None = None,
    ) -> dict[str, Any]:
        qid = query_id or query.query_id
        self._log_verbose(f"Executing query {qid}: {query.query_name}")

        start_time = mono_time()

        try:
            impl = query.get_impl_for_family("pandas")
            if impl is None:
                raise ValueError(f"Query '{qid}' has no pandas implementation")

            result_df = impl(ctx)

            if hasattr(result_df, "compute"):
                compute = getattr(self, "compute", None)
                result_df = compute(result_df) if callable(compute) else result_df.compute()

            row_count = self.get_row_count(result_df)

            first_row = self._get_first_row(result_df)

            execution_time = elapsed_seconds(start_time)

            self._log_verbose(f"Query {qid} completed in {execution_time:.3f}s, returned {row_count} rows")

            return build_success_result_dict(
                query_id=qid,
                execution_time_seconds=execution_time,
                row_count=row_count,
                first_row=first_row,
            )

        except Exception as e:
            execution_time = elapsed_seconds(start_time)
            error_msg = str(e)
            logger.error(f"Query {qid} failed: {error_msg}")

            return build_failure_result_dict(
                query_id=qid,
                execution_time_seconds=execution_time,
                error_message=error_msg,
            )

    def execute_query_profiled(
        self,
        ctx: PandasFamilyContext[DF],
        query: DataFrameQuery,
        query_id: str | None = None,
        *,
        track_memory: bool = True,
        memory_sample_interval_ms: int = 50,
    ) -> tuple[dict[str, Any], QueryExecutionProfile]:
        qid = query_id or query.query_id
        self._log_verbose(f"Executing query {qid} with profiling: {query.query_name}")

        profile_ctx = QueryProfileContext(qid, self.platform_name)
        profile_ctx._start_time = mono_time()

        memory_tracker: MemoryTracker | None = None
        if track_memory:
            memory_tracker = MemoryTracker(sample_interval_ms=memory_sample_interval_ms)
            memory_tracker.start()

        try:
            impl = query.get_impl_for_family("pandas")
            if impl is None:
                raise ValueError(f"Query '{qid}' has no pandas implementation")

            result_df = impl(ctx)

            if hasattr(result_df, "compute"):
                profile_ctx.start_collect()
                compute = getattr(self, "compute", None)
                result_df = compute(result_df) if callable(compute) else result_df.compute()
                profile_ctx.end_collect()

            row_count = self.get_row_count(result_df)
            profile_ctx.set_rows(row_count)

            first_row = self._get_first_row(result_df)

            if memory_tracker is not None:
                peak_memory = memory_tracker.stop()
                profile_ctx.set_peak_memory(peak_memory)

                stats = memory_tracker.get_statistics()
                profile_ctx.add_metric("memory_baseline_mb", stats["baseline_mb"])
                profile_ctx.add_metric("memory_delta_mb", stats["peak_delta_mb"])
                profile_ctx.add_metric("memory_samples", stats["sample_count"])

            profile = profile_ctx.get_profile()
            execution_time = profile.execution_time_ms / 1000.0

            self._log_verbose(
                f"Query {qid} completed in {execution_time:.3f}s, "
                f"collect={profile.collect_time_ms:.1f}ms, "
                f"rows={row_count}"
            )

            result_dict = build_success_result_dict(
                query_id=qid,
                execution_time_seconds=execution_time,
                row_count=row_count,
                first_row=first_row,
            )

            return result_dict, profile

        except Exception as e:
            if memory_tracker is not None:
                memory_tracker.stop()

            profile = profile_ctx.get_profile()
            execution_time = profile.execution_time_ms / 1000.0
            error_msg = str(e)
            logger.error(f"Query {qid} failed: {error_msg}")

            result_dict = build_failure_result_dict(
                query_id=qid,
                execution_time_seconds=execution_time,
                error_message=error_msg,
            )

            return result_dict, profile

    def _detect_format(self, path: Path) -> str:
        return detect_data_format(path)

    def _load_parquet_files(self, file_paths: list[Path]) -> DF:
        if len(file_paths) == 1:
            return self.read_parquet(file_paths[0])

        dfs = [self.read_parquet(f) for f in file_paths]
        return self.concat_shards(dfs)

    def _load_csv_files(
        self,
        file_paths: list[Path],
        delimiter: str,
        has_header: bool,
        column_names: list[str] | None,
        null_marker: str | None = None,
        column_types: list[str] | None = None,
    ) -> DF:
        header = 0 if has_header else None
        names = column_names

        if len(file_paths) == 1:
            return self.read_csv(
                file_paths[0],
                delimiter=delimiter,
                header=header,
                names=names,
                null_marker=null_marker,
                column_types=column_types,
            )

        dfs = [
            self.read_csv(
                f,
                delimiter=delimiter,
                header=header,
                names=names,
                null_marker=null_marker,
                column_types=column_types,
            )
            for f in file_paths
        ]
        return self.concat_shards(dfs)

    def _get_first_row(self, df: DF) -> tuple | None:
        return None

    def _log_verbose(self, message: str) -> None:
        if self.verbose:
            logger.info(message)

    def _log_very_verbose(self, message: str) -> None:
        if self.very_verbose:
            logger.debug(message)
