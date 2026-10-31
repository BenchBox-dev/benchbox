from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchbox.core.dataframe.compat import _to_list
from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.tpch.dataframe_queries import (
    get_query as get_tpch_query,
    get_tpch_parameters,
    q21_expression_impl as _q21_expr_base,
    q21_pandas_impl as _q21_pandas_base,
)
from benchbox.core.tpchavoc.dataframe_queries.loader import build_variants

VariantImpl = Callable[[DataFrameContext], Any]

_DESCRIPTIONS = [
    "Baseline: direct delegation to TPC-H Q21 implementation",
    "Exists-via-inner: inner join plus unique instead of semi-joins",
    "Not-exists-via-anti: anti-join against other late suppliers",
    "Counts-first-late: late-supplier counts before total supplier counts",
    "Column prune: select only needed columns before each join",
    "Orders-first: valid orders filter pushed before candidate assembly",
    "Chained style: maximum method chaining, no named intermediates",
    "Combined predicates: single compound EXISTS/NOT EXISTS filter",
    "Late-join-order swap: late counts joined before supplier counts",
    "Candidate-narrowing: candidate orders restricted before aggregation",
]


def _q21_expr_targets(supplier: Any, nation: Any, col: Any, lit: Any, nation_name: str) -> Any:
    return (
        supplier.join(nation, left_on="s_nationkey", right_on="n_nationkey")
        .filter(col("n_name") == lit(nation_name))
        .select(col("s_suppkey").alias("target_suppkey"), col("s_name"))
    )


def _q21_expr_valid_orders(orders: Any, col: Any, lit: Any) -> Any:
    return orders.filter(col("o_orderstatus") == lit("F")).select(col("o_orderkey").alias("valid_orderkey"))


def _q21_expr_candidates(lineitem: Any, targets: Any, valid: Any, col: Any) -> Any:
    return (
        lineitem.filter(col("l_receiptdate") > col("l_commitdate"))
        .join(targets, left_on="l_suppkey", right_on="target_suppkey")
        .join(valid, left_on="l_orderkey", right_on="valid_orderkey")
        .select("l_orderkey", "l_suppkey", "s_name")
    )


def _q21_expr_final(candidates: Any, per_order: Any, late_per_order: Any, col: Any, lit: Any) -> Any:
    return (
        candidates.join(per_order, left_on="l_orderkey", right_on="supp_orderkey")
        .join(late_per_order, left_on="l_orderkey", right_on="late_orderkey")
        .filter(col("num_suppliers") > lit(1))
        .filter(col("num_late_suppliers") == lit(1))
        .group_by("s_name")
        .agg(col("l_orderkey").count().alias("numwait"))
        .sort(["numwait", "s_name"], descending=[True, False])
        .limit(100)
    )


