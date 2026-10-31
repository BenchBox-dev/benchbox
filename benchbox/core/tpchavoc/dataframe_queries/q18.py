from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchbox.core.dataframe.compat import _to_list
from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.pandas_ops import is_lazy_frame
from benchbox.core.tpch.dataframe_queries import (
    get_query as get_tpch_query,
    get_tpch_parameters,
    q18_expression_impl as _q18_expr_base,
    q18_pandas_impl as _q18_pandas_base,
)
from benchbox.core.tpchavoc.dataframe_queries.loader import build_variants

VariantImpl = Callable[[DataFrameContext], Any]

_DESCRIPTIONS = [
    "Baseline: direct delegation to TPC-H Q18 implementation",
    "Distinct-key join: deduplicated large-order keys inner-joined instead of a semi-join",
    "Late filtering: lineitem joined before the large-order semi-filter",
    "Order-key prefilter: lineitem semi-pruned to qualifying keys before the quantity aggregate",
    "Column prune: select only needed columns before the joins",
    "Nation-band branches: large orders split by customer nation range, joined per branch, concatenated after",
    "Marker reformulation: left-join large-order keys with a null-marker exclusion instead of a semi-join",
    "Deferred enrichment: per-order quantity sums aggregated before joining customer and order columns",
    "Top-100 pushdown: qualifying orders ranked by total price before the lineitem join",
    "Filter-first ordering: large-order keys applied before customer join",
]

_GROUP_KEYS = ["c_name", "c_custkey", "o_orderkey", "o_orderdate", "o_totalprice"]
_SORT_KEYS = ["o_totalprice", "o_orderdate"]
_SORT_DESC = [True, False]


def _q18_expr_large_orders(lineitem: Any, col: Any, lit: Any, threshold: Any) -> Any:
    return (
        lineitem.group_by("l_orderkey")
        .agg(col("l_quantity").sum().alias("total_qty"))
        .filter(col("total_qty") > lit(threshold))
        .select("l_orderkey")
    )


def _q18_expr_aggregate(joined: Any, col: Any) -> Any:
    return (
        joined.group_by(*_GROUP_KEYS)
        .agg(col("l_quantity").sum().alias("sum_qty"))
        .sort(_SORT_KEYS, descending=_SORT_DESC)
        .limit(100)
    )


