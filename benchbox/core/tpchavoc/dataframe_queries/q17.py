from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.tpch.dataframe_queries import (
    get_query as get_tpch_query,
    get_tpch_parameters,
    q17_expression_impl as _q17_expr_base,
    q17_pandas_impl as _q17_pandas_base,
)
from benchbox.core.tpchavoc.dataframe_queries.loader import build_variants

VariantImpl = Callable[[DataFrameContext], Any]

_DESCRIPTIONS = [
    "Baseline: direct delegation to TPC-H Q17 implementation",
    "Average materialization: precompute per-part average table before the join",
    "Late average join: lineitem joined before the per-part average table",
    "Part-side prefilter: brand/container predicates applied before the join",
    "Column prune: select only needed columns before the join",
    "Size-band branches: part-size bands joined and filtered independently, summed after",
    "Anti-null reformulation: left-join the average table with an is-null exclusion",
    "Commuted formula: per-row revenue divided by 7.0 before the final sum",
    "Revenue materialization: with_columns revenue before the final sum",
    "Threshold-first ordering: threshold survivors selected before the part join",
]


def _q17_expr_avg(col: Any, lit: Any) -> Any:
    return (col("l_quantity").mean() * lit(0.2)).alias("avg_qty")


_Q17_SIZE_SPLIT = 25


def _make_q17_expression_impl(variant: int) -> VariantImpl:
    def impl(ctx: DataFrameContext) -> Any:
        if variant == 1:
            return _q17_expr_base(ctx)

        col = ctx.col
        lit = ctx.lit
        params = get_tpch_parameters(17)
        lineitem = ctx.get_table("lineitem")
        part = ctx.get_table("part")
        part_filter = (col("p_brand") == lit(params["brand"])) & (col("p_container") == lit(params["container"]))

        if variant == 2:
            avg_table = lineitem.group_by("l_partkey").agg(_q17_expr_avg(col, lit))
            joined = (
                part.filter(part_filter)
                .join(lineitem, left_on="p_partkey", right_on="l_partkey")
                .join(avg_table, left_on="p_partkey", right_on="l_partkey")
            )
            return joined.filter(col("l_quantity") < col("avg_qty")).select(
                (col("l_extendedprice").sum() / lit(7.0)).alias("avg_yearly")
            )

        if variant == 3:
            joined = part.filter(part_filter).join(lineitem, left_on="p_partkey", right_on="l_partkey")
            avg_table = joined.group_by("p_partkey").agg(_q17_expr_avg(col, lit))
            joined = joined.join(avg_table, left_on="p_partkey", right_on="p_partkey")
            return joined.filter(col("l_quantity") < col("avg_qty")).select(
                (col("l_extendedprice").sum() / lit(7.0)).alias("avg_yearly")
            )

        if variant == 4:
            part_branch = part.filter(col("p_brand") == lit(params["brand"])).filter(
                col("p_container") == lit(params["container"])
            )
            avg_table = lineitem.group_by("l_partkey").agg(_q17_expr_avg(col, lit))
            joined = part_branch.join(lineitem, left_on="p_partkey", right_on="l_partkey").join(
                avg_table, left_on="p_partkey", right_on="l_partkey"
            )
            return joined.filter(col("l_quantity") < col("avg_qty")).select(
                (col("l_extendedprice").sum() / lit(7.0)).alias("avg_yearly")
            )

        if variant == 5:
            lineitem_cols = lineitem.select("l_partkey", "l_quantity", "l_extendedprice")
            part_cols = part.select("p_partkey", "p_brand", "p_container").filter(part_filter)
            avg_table = lineitem.group_by("l_partkey").agg(_q17_expr_avg(col, lit))
            joined = part_cols.join(lineitem_cols, left_on="p_partkey", right_on="l_partkey").join(
                avg_table, left_on="p_partkey", right_on="l_partkey"
            )
            return joined.filter(col("l_quantity") < col("avg_qty")).select(
                (col("l_extendedprice").sum() / lit(7.0)).alias("avg_yearly")
            )

        if variant == 6:
            filtered_parts = part.filter(part_filter)
            avg_table = lineitem.group_by("l_partkey").agg(_q17_expr_avg(col, lit))

            def _branch(band: Any) -> Any:
                return band.join(lineitem, left_on="p_partkey", right_on="l_partkey").join(
                    avg_table, left_on="p_partkey", right_on="l_partkey"
                )

            small_band = filtered_parts.filter(col("p_size") <= lit(_Q17_SIZE_SPLIT))
            large_band = filtered_parts.filter(col("p_size") > lit(_Q17_SIZE_SPLIT))
            return (
                ctx.concat([_branch(small_band), _branch(large_band)])
                .filter(col("l_quantity") < col("avg_qty"))
                .select((col("l_extendedprice").sum() / lit(7.0)).alias("avg_yearly"))
            )

        if variant == 7:
            avg_table = lineitem.group_by("l_partkey").agg(_q17_expr_avg(col, lit))
            joined = (
                part.filter(part_filter)
                .join(lineitem, left_on="p_partkey", right_on="l_partkey")
                .join(avg_table, left_on="p_partkey", right_on="l_partkey", how="left")
                .filter(col("avg_qty").is_not_null())
            )
            return joined.filter(col("l_quantity") < col("avg_qty")).select(
                (col("l_extendedprice").sum() / lit(7.0)).alias("avg_yearly")
            )

        if variant == 8:
            avg_table = lineitem.group_by("l_partkey").agg(_q17_expr_avg(col, lit))
            joined = (
                part.filter(part_filter)
                .join(lineitem, left_on="p_partkey", right_on="l_partkey")
                .join(avg_table, left_on="p_partkey", right_on="l_partkey")
            )
            return joined.filter(col("l_quantity") < col("avg_qty")).select(
                ((col("l_extendedprice") / lit(7.0)).sum()).alias("avg_yearly")
            )

        if variant == 9:
            avg_table = lineitem.group_by("l_partkey").agg(_q17_expr_avg(col, lit))
            joined = (
                part.filter(part_filter)
                .join(lineitem, left_on="p_partkey", right_on="l_partkey")
                .join(avg_table, left_on="p_partkey", right_on="l_partkey")
            )
            return (
                joined.filter(col("l_quantity") < col("avg_qty"))
                .with_columns((col("l_extendedprice") / lit(7.0)).alias("revenue"))
                .select(col("revenue").sum().alias("avg_yearly"))
            )

        avg_table = lineitem.group_by("l_partkey").agg(_q17_expr_avg(col, lit))
        survivors = lineitem.join(avg_table, left_on="l_partkey", right_on="l_partkey").filter(
            col("avg_qty") > col("l_quantity")
        )
        joined = part.filter(part_filter).join(survivors, left_on="p_partkey", right_on="l_partkey")
        return joined.select((col("l_extendedprice").sum() / lit(7.0)).alias("avg_yearly"))

    impl.__name__ = f"q17_v{variant}_expression_impl"
    impl.__qualname__ = impl.__name__
    return impl