def _make_q21_expression_impl(variant: int) -> VariantImpl:
    def impl(ctx: DataFrameContext) -> Any:
        if variant == 1:
            return _q21_expr_base(ctx)

        col = ctx.col
        lit = ctx.lit
        params = get_tpch_parameters(21)
        nation_name = params["nation_name"]
        supplier = ctx.get_table("supplier")
        lineitem = ctx.get_table("lineitem")
        orders = ctx.get_table("orders")
        nation = ctx.get_table("nation")
        late = col("l_receiptdate") > col("l_commitdate")

        if variant == 2:
            targets = _q21_expr_targets(supplier, nation, col, lit, nation_name)
            valid = _q21_expr_valid_orders(orders, col, lit)
            candidates = _q21_expr_candidates(lineitem, targets, valid, col)
            cand_orders = candidates.select(col("l_orderkey").alias("cand_orderkey")).unique()
            per_order = (
                lineitem.join(cand_orders, left_on="l_orderkey", right_on="cand_orderkey")
                .select("l_orderkey", "l_suppkey")
                .unique()
                .group_by("l_orderkey")
                .agg(col("l_suppkey").count().alias("num_suppliers"))
                .select(col("l_orderkey").alias("supp_orderkey"), col("num_suppliers"))
            )
            late_per_order = (
                lineitem.filter(late)
                .join(cand_orders, left_on="l_orderkey", right_on="cand_orderkey")
                .select("l_orderkey", "l_suppkey")
                .unique()
                .group_by("l_orderkey")
                .agg(col("l_suppkey").count().alias("num_late_suppliers"))
                .select(col("l_orderkey").alias("late_orderkey"), col("num_late_suppliers"))
            )
            return _q21_expr_final(candidates, per_order, late_per_order, col, lit)

        if variant == 3:
            targets = _q21_expr_targets(supplier, nation, col, lit, nation_name)
            valid = _q21_expr_valid_orders(orders, col, lit)
            candidates = _q21_expr_candidates(lineitem, targets, valid, col)
            cand_orders = candidates.select(col("l_orderkey").alias("cand_orderkey")).unique()
            per_order = (
                lineitem.join(cand_orders, left_on="l_orderkey", right_on="cand_orderkey", how="semi")
                .group_by("l_orderkey")
                .agg(col("l_suppkey").n_unique().alias("num_suppliers"))
                .select(col("l_orderkey").alias("supp_orderkey"), col("num_suppliers"))
            )
            late_pairs = (
                lineitem.filter(late)
                .join(cand_orders, left_on="l_orderkey", right_on="cand_orderkey", how="semi")
                .select(col("l_orderkey"), col("l_suppkey").alias("late_supp"))
                .unique()
            )
            cand_pairs = candidates.select("l_orderkey", "l_suppkey", "s_name").unique()
            bad_pairs = (
                cand_pairs.join(late_pairs, left_on="l_orderkey", right_on="l_orderkey")
                .filter(col("l_suppkey") != col("late_supp"))
                .select("l_orderkey", "l_suppkey")
                .unique()
            )
            good_orders = (
                cand_pairs.select("l_orderkey", "l_suppkey")
                .unique()
                .join(bad_pairs, left_on=["l_orderkey", "l_suppkey"], right_on=["l_orderkey", "l_suppkey"], how="anti")
                .select(col("l_orderkey").alias("late_orderkey"))
                .unique()
            )
            late_per_order = good_orders.with_columns(lit(1).alias("num_late_suppliers"))
            return _q21_expr_final(candidates, per_order, late_per_order, col, lit)

        targets = _q21_expr_targets(supplier, nation, col, lit, nation_name)
        valid = _q21_expr_valid_orders(orders, col, lit)

        if variant == 4:
            candidates = _q21_expr_candidates(lineitem, targets, valid, col)
            cand_orders = candidates.select(col("l_orderkey").alias("cand_orderkey")).unique()
            late_per_order = (
                lineitem.filter(late)
                .join(cand_orders, left_on="l_orderkey", right_on="cand_orderkey", how="semi")
                .group_by("l_orderkey")
                .agg(col("l_suppkey").n_unique().alias("num_late_suppliers"))
                .select(col("l_orderkey").alias("late_orderkey"), col("num_late_suppliers"))
            )
            per_order = (
                lineitem.join(cand_orders, left_on="l_orderkey", right_on="cand_orderkey", how="semi")
                .group_by("l_orderkey")
                .agg(col("l_suppkey").n_unique().alias("num_suppliers"))
                .select(col("l_orderkey").alias("supp_orderkey"), col("num_suppliers"))
            )
            return _q21_expr_final(candidates, per_order, late_per_order, col, lit)

        if variant == 5:
            targets = (
                supplier.select("s_suppkey", "s_nationkey", "s_name")
                .join(nation.select("n_nationkey", "n_name"), left_on="s_nationkey", right_on="n_nationkey")
                .filter(col("n_name") == lit(nation_name))
                .select(col("s_suppkey").alias("target_suppkey"), col("s_name"))
            )
            valid = (
                orders.select("o_orderkey", "o_orderstatus")
                .filter(col("o_orderstatus") == lit("F"))
                .select(col("o_orderkey").alias("valid_orderkey"))
            )
            candidates = (
                lineitem.select("l_orderkey", "l_suppkey", "l_receiptdate", "l_commitdate")
                .filter(col("l_receiptdate") > col("l_commitdate"))
                .join(targets, left_on="l_suppkey", right_on="target_suppkey")
                .join(valid, left_on="l_orderkey", right_on="valid_orderkey")
                .select("l_orderkey", "l_suppkey", "s_name")
            )
            cand_orders = candidates.select(col("l_orderkey").alias("cand_orderkey")).unique()
            scoped = lineitem.select("l_orderkey", "l_suppkey", "l_receiptdate", "l_commitdate").join(
                cand_orders, left_on="l_orderkey", right_on="cand_orderkey", how="semi"
            )
            per_order = (
                scoped.group_by("l_orderkey")
                .agg(col("l_suppkey").n_unique().alias("num_suppliers"))
                .select(col("l_orderkey").alias("supp_orderkey"), col("num_suppliers"))
            )
            late_per_order = (
                scoped.filter(col("l_receiptdate") > col("l_commitdate"))
                .group_by("l_orderkey")
                .agg(col("l_suppkey").n_unique().alias("num_late_suppliers"))
                .select(col("l_orderkey").alias("late_orderkey"), col("num_late_suppliers"))
            )
            return _q21_expr_final(candidates, per_order, late_per_order, col, lit)

        if variant == 6:
            valid = _q21_expr_valid_orders(orders, col, lit)
            scoped_lineitem = lineitem.join(valid, left_on="l_orderkey", right_on="valid_orderkey", how="semi")
            candidates = _q21_expr_candidates(scoped_lineitem, targets, valid, col)
            cand_orders = candidates.select(col("l_orderkey").alias("cand_orderkey")).unique()
            scoped = scoped_lineitem.join(cand_orders, left_on="l_orderkey", right_on="cand_orderkey", how="semi")
            per_order = (
                scoped.group_by("l_orderkey")
                .agg(col("l_suppkey").n_unique().alias("num_suppliers"))
                .select(col("l_orderkey").alias("supp_orderkey"), col("num_suppliers"))
            )
            late_per_order = (
                scoped.filter(late)
                .group_by("l_orderkey")
                .agg(col("l_suppkey").n_unique().alias("num_late_suppliers"))
                .select(col("l_orderkey").alias("late_orderkey"), col("num_late_suppliers"))
            )
            return _q21_expr_final(candidates, per_order, late_per_order, col, lit)

        if variant == 7:
            candidates = (
                lineitem.filter(col("l_receiptdate") > col("l_commitdate"))
                .join(targets, left_on="l_suppkey", right_on="target_suppkey")
                .join(valid, left_on="l_orderkey", right_on="valid_orderkey")
                .select("l_orderkey", "l_suppkey", "s_name")
            )
            cand_orders = candidates.select(col("l_orderkey").alias("cand_orderkey")).unique()
            return _q21_expr_final(
                candidates,
                lineitem.join(cand_orders, left_on="l_orderkey", right_on="cand_orderkey", how="semi")
                .group_by("l_orderkey")
                .agg(col("l_suppkey").n_unique().alias("num_suppliers"))
                .select(col("l_orderkey").alias("supp_orderkey"), col("num_suppliers")),
                lineitem.filter(late)
                .join(cand_orders, left_on="l_orderkey", right_on="cand_orderkey", how="semi")
                .group_by("l_orderkey")
                .agg(col("l_suppkey").n_unique().alias("num_late_suppliers"))
                .select(col("l_orderkey").alias("late_orderkey"), col("num_late_suppliers")),
                col,
                lit,
            )

        candidates = _q21_expr_candidates(lineitem, targets, valid, col)
        cand_orders = candidates.select(col("l_orderkey").alias("cand_orderkey")).unique()
        scoped = lineitem.join(cand_orders, left_on="l_orderkey", right_on="cand_orderkey", how="semi")
        per_order = (
            scoped.group_by("l_orderkey")
            .agg(col("l_suppkey").n_unique().alias("num_suppliers"))
            .select(col("l_orderkey").alias("supp_orderkey"), col("num_suppliers"))
        )
        late_per_order = (
            scoped.filter(late)
            .group_by("l_orderkey")
            .agg(col("l_suppkey").n_unique().alias("num_late_suppliers"))
            .select(col("l_orderkey").alias("late_orderkey"), col("num_late_suppliers"))
        )

        if variant == 8:
            return (
                candidates.join(per_order, left_on="l_orderkey", right_on="supp_orderkey")
                .join(late_per_order, left_on="l_orderkey", right_on="late_orderkey")
                .filter((col("num_suppliers") > lit(1)) & (col("num_late_suppliers") == lit(1)))
                .group_by("s_name")
                .agg(col("l_orderkey").count().alias("numwait"))
                .sort(["numwait", "s_name"], descending=[True, False])
                .limit(100)
            )

        if variant == 9:
            return (
                candidates.join(late_per_order, left_on="l_orderkey", right_on="late_orderkey")
                .join(per_order, left_on="l_orderkey", right_on="supp_orderkey")
                .filter(col("num_suppliers") > lit(1))
                .filter(col("num_late_suppliers") == lit(1))
                .group_by("s_name")
                .agg(col("l_orderkey").count().alias("numwait"))
                .sort(["numwait", "s_name"], descending=[True, False])
                .limit(100)
            )

        late_orders = scoped.filter(late).select(col("l_orderkey").alias("late_cand_orderkey")).unique()
        narrowed = cand_orders.join(late_orders, left_on="cand_orderkey", right_on="late_cand_orderkey", how="semi")
        scoped_narrow = lineitem.join(narrowed, left_on="l_orderkey", right_on="cand_orderkey", how="semi")
        per_order = (
            scoped_narrow.group_by("l_orderkey")
            .agg(col("l_suppkey").n_unique().alias("num_suppliers"))
            .select(col("l_orderkey").alias("supp_orderkey"), col("num_suppliers"))
        )
        late_per_order = (
            scoped_narrow.filter(late)
            .group_by("l_orderkey")
            .agg(col("l_suppkey").n_unique().alias("num_late_suppliers"))
            .select(col("l_orderkey").alias("late_orderkey"), col("num_late_suppliers"))
        )
        return _q21_expr_final(candidates, per_order, late_per_order, col, lit)

    impl.__name__ = f"q21_v{variant}_expression_impl"
    impl.__qualname__ = impl.__name__
    return impl


