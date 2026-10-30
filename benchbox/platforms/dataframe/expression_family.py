# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from benchbox.core.dataframe.context import DataFrameContextImpl
from benchbox.core.dataframe.profiling import (
    MemoryTracker,
    QueryExecutionProfile,
    QueryProfileContext,
    capture_query_plan,
)
from benchbox.core.dataframe.query import DataFrameQuery
from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.core.errors import PlanCaptureError
from benchbox.platforms.dataframe._result_helpers import (
    build_failure_result_dict,
    build_success_result_dict,
)
from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin
from benchbox.platforms.dataframe.datafusion_identifiers import datafusion_column as df_col
from benchbox.platforms.dataframe.shared_loading import (
    declared_string_columns,
    declared_temporal_columns,
    resolve_dataframe_csv_dialect,
)
from benchbox.platforms.dataframe.tuning_mixin import TuningConfigurableMixin
from benchbox.platforms.dataframe.unified_frame import (
    UnifiedExpr,
    UnifiedLazyFrame,
    UnifiedWhen,
    _defer_datafusion_operation,
)
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.file_format import detect_data_format

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

DF = TypeVar("DF")
LazyDF = TypeVar("LazyDF")
Expr = TypeVar("Expr")


class ExpressionFamilyContext(DataFrameContextImpl[DF], Generic[DF, Expr]):
    def __init__(self, adapter: ExpressionFamilyAdapter[DF, LazyDF, Expr]) -> None:
        super().__init__(platform=adapter.platform_name, family="expression")
        self._adapter = adapter

    def get_table(self, name: str) -> UnifiedLazyFrame:
        native_df = super().get_table(name)
        return UnifiedLazyFrame(native_df, self._adapter)

    def col(self, name: str) -> UnifiedExpr:
        return UnifiedExpr(self._adapter.col(name))

    def lit(self, value: Any) -> UnifiedExpr:
        if isinstance(value, UnifiedExpr):
            return value
        is_string = isinstance(value, str)
        return UnifiedExpr(self._adapter.lit(value), _is_string_literal=is_string, _literal_value=value)

    def element(self) -> UnifiedExpr:
        return UnifiedExpr(self._adapter.element())

    def date_sub(self, column: Expr, days: int) -> Expr:
        return self._adapter.date_sub(column, days)

    def date_add(self, column: Expr, days: int) -> Expr:
        return self._adapter.date_add(column, days)

    def cast_date(self, column: Expr) -> Expr:
        return self._adapter.cast_date(column)

    def cast_string(self, column: Expr) -> Expr:
        return self._adapter.cast_string(column)

    def window_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        return self._adapter.window_rank(order_by, partition_by)

    def window_row_number(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        return self._adapter.window_row_number(order_by, partition_by)

    def window_dense_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        return self._adapter.window_dense_rank(order_by, partition_by)

    def window_sum(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Expr:
        return self._adapter.window_sum(column, partition_by, order_by)

    def window_avg(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Expr:
        return self._adapter.window_avg(column, partition_by, order_by)

    def window_count(
        self,
        column: str | None = None,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Expr:
        return self._adapter.window_count(column, partition_by, order_by)

    def window_min(self, column: str, partition_by: list[str] | None = None) -> Expr:
        return self._adapter.window_min(column, partition_by)

    def window_max(self, column: str, partition_by: list[str] | None = None) -> Expr:
        return self._adapter.window_max(column, partition_by)

    def window_lag(
        self,
        column: str,
        offset: int = 1,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Expr:
        return self._adapter.window_lag(column, offset, partition_by, order_by)

    def window_lead(
        self,
        column: str,
        offset: int = 1,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Expr:
        return self._adapter.window_lead(column, offset, partition_by, order_by)

    def window_ntile(
        self,
        n: int,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        return self._adapter.window_ntile(n, order_by, partition_by)

    def window_percent_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        return self._adapter.window_percent_rank(order_by, partition_by)

    def window_cume_dist(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        return self._adapter.window_cume_dist(order_by, partition_by)

    def union_all(self, *dataframes: LazyDF) -> LazyDF:
        return cast(LazyDF, self._adapter.union_all(*dataframes))

    def rename_columns(self, df: LazyDF, mapping: dict[str, str]) -> LazyDF:
        return cast(LazyDF, self._adapter.rename_columns(df, mapping))

    @_defer_datafusion_operation
    def when(self, condition: Any) -> UnifiedWhen:
        cond = condition._expr if isinstance(condition, UnifiedExpr) else condition

        platform = self._adapter.platform_name

        if platform in {"PySpark", "LakeSail"}:
            return UnifiedWhen(cond, platform="PySpark")

        if platform == "DataFusion":
            return UnifiedWhen(cond, platform="DataFusion")

        import polars as pl

        return UnifiedWhen(pl.when(cond), platform="Polars")

    def concat(self, dataframes: list[Any]) -> UnifiedLazyFrame:
        native_dfs = [df.native if isinstance(df, UnifiedLazyFrame) else df for df in dataframes]

        result = self._adapter.concat_dataframes(native_dfs)
        return UnifiedLazyFrame(result, self._adapter)

    @_defer_datafusion_operation
    def struct(self, *columns: Any) -> UnifiedExpr:
        platform = self._adapter.platform_name

        native_cols = []
        for c in columns:
            if isinstance(c, UnifiedExpr):
                native_cols.append(c._expr)
            elif isinstance(c, str):
                native_cols.append(self._adapter.col(c))
            else:
                native_cols.append(c)

        if platform == "PySpark":
            from pyspark.sql import functions as F

            return UnifiedExpr(F.struct(*native_cols))

        if platform == "DataFusion":
            from datafusion import functions as df_f

            name_pairs = []
            for c in native_cols:
                field_name = c.schema_name()
                if c.variant_name() == "Alias":
                    inner = c.rex_call_operands()[0]
                    name_pairs.append((field_name, inner))
                else:
                    name_pairs.append((field_name, c))
            return UnifiedExpr(df_f.named_struct(name_pairs))

        import polars as pl

        return UnifiedExpr(pl.struct(*native_cols))

    @_defer_datafusion_operation
    def map_from_entries(self, column: Any) -> UnifiedExpr:
        platform = self._adapter.platform_name

        if isinstance(column, UnifiedExpr):
            native_col = column._expr
        elif isinstance(column, str):
            native_col = self._adapter.col(column)
        else:
            native_col = column

        if platform == "PySpark":
            from pyspark.sql import functions as F

            return UnifiedExpr(F.map_from_entries(native_col))

        if platform == "DataFusion":
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.map_from_entries(native_col))

        raise NotImplementedError("map_from_entries not supported on Polars (no native Map dtype)")

    def scalar(self, df: LazyDF, column: str | None = None) -> Any:
        native_df = df.native if isinstance(df, UnifiedLazyFrame) else df
        return self._adapter.scalar(native_df, column)

    def scalar_to_df(self, data: dict[str, Any]) -> UnifiedLazyFrame:
        native_df = self._adapter.scalar_to_df(data)
        return UnifiedLazyFrame(native_df, self._adapter)

    def count(self, column: str | None = None) -> UnifiedExpr:
        platform = self._adapter.platform_name

        if platform == "PySpark":
            from pyspark.sql import functions as F

            if column:
                return UnifiedExpr(F.count(column))
            return UnifiedExpr(F.count(F.lit(1)))

        if platform == "DataFusion":
            from datafusion import functions as df_f, lit as df_lit

            if column:
                return UnifiedExpr(df_f.count(df_col(column)))
            return UnifiedExpr(df_f.count(df_lit(1)))

        import polars as pl

        if column:
            return UnifiedExpr(pl.count(column))
        return UnifiedExpr(pl.len())

    def len(self) -> UnifiedExpr:
        platform = self._adapter.platform_name

        if platform == "PySpark":
            from pyspark.sql import functions as F

            return UnifiedExpr(F.count(F.lit(1)))

        if platform == "DataFusion":
            from datafusion import functions as df_f, lit as df_lit

            return UnifiedExpr(df_f.count(df_lit(1)))

        import polars as pl

        return UnifiedExpr(pl.len())

    def sum(self, column: str) -> UnifiedExpr:
        platform = self._adapter.platform_name

        if platform == "PySpark":
            from pyspark.sql import functions as F

            return UnifiedExpr(F.sum(column))

        if platform == "DataFusion":
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.sum(df_col(column)))

        import polars as pl

        return UnifiedExpr(pl.sum(column))

    def mean(self, column: str) -> UnifiedExpr:
        platform = self._adapter.platform_name

        if platform == "PySpark":
            from pyspark.sql import functions as F

            return UnifiedExpr(F.avg(column))

        if platform == "DataFusion":
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.avg(df_col(column)))

        import polars as pl

        return UnifiedExpr(pl.mean(column))

    def std(self, column: str) -> UnifiedExpr:
        platform = self._adapter.platform_name

        if platform == "PySpark":
            from pyspark.sql import functions as F

            return UnifiedExpr(F.stddev(column))

        if platform == "DataFusion":
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.stddev(df_col(column)))

        import polars as pl

        return UnifiedExpr(pl.std(column))

    def max_(self, column: str) -> UnifiedExpr:
        platform = self._adapter.platform_name

        if platform == "PySpark":
            from pyspark.sql import functions as F

            return UnifiedExpr(F.max(column))

        if platform == "DataFusion":
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.max(df_col(column)))

        import polars as pl

        return UnifiedExpr(pl.max(column))

    @_defer_datafusion_operation
    def coalesce(self, *exprs: Any) -> UnifiedExpr:
        platform = self._adapter.platform_name

        unwrapped = []
        for e in exprs:
            if isinstance(e, UnifiedExpr):
                unwrapped.append(e._expr)
            elif isinstance(e, str):
                if platform == "PySpark":
                    from pyspark.sql import functions as F

                    unwrapped.append(F.col(e))
                elif platform == "DataFusion":
                    unwrapped.append(df_col(e))
                else:
                    import polars as pl

                    unwrapped.append(pl.col(e))
            else:
                unwrapped.append(e)

        if platform == "PySpark":
            from pyspark.sql import functions as F

            return UnifiedExpr(F.coalesce(*unwrapped))

        if platform == "DataFusion":
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.coalesce(*unwrapped))

        import polars as pl

        return UnifiedExpr(pl.coalesce(*unwrapped))

    def create_dataframe(self, data: dict[str, list]) -> UnifiedLazyFrame:
        platform = self._adapter.platform_name

        if platform == "PySpark":
            from pyspark.sql import Row

            spark = self._adapter._spark
            rows = []
            keys = list(data.keys())
            num_rows = len(data[keys[0]]) if keys else 0
            for i in range(num_rows):
                row_data = {k: data[k][i] for k in keys}
                rows.append(Row(**row_data))
            df = spark.createDataFrame(rows)
            return UnifiedLazyFrame(df, self._adapter)

        if platform == "DataFusion":
            import pyarrow as pa

            table = pa.table(data)

            import uuid

            temp_name = f"_temp_df_{uuid.uuid4().hex[:8]}"
            self._adapter.session_ctx.register_record_batches(temp_name, [table.to_batches()])
            df = self._adapter.session_ctx.table(temp_name)
            return UnifiedLazyFrame(df, self._adapter)

        import polars as pl

        return UnifiedLazyFrame(pl.DataFrame(data).lazy(), self._adapter)


class ExpressionFamilyAdapter(BenchmarkExecutionMixin, TuningConfigurableMixin, ABC, Generic[DF, LazyDF, Expr]):
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
        self._context: ExpressionFamilyContext[DF, Expr] | None = None

        self._init_tuning(tuning_config)

    @property
    @abstractmethod
    def platform_name(self) -> str:
        pass

    @property
    def family(self) -> str:
        return "expression"

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[Any]) -> None:
        return None

    @abstractmethod
    def col(self, name: str) -> Expr:
        pass

    @abstractmethod
    def lit(self, value: Any) -> Expr:
        pass

    def element(self) -> Expr:
        raise NotImplementedError(f"element() not supported on {self.platform_name}")

    @abstractmethod
    def date_sub(self, column: Expr, days: int) -> Expr:
        pass

    @abstractmethod
    def date_add(self, column: Expr, days: int) -> Expr:
        pass

    @abstractmethod
    def cast_date(self, column: Expr) -> Expr:
        pass

    @abstractmethod
    def cast_string(self, column: Expr) -> Expr:
        pass

    @abstractmethod
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
    ) -> LazyDF:
        pass

    @abstractmethod
    def read_parquet(self, path: Path) -> LazyDF:
        pass

    @abstractmethod
    def collect(self, df: LazyDF) -> DF:
        pass

    @abstractmethod
    def get_row_count(self, df: DF | LazyDF) -> int:
        pass

    @abstractmethod
    def scalar(self, df: DF | LazyDF, column: str | None = None) -> Any:
        pass

    @abstractmethod
    def scalar_to_df(self, data: dict[str, Any]) -> DF | LazyDF:
        pass

    @abstractmethod
    def window_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        pass

    @abstractmethod
    def window_row_number(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        pass

    @abstractmethod
    def window_dense_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        pass

    @abstractmethod
    def window_sum(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Expr:
        pass

    @abstractmethod
    def window_avg(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Expr:
        pass

    @abstractmethod
    def window_count(
        self,
        column: str | None = None,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Expr:
        pass

    @abstractmethod
    def window_min(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> Expr:
        pass

    @abstractmethod
    def window_max(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> Expr:
        pass

    def window_lag(
        self,
        column: str,
        offset: int = 1,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Expr:
        raise NotImplementedError(f"{type(self).__name__} does not implement window_lag")

    def window_lead(
        self,
        column: str,
        offset: int = 1,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Expr:
        raise NotImplementedError(f"{type(self).__name__} does not implement window_lead")

    def window_ntile(
        self,
        n: int,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        raise NotImplementedError(f"{type(self).__name__} does not implement window_ntile")

    def window_percent_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        raise NotImplementedError(f"{type(self).__name__} does not implement window_percent_rank")

    def window_cume_dist(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Expr:
        raise NotImplementedError(f"{type(self).__name__} does not implement window_cume_dist")

    @abstractmethod
    def union_all(self, *dataframes: LazyDF) -> LazyDF:
        pass

    @abstractmethod
    def rename_columns(self, df: LazyDF, mapping: dict[str, str]) -> LazyDF:
        pass

    def create_context(self) -> ExpressionFamilyContext[DF, Expr]:
        self._context = ExpressionFamilyContext(self)
        return self._context

    def get_context(self) -> ExpressionFamilyContext[DF, Expr]:
        if self._context is None:
            return self.create_context()
        return self._context

    def load_table(
        self,
        ctx: ExpressionFamilyContext[DF, Expr],
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
            effective_delimiter = delimiter or ("|" if format_type == "tbl" else ",")
            has_header = format_type == "csv" and delimiter is None

            null_marker, has_header = resolve_dataframe_csv_dialect(
                data_source=data_source,
                table_name=table_name,
                first_file=first_file,
                benchmark=benchmark,
                format_type=format_type,
                default_has_header=has_header,
            )
            string_columns = declared_string_columns(benchmark, table_name, column_names)
            temporal_columns = declared_temporal_columns(benchmark, table_name, column_names)

            df = self._load_csv_files(
                file_paths,
                delimiter=effective_delimiter,
                has_header=has_header,
                column_names=column_names,
                null_marker=null_marker,
                string_columns=string_columns,
                temporal_columns=temporal_columns,
            )

        ctx.register_table(table_name, df)

        row_count = self.get_row_count(df)
        self._log_verbose(f"Loaded table {table_name}: {row_count:,} rows")

        return row_count

    def load_tables_from_data_source(
        self,
        ctx: ExpressionFamilyContext[DF, Expr],
        data_dir: Path,
        schema_info: dict[str, dict] | None = None,
    ) -> dict[str, int]:
        from benchbox.platforms.dataframe.shared_loading import load_tables_from_data_source_impl

        return load_tables_from_data_source_impl(self, ctx, data_dir, schema_info)

    def execute_query(
        self,
        ctx: ExpressionFamilyContext[DF, Expr],
        query: DataFrameQuery,
        query_id: str | None = None,
    ) -> dict[str, Any]:
        qid = query_id or query.query_id
        self._log_verbose(f"Executing query {qid}: {query.query_name}")

        start_time = mono_time()

        try:
            impl = query.get_impl_for_family("expression")
            if impl is None:
                raise ValueError(f"Query '{qid}' has no expression implementation")

            result_df = impl(ctx)

            if isinstance(result_df, UnifiedLazyFrame):
                result_df = result_df.native

            if hasattr(result_df, "collect"):
                result_df = self.collect(result_df)

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
        ctx: ExpressionFamilyContext[DF, Expr],
        query: DataFrameQuery,
        query_id: str | None = None,
        *,
        track_memory: bool = True,
        capture_plan: bool = True,
        memory_sample_interval_ms: int = 50,
    ) -> tuple[dict[str, Any], QueryExecutionProfile]:
        qid = query_id or query.query_id
        self._log_verbose(f"Executing query {qid} with profiling: {query.query_name}")

        profile_ctx = QueryProfileContext(qid, self.platform_name)
        profile_ctx._start_time = mono_time()

        plan_capture_error: str | None = None

        memory_tracker: MemoryTracker | None = None
        if track_memory:
            memory_tracker = MemoryTracker(sample_interval_ms=memory_sample_interval_ms)
            memory_tracker.start()

        try:
            impl = query.get_impl_for_family("expression")
            if impl is None:
                raise ValueError(f"Query '{qid}' has no expression implementation")

            profile_ctx.start_planning()
            lazy_result = impl(ctx)
            profile_ctx.end_planning()

            if isinstance(lazy_result, UnifiedLazyFrame):
                lazy_result = lazy_result.native

            profile_ctx.start_collect()
            result_df = self.collect(lazy_result) if hasattr(lazy_result, "collect") else lazy_result
            profile_ctx.end_collect()

            if capture_plan:
                profile_ctx.start_plan_capture()
                try:
                    plan = capture_query_plan(lazy_result, self.platform_name)
                    if plan:
                        profile_ctx.set_query_plan(plan)
                except Exception as e:
                    plan_capture_error = str(e)
                    self._record_dataframe_plan_capture_failure(qid, plan_capture_error)
                    if getattr(self, "strict_plan_capture", False):
                        raise PlanCaptureError(
                            reason="capture_error",
                            platform=self.platform_name,
                            query_id=str(qid),
                            details=plan_capture_error,
                        ) from e
                finally:
                    profile_ctx.end_plan_capture()

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
                f"planning={profile.planning_time_ms:.1f}ms, "
                f"collect={profile.collect_time_ms:.1f}ms, "
                f"rows={row_count}"
            )

            result_dict = build_success_result_dict(
                query_id=qid,
                execution_time_seconds=execution_time,
                row_count=row_count,
                first_row=first_row,
            )
            if plan_capture_error is not None:
                result_dict["plan_capture_error"] = plan_capture_error

            return result_dict, profile

        except PlanCaptureError:
            if memory_tracker is not None:
                memory_tracker.stop()
            raise
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

    def _load_parquet_files(self, file_paths: list[Path]) -> LazyDF:
        if len(file_paths) == 1:
            return self.read_parquet(file_paths[0])

        dfs = [self.read_parquet(f) for f in file_paths]
        return self._concat_dataframes(dfs)

    def _load_csv_files(
        self,
        file_paths: list[Path],
        delimiter: str,
        has_header: bool,
        column_names: list[str] | None,
        null_marker: str | None = None,
        string_columns: list[str] | None = None,
        temporal_columns: dict[str, str] | None = None,
    ) -> LazyDF:
        read_kwargs: dict[str, Any] = {
            "delimiter": delimiter,
            "has_header": has_header,
            "column_names": column_names,
            "null_marker": null_marker,
        }
        if string_columns:
            read_kwargs["string_columns"] = string_columns
        if temporal_columns:
            read_kwargs["temporal_columns"] = temporal_columns

        if len(file_paths) == 1:
            return self.read_csv(file_paths[0], **read_kwargs)

        dfs = [self.read_csv(f, **read_kwargs) for f in file_paths]
        return self._concat_dataframes(dfs)

    def concat_dataframes(self, dfs: list[LazyDF]) -> LazyDF:
        return self._concat_dataframes(dfs)

    def _concat_dataframes(self, dfs: list[LazyDF]) -> LazyDF:
        if len(dfs) == 1:
            return dfs[0]

        logger.warning("DataFrame concatenation not implemented, returning first")
        return dfs[0]

    def _get_first_row(self, df: DF) -> tuple | None:
        return None

    def _log_verbose(self, message: str) -> None:
        if self.verbose:
            logger.info(message)

    def _log_very_verbose(self, message: str) -> None:
        if self.very_verbose:
            logger.debug(message)
