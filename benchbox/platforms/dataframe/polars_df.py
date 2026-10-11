# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    import polars as pl

    POLARS_AVAILABLE = True
except ImportError:
    pl = None
    POLARS_AVAILABLE = False

from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.core.execution_engine import ExecutionEngineReceipt, ExecutionEngineUnavailableError
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE
from benchbox.platforms.dataframe.expression_family import (
    ExpressionFamilyAdapter,
)
from benchbox.platforms.dataframe.shared_loading import dialect_preserves_empty_strings
from benchbox.platforms.polars_compat import (
    ENGINE_AFFINITY_KEY,
    IN_MEMORY_ENGINE,
    active_runtime,
    collect_accepts_engine,
    collect_affinity,
    collect_engine_supported,
    csv_empty_string_option,
    default_collect_engine,
    reader_rechunk_effective,
    reader_rechunk_option,
)
from benchbox.utils.polars_collection import collect_engine_option, collect_frame

logger = logging.getLogger(__name__)

if POLARS_AVAILABLE:
    PolarsDF = pl.DataFrame
    PolarsLazyDF = pl.LazyFrame
    PolarsExpr = pl.Expr
else:
    PolarsDF = Any
    PolarsLazyDF = Any
    PolarsExpr = Any


def _series_to_date(series: PolarsDF) -> PolarsDF:
    if series.dtype == pl.String:
        return series.str.to_date()
    return series.cast(pl.Date)


def polars_cast_date(column: PolarsExpr) -> PolarsExpr:
    return column.map_batches(_series_to_date, return_dtype=pl.Date, is_elementwise=True)