def _q17_pandas_avg(lineitem: Any) -> Any:
    avg_qty = lineitem.groupby("l_partkey", as_index=False).agg(avg_qty=("l_quantity", "mean"))
    avg_qty["avg_qty"] = avg_qty["avg_qty"] * 0.2
    return avg_qty


def _q17_pandas_sum(filtered: Any) -> Any:
    import pandas as pd

    avg_yearly = filtered["l_extendedprice"].sum() / 7.0
    value = avg_yearly.compute() if hasattr(avg_yearly, "compute") else avg_yearly
    return pd.DataFrame({"avg_yearly": [value]})


def _make_q17_pandas_impl(variant: int) -> VariantImpl:
    def impl(ctx: DataFrameContext) -> Any:
        if variant == 1:
            return _q17_pandas_base(ctx)

        lineitem = ctx.get_table("lineitem")
        part = ctx.get_table("part")
        params = get_tpch_parameters(17)
        mask = (part["p_brand"] == params["brand"]) & (part["p_container"] == params["container"])

        if variant == 2:
            avg_table = _q17_pandas_avg(lineitem)
            joined = (
                part[mask].merge(lineitem, left_on="p_partkey", right_on="l_partkey").merge(avg_table, on="l_partkey")
            )
            return _q17_pandas_sum(joined[joined["l_quantity"] < joined["avg_qty"]])

        if variant == 3:
            merged = part[mask].merge(lineitem, left_on="p_partkey", right_on="l_partkey")
            avg_table = merged.groupby("p_partkey", as_index=False).agg(avg_qty=("l_quantity", "mean"))
            avg_table["avg_qty"] = avg_table["avg_qty"] * 0.2
            joined = merged.merge(avg_table, left_on="p_partkey", right_on="p_partkey")
            return _q17_pandas_sum(joined[joined["l_quantity"] < joined["avg_qty"]])

        if variant == 4:
            part_branch = part[part["p_brand"] == params["brand"]]
            part_branch = part_branch[part_branch["p_container"] == params["container"]]
            avg_table = _q17_pandas_avg(lineitem)
            joined = part_branch.merge(lineitem, left_on="p_partkey", right_on="l_partkey").merge(
                avg_table, on="l_partkey"
            )
            return _q17_pandas_sum(joined[joined["l_quantity"] < joined["avg_qty"]])

        if variant == 5:
            lineitem_cols = lineitem[["l_partkey", "l_quantity", "l_extendedprice"]]
            part_cols = part[mask][["p_partkey", "p_brand", "p_container"]]
            avg_table = _q17_pandas_avg(lineitem)
            joined = part_cols.merge(lineitem_cols, left_on="p_partkey", right_on="l_partkey").merge(
                avg_table, on="l_partkey"
            )
            return _q17_pandas_sum(joined[joined["l_quantity"] < joined["avg_qty"]])

        if variant == 6:
            avg_table = _q17_pandas_avg(lineitem)
            filtered_parts = part[mask]

            def _branch(band: Any) -> Any:
                return band.merge(lineitem, left_on="p_partkey", right_on="l_partkey").merge(
                    avg_table, left_on="p_partkey", right_on="l_partkey"
                )

            small_band = filtered_parts[filtered_parts["p_size"] <= _Q17_SIZE_SPLIT]
            large_band = filtered_parts[filtered_parts["p_size"] > _Q17_SIZE_SPLIT]
            merged = ctx.concat([_branch(small_band), _branch(large_band)])
            return _q17_pandas_sum(merged[merged["l_quantity"] < merged["avg_qty"]])

        if variant == 7:
            avg_table = _q17_pandas_avg(lineitem)
            joined = (
                part[mask]
                .merge(lineitem, left_on="p_partkey", right_on="l_partkey")
                .merge(avg_table, left_on="p_partkey", right_on="l_partkey", how="left")
            )
            joined = joined[joined["avg_qty"].notnull()]
            return _q17_pandas_sum(joined[joined["l_quantity"] < joined["avg_qty"]])

        if variant == 8:
            import pandas as pd

            avg_table = _q17_pandas_avg(lineitem)
            joined = (
                part[mask]
                .merge(lineitem, left_on="p_partkey", right_on="l_partkey")
                .merge(avg_table, left_on="p_partkey", right_on="l_partkey")
            )
            filtered = joined[joined["l_quantity"] < joined["avg_qty"]]
            value = (filtered["l_extendedprice"] / 7.0).sum()
            value = value.compute() if hasattr(value, "compute") else value
            return pd.DataFrame({"avg_yearly": [value]})

        if variant == 9:
            avg_table = _q17_pandas_avg(lineitem)
            joined = (
                part[mask].merge(lineitem, left_on="p_partkey", right_on="l_partkey").merge(avg_table, on="l_partkey")
            )
            filtered = joined[joined["l_quantity"] < joined["avg_qty"]].copy()
            filtered["revenue"] = filtered["l_extendedprice"] / 7.0
            import pandas as pd

            value = filtered["revenue"].sum()
            value = value.compute() if hasattr(value, "compute") else value
            return pd.DataFrame({"avg_yearly": [value]})

        avg_table = _q17_pandas_avg(lineitem)
        survivors = lineitem.merge(avg_table, on="l_partkey")
        survivors = survivors[survivors["avg_qty"] > survivors["l_quantity"]]
        joined = part[mask].merge(survivors, left_on="p_partkey", right_on="l_partkey")
        return _q17_pandas_sum(joined)

    impl.__name__ = f"q17_v{variant}_pandas_impl"
    impl.__qualname__ = impl.__name__
    return impl


_Q17_BASE = get_tpch_query("Q17")

Q17_VARIANTS = build_variants(
    17,
    [(_make_q17_expression_impl(v), _make_q17_pandas_impl(v)) for v in range(1, 11)],
    _DESCRIPTIONS,
    _Q17_BASE.categories,
    expected_row_count=_Q17_BASE.expected_row_count,
    scale_factor_dependent=_Q17_BASE.scale_factor_dependent,
    timeout_seconds=_Q17_BASE.timeout_seconds,
    skip_platforms=_Q17_BASE.skip_platforms,
)
