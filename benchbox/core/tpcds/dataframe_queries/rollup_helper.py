# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def expand_rollup_expression(
    df: Any,
    group_cols: list[str],
    agg_exprs: list[Any],
    ctx: Any,
    *,
    count_sources: dict[str, str] | None = None,
) -> Any:
    from benchbox.platforms.dataframe.unified_frame import UnifiedExpr, UnifiedLazyFrame

    lit = ctx.lit
    col = ctx.col
    count_sources = count_sources or {}
    results = []
    n = len(group_cols)

    def get_output_name(expr: Any) -> str | None:
        native = expr.native if isinstance(expr, UnifiedExpr) else expr
        if hasattr(native, "meta") and hasattr(native.meta, "output_name"):
            try:
                return native.meta.output_name()
            except Exception:
                pass
        if hasattr(native, "_jc"):
            try:
                name = str(native._jc.toString())
                if " AS " in name:
                    return name.split(" AS ")[-1].strip("`")
                return name.strip("`")
            except Exception:
                pass
        if hasattr(native, "schema_name"):
            try:
                return native.schema_name()
            except Exception:
                pass
        return None

    resolved_names = [get_output_name(e) for e in agg_exprs]
    names_resolved = all(name is not None for name in resolved_names)
    agg_col_names = [name if name is not None else f"agg_{id(e)}" for name, e in zip(resolved_names, agg_exprs)]
    if not names_resolved:
        unresolved = next(e for name, e in zip(resolved_names, agg_exprs) if name is None)
        logger.warning(
            "ROLLUP: cannot read the output name of a %s aggregation expression; "
            "skipping the column reorder, so levels are unioned in their own column order",
            type(unresolved.native if isinstance(unresolved, UnifiedExpr) else unresolved).__name__,
        )

    for i in range(n + 1):
        current_group = group_cols[: n - i]

        guarded = {out: src for out, src in count_sources.items() if out in agg_col_names}
        count_exprs = [col(source).count().alias(f"__n_{output}") for output, source in guarded.items()]

        if current_group:
            grouped = df.group_by(*current_group).agg(*agg_exprs, *count_exprs)
        else:
            grouped = df.select(*agg_exprs, *count_exprs)

        for output in guarded:
            grouped = grouped.with_columns(
                ctx.when(col(f"__n_{output}") > lit(0)).then(col(output)).otherwise(lit(None)).alias(output)
            )

        rolled_up_cols = group_cols[n - i :]
        for null_col in rolled_up_cols:
            grouped = grouped.with_columns(lit(None).alias(null_col))

        grouping_id = sum(2**j for j in range(i))
        grouped = grouped.with_columns(lit(grouping_id).alias("grouping_id"))

        if names_resolved:
            all_cols = group_cols + agg_col_names + ["grouping_id"]
            grouped = grouped.select(*all_cols) if isinstance(grouped, UnifiedLazyFrame) else grouped.select(all_cols)

        results.append(grouped)

    return ctx.concat(results)


def expand_rollup_pandas(
    df: Any,
    group_cols: list[str],
    agg_dict: dict[str, tuple[str, str]],
    ctx: Any,
    *,
    count_sources: dict[str, str] | None = None,
) -> Any:
    from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

    native_df = df._df if isinstance(df, UnifiedPandasFrame) else df

    adapter = ctx._adapter if hasattr(ctx, "_adapter") else None
    count_sources = count_sources or {}

    results = []
    n = len(group_cols)

    for i in range(n + 1):
        current_group = group_cols[: n - i]

        guarded = {out: src for out, src in count_sources.items() if out in agg_dict}
        count_spec = {f"__n_{out}": (src, "count") for out, src in guarded.items()}

        if current_group:
            agg_spec = {out_col: (in_col, func) for out_col, (in_col, func) in agg_dict.items()}
            agg_spec.update(count_spec)
            if adapter is not None:
                grouped = adapter.groupby_agg(native_df, current_group, agg_spec, as_index=False, dropna=False)
            else:
                grouped = native_df.groupby(current_group, as_index=False, dropna=False).agg(**agg_spec)
        else:
            result_data = {}
            for out_col, (in_col, func) in {**agg_dict, **count_spec}.items():
                col_data = native_df[in_col]
                if func == "sum":
                    val = col_data.sum()
                elif func == "mean":
                    val = col_data.mean()
                elif func == "count":
                    val = col_data.count()
                elif func == "min":
                    val = col_data.min()
                elif func == "max":
                    val = col_data.max()
                else:
                    val = getattr(col_data, func)()
                if hasattr(val, "compute"):
                    val = val.compute()
                result_data[out_col] = [val]

            import pandas as pd

            grouped = pd.DataFrame(result_data)

        import pandas as _pd

        for output in guarded:
            grouped[output] = _pd.Series(
                [value if count > 0 else None for value, count in zip(grouped[output], grouped[f"__n_{output}"])],
                dtype=object,
                index=grouped.index,
            )
        grouped = grouped.drop(columns=list(count_spec))

        for group_col in current_group:
            grouped[group_col] = grouped[group_col].astype(object).where(grouped[group_col].notnull(), None)

        rolled_up_cols = group_cols[n - i :]
        for null_col in rolled_up_cols:
            grouped[null_col] = None

        grouping_id = sum(2**j for j in range(i))
        grouped["grouping_id"] = grouping_id

        results.append(grouped)

    return ctx.concat(results)


def compute_grouping_function(
    df: Any,
    grouping_id_col: str,
    column_position: int,
) -> Any:
    bit_value = 2**column_position

    try:
        import polars as pl

        return (pl.col(grouping_id_col) & bit_value) / bit_value
    except ImportError:
        pass

    return lambda gid: (gid & bit_value) // bit_value


def lochierarchy_expression(
    grouping_id_col: str,
    num_cols: int,
    ctx: Any = None,
) -> Any:
    if ctx is not None:
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        col_expr = ctx.col(grouping_id_col)
        bit_count = ctx.lit(0)

        for i in range(num_cols):
            bit_val = 2**i
            bit_expr = (col_expr & bit_val) / bit_val

            if hasattr(col_expr, "_is_pyspark") and col_expr._is_pyspark:
                from pyspark.sql.types import IntegerType

                bit_expr = bit_expr.cast(IntegerType())
            else:
                try:
                    import polars as pl

                    bit_expr = bit_expr.cast(pl.Int32)
                except ImportError:
                    pass

            bit_count = bit_count + bit_expr

        if isinstance(bit_count, UnifiedExpr):
            return bit_count
        return UnifiedExpr(bit_count)

    try:
        import polars as pl

        col = pl.col(grouping_id_col)

        bit_count = pl.lit(0)
        for i in range(num_cols):
            bit_count = bit_count + ((col & (2**i)) / (2**i)).cast(pl.Int32)
        return bit_count
    except ImportError:
        pass

    def count_bits(gid: int) -> int:
        return bin(gid).count("1")

    return count_bits