def _q21_pandas_targets(supplier: Any, nation: Any, nation_name: str) -> Any:
    supp_nation = supplier.merge(nation, left_on="s_nationkey", right_on="n_nationkey")
    return supp_nation[supp_nation["n_name"] == nation_name][["s_suppkey", "s_name"]]


def _make_q21_pandas_impl(variant: int) -> VariantImpl:
    def impl(ctx: DataFrameContext) -> Any:
        if variant == 1:
            return _q21_pandas_base(ctx)

        supplier = ctx.get_table("supplier")
        lineitem = ctx.get_table("lineitem")
        orders = ctx.get_table("orders")
        nation = ctx.get_table("nation")
        params = get_tpch_parameters(21)
        nation_name = params["nation_name"]

        targets = _q21_pandas_targets(supplier, nation, nation_name)
        valid_keys = _to_list(orders[orders["o_orderstatus"] == "F"]["o_orderkey"])

        def _candidates(li: Any) -> Any:
            late = li[li["l_receiptdate"] > li["l_commitdate"]]
            out = late.merge(targets, left_on="l_suppkey", right_on="s_suppkey")
            return out[out["l_orderkey"].isin(valid_keys)][["l_orderkey", "s_suppkey", "s_name"]]

        def _finish(candidates: Any, scoped: Any) -> Any:
            multi = scoped.groupby("l_orderkey", as_index=False).agg(num_suppliers=("l_suppkey", "nunique"))
            multi_orders = _to_list(multi[multi["num_suppliers"] > 1]["l_orderkey"])
            kept = candidates[candidates["l_orderkey"].isin(multi_orders)]
            all_late = scoped[scoped["l_receiptdate"] > scoped["l_commitdate"]][["l_orderkey", "l_suppkey"]]
            keys = kept[["l_orderkey", "s_suppkey"]].drop_duplicates()
            merged = keys.merge(all_late, on="l_orderkey")
            bad = merged[merged["s_suppkey"] != merged["l_suppkey"]][["l_orderkey", "s_suppkey"]].drop_duplicates()
            bad = bad.copy()
            bad["_exclude"] = True
            kept = kept.merge(bad, on=["l_orderkey", "s_suppkey"], how="left")
            kept = kept[kept["_exclude"].isna()].drop(columns=["_exclude"])
            return (
                kept.groupby("s_name", as_index=False)
                .agg(numwait=("l_orderkey", "count"))
                .sort_values(["numwait", "s_name"], ascending=[False, True])
                .head(100)
            )

        if variant == 2:
            candidates = _candidates(lineitem)
            cand_orders = _to_list(candidates["l_orderkey"].unique())
            scoped = lineitem[lineitem["l_orderkey"].isin(cand_orders)]
            pairs = scoped[["l_orderkey", "l_suppkey"]].drop_duplicates()
            multi = pairs.groupby("l_orderkey").size()
            kept = candidates[candidates["l_orderkey"].isin(_to_list(multi[multi > 1].index))]
            all_late = scoped[scoped["l_receiptdate"] > scoped["l_commitdate"]][["l_orderkey", "l_suppkey"]]
            keys = kept[["l_orderkey", "s_suppkey"]].drop_duplicates()
            merged = keys.merge(all_late, on="l_orderkey")
            bad = merged[merged["s_suppkey"] != merged["l_suppkey"]][["l_orderkey", "s_suppkey"]].drop_duplicates()
            bad = bad.copy()
            bad["_exclude"] = True
            kept = kept.merge(bad, on=["l_orderkey", "s_suppkey"], how="left")
            kept = kept[kept["_exclude"].isna()].drop(columns=["_exclude"])
            return (
                kept.groupby("s_name", as_index=False)
                .agg(numwait=("l_orderkey", "count"))
                .sort_values(["numwait", "s_name"], ascending=[False, True])
                .head(100)
            )

        if variant == 4:
            candidates = _candidates(lineitem)
            cand_orders = _to_list(candidates["l_orderkey"].unique())
            scoped = lineitem[lineitem["l_orderkey"].isin(cand_orders)]
            late_counts = (
                scoped[scoped["l_receiptdate"] > scoped["l_commitdate"]]
                .groupby("l_orderkey", as_index=False)
                .agg(num_late_suppliers=("l_suppkey", "nunique"))
            )
            multi = scoped.groupby("l_orderkey", as_index=False).agg(num_suppliers=("l_suppkey", "nunique"))
            kept = candidates[candidates["l_orderkey"].isin(_to_list(multi[multi["num_suppliers"] > 1]["l_orderkey"]))]
            all_late = scoped[scoped["l_receiptdate"] > scoped["l_commitdate"]][["l_orderkey", "l_suppkey"]]
            keys = kept[["l_orderkey", "s_suppkey"]].drop_duplicates()
            merged = keys.merge(all_late, on="l_orderkey")
            bad = merged[merged["s_suppkey"] != merged["l_suppkey"]][["l_orderkey", "s_suppkey"]].drop_duplicates()
            bad = bad.copy()
            bad["_exclude"] = True
            kept = kept.merge(bad, on=["l_orderkey", "s_suppkey"], how="left")
            kept = kept[kept["_exclude"].isna()].drop(columns=["_exclude"])
            kept = kept.merge(late_counts, on="l_orderkey", how="left")
            kept = kept[kept["num_late_suppliers"] == 1].drop(columns=["num_late_suppliers"])
            return (
                kept.groupby("s_name", as_index=False)
                .agg(numwait=("l_orderkey", "count"))
                .sort_values(["numwait", "s_name"], ascending=[False, True])
                .head(100)
            )

        if variant == 7:
            chained = lineitem[lineitem["l_receiptdate"] > lineitem["l_commitdate"]].merge(
                targets, left_on="l_suppkey", right_on="s_suppkey"
            )
            kept = chained[chained["l_orderkey"].isin(valid_keys)][["l_orderkey", "s_suppkey", "s_name"]]
            cand_orders = _to_list(kept["l_orderkey"].unique())
            scoped = lineitem[lineitem["l_orderkey"].isin(cand_orders)]
            return _finish(kept, scoped)

        if variant == 8:
            candidates = _candidates(lineitem)
            cand_orders = _to_list(candidates["l_orderkey"].unique())
            scoped = lineitem[lineitem["l_orderkey"].isin(cand_orders)]
            multi = scoped.groupby("l_orderkey", as_index=False).agg(num_suppliers=("l_suppkey", "nunique"))
            multi_orders = _to_list(multi[multi["num_suppliers"] > 1]["l_orderkey"])
            all_late = scoped[scoped["l_receiptdate"] > scoped["l_commitdate"]][["l_orderkey", "l_suppkey"]]
            keys = candidates[["l_orderkey", "s_suppkey"]].drop_duplicates()
            merged = keys.merge(all_late, on="l_orderkey")
            bad = merged[merged["s_suppkey"] != merged["l_suppkey"]][["l_orderkey", "s_suppkey"]].drop_duplicates()
            bad = bad.copy()
            bad["_exclude"] = True
            marked = candidates.merge(bad, on=["l_orderkey", "s_suppkey"], how="left")
            kept = marked[(marked["l_orderkey"].isin(multi_orders)) & (marked["_exclude"].isna())].drop(
                columns=["_exclude"]
            )
            return (
                kept.groupby("s_name", as_index=False)
                .agg(numwait=("l_orderkey", "count"))
                .sort_values(["numwait", "s_name"], ascending=[False, True])
                .head(100)
            )

        if variant == 9:
            candidates = _candidates(lineitem)
            cand_orders = _to_list(candidates["l_orderkey"].unique())
            scoped = lineitem[lineitem["l_orderkey"].isin(cand_orders)]
            late_counts = (
                scoped[scoped["l_receiptdate"] > scoped["l_commitdate"]]
                .groupby("l_orderkey")
                .agg(num_late_suppliers=("l_suppkey", "nunique"))
                .reset_index()
            )
            multi = scoped.groupby("l_orderkey").agg(num_suppliers=("l_suppkey", "nunique")).reset_index()
            kept = candidates.merge(late_counts, on="l_orderkey").merge(multi, on="l_orderkey")
            kept = kept[(kept["num_late_suppliers"] == 1) & (kept["num_suppliers"] > 1)].drop(
                columns=["num_late_suppliers", "num_suppliers"]
            )
            all_late = scoped[scoped["l_receiptdate"] > scoped["l_commitdate"]][["l_orderkey", "l_suppkey"]]
            keys = kept[["l_orderkey", "s_suppkey"]].drop_duplicates()
            merged = keys.merge(all_late, on="l_orderkey")
            bad = merged[merged["s_suppkey"] != merged["l_suppkey"]][["l_orderkey", "s_suppkey"]].drop_duplicates()
            bad = bad.copy()
            bad["_exclude"] = True
            kept = kept.merge(bad, on=["l_orderkey", "s_suppkey"], how="left")
            kept = kept[kept["_exclude"].isna()].drop(columns=["_exclude"])
            return (
                kept.groupby("s_name", as_index=False)
                .agg(numwait=("l_orderkey", "count"))
                .sort_values(["numwait", "s_name"], ascending=[False, True])
                .head(100)
            )

        if variant == 10:
            late_targets = lineitem[
                (lineitem["l_receiptdate"] > lineitem["l_commitdate"])
                & (lineitem["l_suppkey"].isin(_to_list(targets["s_suppkey"])))
            ]
            candidates = _candidates(lineitem)
            cand_orders = _to_list(
                late_targets[late_targets["l_orderkey"].isin(_to_list(candidates["l_orderkey"].unique()))][
                    "l_orderkey"
                ].unique()
            )
            scoped = lineitem[lineitem["l_orderkey"].isin(cand_orders)]
            return _finish(candidates[candidates["l_orderkey"].isin(cand_orders)], scoped)

        if variant == 3:
            candidates = _candidates(lineitem)
            cand_orders = _to_list(candidates["l_orderkey"].unique())
            scoped = lineitem[lineitem["l_orderkey"].isin(cand_orders)]
            all_late = scoped[scoped["l_receiptdate"] > scoped["l_commitdate"]][
                ["l_orderkey", "l_suppkey"]
            ].drop_duplicates()
            pairs = candidates[["l_orderkey", "s_suppkey"]].drop_duplicates()
            others = pairs.merge(all_late, on="l_orderkey")
            others = others[others["s_suppkey"] != others["l_suppkey"]][["l_orderkey", "s_suppkey"]].drop_duplicates()
            others = others.copy()
            others["_bad"] = True
            marked = pairs.merge(others, on=["l_orderkey", "s_suppkey"], how="left")
            good = marked[marked["_bad"].isna()][["l_orderkey"]].drop_duplicates()
            kept = candidates[candidates["l_orderkey"].isin(_to_list(good["l_orderkey"]))]
            multi = scoped.groupby("l_orderkey", as_index=False).agg(num_suppliers=("l_suppkey", "nunique"))
            kept = kept[kept["l_orderkey"].isin(_to_list(multi[multi["num_suppliers"] > 1]["l_orderkey"]))]
            return (
                kept.groupby("s_name", as_index=False)
                .agg(numwait=("l_orderkey", "count"))
                .sort_values(["numwait", "s_name"], ascending=[False, True])
                .head(100)
            )

        if variant == 5:
            li = lineitem[["l_orderkey", "l_suppkey", "l_receiptdate", "l_commitdate"]]
            candidates = _candidates(li)
            cand_orders = _to_list(candidates["l_orderkey"].unique())
            return _finish(candidates, li[li["l_orderkey"].isin(cand_orders)])

        li = lineitem[lineitem["l_orderkey"].isin(valid_keys)]
        candidates = _candidates(li)
        cand_orders = _to_list(candidates["l_orderkey"].unique())
        return _finish(candidates, li[li["l_orderkey"].isin(cand_orders)])

    impl.__name__ = f"q21_v{variant}_pandas_impl"
    impl.__qualname__ = impl.__name__
    return impl


_Q21_BASE = get_tpch_query("Q21")

Q21_VARIANTS = build_variants(
    21,
    [(_make_q21_expression_impl(v), _make_q21_pandas_impl(v)) for v in range(1, 11)],
    _DESCRIPTIONS,
    _Q21_BASE.categories,
)
