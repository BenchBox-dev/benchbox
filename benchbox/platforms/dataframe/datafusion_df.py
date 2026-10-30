# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import datafusion
    from datafusion import SessionContext, functions as f, lit
    from datafusion.expr import Window

    try:
        from datafusion import SessionConfig
    except ImportError:
        SessionConfig = None

    import pyarrow as pa

    DATAFUSION_DF_AVAILABLE = True
except ImportError:
    datafusion = None
    SessionContext = None
    SessionConfig = None
    Window = None
    lit = None
    f = None
    pa = None
    DATAFUSION_DF_AVAILABLE = False

from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.platforms.base.adapter import DriverIsolationCapability
from benchbox.platforms.dataframe.datafusion_identifiers import datafusion_column as col, datafusion_identifier
from benchbox.platforms.dataframe.expression_family import (
    ExpressionFamilyAdapter,
)
from benchbox.platforms.dataframe.shared_loading import (
    dialect_preserves_empty_strings,
    resolve_empty_string_restore_columns,
)
from benchbox.utils.file_format import (
    TRAILING_DUMMY_COLUMN,
    detect_data_format,
    has_trailing_delimiter,
    strip_compression_suffix,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

if DATAFUSION_DF_AVAILABLE:
    DataFusionDF = pa.Table
    DataFusionLazyDF = "DFDataFrame"
    DataFusionExpr = "DFExpr"
else:
    DataFusionDF = Any
    DataFusionLazyDF = Any
    DataFusionExpr = Any


class DataFusionDataFrameAdapter(ExpressionFamilyAdapter[DataFusionDF, DataFusionLazyDF, DataFusionExpr]):
    driver_isolation_capability = DriverIsolationCapability.SUPPORTED

    def __init__(
        self,
        working_dir: str | Path | None = None,
        verbose: bool = False,
        very_verbose: bool = False,
        tuning_config: DataFrameTuningConfiguration | None = None,
        target_partitions: int | None = None,
        repartition_joins: bool = True,
        parquet_pushdown: bool = True,
        batch_size: int = 8192,
        memory_limit: str | None = None,
        temp_dir: str | Path | None = None,
    ) -> None:
        if not DATAFUSION_DF_AVAILABLE:
            raise ImportError("DataFusion not installed. Install with: pip install datafusion pyarrow")

        super().__init__(
            working_dir=working_dir,
            verbose=verbose,
            very_verbose=very_verbose,
            tuning_config=tuning_config,
        )

        self._target_partitions = target_partitions or os.cpu_count() or 4
        self._repartition_joins = repartition_joins
        self._parquet_pushdown = parquet_pushdown
        self._batch_size = batch_size
        self._memory_limit = memory_limit
        self._temp_dir = str(temp_dir) if temp_dir else None

        self._session_ctx: SessionContext | None = None

        self._validate_and_apply_tuning()

    def _apply_tuning(self) -> None:
        config = self._tuning_config

        if config.parallelism.thread_count is not None:
            self._target_partitions = config.parallelism.thread_count
            self._log_verbose(f"Set target_partitions={self._target_partitions}")

        if config.execution.streaming_mode:
            self._log_verbose("Note: DataFusion streaming mode is per-query, not global")

        if config.memory.chunk_size is not None:
            self._batch_size = config.memory.chunk_size
            self._log_verbose(f"Set batch_size={self._batch_size}")

    @property
    def platform_name(self) -> str:
        return "DataFusion"

    @property
    def session_ctx(self) -> SessionContext:
        if self._session_ctx is None:
            self._session_ctx = self._create_session_context()
        return self._session_ctx

    def _create_session_context(self) -> SessionContext:
        runtime = self._configure_runtime_environment()

        configured_context = False
        if SessionConfig is not None:
            try:
                config = SessionConfig()

                config = config.with_target_partitions(self._target_partitions)

                if self._repartition_joins:
                    config = config.with_repartition_joins(True)

                if self._parquet_pushdown:
                    config = config.with_parquet_pruning(True)

                try:
                    config = config.with_repartition_aggregations(True)
                    config = config.with_repartition_windows(True)
                except AttributeError:
                    pass

                config = config.with_batch_size(self._batch_size)

                if runtime is not None:
                    try:
                        ctx = SessionContext(config, runtime)
                    except TypeError:
                        ctx = SessionContext(config)
                else:
                    ctx = SessionContext(config)
                configured_context = True

            except Exception as e:
                self._log_verbose(f"SessionConfig failed, using defaults: {e}")
                ctx = SessionContext()
        else:
            ctx = SessionContext()

        if configured_context:
            config = self._tuning_config
            if config.parallelism.thread_count is not None:
                self._record_runtime_tuning(f"target_partitions={self._target_partitions}")
            if config.memory.chunk_size is not None:
                self._record_runtime_tuning(f"batch_size={self._batch_size}")

        config_parts = [
            f"partitions={self._target_partitions}",
            f"batch_size={self._batch_size}",
        ]
        if self._memory_limit:
            config_parts.append(f"memory={self._memory_limit}")
        if self._repartition_joins:
            config_parts.append("repartition_joins=on")
        if self._parquet_pushdown:
            config_parts.append("parquet_pushdown=on")

        self._log_verbose(f"DataFusion context created: {', '.join(config_parts)}")

        return ctx

    def _configure_runtime_environment(self) -> Any:
        try:
            try:
                from datafusion import RuntimeEnvBuilder

                has_builder = True
            except ImportError:
                try:
                    from datafusion import RuntimeEnv as RuntimeEnvBuilder

                    has_builder = hasattr(RuntimeEnvBuilder, "build")
                except ImportError:
                    return None
        except Exception:
            return None

        if not has_builder:
            return None

        try:
            builder = RuntimeEnvBuilder()

            if self._memory_limit:
                memory_bytes = self._parse_memory_limit(self._memory_limit)
                builder = builder.with_fair_spill_pool(memory_bytes)
                self._log_verbose(f"Configured fair spill pool: {self._memory_limit} ({memory_bytes:,} bytes)")

            builder = builder.with_disk_manager_os()
            if self._temp_dir:
                self._log_verbose(f"Enabled disk spilling (temp dir: {self._temp_dir})")
            else:
                self._log_verbose("Enabled disk spilling (using system temp dir)")

            return builder.build()

        except Exception as e:
            self._log_verbose(f"Could not configure RuntimeEnv: {e}, using defaults")
            return None

    def _parse_memory_limit(self, memory_limit: str) -> int:
        memory_str = memory_limit.upper().strip()

        if memory_str.endswith("B"):
            memory_str = memory_str[:-1]

        if memory_str.endswith("G"):
            return int(float(memory_str[:-1]) * 1024 * 1024 * 1024)
        elif memory_str.endswith("M"):
            return int(float(memory_str[:-1]) * 1024 * 1024)
        elif memory_str.endswith("K"):
            return int(float(memory_str[:-1]) * 1024)
        else:
            return int(memory_str)

    def col(self, name: str) -> DataFusionExpr:
        return col(name)

    def lit(self, value: Any) -> DataFusionExpr:
        if type(value).__name__ == "Expr" and "datafusion" in type(value).__module__:
            return value
        return lit(value)

    def date_sub(self, column: DataFusionExpr, days: int) -> DataFusionExpr:
        interval = pa.scalar((0, days, 0), type=pa.month_day_nano_interval())
        return column - lit(interval)

    def date_add(self, column: DataFusionExpr, days: int) -> DataFusionExpr:
        interval = pa.scalar((0, days, 0), type=pa.month_day_nano_interval())
        return column + lit(interval)

    def cast_date(self, column: DataFusionExpr) -> DataFusionExpr:
        return column.cast(pa.date32())

    def cast_string(self, column: DataFusionExpr) -> DataFusionExpr:
        return column.cast(pa.utf8())

    def sum(self, column: str) -> DataFusionExpr:
        return f.sum(col(column))

    def mean(self, column: str) -> DataFusionExpr:
        return f.avg(col(column))

    def count(self, column: str | None = None) -> DataFusionExpr:
        if column:
            return f.count(col(column))
        return f.count(lit(1))

    def min(self, column: str) -> DataFusionExpr:
        return f.min(col(column))

    def max(self, column: str) -> DataFusionExpr:
        return f.max(col(column))

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
    ) -> DataFusionLazyDF:
        path = Path(path)
        path_str = str(path)
        format_type = detect_data_format(path)

        if format_type == "tbl":
            if has_trailing_delimiter(path, delimiter, column_names):
                return self._read_tbl_via_pyarrow(
                    path, delimiter=delimiter, has_header=has_header, column_names=column_names
                )
            df = self._read_tbl_via_datafusion(
                path,
                delimiter=delimiter,
                has_header=has_header,
                column_names=column_names,
            )
            if df is not None:
                return df
            return self._read_tbl_via_pyarrow(
                path, delimiter=delimiter, has_header=has_header, column_names=column_names
            )

        try:
            df = self.session_ctx.read_csv(
                path_str,
                has_header=has_header,
                delimiter=delimiter,
            )
        except TypeError:
            df = self.session_ctx.read_csv(path_str)

        if column_names:
            df = self._apply_tbl_column_names(df, column_names)

        if dialect_preserves_empty_strings(null_marker):
            present_columns = (field.name for field in df.schema())
            for name in resolve_empty_string_restore_columns(string_columns, null_marker, present_columns):
                df = df.with_column(name, f.coalesce(col(name), lit("")))

        return df

    def _read_tbl_via_datafusion(
        self,
        path: Path,
        *,
        delimiter: str,
        has_header: bool,
        column_names: list[str] | None,
    ) -> DataFusionLazyDF | None:
        path_str = str(path)
        extension = strip_compression_suffix(path).suffix

        try:
            df = self.session_ctx.read_csv(
                path_str,
                has_header=has_header,
                delimiter=delimiter,
                file_extension=extension,
            )
            if column_names:
                df = self._apply_tbl_column_names(df, column_names)
            return df
        except TypeError:
            return None
        except Exception as err:
            err_msg = str(err)
            if "expected extension" in err_msg or "extension" in err_msg:
                return None
            raise

    def _read_tbl_via_pyarrow(
        self,
        path: Path,
        *,
        delimiter: str,
        has_header: bool = False,
        column_names: list[str] | None,
    ) -> DataFusionLazyDF:
        import uuid

        import pyarrow.csv as pv

        from benchbox.utils.compression import CompressionError, CompressionManager
        from benchbox.utils.file_format import detect_compression

        has_trailing = False
        if column_names:
            has_trailing = has_trailing_delimiter(path, delimiter, column_names)

        actual_column_names = column_names
        if column_names and has_trailing:
            actual_column_names = column_names + [TRAILING_DUMMY_COLUMN]

        read_options = pv.ReadOptions(
            column_names=actual_column_names if actual_column_names else None,
            skip_rows=1 if (column_names and has_header) else 0,
        )
        parse_options = pv.ParseOptions(delimiter=delimiter)
        compression = detect_compression(path)
        if compression:
            manager = CompressionManager()
            try:
                compressor = manager.get_compressor(compression)
                with compressor.open_for_read(path, mode="rb") as stream:
                    table = pv.read_csv(stream, read_options=read_options, parse_options=parse_options)
            except CompressionError as err:
                raise RuntimeError(f"Failed to read compressed data file {path}: {err}") from err
        else:
            table = pv.read_csv(path, read_options=read_options, parse_options=parse_options)

        if column_names and has_trailing:
            table = table.select(column_names)

        temp_name = f"_tbl_{uuid.uuid4().hex[:8]}"
        self.session_ctx.register_record_batches(temp_name, [table.to_batches()])
        return self.session_ctx.table(temp_name)

    def _apply_tbl_column_names(self, df: DataFusionLazyDF, column_names: list[str]) -> DataFusionLazyDF:
        current_cols = [field.name for field in df.schema()]

        if len(current_cols) > len(column_names):
            df = df.select(*[col(c) for c in current_cols[: len(column_names)]])
            current_cols = current_cols[: len(column_names)]

        if current_cols != column_names:
            mapping = dict(zip(current_cols, column_names))
            for old_name, new_name in mapping.items():
                if old_name != new_name:
                    df = df.with_column_renamed(datafusion_identifier(old_name), new_name)

        return df

    def read_parquet(self, path: Path) -> DataFusionLazyDF:
        return self.session_ctx.read_parquet(str(path))

    def collect(self, df: DataFusionLazyDF) -> DataFusionDF:
        batches = df.collect()
        if not batches:
            return pa.table({})

        return pa.Table.from_batches(batches)

    def get_row_count(self, df: DataFusionLazyDF | DataFusionDF) -> int:
        if isinstance(df, pa.Table):
            return df.num_rows

        return df.count()

    def scalar(self, df: DataFusionLazyDF | DataFusionDF, column: str | None = None) -> Any:
        if not isinstance(df, pa.Table):
            batches = df.collect()
            if not batches:
                raise ValueError("Cannot extract scalar from empty DataFrame")
            table = pa.Table.from_batches(batches)
        else:
            table = df

        if table.num_rows == 0:
            raise ValueError("Cannot extract scalar from empty DataFrame")
        if table.num_rows > 1:
            raise ValueError(f"Expected exactly one row, got {table.num_rows}")

        col_data = table.column(column) if column is not None else table.column(0)

        return col_data[0].as_py()

    def scalar_to_df(self, data: dict[str, Any]) -> DataFusionLazyDF:
        table = pa.table({k: [v] for k, v in data.items()})
        return self.session_ctx.from_arrow(table)

    def _datafusion_window_rank(
        self,
        rank_method: str,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> DataFusionExpr:
        order_exprs = self._build_order_exprs(order_by)
        partition_exprs = [col(c) for c in (partition_by or [])]
        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
            order_by=order_exprs if order_exprs else None,
        )
        return getattr(f, rank_method)().over(window)

    def window_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> DataFusionExpr:
        return self._datafusion_window_rank("rank", order_by, partition_by)

    def window_row_number(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> DataFusionExpr:
        return self._datafusion_window_rank("row_number", order_by, partition_by)

    def window_dense_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> DataFusionExpr:
        return self._datafusion_window_rank("dense_rank", order_by, partition_by)

    def _build_order_exprs(
        self,
        order_by: list[tuple[str, bool]],
    ) -> list[DataFusionExpr]:
        order_exprs = []
        for col_name, ascending in order_by:
            expr = col(col_name)
            expr = expr.sort(ascending=ascending)
            order_exprs.append(expr)
        return order_exprs

    def window_sum(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> DataFusionExpr:
        partition_exprs = [col(c) for c in (partition_by or [])]
        order_exprs = self._build_order_exprs(order_by) if order_by else None

        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
            order_by=order_exprs,
        )
        return f.sum(col(column)).over(window)

    def window_avg(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> DataFusionExpr:
        partition_exprs = [col(c) for c in (partition_by or [])]
        order_exprs = self._build_order_exprs(order_by) if order_by else None

        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
            order_by=order_exprs,
        )
        return f.avg(col(column)).over(window)

    def window_count(
        self,
        column: str | None = None,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> DataFusionExpr:
        partition_exprs = [col(c) for c in (partition_by or [])]
        order_exprs = self._build_order_exprs(order_by) if order_by else None

        count_expr = f.count(col(column)) if column else f.count(lit(1))

        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
            order_by=order_exprs,
        )
        return count_expr.over(window)

    def window_min(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> DataFusionExpr:
        partition_exprs = [col(c) for c in (partition_by or [])]

        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
        )
        return f.min(col(column)).over(window)

    def window_max(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> DataFusionExpr:
        partition_exprs = [col(c) for c in (partition_by or [])]

        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
        )
        return f.max(col(column)).over(window)

    def window_lag(
        self,
        column: str,
        offset: int = 1,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> DataFusionExpr:
        partition_exprs = [col(c) for c in (partition_by or [])]
        order_exprs = self._build_order_exprs(order_by) if order_by else None

        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
            order_by=order_exprs,
        )
        return f.lag(col(column), offset).over(window)

    def window_lead(
        self,
        column: str,
        offset: int = 1,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> DataFusionExpr:
        partition_exprs = [col(c) for c in (partition_by or [])]
        order_exprs = self._build_order_exprs(order_by) if order_by else None

        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
            order_by=order_exprs,
        )
        return f.lead(col(column), offset).over(window)

    def window_ntile(
        self,
        n: int,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> DataFusionExpr:
        partition_exprs = [col(c) for c in (partition_by or [])]
        order_exprs = self._build_order_exprs(order_by)

        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
            order_by=order_exprs if order_exprs else None,
        )
        return f.ntile(n).over(window)

    def window_percent_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> DataFusionExpr:
        partition_exprs = [col(c) for c in (partition_by or [])]
        order_exprs = self._build_order_exprs(order_by)

        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
            order_by=order_exprs if order_exprs else None,
        )
        return f.percent_rank().over(window)

    def window_cume_dist(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> DataFusionExpr:
        partition_exprs = [col(c) for c in (partition_by or [])]
        order_exprs = self._build_order_exprs(order_by)

        window = Window(
            partition_by=partition_exprs if partition_exprs else None,
            order_by=order_exprs if order_exprs else None,
        )
        return f.cume_dist().over(window)

    def union_all(self, *dataframes: DataFusionLazyDF) -> DataFusionLazyDF:
        if len(dataframes) == 0:
            raise ValueError("At least one DataFrame required for union")
        if len(dataframes) == 1:
            return dataframes[0]

        result = dataframes[0]
        for df in dataframes[1:]:
            result = result.union(df)
        return result

    def rename_columns(self, df: DataFusionLazyDF, mapping: dict[str, str]) -> DataFusionLazyDF:
        result = df
        schema_names = [field.name for field in result.schema()]
        for old_name, new_name in mapping.items():
            if old_name in schema_names:
                result = result.with_column_renamed(datafusion_identifier(old_name), new_name)
        return result

    def _concat_dataframes(self, dfs: list[DataFusionLazyDF]) -> DataFusionLazyDF:
        if len(dfs) == 1:
            return dfs[0]
        return self.union_all(*dfs)

    def _get_first_row(self, df: DataFusionDF) -> tuple | None:
        if isinstance(df, pa.Table):
            if df.num_rows == 0:
                return None
            row_dict = {col: df.column(col)[0].as_py() for col in df.column_names}
            return tuple(row_dict.values())

        table = self.collect(df)
        return self._get_first_row(table)

    def get_platform_info(self) -> dict[str, Any]:
        info = {
            "platform": self.platform_name,
            "family": self.family,
            "target_partitions": self._target_partitions,
            "batch_size": self._batch_size,
            "repartition_joins": self._repartition_joins,
            "parquet_pushdown": self._parquet_pushdown,
            "memory_limit": self._memory_limit,
            "temp_dir": self._temp_dir,
            "working_dir": str(self.working_dir),
        }

        if DATAFUSION_DF_AVAILABLE:
            live_version = datafusion.__version__
            info["version"] = live_version
            self.driver_version_actual = live_version

        self._enrich_with_driver_metadata(info)
        return info

    def get_tuning_summary(self) -> dict[str, Any]:
        base_summary = super().get_tuning_summary()
        base_summary.update(
            {
                "target_partitions": self._target_partitions,
                "batch_size": self._batch_size,
                "repartition_joins": self._repartition_joins,
                "parquet_pushdown": self._parquet_pushdown,
                "memory_limit": self._memory_limit,
                "temp_dir": self._temp_dir,
                "datafusion_version": datafusion.__version__ if DATAFUSION_DF_AVAILABLE else None,
            }
        )
        return base_summary

    def explain(self, df: DataFusionLazyDF, analyze: bool = False) -> str:
        if analyze:
            return df.explain(analyze=True)
        return df.explain()

    def get_logical_plan(self, df: DataFusionLazyDF) -> str:
        return str(df.logical_plan())

    def get_query_plan(self, df: DataFusionLazyDF) -> dict[str, str]:
        return {
            "logical": self.get_logical_plan(df),
            "physical": self.explain(df),
        }

    def sql(self, query: str) -> DataFusionLazyDF:
        return self.session_ctx.sql(query)

    def register_table(self, name: str, df: DataFusionLazyDF | DataFusionDF) -> None:
        table = df if isinstance(df, pa.Table) else self.collect(df)
        self.session_ctx.register_record_batches(name, [table.to_batches()])

    def register_parquet_table(self, name: str, path: Path) -> None:
        self.session_ctx.register_parquet(name, str(path))

    def to_pandas(self, df: DataFusionLazyDF | DataFusionDF) -> Any:
        if isinstance(df, pa.Table):
            return df.to_pandas()
        table = self.collect(df)
        return table.to_pandas()

    def to_polars(self, df: DataFusionLazyDF | DataFusionDF) -> Any:
        try:
            import polars as pl

            if isinstance(df, pa.Table):
                return pl.from_arrow(df)
            table = self.collect(df)
            return pl.from_arrow(table)
        except ImportError as err:
            raise ImportError("Polars not installed. Install with: pip install polars") from err