def _make_q18_expression_impl(variant: int) -> VariantImpl:
    def impl(ctx: DataFrameContext) -> Any:
        if variant == 1:
            return _q18_expr_base(ctx)

        col = ctx.col
        lit = ctx.lit
        params = get_tpch_parameters(18)
        threshold = params["quantity_threshold"]
        customer = ctx.get_table("customer")
        orders = ctx.get_table("orders")
        lineitem = ctx.get_table("lineitem")

        if variant == 2:
            large_orders = _q18_expr_large_orders(lineitem, col, lit, threshold).distinct()
            joined = (
                customer.join(orders, left_on="c_custkey", right_on="o_custkey")
                .join(large_orders, left_on="o_orderkey", right_on="l_orderkey")
                .join(lineitem, left_on="o_orderkey", right_on="l_orderkey")
            )
            return _q18_expr_aggregate(joined, col)

        if variant == 3:
            large_orders = _q18_expr_large_orders(lineitem, col, lit, threshold)
            joined = customer.join(orders, left_on="c_custkey", right_on="o_custkey").join(
                lineitem, left_on="o_orderkey", right_on="l_orderkey"
            )
            return _q18_expr_aggregate(
                joined.join(large_orders, left_on="o_orderkey", right_on="l_orderkey", how="semi"), col
            )

        if variant == 4:
            if threshold > 50:
                multi_line_keys = (
                    lineitem.group_by("l_orderkey")
                    .agg(col("l_orderkey").count().alias("line_cnt"))
                    .filter(col("line_cnt") > lit(1))
                    .select("l_orderkey")
                )
                pruned = lineitem.join(multi_line_keys, left_on="l_orderkey", right_on="l_orderkey", how="semi")
            else:
                pruned = lineitem
            large_orders = _q18_expr_large_orders(pruned, col, lit, threshold)
            joined = (
                customer.join(orders, left_on="c_custkey", right_on="o_custkey")
                .join(large_orders, left_on="o_orderkey", right_on="l_orderkey", how="semi")
                .join(lineitem, left_on="o_orderkey", right_on="l_orderkey")
            )
            return _q18_expr_aggregate(joined, col)

        if variant == 5:
            customer_cols = customer.select("c_custkey", "c_name")
            orders_cols = orders.select("o_orderkey", "o_custkey", "o_orderdate", "o_totalprice")
            lineitem_cols = lineitem.select("l_orderkey", "l_quantity")
            large_orders = _q18_expr_large_orders(lineitem, col, lit, threshold)
            joined = (
                customer_cols.join(orders_cols, left_on="c_custkey", right_on="o_custkey")
                .join(large_orders, left_on="o_orderkey", right_on="l_orderkey", how="semi")
                .join(lineitem_cols, left_on="o_orderkey", right_on="l_orderkey")
            )
            return _q18_expr_aggregate(joined, col)

        if variant == 6:
            order_totals = (
                lineitem.group_by("l_orderkey")
                .agg(col("l_quantity").sum().alias("total_qty"))
                .filter(col("total_qty") > lit(threshold))
                .select("l_orderkey")
            )

            def _nation_branch(nation_gate: Any) -> Any:
                base = customer.filter(nation_gate).join(orders, left_on="c_custkey", right_on="o_custkey")
                large = base.join(order_totals, left_on="o_orderkey", right_on="l_orderkey", how="semi")
                return large.join(lineitem, left_on="o_orderkey", right_on="l_orderkey")

            low_band = _nation_branch(col("c_nationkey") < lit(12))
            high_band = _nation_branch(col("c_nationkey") >= lit(12))
            return _q18_expr_aggregate(ctx.concat([low_band, high_band]), col)

        if variant == 7:
            large_orders = (
                lineitem.group_by("l_orderkey")
                .agg(col("l_quantity").sum().alias("total_qty"))
                .filter(col("total_qty") > lit(threshold))
                .select("l_orderkey")
                .with_columns(lit(1).alias("is_large"))
            )
            keyed = (
                customer.join(orders, left_on="c_custkey", right_on="o_custkey")
                .join(large_orders, left_on="o_orderkey", right_on="l_orderkey", how="left")
                .filter(col("is_large").is_not_null())
                .drop("is_large")
            )
            return _q18_expr_aggregate(keyed.join(lineitem, left_on="o_orderkey", right_on="l_orderkey"), col)

        if variant == 8:
            large_orders = _q18_expr_large_orders(lineitem, col, lit, threshold)
            order_sums = (
                lineitem.group_by("l_orderkey")
                .agg(col("l_quantity").sum().alias("sum_qty"))
                .join(large_orders, left_on="l_orderkey", right_on="l_orderkey", how="semi")
            )
            joined = customer.join(orders, left_on="c_custkey", right_on="o_custkey").join(
                order_sums, left_on="o_orderkey", right_on="l_orderkey"
            )
            return (
                joined.group_by(*_GROUP_KEYS)
                .agg(col("sum_qty").first().alias("sum_qty"))
                .sort(_SORT_KEYS, descending=_SORT_DESC)
                .limit(100)
            )

        if variant == 9:
            large_orders = _q18_expr_large_orders(lineitem, col, lit, threshold)
            ranked = (
                customer.join(orders, left_on="c_custkey", right_on="o_custkey")
                .join(large_orders, left_on="o_orderkey", right_on="l_orderkey", how="semi")
                .sort(_SORT_KEYS, descending=_SORT_DESC)
                .limit(100)
            )
            joined = ranked.join(lineitem, left_on="o_orderkey", right_on="l_orderkey")
            return (
                joined.group_by(*_GROUP_KEYS)
                .agg(col("l_quantity").sum().alias("sum_qty"))
                .sort(_SORT_KEYS, descending=_SORT_DESC)
                .limit(100)
            )

        large_orders = _q18_expr_large_orders(lineitem, col, lit, threshold)
        filtered_orders = orders.join(large_orders, left_on="o_orderkey", right_on="l_orderkey", how="semi")
        joined = customer.join(filtered_orders, left_on="c_custkey", right_on="o_custkey").join(
            lineitem, left_on="o_orderkey", right_on="l_orderkey"
        )
        return _q18_expr_aggregate(joined, col)

    impl.__name__ = f"q18_v{variant}_expression_impl"
    impl.__qualname__ = impl.__name__
    return impl