class PolarsDataFrameAdapter(ExpressionFamilyAdapter[PolarsDF, PolarsLazyDF, PolarsExpr]):
    def __init__(
        self,
        working_dir: str | Path | None = None,
        verbose: bool = False,
        very_verbose: bool = False,
        streaming: bool = False,
        rechunk: bool = True,
        n_rows: int | None = None,
        tuning_config: DataFrameTuningConfiguration | None = None,
        execution_engine: str = "default",
    ) -> None:
        if not POLARS_AVAILABLE:
            raise ImportError("Polars not installed. Install with: pip install polars")

        super().__init__(
            working_dir=working_dir,
            verbose=verbose,
            very_verbose=very_verbose,
            tuning_config=tuning_config,
        )

        self.streaming = streaming
        self.rechunk = rechunk
        self.n_rows = n_rows
        self.engine = execution_engine

        pl.enable_string_cache()

        self._engine_receipt = self.resolve_execution_engine(execution_engine)

        self._validate_and_apply_tuning()

    def _tuning_engine(self) -> str | None:
        from benchbox.core.execution_engine import resolve_requested

        affinity = self._tuning_config.execution.engine_affinity
        if affinity is not None:
            resolved = resolve_requested("polars", affinity)
            if self._tuning_config.execution.streaming_mode and resolved != "streaming":
                raise ValueError(
                    f"Conflicting tuning execution engines: engine_affinity={affinity!r} and streaming_mode=True"
                )
            return resolved
        if self._tuning_config.execution.streaming_mode:
            return "streaming"
        return None

    def resolve_execution_engine(self, requested: str) -> ExecutionEngineReceipt:
        import warnings

        from benchbox.core.execution_engine import (
            ExecutionEngineResolution,
            resolve_requested,
            supported_execution_engines,
        )

        resolved = resolve_requested("polars", requested)
        explicit = None if resolved == DEFAULT_EXECUTION_ENGINE else resolved
        tuning = self._tuning_engine()
        legacy = "streaming" if self.streaming else None
        ordered: tuple[tuple[str, str | None, ExecutionEngineResolution], ...] = (
            ("explicit setting", explicit, "explicit"),
            ("tuning file", tuning, "tuning_profile"),
            ("legacy option", legacy, "legacy_option"),
        )
        present = [(source, value, resolution) for source, value, resolution in ordered if value is not None]
        if len({value for _, value, _ in present}) > 1:
            raise ValueError(
                "Conflicting execution engine sources: "
                + " vs ".join(f"{source} requests {value!r}" for source, value, _ in present)
            )
        affinity = collect_affinity()
        if tuning is not None:
            logger.warning(
                "Tuning [%s]: tuning file selects execution engine %r; runs with different engines may not be comparable",
                self.platform_name,
                tuning,
            )
        if affinity is not None:
            logger.warning(
                "Tuning [%s]: %s is set; runs with different engines may not be comparable",
                self.platform_name,
                ENGINE_AFFINITY_KEY,
            )
        if legacy is not None:
            warnings.warn(
                "The Polars streaming option is deprecated; use execution_engine='streaming'",
                DeprecationWarning,
                stacklevel=3,
            )
        selected: str
        resolution: ExecutionEngineResolution
        if present:
            _, selected, resolution = present[0]
            if selected != DEFAULT_EXECUTION_ENGINE and not collect_engine_supported(selected):
                raise ExecutionEngineUnavailableError(
                    f"Polars {pl.__version__} does not support execution engine {selected!r}"
                )
        else:
            selected, resolution = default_collect_engine(), "version_default"
        engines = supported_execution_engines("polars")
        collect_kwargs = collect_engine_option(selected) if collect_accepts_engine() else {}
        applied_native: dict[str, Any] = {"collect_kwargs": collect_kwargs}
        applied_native[ENGINE_AFFINITY_KEY] = affinity
        return ExecutionEngineReceipt(
            requested=selected if present else resolved,
            applied=selected,
            applied_class=engines[selected].engine_class if selected in engines else None,
            applied_native=applied_native,
            resolution=resolution,
            observed="not_captured",
            observed_source="none",
        )

    def _apply_tuning(self) -> None:
        import os

        config = self._tuning_config

        if config.parallelism.thread_count is not None:
            os.environ["POLARS_MAX_THREADS"] = str(config.parallelism.thread_count)
            self._log_verbose(f"Set POLARS_MAX_THREADS={config.parallelism.thread_count}")
            self._record_runtime_tuning(f"POLARS_MAX_THREADS={config.parallelism.thread_count}")

        if not config.memory.rechunk_after_filter:
            self.rechunk = False
            self._log_verbose("Disabled rechunk from tuning configuration")
            self._record_runtime_tuning("rechunk_after_filter=off")

        if config.memory.chunk_size is not None:
            pl.Config.set_streaming_chunk_size(config.memory.chunk_size)
            self._log_verbose(f"Set streaming chunk size={config.memory.chunk_size}")
            self._record_runtime_tuning(f"streaming_chunk_size={config.memory.chunk_size}")

    @property
    def platform_name(self) -> str:
        return "Polars"

    @property
    def collect_engine(self) -> str:
        receipt = self._engine_receipt
        applied = receipt.applied if receipt.applied is not None else IN_MEMORY_ENGINE
        return applied if collect_accepts_engine() else DEFAULT_EXECUTION_ENGINE

    def materialize(self, df: PolarsLazyDF) -> PolarsDF:
        return collect_frame(df, self.collect_engine)

    def col(self, name: str) -> PolarsExpr:
        return pl.col(name)

    def lit(self, value: Any) -> PolarsExpr:
        return pl.lit(value)

    def element(self) -> PolarsExpr:
        return pl.element()

    def date_sub(self, column: PolarsExpr, days: int) -> PolarsExpr:

        return column - pl.duration(days=days)

    def date_add(self, column: PolarsExpr, days: int) -> PolarsExpr:
        return column + pl.duration(days=days)

    def cast_date(self, column: PolarsExpr) -> PolarsExpr:
        return polars_cast_date(column)

    def cast_string(self, column: PolarsExpr) -> PolarsExpr:
        return column.cast(pl.Utf8)

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
    ) -> PolarsLazyDF:
        scan_kwargs: dict[str, Any] = {
            "separator": delimiter,
            "has_header": has_header,
            **reader_rechunk_option("scan_csv", self.rechunk),
            "ignore_errors": True,
            "truncate_ragged_lines": True,
            **csv_empty_string_option(dialect_preserves_empty_strings(null_marker)),
        }

        if self.n_rows is not None:
            scan_kwargs["n_rows"] = self.n_rows

        if column_names:
            scan_kwargs["new_columns"] = column_names
        if temporal_columns:
            scan_kwargs["schema_overrides"] = {
                name: pl.Date if normalized_type == "date32" else pl.Datetime("us")
                for name, normalized_type in temporal_columns.items()
            }

        lf = pl.scan_csv(path, **scan_kwargs)

        return lf

    def read_parquet(self, path: Path) -> PolarsLazyDF:
        scan_kwargs: dict[str, Any] = reader_rechunk_option("scan_parquet", self.rechunk)

        if self.n_rows is not None:
            scan_kwargs["n_rows"] = self.n_rows

        lf = pl.scan_parquet(path, **scan_kwargs)

        schema = lf.collect_schema()
        cat_cols = [name for name, dtype in schema.items() if dtype == pl.Categorical]
        if cat_cols:
            lf = lf.with_columns([pl.col(c).cast(pl.String) for c in cat_cols])

        return lf

    def collect(self, df: PolarsLazyDF) -> PolarsDF:
        if isinstance(df, pl.LazyFrame):
            return self.materialize(df)
        return df

    def get_row_count(self, df: PolarsDF | PolarsLazyDF) -> int:
        if isinstance(df, pl.LazyFrame):
            return self.materialize(df.select(pl.len())).item()
        return len(df)

    def scalar(self, df: PolarsDF | PolarsLazyDF, column: str | None = None) -> Any:
        if isinstance(df, pl.LazyFrame):
            df = self.materialize(df)

        if len(df) == 0:
            raise ValueError("Cannot extract scalar from empty DataFrame")

        if column is not None:
            return df.select(column).item()

        if len(df.columns) == 1:
            return df.item()

        return df.select(df.columns[0]).item()

    def scalar_to_df(self, data: dict[str, Any]) -> PolarsDF:
        return pl.DataFrame({k: [v] for k, v in data.items()})

    def _concat_dataframes(self, dfs: list[PolarsLazyDF]) -> PolarsLazyDF:
        if len(dfs) == 1:
            return dfs[0]
        return pl.concat(dfs)

    def _get_first_row(self, df: PolarsDF) -> tuple | None:
        if isinstance(df, pl.LazyFrame):
            df = self.materialize(df)

        if len(df) == 0:
            return None

        return tuple(df.row(0))

    def get_platform_info(self) -> dict[str, Any]:
        from dataclasses import asdict

        receipt = asdict(self._engine_receipt)
        receipt["applied_native"] = dict(receipt.get("applied_native") or {})
        info = {
            "platform": self.platform_name,
            "family": self.family,
            "execution_engine": receipt,
            "rechunk": self.rechunk,
            "rechunk_effective": reader_rechunk_effective(self.rechunk),
            "working_dir": str(self.working_dir),
        }

        if POLARS_AVAILABLE:
            info["version"] = pl.__version__
            info["polars_runtime_package"], info["polars_runtime_version"] = active_runtime()

        return info

    def when(self, condition: PolarsExpr) -> Any:
        return pl.when(condition)

    def concat_str(self, *columns: str, separator: str = "") -> PolarsExpr:
        return pl.concat_str([pl.col(c) for c in columns], separator=separator)

    def sum(self, column: str) -> PolarsExpr:
        return pl.col(column).sum()

    def mean(self, column: str) -> PolarsExpr:
        return pl.col(column).mean()

    def count(self) -> PolarsExpr:
        return pl.len()

    def min(self, column: str) -> PolarsExpr:
        return pl.col(column).min()

    def max(self, column: str) -> PolarsExpr:
        return pl.col(column).max()

    def _polars_window_rank(
        self,
        method: str,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PolarsExpr:
        order_col, ascending = order_by[0]
        expr = pl.col(order_col)

        if ascending:
            rank_expr = expr.rank(method=method)
        else:
            rank_expr = expr.rank(method=method, descending=True)

        if partition_by:
            return rank_expr.over(partition_by)
        return rank_expr

    def window_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PolarsExpr:
        return self._polars_window_rank("min", order_by, partition_by)

    def window_row_number(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PolarsExpr:
        return self._polars_window_rank("ordinal", order_by, partition_by)

    def window_dense_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PolarsExpr:
        return self._polars_window_rank("dense", order_by, partition_by)

    def window_sum(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> PolarsExpr:
        if order_by:
            sum_expr = pl.col(column).cum_sum()
        else:
            sum_expr = pl.col(column).sum()

        if partition_by:
            return sum_expr.over(partition_by)
        return sum_expr

    def window_avg(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> PolarsExpr:
        if order_by:
            avg_expr = pl.col(column).cum_sum() / pl.col(column).cum_count()
        else:
            avg_expr = pl.col(column).mean()

        if partition_by:
            return avg_expr.over(partition_by)
        return avg_expr

    def window_count(
        self,
        column: str | None = None,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> PolarsExpr:
        if order_by:
            if column:
                count_expr = pl.col(column).cum_count()
            else:
                count_expr = pl.lit(1).cum_sum()
        else:
            if column:
                count_expr = pl.col(column).count()
            else:
                count_expr = pl.len()

        if partition_by:
            return count_expr.over(partition_by)
        return count_expr

    def window_min(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> PolarsExpr:
        min_expr = pl.col(column).min()

        if partition_by:
            return min_expr.over(partition_by)
        return min_expr

    def window_max(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> PolarsExpr:
        max_expr = pl.col(column).max()

        if partition_by:
            return max_expr.over(partition_by)
        return max_expr

    @staticmethod
    def _window_order_columns(order_by: list[tuple[str, bool]] | None, column: str) -> tuple[list[str], bool]:
        order_by = order_by or [(column, True)]
        directions = {ascending for _, ascending in order_by}
        if len(directions) > 1:
            raise ValueError(
                "Polars window helpers require a uniform ORDER BY direction, "
                f"got {order_by!r}; encode mixed directions per column first."
            )
        return [name for name, _ in order_by], order_by[0][1]

    def window_lag(
        self,
        column: str,
        offset: int = 1,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> PolarsExpr:
        order_cols, ascending = self._window_order_columns(order_by, column)
        parts = partition_by if partition_by else [pl.lit(1)]
        return pl.col(column).shift(offset).over(parts, order_by=order_cols, descending=not ascending)

    def window_lead(
        self,
        column: str,
        offset: int = 1,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> PolarsExpr:
        order_cols, ascending = self._window_order_columns(order_by, column)
        parts = partition_by if partition_by else [pl.lit(1)]
        return pl.col(column).shift(-offset).over(parts, order_by=order_cols, descending=not ascending)

    def window_ntile(
        self,
        n: int,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PolarsExpr:
        order_cols, ascending = self._window_order_columns(order_by, order_by[0][0])
        rank_col: PolarsExpr = pl.struct(order_cols) if len(order_cols) > 1 else pl.col(order_cols[0])
        r0 = rank_col.rank(method="ordinal", descending=not ascending) - pl.lit(1)
        count_expr = pl.col(order_cols[0]).count()
        base = count_expr // n
        rem = count_expr % n
        big = rem * (base + pl.lit(1))
        denom = pl.when(base == pl.lit(0)).then(pl.lit(1)).otherwise(base)
        ntile_expr = (
            pl.when(r0 < big)
            .then(r0 // (base + pl.lit(1)) + pl.lit(1))
            .otherwise(rem + (r0 - big) // denom + pl.lit(1))
            .cast(pl.Int64)
        )
        if partition_by:
            return ntile_expr.over(partition_by)
        return ntile_expr

    def window_percent_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PolarsExpr:
        order_col, ascending = order_by[0]
        rank_expr = pl.col(order_col).rank(method="min", descending=not ascending)
        count_expr = pl.col(order_col).count()
        pct_expr = (rank_expr.cast(pl.Float64) - pl.lit(1.0)) / (count_expr.cast(pl.Float64) - pl.lit(1.0))
        if partition_by:
            return pct_expr.over(partition_by)
        return pct_expr

    def window_cume_dist(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> PolarsExpr:
        order_col, ascending = order_by[0]
        rank_expr = pl.col(order_col).rank(method="max", descending=not ascending)
        count_expr = pl.col(order_col).count()
        cd_expr = rank_expr.cast(pl.Float64) / count_expr.cast(pl.Float64)
        if partition_by:
            return cd_expr.over(partition_by)
        return cd_expr

    def union_all(self, *dataframes: PolarsLazyDF) -> PolarsLazyDF:
        if len(dataframes) == 0:
            raise ValueError("At least one DataFrame required for union")
        if len(dataframes) == 1:
            return dataframes[0]
        return pl.concat(list(dataframes))

    def rename_columns(self, df: PolarsLazyDF, mapping: dict[str, str]) -> PolarsLazyDF:
        return df.rename(mapping)