def _q18_pandas_large_orders(lineitem: Any, threshold: Any) -> Any:
    order_qty = lineitem.groupby("l_orderkey", as_index=False).agg(total_qty=("l_quantity", "sum"))
    return _to_list(order_qty[order_qty["total_qty"] > threshold]["l_orderkey"])


def _q18_pandas_aggregate(joined: Any) -> Any:
    return (
        joined.groupby(["c_name", "c_custkey", "o_orderkey", "o_orderdate", "o_totalprice"], as_index=False)
        .agg(sum_qty=("l_quantity", "sum"))
        .sort_values(["o_totalprice", "o_orderdate"], ascending=[False, True])
        .head(100)
    )


def _top_rows(frame: Any, count: int) -> Any:
    native = getattr(frame, "native", frame)
    if is_lazy_frame(native):
        return native.head(count, npartitions=-1)
    return native.head(count)


def _make_q18_pandas_impl(variant: int) -> VariantImpl:
    def impl(ctx: DataFrameContext) -> Any:
        if variant == 1:
            return _q18_pandas_base(ctx)

        customer = ctx.get_table("customer")
        orders = ctx.get_table("orders")
        lineitem = ctx.get_table("lineitem")
        params = get_tpch_parameters(18)
        threshold = params["quantity_threshold"]

        if variant == 2:
            import pandas as pd

            order_qty = lineitem.groupby("l_orderkey", as_index=False).agg(total_qty=("l_quantity", "sum"))
            large_orders = _to_list(
                order_qty[order_qty["total_qty"] > threshold].drop_duplicates("l_orderkey")["l_orderkey"]
            )
            joined = customer.merge(orders, left_on="c_custkey", right_on="o_custkey")
            keyed = joined.merge(
                pd.DataFrame({"l_orderkey": large_orders}),
                left_on="o_orderkey",
                right_on="l_orderkey",
            ).drop(columns=["l_orderkey"])
            return _q18_pandas_aggregate(keyed.merge(lineitem, left_on="o_orderkey", right_on="l_orderkey"))

        if variant == 3:
            large_orders = _q18_pandas_large_orders(lineitem, threshold)
            joined = customer.merge(orders, left_on="c_custkey", right_on="o_custkey").merge(
                lineitem, left_on="o_orderkey", right_on="l_orderkey"
            )
            return _q18_pandas_aggregate(joined[joined["o_orderkey"].isin(large_orders)])

        if variant == 4:
            if threshold > 50:
                line_counts = lineitem.groupby("l_orderkey", as_index=False).agg(line_cnt=("l_orderkey", "count"))
                multi_line = _to_list(line_counts[line_counts["line_cnt"] > 1]["l_orderkey"])
                pruned = lineitem[lineitem["l_orderkey"].isin(multi_line)]
            else:
                pruned = lineitem
            large_orders = _q18_pandas_large_orders(pruned, threshold)
            joined = customer.merge(orders, left_on="c_custkey", right_on="o_custkey")
            joined = joined[joined["o_orderkey"].isin(large_orders)]
            return _q18_pandas_aggregate(joined.merge(lineitem, left_on="o_orderkey", right_on="l_orderkey"))

        if variant == 5:
            customer_cols = customer[["c_custkey", "c_name"]]
            orders_cols = orders[["o_orderkey", "o_custkey", "o_orderdate", "o_totalprice"]]
            lineitem_cols = lineitem[["l_orderkey", "l_quantity"]]
            large_orders = _q18_pandas_large_orders(lineitem, threshold)
            joined = customer_cols.merge(orders_cols, left_on="c_custkey", right_on="o_custkey")
            joined = joined[joined["o_orderkey"].isin(large_orders)]
            return _q18_pandas_aggregate(joined.merge(lineitem_cols, left_on="o_orderkey", right_on="l_orderkey"))

        if variant == 6:
            order_qty = lineitem.groupby("l_orderkey", as_index=False).agg(total_qty=("l_quantity", "sum"))
            large_set = set(_to_list(order_qty[order_qty["total_qty"] > threshold]["l_orderkey"]))
            cust_orders = customer.merge(orders, left_on="c_custkey", right_on="o_custkey")

            def _nation_frame(gate: Any) -> Any:
                band = cust_orders[gate(cust_orders)]
                band = band[band["o_orderkey"].isin(large_set)]
                return band.merge(lineitem, left_on="o_orderkey", right_on="l_orderkey")

            merged = ctx.concat(
                [
                    _nation_frame(lambda frame: frame["c_nationkey"] < 12),
                    _nation_frame(lambda frame: frame["c_nationkey"] >= 12),
                ]
            )
            return _q18_pandas_aggregate(merged)

        if variant == 7:
            order_qty = lineitem.groupby("l_orderkey", as_index=False).agg(total_qty=("l_quantity", "sum"))
            keys = order_qty[order_qty["total_qty"] > threshold][["l_orderkey"]].copy()
            keys["is_large"] = 1
            joined = customer.merge(orders, left_on="c_custkey", right_on="o_custkey").merge(
                keys, left_on="o_orderkey", right_on="l_orderkey", how="left"
            )
            joined = joined[joined["is_large"].notnull()].drop(columns=["is_large", "l_orderkey"])
            return _q18_pandas_aggregate(joined.merge(lineitem, left_on="o_orderkey", right_on="l_orderkey"))

        if variant == 8:
            large_orders = _q18_pandas_large_orders(lineitem, threshold)
            order_sums = lineitem.groupby("l_orderkey", as_index=False).agg(sum_qty=("l_quantity", "sum"))
            import pandas as pd

            order_sums = order_sums.merge(
                pd.DataFrame({"l_orderkey": pd.Series(large_orders, dtype="int64")}), on="l_orderkey"
            )
            joined = customer.merge(orders, left_on="c_custkey", right_on="o_custkey").merge(
                order_sums, left_on="o_orderkey", right_on="l_orderkey"
            )
            return (
                joined.groupby(["c_name", "c_custkey", "o_orderkey", "o_orderdate", "o_totalprice"], as_index=False)
                .agg(sum_qty=("sum_qty", "first"))
                .sort_values(["o_totalprice", "o_orderdate"], ascending=[False, True])
                .head(100)
            )

        if variant == 9:
            large_orders = _q18_pandas_large_orders(lineitem, threshold)
            ranked = customer.merge(orders, left_on="c_custkey", right_on="o_custkey")
            ranked = ranked[ranked["o_orderkey"].isin(large_orders)]
            ranked = ranked.sort_values(["o_totalprice", "o_orderdate"], ascending=[False, True])
            top_orders = _to_list(_top_rows(ranked, 100)["o_orderkey"])
            ranked = ranked[ranked["o_orderkey"].isin(top_orders)]
            return _q18_pandas_aggregate(ranked.merge(lineitem, left_on="o_orderkey", right_on="l_orderkey"))

        large_orders = _q18_pandas_large_orders(lineitem, threshold)
        filtered_orders = orders[orders["o_orderkey"].isin(large_orders)]
        joined = customer.merge(filtered_orders, left_on="c_custkey", right_on="o_custkey")
        return _q18_pandas_aggregate(joined.merge(lineitem, left_on="o_orderkey", right_on="l_orderkey"))

    impl.__name__ = f"q18_v{variant}_pandas_impl"
    impl.__qualname__ = impl.__name__
    return impl


_Q18_BASE = get_tpch_query("Q18")

Q18_VARIANTS = build_variants(
    18,
    [(_make_q18_expression_impl(v), _make_q18_pandas_impl(v)) for v in range(1, 11)],
    _DESCRIPTIONS,
    _Q18_BASE.categories,
    expected_row_count=_Q18_BASE.expected_row_count,
    scale_factor_dependent=_Q18_BASE.scale_factor_dependent,
    timeout_seconds=_Q18_BASE.timeout_seconds,
    skip_platforms=_Q18_BASE.skip_platforms,
)
