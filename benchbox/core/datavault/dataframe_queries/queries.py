# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from csv import reader
from datetime import date, timedelta
from typing import Any

from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.pandas_ops import where as pandas_where
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory
from benchbox.core.datavault.dataframe_queries.parameters import get_parameters
from benchbox.core.datavault.dataframe_queries.registry import register_query

_AUDIT_COLUMNS = ("load_dts", "record_source", "load_end_dts", "hashdiff")


def _strip_audit_columns(frame: Any) -> Any:
    drop = [c for c in _AUDIT_COLUMNS if c in frame.columns]
    return frame.drop(*drop) if drop else frame


def _strip_audit_columns_pandas(frame: Any) -> Any:
    drop = [c for c in _AUDIT_COLUMNS if c in frame.columns]
    return frame.drop(columns=drop) if drop else frame


def q1_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q1")
    col, lit = ctx.col, ctx.lit
    cutoff = date(1998, 12, 1) - timedelta(days=params.get("delta", 90))

    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))

    df = ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
    df = df.filter(col("l_shipdate") <= lit(cutoff))

    df = df.with_columns(
        (col("l_extendedprice") * (lit(1) - col("l_discount"))).alias("disc_price"),
        (col("l_extendedprice") * (lit(1) - col("l_discount")) * (lit(1) + col("l_tax"))).alias("charge"),
    )
    result = df.group_by("l_returnflag", "l_linestatus").agg(
        col("l_quantity").sum().alias("sum_qty"),
        col("l_extendedprice").sum().alias("sum_base_price"),
        col("disc_price").sum().alias("sum_disc_price"),
        col("charge").sum().alias("sum_charge"),
        col("l_quantity").mean().alias("avg_qty"),
        col("l_extendedprice").mean().alias("avg_price"),
        col("l_discount").mean().alias("avg_disc"),
        col("l_returnflag").count().alias("count_order"),
    )
    return result.sort("l_returnflag", "l_linestatus")


def q1_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q1")
    cutoff = date(1998, 12, 1) - timedelta(days=params.get("delta", 90))

    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])

    df = ll.merge(sl, on="hk_lineitem_link")
    df = df[df["l_shipdate"] <= cutoff]
    df["disc_price"] = df["l_extendedprice"] * (1 - df["l_discount"])
    df["charge"] = df["disc_price"] * (1 + df["l_tax"])

    result = df.groupby(["l_returnflag", "l_linestatus"]).agg(
        sum_qty=("l_quantity", "sum"),
        sum_base_price=("l_extendedprice", "sum"),
        sum_disc_price=("disc_price", "sum"),
        sum_charge=("charge", "sum"),
        avg_qty=("l_quantity", "mean"),
        avg_price=("l_extendedprice", "mean"),
        avg_disc=("l_discount", "mean"),
        count_order=("l_quantity", "count"),
    )
    return result.reset_index().sort_values(["l_returnflag", "l_linestatus"])


def q2_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q2")
    col, lit = ctx.col, ctx.lit
    size = params.get("size", 15)
    type_suffix = params.get("type_suffix", "BRASS")
    region = params.get("region", "EUROPE")

    hp = _strip_audit_columns(ctx.get_table("hub_part"))
    sp = _strip_audit_columns(ctx.get_table("sat_part").filter(col("load_end_dts").is_null()))
    lps = _strip_audit_columns(ctx.get_table("link_part_supplier"))
    hs = _strip_audit_columns(ctx.get_table("hub_supplier"))
    ss = _strip_audit_columns(ctx.get_table("sat_supplier").filter(col("load_end_dts").is_null()))
    sps = _strip_audit_columns(ctx.get_table("sat_partsupp").filter(col("load_end_dts").is_null()))
    lsn = _strip_audit_columns(ctx.get_table("link_supplier_nation"))
    hn = _strip_audit_columns(ctx.get_table("hub_nation"))
    sn = _strip_audit_columns(ctx.get_table("sat_nation").filter(col("load_end_dts").is_null()))
    lnr = _strip_audit_columns(ctx.get_table("link_nation_region"))
    sr = _strip_audit_columns(ctx.get_table("sat_region").filter(col("load_end_dts").is_null()))

    supplier_region = (
        hs.join(ss, left_on="hk_supplier", right_on="hk_supplier")
        .join(lsn, left_on="hk_supplier", right_on="hk_supplier")
        .join(hn, left_on="hk_nation", right_on="hk_nation")
        .join(sn, left_on="hk_nation", right_on="hk_nation")
        .join(lnr, left_on="hk_nation", right_on="hk_nation")
        .join(sr, left_on="hk_region", right_on="hk_region")
        .filter(col("r_name") == lit(region))
    )

    part_supplier = (
        hp.join(sp, left_on="hk_part", right_on="hk_part")
        .filter((col("p_size") == lit(size)) & col("p_type").str.ends_with(type_suffix))
        .join(lps, left_on="hk_part", right_on="hk_part")
        .join(sps, left_on="hk_part_supplier", right_on="hk_part_supplier")
        .join(supplier_region, left_on="hk_supplier", right_on="hk_supplier")
    )

    min_cost = part_supplier.group_by("p_partkey").agg(
        col("ps_supplycost").min().alias("min_cost"),
    )

    return (
        part_supplier.join(min_cost, left_on="p_partkey", right_on="p_partkey")
        .filter(col("ps_supplycost") == col("min_cost"))
        .select("s_acctbal", "s_name", "n_name", "p_partkey", "p_mfgr", "s_address", "s_phone", "s_comment")
        .sort([("s_acctbal", "desc"), ("n_name", "asc"), ("s_name", "asc"), ("p_partkey", "asc")])
        .limit(100)
    )


def q2_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q2")
    size = params.get("size", 15)
    type_suffix = params.get("type_suffix", "BRASS")
    region = params.get("region", "EUROPE")

    hp = _strip_audit_columns_pandas(ctx.get_table("hub_part"))
    sp = ctx.get_table("sat_part")
    sp = _strip_audit_columns_pandas(sp[sp["load_end_dts"].isna()])
    lps = _strip_audit_columns_pandas(ctx.get_table("link_part_supplier"))
    hs = _strip_audit_columns_pandas(ctx.get_table("hub_supplier"))
    ss = ctx.get_table("sat_supplier")
    ss = _strip_audit_columns_pandas(ss[ss["load_end_dts"].isna()])
    sps = ctx.get_table("sat_partsupp")
    sps = _strip_audit_columns_pandas(sps[sps["load_end_dts"].isna()])
    lsn = _strip_audit_columns_pandas(ctx.get_table("link_supplier_nation"))
    hn = _strip_audit_columns_pandas(ctx.get_table("hub_nation"))
    sn = ctx.get_table("sat_nation")
    sn = _strip_audit_columns_pandas(sn[sn["load_end_dts"].isna()])
    lnr = _strip_audit_columns_pandas(ctx.get_table("link_nation_region"))
    sr = ctx.get_table("sat_region")
    sr = _strip_audit_columns_pandas(sr[sr["load_end_dts"].isna()])

    supplier_region = hs.merge(ss, on="hk_supplier").merge(lsn, on="hk_supplier")
    supplier_region = supplier_region.merge(hn, on="hk_nation").merge(sn, on="hk_nation")
    supplier_region = supplier_region.merge(lnr, on="hk_nation").merge(sr, on="hk_region")
    supplier_region = supplier_region[supplier_region["r_name"] == region]

    parts = hp.merge(sp, on="hk_part")
    parts = parts[(parts["p_size"] == size) & parts["p_type"].str.endswith(type_suffix)]

    ps = parts.merge(lps, on="hk_part").merge(sps, on="hk_part_supplier")
    ps = ps.merge(supplier_region, on="hk_supplier")

    min_cost = ps.groupby("p_partkey")["ps_supplycost"].min().reset_index()
    min_cost.columns = ["p_partkey", "min_cost"]
    result = ps.merge(min_cost, on="p_partkey")
    result = result[result["ps_supplycost"] == result["min_cost"]]

    cols = ["s_acctbal", "s_name", "n_name", "p_partkey", "p_mfgr", "s_address", "s_phone", "s_comment"]
    return (
        result[cols]
        .sort_values(["s_acctbal", "n_name", "s_name", "p_partkey"], ascending=[False, True, True, True])
        .head(100)
    )


def q3_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q3")
    col, lit = ctx.col, ctx.lit
    segment = params.get("segment", "BUILDING")
    order_date = params.get("order_date", date(1995, 3, 15))

    hc = _strip_audit_columns(ctx.get_table("hub_customer"))
    sc = _strip_audit_columns(ctx.get_table("sat_customer").filter(col("load_end_dts").is_null()))
    loc = _strip_audit_columns(ctx.get_table("link_order_customer"))
    ho = _strip_audit_columns(ctx.get_table("hub_order"))
    so = _strip_audit_columns(ctx.get_table("sat_order").filter(col("load_end_dts").is_null()))
    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))

    df = (
        hc.join(sc, left_on="hk_customer", right_on="hk_customer")
        .filter(col("c_mktsegment") == lit(segment))
        .join(loc, left_on="hk_customer", right_on="hk_customer")
        .join(ho, left_on="hk_order", right_on="hk_order")
        .join(so, left_on="hk_order", right_on="hk_order")
        .filter(col("o_orderdate") < lit(order_date))
        .join(ll, left_on="hk_order", right_on="hk_order")
        .join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .filter(col("l_shipdate") > lit(order_date))
    )

    df = df.with_columns((col("l_extendedprice") * (lit(1) - col("l_discount"))).alias("revenue"))
    result = df.group_by("o_orderkey", "o_orderdate", "o_shippriority").agg(
        col("revenue").sum().alias("revenue"),
    )
    result = result.select("o_orderkey", "revenue", "o_orderdate", "o_shippriority")
    return result.sort([("revenue", "desc"), ("o_orderdate", "asc")]).limit(10)


def q3_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q3")
    segment = params.get("segment", "BUILDING")
    order_date = params.get("order_date", date(1995, 3, 15))

    hc = _strip_audit_columns_pandas(ctx.get_table("hub_customer"))
    sc = ctx.get_table("sat_customer")
    sc = _strip_audit_columns_pandas(sc[sc["load_end_dts"].isna()])
    loc = _strip_audit_columns_pandas(ctx.get_table("link_order_customer"))
    ho = _strip_audit_columns_pandas(ctx.get_table("hub_order"))
    so = ctx.get_table("sat_order")
    so = _strip_audit_columns_pandas(so[so["load_end_dts"].isna()])
    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])

    df = hc.merge(sc, on="hk_customer")
    df = df[df["c_mktsegment"] == segment]
    df = df.merge(loc, on="hk_customer").merge(ho, on="hk_order").merge(so, on="hk_order")
    df = df[df["o_orderdate"] < order_date]
    df = df.merge(ll, on="hk_order").merge(sl, on="hk_lineitem_link")
    df = df[df["l_shipdate"] > order_date]

    df["revenue"] = df["l_extendedprice"] * (1 - df["l_discount"])
    result = df.groupby(["o_orderkey", "o_orderdate", "o_shippriority"]).agg(revenue=("revenue", "sum")).reset_index()
    result = result[["o_orderkey", "revenue", "o_orderdate", "o_shippriority"]]
    return result.sort_values(["revenue", "o_orderdate"], ascending=[False, True]).head(10)


def q4_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q4")
    col, lit = ctx.col, ctx.lit
    start_date = params.get("start_date", date(1993, 7, 1))
    end_date = params.get("end_date", date(1993, 10, 1))

    ho = _strip_audit_columns(ctx.get_table("hub_order"))
    so = _strip_audit_columns(ctx.get_table("sat_order").filter(col("load_end_dts").is_null()))
    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))

    late_orders = (
        ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .filter(col("l_commitdate") < col("l_receiptdate"))
        .select("hk_order")
        .unique()
    )

    orders = ho.join(so, left_on="hk_order", right_on="hk_order").filter(
        (col("o_orderdate") >= lit(start_date)) & (col("o_orderdate") < lit(end_date))
    )

    result = (
        orders.join(late_orders, left_on="hk_order", right_on="hk_order")
        .group_by("o_orderpriority")
        .agg(col("hk_order").count().alias("order_count"))
    )
    return result.sort("o_orderpriority")


def q4_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q4")
    start_date = params.get("start_date", date(1993, 7, 1))
    end_date = params.get("end_date", date(1993, 10, 1))

    ho = _strip_audit_columns_pandas(ctx.get_table("hub_order"))
    so = ctx.get_table("sat_order")
    so = _strip_audit_columns_pandas(so[so["load_end_dts"].isna()])
    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])

    late = ll.merge(sl, on="hk_lineitem_link")
    late = late[late["l_commitdate"] < late["l_receiptdate"]]
    late_keys = late[["hk_order"]].drop_duplicates()

    orders = ho.merge(so, on="hk_order")
    orders = orders[(orders["o_orderdate"] >= start_date) & (orders["o_orderdate"] < end_date)]

    result = orders.merge(late_keys, on="hk_order")
    result = result.groupby("o_orderpriority").size().reset_index(name="order_count")
    return result.sort_values("o_orderpriority")


def q5_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q5")
    col, lit = ctx.col, ctx.lit
    region = params.get("region", "ASIA")
    start_date = params.get("start_date", date(1994, 1, 1))
    end_date = params.get("end_date", date(1995, 1, 1))

    sr = _strip_audit_columns(ctx.get_table("sat_region").filter(col("load_end_dts").is_null()))
    lnr = _strip_audit_columns(ctx.get_table("link_nation_region"))
    sn = _strip_audit_columns(ctx.get_table("sat_nation").filter(col("load_end_dts").is_null()))
    lcn = _strip_audit_columns(ctx.get_table("link_customer_nation"))
    loc = _strip_audit_columns(ctx.get_table("link_order_customer"))
    so = _strip_audit_columns(ctx.get_table("sat_order").filter(col("load_end_dts").is_null()))
    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    lsn = _strip_audit_columns(ctx.get_table("link_supplier_nation"))

    nations = (
        sr.filter(col("r_name") == lit(region))
        .join(lnr, left_on="hk_region", right_on="hk_region")
        .join(sn, left_on="hk_nation", right_on="hk_nation")
    )

    cust_orders = (
        nations.join(lcn, left_on="hk_nation", right_on="hk_nation")
        .join(loc, left_on="hk_customer", right_on="hk_customer")
        .join(so, left_on="hk_order", right_on="hk_order")
        .filter((col("o_orderdate") >= lit(start_date)) & (col("o_orderdate") < lit(end_date)))
        .join(ll, left_on="hk_order", right_on="hk_order")
        .join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
    )

    df = cust_orders.join(lsn, left_on="hk_nation", right_on="hk_nation", suffix="_lsn").filter(
        col("hk_supplier") == col("hk_supplier_lsn")
    )

    df = df.with_columns((col("l_extendedprice") * (lit(1) - col("l_discount"))).alias("revenue"))
    result = df.group_by("n_name").agg(
        col("revenue").sum().alias("revenue"),
    )
    return result.sort([("revenue", "desc")])


def q5_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q5")
    region = params.get("region", "ASIA")
    start_date = params.get("start_date", date(1994, 1, 1))
    end_date = params.get("end_date", date(1995, 1, 1))

    sr = ctx.get_table("sat_region")
    sr = _strip_audit_columns_pandas(sr[sr["load_end_dts"].isna()])
    lnr = _strip_audit_columns_pandas(ctx.get_table("link_nation_region"))
    sn = ctx.get_table("sat_nation")
    sn = _strip_audit_columns_pandas(sn[sn["load_end_dts"].isna()])
    lcn = _strip_audit_columns_pandas(ctx.get_table("link_customer_nation"))
    loc = _strip_audit_columns_pandas(ctx.get_table("link_order_customer"))
    so = ctx.get_table("sat_order")
    so = _strip_audit_columns_pandas(so[so["load_end_dts"].isna()])
    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    lsn = _strip_audit_columns_pandas(ctx.get_table("link_supplier_nation"))

    nations = sr[sr["r_name"] == region].merge(lnr, on="hk_region").merge(sn, on="hk_nation")
    df = nations.merge(lcn, on="hk_nation").merge(loc, on="hk_customer")
    df = df.merge(so, on="hk_order")
    df = df[(df["o_orderdate"] >= start_date) & (df["o_orderdate"] < end_date)]
    df = df.merge(ll, on="hk_order").merge(sl, on="hk_lineitem_link")

    df = df.merge(lsn, on="hk_nation", suffixes=("", "_supp"))
    df = df[df["hk_supplier"] == df["hk_supplier_supp"]]

    df["revenue"] = df["l_extendedprice"] * (1 - df["l_discount"])
    result = df.groupby("n_name").agg(revenue=("revenue", "sum")).reset_index()
    return result.sort_values("revenue", ascending=False)


def q6_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q6")
    col, lit = ctx.col, ctx.lit
    start_date = params.get("start_date", date(1994, 1, 1))
    end_date = params.get("end_date", date(1995, 1, 1))
    discount_low = params.get("discount_low", 0.05)
    discount_high = params.get("discount_high", 0.07)
    quantity = params.get("quantity", 24)

    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))

    df = ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link").filter(
        (col("l_shipdate") >= lit(start_date))
        & (col("l_shipdate") < lit(end_date))
        & (col("l_discount") >= lit(discount_low))
        & (col("l_discount") <= lit(discount_high))
        & (col("l_quantity") < lit(quantity))
    )
    return (
        df.with_columns((col("l_extendedprice") * col("l_discount")).alias("charge"))
        .agg(
            col("charge").sum().alias("revenue"),
            col("charge").count().alias("n"),
        )
        .select(ctx.when(col("n") > lit(0)).then(col("revenue")).otherwise(lit(None)).alias("revenue"))
    )


def q6_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q6")
    start_date = params.get("start_date", date(1994, 1, 1))
    end_date = params.get("end_date", date(1995, 1, 1))
    discount_low = params.get("discount_low", 0.05)
    discount_high = params.get("discount_high", 0.07)
    quantity = params.get("quantity", 24)

    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])

    df = ll.merge(sl, on="hk_lineitem_link")
    df = df[
        (df["l_shipdate"] >= start_date)
        & (df["l_shipdate"] < end_date)
        & (df["l_discount"] >= discount_low)
        & (df["l_discount"] <= discount_high)
        & (df["l_quantity"] < quantity)
    ]
    import pandas as pd

    if len(df) == 0:
        return pd.DataFrame({"revenue": [None]})
    return pd.DataFrame({"revenue": [(df["l_extendedprice"] * df["l_discount"]).sum()]})


def q7_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q7")
    col, lit = ctx.col, ctx.lit
    nation1 = params.get("nation1", "FRANCE")
    nation2 = params.get("nation2", "GERMANY")

    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    lsn = _strip_audit_columns(ctx.get_table("link_supplier_nation"))
    sn = _strip_audit_columns(ctx.get_table("sat_nation").filter(col("load_end_dts").is_null()))
    loc = _strip_audit_columns(ctx.get_table("link_order_customer"))
    lcn = _strip_audit_columns(ctx.get_table("link_customer_nation"))

    lineitem = ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link").filter(
        (col("l_shipdate") >= lit(date(1995, 1, 1))) & (col("l_shipdate") <= lit(date(1996, 12, 31)))
    )

    supp_nation = lsn.join(sn, left_on="hk_nation", right_on="hk_nation").rename(
        {"n_name": "supp_nation", "hk_nation": "hk_supp_nation"}
    )

    cust_nation_base = loc.join(lcn, left_on="hk_customer", right_on="hk_customer")
    cust_nation = cust_nation_base.join(sn, left_on="hk_nation", right_on="hk_nation").rename(
        {"n_name": "cust_nation", "hk_nation": "hk_cust_nation"}
    )

    df = (
        lineitem.join(supp_nation, left_on="hk_supplier", right_on="hk_supplier")
        .join(cust_nation, left_on="hk_order", right_on="hk_order")
        .filter(
            ((col("supp_nation") == lit(nation1)) & (col("cust_nation") == lit(nation2)))
            | ((col("supp_nation") == lit(nation2)) & (col("cust_nation") == lit(nation1)))
        )
    )

    result = (
        df.with_columns(col("l_shipdate").dt.year().alias("l_year"))
        .with_columns((col("l_extendedprice") * (lit(1) - col("l_discount"))).alias("revenue"))
        .group_by("supp_nation", "cust_nation", "l_year")
        .agg(
            col("revenue").sum().alias("revenue"),
        )
    )
    return result.sort("supp_nation", "cust_nation", "l_year")


def q7_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q7")
    nation1 = params.get("nation1", "FRANCE")
    nation2 = params.get("nation2", "GERMANY")

    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    lsn = _strip_audit_columns_pandas(ctx.get_table("link_supplier_nation"))
    sn = ctx.get_table("sat_nation")
    sn = _strip_audit_columns_pandas(sn[sn["load_end_dts"].isna()])
    loc = _strip_audit_columns_pandas(ctx.get_table("link_order_customer"))
    lcn = _strip_audit_columns_pandas(ctx.get_table("link_customer_nation"))

    lineitem = ll.merge(sl, on="hk_lineitem_link")
    lineitem = lineitem[(lineitem["l_shipdate"] >= date(1995, 1, 1)) & (lineitem["l_shipdate"] <= date(1996, 12, 31))]

    supp_n = lsn.merge(sn, on="hk_nation").rename(columns={"n_name": "supp_nation"})
    cust_n = loc.merge(lcn, on="hk_customer").merge(sn, on="hk_nation").rename(columns={"n_name": "cust_nation"})

    df = lineitem.merge(supp_n[["hk_supplier", "supp_nation"]], on="hk_supplier")
    df = df.merge(cust_n[["hk_order", "cust_nation"]].drop_duplicates(), on="hk_order")

    mask = ((df["supp_nation"] == nation1) & (df["cust_nation"] == nation2)) | (
        (df["supp_nation"] == nation2) & (df["cust_nation"] == nation1)
    )
    df = df[mask]
    df["l_year"] = df["l_shipdate"].apply(lambda d: d.year if hasattr(d, "year") else d)
    df["volume"] = df["l_extendedprice"] * (1 - df["l_discount"])

    result = df.groupby(["supp_nation", "cust_nation", "l_year"]).agg(revenue=("volume", "sum")).reset_index()
    return result.sort_values(["supp_nation", "cust_nation", "l_year"])


def q8_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q8")
    col, lit = ctx.col, ctx.lit
    target_nation = params.get("nation", "BRAZIL")
    target_region = params.get("region", "AMERICA")
    part_type = params.get("part_type", "ECONOMY ANODIZED STEEL")

    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    sp = _strip_audit_columns(ctx.get_table("sat_part").filter(col("load_end_dts").is_null()))
    lsn = _strip_audit_columns(ctx.get_table("link_supplier_nation"))
    sn = _strip_audit_columns(ctx.get_table("sat_nation").filter(col("load_end_dts").is_null()))
    so = _strip_audit_columns(ctx.get_table("sat_order").filter(col("load_end_dts").is_null()))
    loc = _strip_audit_columns(ctx.get_table("link_order_customer"))
    lcn = _strip_audit_columns(ctx.get_table("link_customer_nation"))
    lnr = _strip_audit_columns(ctx.get_table("link_nation_region"))
    sr = _strip_audit_columns(ctx.get_table("sat_region").filter(col("load_end_dts").is_null()))

    lineitem = (
        ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .join(sp, left_on="hk_part", right_on="hk_part")
        .filter(col("p_type") == lit(part_type))
    )

    supp_nation = lsn.join(sn, left_on="hk_nation", right_on="hk_nation").rename({"n_name": "supp_nation"})

    cust_region = (
        loc.join(lcn, left_on="hk_customer", right_on="hk_customer")
        .join(lnr, left_on="hk_nation", right_on="hk_nation")
        .join(sr, left_on="hk_region", right_on="hk_region")
        .filter(col("r_name") == lit(target_region))
    )

    df = (
        lineitem.join(so, left_on="hk_order", right_on="hk_order")
        .filter((col("o_orderdate") >= lit(date(1995, 1, 1))) & (col("o_orderdate") <= lit(date(1996, 12, 31))))
        .join(cust_region, left_on="hk_order", right_on="hk_order")
        .join(supp_nation, left_on="hk_supplier", right_on="hk_supplier")
    )

    df = df.with_columns(col("o_orderdate").dt.year().alias("o_year")).with_columns(
        (col("l_extendedprice") * (lit(1) - col("l_discount"))).alias("volume")
    )
    df = df.with_columns(
        ctx.when(col("supp_nation") == lit(target_nation)).then(col("volume")).otherwise(lit(0)).alias("nation_vol")
    )

    result = df.group_by("o_year").agg(
        col("nation_vol").sum().alias("nation_vol"),
        col("volume").sum().alias("total_vol"),
    )
    result = result.with_columns((col("nation_vol") / col("total_vol")).alias("mkt_share"))
    return result.select("o_year", "mkt_share").sort("o_year")


def q8_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q8")
    target_nation = params.get("nation", "BRAZIL")
    target_region = params.get("region", "AMERICA")
    part_type = params.get("part_type", "ECONOMY ANODIZED STEEL")

    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    sp = ctx.get_table("sat_part")
    sp = _strip_audit_columns_pandas(sp[sp["load_end_dts"].isna()])
    lsn = _strip_audit_columns_pandas(ctx.get_table("link_supplier_nation"))
    sn = ctx.get_table("sat_nation")
    sn = _strip_audit_columns_pandas(sn[sn["load_end_dts"].isna()])
    so = ctx.get_table("sat_order")
    so = _strip_audit_columns_pandas(so[so["load_end_dts"].isna()])
    loc = _strip_audit_columns_pandas(ctx.get_table("link_order_customer"))
    lcn = _strip_audit_columns_pandas(ctx.get_table("link_customer_nation"))
    lnr = _strip_audit_columns_pandas(ctx.get_table("link_nation_region"))
    sr = ctx.get_table("sat_region")
    sr = _strip_audit_columns_pandas(sr[sr["load_end_dts"].isna()])

    lineitem = ll.merge(sl, on="hk_lineitem_link").merge(sp, on="hk_part")
    lineitem = lineitem[lineitem["p_type"] == part_type]

    supp_n = lsn.merge(sn, on="hk_nation").rename(columns={"n_name": "supp_nation"})
    cust_reg = loc.merge(lcn, on="hk_customer").merge(lnr, on="hk_nation").merge(sr, on="hk_region")
    cust_reg = cust_reg[cust_reg["r_name"] == target_region]

    df = lineitem.merge(so, on="hk_order")
    df = df[(df["o_orderdate"] >= date(1995, 1, 1)) & (df["o_orderdate"] <= date(1996, 12, 31))]
    df = df.merge(cust_reg[["hk_order"]].drop_duplicates(), on="hk_order")
    df = df.merge(supp_n[["hk_supplier", "supp_nation"]], on="hk_supplier")

    df["o_year"] = df["o_orderdate"].apply(lambda d: d.year if hasattr(d, "year") else d)
    df["volume"] = df["l_extendedprice"] * (1 - df["l_discount"])

    df["nation_vol"] = pandas_where(df["supp_nation"] == target_nation, df["volume"], 0)
    result = df.groupby("o_year").agg(nation_vol=("nation_vol", "sum"), total_vol=("volume", "sum")).reset_index()
    result["mkt_share"] = result["nation_vol"] / result["total_vol"]
    return result[["o_year", "mkt_share"]].sort_values("o_year")


def q9_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q9")
    col, lit = ctx.col, ctx.lit
    color = params.get("color", "green")

    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    sp = _strip_audit_columns(ctx.get_table("sat_part").filter(col("load_end_dts").is_null()))
    lps = _strip_audit_columns(ctx.get_table("link_part_supplier"))
    sps = _strip_audit_columns(ctx.get_table("sat_partsupp").filter(col("load_end_dts").is_null()))
    lsn = _strip_audit_columns(ctx.get_table("link_supplier_nation"))
    sn = _strip_audit_columns(ctx.get_table("sat_nation").filter(col("load_end_dts").is_null()))
    so = _strip_audit_columns(ctx.get_table("sat_order").filter(col("load_end_dts").is_null()))

    df = (
        ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .join(sp, left_on="hk_part", right_on="hk_part")
        .filter(col("p_name").str.contains(color))
        .join(lps, left_on=["hk_part", "hk_supplier"], right_on=["hk_part", "hk_supplier"])
        .join(sps, left_on="hk_part_supplier", right_on="hk_part_supplier")
        .join(lsn, left_on="hk_supplier", right_on="hk_supplier")
        .join(sn, left_on="hk_nation", right_on="hk_nation")
        .join(so, left_on="hk_order", right_on="hk_order")
    )

    df = df.with_columns(col("o_orderdate").dt.year().alias("o_year")).with_columns(
        (col("l_extendedprice") * (lit(1) - col("l_discount")) - col("ps_supplycost") * col("l_quantity")).alias(
            "amount"
        )
    )

    result = df.group_by("n_name", "o_year").agg(col("amount").sum().alias("sum_profit"))
    return result.sort([("n_name", "asc"), ("o_year", "desc")])


def q9_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q9")
    color = params.get("color", "green")

    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    sp = ctx.get_table("sat_part")
    sp = _strip_audit_columns_pandas(sp[sp["load_end_dts"].isna()])
    lps = _strip_audit_columns_pandas(ctx.get_table("link_part_supplier"))
    sps = ctx.get_table("sat_partsupp")
    sps = _strip_audit_columns_pandas(sps[sps["load_end_dts"].isna()])
    lsn = _strip_audit_columns_pandas(ctx.get_table("link_supplier_nation"))
    sn = ctx.get_table("sat_nation")
    sn = _strip_audit_columns_pandas(sn[sn["load_end_dts"].isna()])
    so = ctx.get_table("sat_order")
    so = _strip_audit_columns_pandas(so[so["load_end_dts"].isna()])

    df = ll.merge(sl, on="hk_lineitem_link").merge(sp, on="hk_part")
    df = df[df["p_name"].str.contains(color, na=False)]
    df = df.merge(lps, on=["hk_part", "hk_supplier"]).merge(sps, on="hk_part_supplier")
    df = df.merge(lsn, on="hk_supplier").merge(sn, on="hk_nation").merge(so, on="hk_order")

    df["o_year"] = df["o_orderdate"].apply(lambda d: d.year if hasattr(d, "year") else d)
    df["amount"] = df["l_extendedprice"] * (1 - df["l_discount"]) - df["ps_supplycost"] * df["l_quantity"]

    result = df.groupby(["n_name", "o_year"]).agg(sum_profit=("amount", "sum")).reset_index()
    return result.sort_values(["n_name", "o_year"], ascending=[True, False])


def q10_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q10")
    col, lit = ctx.col, ctx.lit
    start_date = params.get("start_date", date(1993, 10, 1))
    end_date = params.get("end_date", date(1994, 1, 1))

    hc = _strip_audit_columns(ctx.get_table("hub_customer"))
    sc = _strip_audit_columns(ctx.get_table("sat_customer").filter(col("load_end_dts").is_null()))
    loc = _strip_audit_columns(ctx.get_table("link_order_customer"))
    so = _strip_audit_columns(ctx.get_table("sat_order").filter(col("load_end_dts").is_null()))
    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    lcn = _strip_audit_columns(ctx.get_table("link_customer_nation"))
    sn = _strip_audit_columns(ctx.get_table("sat_nation").filter(col("load_end_dts").is_null()))

    df = (
        hc.join(sc, left_on="hk_customer", right_on="hk_customer")
        .join(loc, left_on="hk_customer", right_on="hk_customer")
        .join(so, left_on="hk_order", right_on="hk_order")
        .filter((col("o_orderdate") >= lit(start_date)) & (col("o_orderdate") < lit(end_date)))
        .join(ll, left_on="hk_order", right_on="hk_order")
        .join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .filter(col("l_returnflag") == lit("R"))
        .join(lcn, left_on="hk_customer", right_on="hk_customer")
        .join(sn, left_on="hk_nation", right_on="hk_nation")
    )

    df = df.with_columns((col("l_extendedprice") * (lit(1) - col("l_discount"))).alias("revenue"))
    result = df.group_by("c_custkey", "c_name", "c_acctbal", "c_phone", "n_name", "c_address", "c_comment").agg(
        col("revenue").sum().alias("revenue"),
    )
    result = result.select("c_custkey", "c_name", "revenue", "c_acctbal", "n_name", "c_address", "c_phone", "c_comment")
    return result.sort([("revenue", "desc")]).limit(20)


def q10_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q10")
    start_date = params.get("start_date", date(1993, 10, 1))
    end_date = params.get("end_date", date(1994, 1, 1))

    hc = _strip_audit_columns_pandas(ctx.get_table("hub_customer"))
    sc = ctx.get_table("sat_customer")
    sc = _strip_audit_columns_pandas(sc[sc["load_end_dts"].isna()])
    loc = _strip_audit_columns_pandas(ctx.get_table("link_order_customer"))
    so = ctx.get_table("sat_order")
    so = _strip_audit_columns_pandas(so[so["load_end_dts"].isna()])
    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    lcn = _strip_audit_columns_pandas(ctx.get_table("link_customer_nation"))
    sn = ctx.get_table("sat_nation")
    sn = _strip_audit_columns_pandas(sn[sn["load_end_dts"].isna()])

    df = hc.merge(sc, on="hk_customer").merge(loc, on="hk_customer")
    df = df.merge(so, on="hk_order")
    df = df[(df["o_orderdate"] >= start_date) & (df["o_orderdate"] < end_date)]
    df = df.merge(ll, on="hk_order").merge(sl, on="hk_lineitem_link")
    df = df[df["l_returnflag"] == "R"]
    df = df.merge(lcn, on="hk_customer").merge(sn, on="hk_nation")

    df["revenue"] = df["l_extendedprice"] * (1 - df["l_discount"])
    grp = ["c_custkey", "c_name", "c_acctbal", "c_phone", "n_name", "c_address", "c_comment"]
    result = df.groupby(grp).agg(revenue=("revenue", "sum")).reset_index()
    result = result[["c_custkey", "c_name", "revenue", "c_acctbal", "n_name", "c_address", "c_phone", "c_comment"]]
    return result.sort_values("revenue", ascending=False).head(20)


def q11_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q11")
    col, lit = ctx.col, ctx.lit
    nation = params.get("nation", "GERMANY")
    fraction = params.get("fraction", 0.0001)

    lps = _strip_audit_columns(ctx.get_table("link_part_supplier"))
    sps = _strip_audit_columns(ctx.get_table("sat_partsupp").filter(col("load_end_dts").is_null()))
    lsn = _strip_audit_columns(ctx.get_table("link_supplier_nation"))
    sn = _strip_audit_columns(ctx.get_table("sat_nation").filter(col("load_end_dts").is_null()))
    hp = _strip_audit_columns(ctx.get_table("hub_part"))

    df = (
        lps.join(sps, left_on="hk_part_supplier", right_on="hk_part_supplier")
        .join(lsn, left_on="hk_supplier", right_on="hk_supplier")
        .join(sn, left_on="hk_nation", right_on="hk_nation")
        .filter(col("n_name") == lit(nation))
        .join(hp, left_on="hk_part", right_on="hk_part")
    )

    df = df.with_columns((col("ps_supplycost") * col("ps_availqty")).alias("stock_value"))

    total = df.agg(col("stock_value").sum().alias("total_value"))
    threshold = ctx.scalar(total, "total_value") * fraction

    by_part = df.group_by("p_partkey").agg(
        col("stock_value").sum().alias("value"),
    )
    result = by_part.filter(col("value") > lit(threshold))
    return result.sort([("value", "desc")])


def q11_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q11")
    nation = params.get("nation", "GERMANY")
    fraction = params.get("fraction", 0.0001)

    lps = _strip_audit_columns_pandas(ctx.get_table("link_part_supplier"))
    sps = ctx.get_table("sat_partsupp")
    sps = _strip_audit_columns_pandas(sps[sps["load_end_dts"].isna()])
    lsn = _strip_audit_columns_pandas(ctx.get_table("link_supplier_nation"))
    sn = ctx.get_table("sat_nation")
    sn = _strip_audit_columns_pandas(sn[sn["load_end_dts"].isna()])
    hp = _strip_audit_columns_pandas(ctx.get_table("hub_part"))

    df = lps.merge(sps, on="hk_part_supplier").merge(lsn, on="hk_supplier").merge(sn, on="hk_nation")
    df = df[df["n_name"] == nation]
    df = df.merge(hp[["hk_part", "p_partkey"]], on="hk_part")

    df["value"] = df["ps_supplycost"] * df["ps_availqty"]
    threshold = df["value"].sum() * fraction

    by_part = df.groupby("p_partkey").agg(value=("value", "sum")).reset_index()
    result = by_part[by_part["value"] > threshold]
    return result.sort_values("value", ascending=False)


def q12_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q12")
    col, lit = ctx.col, ctx.lit
    shipmode1 = params.get("shipmode1", "MAIL")
    shipmode2 = params.get("shipmode2", "SHIP")
    start_date = params.get("start_date", date(1994, 1, 1))
    end_date = params.get("end_date", date(1995, 1, 1))

    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    so = _strip_audit_columns(ctx.get_table("sat_order").filter(col("load_end_dts").is_null()))

    df = (
        ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .join(so, left_on="hk_order", right_on="hk_order")
        .filter(
            col("l_shipmode").is_in([shipmode1, shipmode2])
            & (col("l_commitdate") < col("l_receiptdate"))
            & (col("l_shipdate") < col("l_commitdate"))
            & (col("l_receiptdate") >= lit(start_date))
            & (col("l_receiptdate") < lit(end_date))
        )
    )

    result = df.group_by("l_shipmode").agg(
        ctx.when((col("o_orderpriority") == lit("1-URGENT")) | (col("o_orderpriority") == lit("2-HIGH")))
        .then(lit(1))
        .otherwise(lit(0))
        .sum()
        .alias("high_line_count"),
        ctx.when((col("o_orderpriority") != lit("1-URGENT")) & (col("o_orderpriority") != lit("2-HIGH")))
        .then(lit(1))
        .otherwise(lit(0))
        .sum()
        .alias("low_line_count"),
    )
    return result.sort("l_shipmode")


def q12_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q12")
    shipmode1 = params.get("shipmode1", "MAIL")
    shipmode2 = params.get("shipmode2", "SHIP")
    start_date = params.get("start_date", date(1994, 1, 1))
    end_date = params.get("end_date", date(1995, 1, 1))

    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    so = ctx.get_table("sat_order")
    so = _strip_audit_columns_pandas(so[so["load_end_dts"].isna()])

    df = ll.merge(sl, on="hk_lineitem_link").merge(so, on="hk_order")
    df = df[
        df["l_shipmode"].isin([shipmode1, shipmode2])
        & (df["l_commitdate"] < df["l_receiptdate"])
        & (df["l_shipdate"] < df["l_commitdate"])
        & (df["l_receiptdate"] >= start_date)
        & (df["l_receiptdate"] < end_date)
    ]

    df["high"] = pandas_where(df["o_orderpriority"].isin(["1-URGENT", "2-HIGH"]), 1, 0)
    df["low"] = pandas_where(~df["o_orderpriority"].isin(["1-URGENT", "2-HIGH"]), 1, 0)
    result = df.groupby("l_shipmode").agg(high_line_count=("high", "sum"), low_line_count=("low", "sum")).reset_index()
    return result.sort_values("l_shipmode")


def q13_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q13")
    col = ctx.col
    word1 = params.get("word1", "special")
    word2 = params.get("word2", "requests")
    pattern = f"{word1}.*{word2}"

    hc = _strip_audit_columns(ctx.get_table("hub_customer"))
    loc = _strip_audit_columns(ctx.get_table("link_order_customer"))
    so = _strip_audit_columns(ctx.get_table("sat_order").filter(col("load_end_dts").is_null()))

    valid_orders = so.filter(~col("o_comment").str.contains(pattern))
    order_customer = loc.join(valid_orders, left_on="hk_order", right_on="hk_order")

    cust_orders = hc.join(order_customer, left_on="hk_customer", right_on="hk_customer", how="left")
    c_counts = cust_orders.group_by("c_custkey").agg(col("hk_order").count().alias("c_count"))

    result = c_counts.group_by("c_count").agg(col("c_custkey").count().alias("custdist"))
    return result.sort([("custdist", "desc"), ("c_count", "desc")])


def q13_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q13")
    word1 = params.get("word1", "special")
    word2 = params.get("word2", "requests")
    pattern = f"{word1}.*{word2}"

    hc = _strip_audit_columns_pandas(ctx.get_table("hub_customer"))
    loc = _strip_audit_columns_pandas(ctx.get_table("link_order_customer"))
    so = ctx.get_table("sat_order")
    so = _strip_audit_columns_pandas(so[so["load_end_dts"].isna()])

    valid_orders = so[~so["o_comment"].str.contains(pattern, na=False, regex=True)]
    order_cust = loc.merge(valid_orders, on="hk_order")

    cust_orders = hc.merge(order_cust, on="hk_customer", how="left")
    c_counts = cust_orders.groupby("c_custkey")["hk_order"].count().reset_index(name="c_count")

    result = c_counts.groupby("c_count").size().reset_index(name="custdist")
    return result.sort_values(["custdist", "c_count"], ascending=[False, False])


def q14_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q14")
    col, lit = ctx.col, ctx.lit
    start_date = params.get("start_date", date(1995, 9, 1))
    end_date = params.get("end_date", date(1995, 10, 1))

    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    sp = _strip_audit_columns(ctx.get_table("sat_part").filter(col("load_end_dts").is_null()))

    df = (
        ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .join(sp, left_on="hk_part", right_on="hk_part")
        .filter((col("l_shipdate") >= lit(start_date)) & (col("l_shipdate") < lit(end_date)))
    )

    df = df.with_columns((col("l_extendedprice") * (lit(1) - col("l_discount"))).alias("revenue"))
    df = df.with_columns(
        ctx.when(col("p_type").str.starts_with("PROMO")).then(col("revenue")).otherwise(lit(0)).alias("promo_revenue")
    )
    return df.agg(
        col("promo_revenue").sum().alias("promo_sum"),
        col("revenue").sum().alias("revenue_sum"),
        col("revenue").count().alias("n"),
    ).select(
        ctx.when(col("n") > lit(0))
        .then(col("promo_sum") * lit(100.0) / col("revenue_sum"))
        .otherwise(lit(None))
        .alias("promo_revenue")
    )


def q14_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q14")
    start_date = params.get("start_date", date(1995, 9, 1))
    end_date = params.get("end_date", date(1995, 10, 1))

    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    sp = ctx.get_table("sat_part")
    sp = _strip_audit_columns_pandas(sp[sp["load_end_dts"].isna()])

    df = ll.merge(sl, on="hk_lineitem_link").merge(sp, on="hk_part")
    df = df[(df["l_shipdate"] >= start_date) & (df["l_shipdate"] < end_date)]

    import pandas as pd

    df["revenue"] = df["l_extendedprice"] * (1 - df["l_discount"])
    df["promo_revenue"] = pandas_where(df["p_type"].str.startswith("PROMO"), df["revenue"], 0)
    if len(df) == 0:
        return pd.DataFrame({"promo_revenue": [None]})
    revenue_sum = float(df["revenue"].sum())
    if revenue_sum == 0:
        return pd.DataFrame({"promo_revenue": [float("nan")]})
    promo_pct = 100.0 * float(df["promo_revenue"].sum()) / revenue_sum
    return pd.DataFrame({"promo_revenue": [promo_pct]})


def q15_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q15")
    col, lit = ctx.col, ctx.lit
    start_date = params.get("start_date", date(1996, 1, 1))
    end_date = params.get("end_date", date(1996, 4, 1))

    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    hs = _strip_audit_columns(ctx.get_table("hub_supplier"))
    ss = _strip_audit_columns(ctx.get_table("sat_supplier").filter(col("load_end_dts").is_null()))

    revenue = (
        ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .filter((col("l_shipdate") >= lit(start_date)) & (col("l_shipdate") < lit(end_date)))
        .with_columns((col("l_extendedprice") * (lit(1) - col("l_discount"))).alias("revenue"))
        .group_by("hk_supplier")
        .agg(col("revenue").sum().alias("total_revenue"))
    )

    max_rev = ctx.scalar(revenue.agg(col("total_revenue").max().alias("max_rev")), "max_rev")

    suppliers = hs.join(ss, left_on="hk_supplier", right_on="hk_supplier").join(
        revenue, left_on="hk_supplier", right_on="hk_supplier"
    )
    if max_rev is None:
        return (
            suppliers.filter(lit(False))
            .select("s_suppkey", "s_name", "s_address", "s_phone", "total_revenue")
            .sort("s_suppkey")
        )
    return (
        suppliers.filter((col("total_revenue") - lit(max_rev)).abs() < lit(5e-5))
        .select("s_suppkey", "s_name", "s_address", "s_phone", "total_revenue")
        .sort("s_suppkey")
    )


def q15_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q15")
    start_date = params.get("start_date", date(1996, 1, 1))
    end_date = params.get("end_date", date(1996, 4, 1))

    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    hs = _strip_audit_columns_pandas(ctx.get_table("hub_supplier"))
    ss = ctx.get_table("sat_supplier")
    ss = _strip_audit_columns_pandas(ss[ss["load_end_dts"].isna()])

    li = ll.merge(sl, on="hk_lineitem_link")
    li = li[(li["l_shipdate"] >= start_date) & (li["l_shipdate"] < end_date)]
    li["rev"] = li["l_extendedprice"] * (1 - li["l_discount"])
    revenue = li.groupby("hk_supplier").agg(total_revenue=("rev", "sum")).reset_index()

    max_rev = revenue["total_revenue"].max()
    top = revenue[revenue["total_revenue"] == max_rev]

    suppliers = hs.merge(ss, on="hk_supplier")
    result = suppliers.merge(top, on="hk_supplier")
    return result[["s_suppkey", "s_name", "s_address", "s_phone", "total_revenue"]].sort_values("s_suppkey")


def q16_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q16")
    col, lit = ctx.col, ctx.lit
    brand = params.get("brand", "Brand#45")
    type_prefix = params.get("type_prefix", "MEDIUM POLISHED")
    sizes = params.get("sizes", [49, 14, 23, 45, 19, 3, 36, 9])

    sp = _strip_audit_columns(ctx.get_table("sat_part").filter(col("load_end_dts").is_null()))
    lps = _strip_audit_columns(ctx.get_table("link_part_supplier"))
    ss = _strip_audit_columns(ctx.get_table("sat_supplier").filter(col("load_end_dts").is_null()))

    bad_suppliers = ss.filter(col("s_comment").str.contains("Customer.*Complaints"))

    parts = sp.filter(
        (col("p_brand") != lit(brand)) & ~col("p_type").str.starts_with(type_prefix) & col("p_size").is_in(sizes)
    )

    df = parts.join(lps, left_on="hk_part", right_on="hk_part")

    df = df.join(bad_suppliers.select("hk_supplier"), left_on="hk_supplier", right_on="hk_supplier", how="anti")

    result = df.group_by("p_brand", "p_type", "p_size").agg(
        col("hk_supplier").n_unique().alias("supplier_cnt"),
    )
    return result.sort([("supplier_cnt", "desc"), ("p_brand", "asc"), ("p_type", "asc"), ("p_size", "asc")])


def q16_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q16")
    brand = params.get("brand", "Brand#45")
    type_prefix = params.get("type_prefix", "MEDIUM POLISHED")
    sizes = params.get("sizes", [49, 14, 23, 45, 19, 3, 36, 9])

    sp = ctx.get_table("sat_part")
    sp = _strip_audit_columns_pandas(sp[sp["load_end_dts"].isna()])
    lps = _strip_audit_columns_pandas(ctx.get_table("link_part_supplier"))
    ss = ctx.get_table("sat_supplier")
    ss = _strip_audit_columns_pandas(ss[ss["load_end_dts"].isna()])

    bad = ss[ss["s_comment"].str.contains("Customer.*Complaints", na=False, regex=True)]["hk_supplier"]
    parts = sp[(sp["p_brand"] != brand) & ~sp["p_type"].str.startswith(type_prefix) & sp["p_size"].isin(sizes)]

    df = parts.merge(lps, on="hk_part")
    df = df[~df["hk_supplier"].isin(bad)]

    result = df.groupby(["p_brand", "p_type", "p_size"]).agg(supplier_cnt=("hk_supplier", "nunique")).reset_index()
    return result.sort_values(["supplier_cnt", "p_brand", "p_type", "p_size"], ascending=[False, True, True, True])


def q17_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q17")
    col, lit = ctx.col, ctx.lit
    brand = params.get("brand", "Brand#23")
    container = params.get("container", "MED BOX")

    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    sp = _strip_audit_columns(ctx.get_table("sat_part").filter(col("load_end_dts").is_null()))

    df = (
        ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .join(sp, left_on="hk_part", right_on="hk_part")
        .filter((col("p_brand") == lit(brand)) & (col("p_container") == lit(container)))
    )

    avg_qty = (
        df.group_by("hk_part")
        .agg(col("l_quantity").mean().alias("mean_qty"))
        .with_columns((col("mean_qty") * lit(0.2)).alias("avg_qty"))
        .drop("mean_qty")
    )

    filtered = df.join(avg_qty, left_on="hk_part", right_on="hk_part").filter(col("l_quantity") < col("avg_qty"))
    return filtered.agg(
        col("l_extendedprice").sum().alias("total"),
        col("l_extendedprice").count().alias("n"),
    ).select(ctx.when(col("n") > lit(0)).then(col("total") / lit(7.0)).otherwise(lit(None)).alias("avg_yearly"))


def q17_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q17")
    brand = params.get("brand", "Brand#23")
    container = params.get("container", "MED BOX")

    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    sp = ctx.get_table("sat_part")
    sp = _strip_audit_columns_pandas(sp[sp["load_end_dts"].isna()])

    df = ll.merge(sl, on="hk_lineitem_link").merge(sp, on="hk_part")
    df = df[(df["p_brand"] == brand) & (df["p_container"] == container)]

    avg_qty = df.groupby("hk_part")["l_quantity"].mean().reset_index()
    avg_qty.columns = ["hk_part", "avg_qty"]
    avg_qty["avg_qty"] = avg_qty["avg_qty"] * 0.2

    df = df.merge(avg_qty, on="hk_part")
    df = df[df["l_quantity"] < df["avg_qty"]]

    import pandas as pd

    if len(df) == 0:
        return pd.DataFrame({"avg_yearly": [None]})
    return pd.DataFrame({"avg_yearly": [df["l_extendedprice"].sum() / 7.0]})


def q18_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q18")
    col, lit = ctx.col, ctx.lit
    quantity = params.get("quantity", 300)

    hc = _strip_audit_columns(ctx.get_table("hub_customer"))
    sc = _strip_audit_columns(ctx.get_table("sat_customer").filter(col("load_end_dts").is_null()))
    loc = _strip_audit_columns(ctx.get_table("link_order_customer"))
    ho = _strip_audit_columns(ctx.get_table("hub_order"))
    so = _strip_audit_columns(ctx.get_table("sat_order").filter(col("load_end_dts").is_null()))
    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))

    order_qty = (
        ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .group_by("hk_order")
        .agg(col("l_quantity").sum().alias("total_qty"))
        .filter(col("total_qty") > lit(quantity))
    )

    df = (
        hc.join(sc, left_on="hk_customer", right_on="hk_customer")
        .join(loc, left_on="hk_customer", right_on="hk_customer")
        .join(ho, left_on="hk_order", right_on="hk_order")
        .join(so, left_on="hk_order", right_on="hk_order")
        .join(order_qty, left_on="hk_order", right_on="hk_order")
    )

    result = df.select("c_name", "c_custkey", "o_orderkey", "o_orderdate", "o_totalprice", "total_qty")
    return result.sort([("o_totalprice", "desc"), ("o_orderdate", "asc")]).limit(100)


def q18_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q18")
    quantity = params.get("quantity", 300)

    hc = _strip_audit_columns_pandas(ctx.get_table("hub_customer"))
    sc = ctx.get_table("sat_customer")
    sc = _strip_audit_columns_pandas(sc[sc["load_end_dts"].isna()])
    loc = _strip_audit_columns_pandas(ctx.get_table("link_order_customer"))
    ho = _strip_audit_columns_pandas(ctx.get_table("hub_order"))
    so = ctx.get_table("sat_order")
    so = _strip_audit_columns_pandas(so[so["load_end_dts"].isna()])
    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])

    li = ll.merge(sl, on="hk_lineitem_link")
    order_qty = li.groupby("hk_order")["l_quantity"].sum().reset_index()
    order_qty.columns = ["hk_order", "total_qty"]
    order_qty = order_qty[order_qty["total_qty"] > quantity]

    df = hc.merge(sc, on="hk_customer").merge(loc, on="hk_customer")
    df = df.merge(ho, on="hk_order").merge(so, on="hk_order").merge(order_qty, on="hk_order")

    cols = ["c_name", "c_custkey", "o_orderkey", "o_orderdate", "o_totalprice", "total_qty"]
    return df[cols].sort_values(["o_totalprice", "o_orderdate"], ascending=[False, True]).head(100)


def q19_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q19")
    col, lit = ctx.col, ctx.lit

    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    sp = _strip_audit_columns(ctx.get_table("sat_part").filter(col("load_end_dts").is_null()))

    df = ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link").join(
        sp, left_on="hk_part", right_on="hk_part"
    )

    air_modes = col("l_shipmode").is_in(["AIR", "AIR REG"])
    deliver = col("l_shipinstruct") == lit("DELIVER IN PERSON")

    cond1 = (
        (col("p_brand") == lit(params.get("brand1")))
        & col("p_container").is_in(["SM CASE", "SM BOX", "SM PACK", "SM PKG"])
        & (col("l_quantity") >= lit(params.get("quantity1")))
        & (col("l_quantity") <= lit(params.get("quantity1") + 10))
        & (col("p_size") >= lit(1))
        & (col("p_size") <= lit(5))
        & air_modes
        & deliver
    )
    cond2 = (
        (col("p_brand") == lit(params.get("brand2")))
        & col("p_container").is_in(["MED BAG", "MED BOX", "MED PKG", "MED PACK"])
        & (col("l_quantity") >= lit(params.get("quantity2")))
        & (col("l_quantity") <= lit(params.get("quantity2") + 10))
        & (col("p_size") >= lit(1))
        & (col("p_size") <= lit(10))
        & air_modes
        & deliver
    )
    cond3 = (
        (col("p_brand") == lit(params.get("brand3")))
        & col("p_container").is_in(["LG CASE", "LG BOX", "LG PACK", "LG PKG"])
        & (col("l_quantity") >= lit(params.get("quantity3")))
        & (col("l_quantity") <= lit(params.get("quantity3") + 10))
        & (col("p_size") >= lit(1))
        & (col("p_size") <= lit(15))
        & air_modes
        & deliver
    )

    return (
        df.filter(cond1 | cond2 | cond3)
        .with_columns((col("l_extendedprice") * (lit(1) - col("l_discount"))).alias("revenue"))
        .agg(
            col("revenue").sum().alias("revenue"),
            col("revenue").count().alias("n"),
        )
        .select(ctx.when(col("n") > lit(0)).then(col("revenue")).otherwise(lit(None)).alias("revenue"))
    )


def q19_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q19")

    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    sp = ctx.get_table("sat_part")
    sp = _strip_audit_columns_pandas(sp[sp["load_end_dts"].isna()])

    df = ll.merge(sl, on="hk_lineitem_link").merge(sp, on="hk_part")

    air = df["l_shipmode"].isin(["AIR", "AIR REG"])
    deliver = df["l_shipinstruct"] == "DELIVER IN PERSON"

    c1 = (
        (df["p_brand"] == params.get("brand1"))
        & df["p_container"].isin(["SM CASE", "SM BOX", "SM PACK", "SM PKG"])
        & (df["l_quantity"] >= params.get("quantity1"))
        & (df["l_quantity"] <= params.get("quantity1") + 10)
        & (df["p_size"] >= 1)
        & (df["p_size"] <= 5)
        & air
        & deliver
    )
    c2 = (
        (df["p_brand"] == params.get("brand2"))
        & df["p_container"].isin(["MED BAG", "MED BOX", "MED PKG", "MED PACK"])
        & (df["l_quantity"] >= params.get("quantity2"))
        & (df["l_quantity"] <= params.get("quantity2") + 10)
        & (df["p_size"] >= 1)
        & (df["p_size"] <= 10)
        & air
        & deliver
    )
    c3 = (
        (df["p_brand"] == params.get("brand3"))
        & df["p_container"].isin(["LG CASE", "LG BOX", "LG PACK", "LG PKG"])
        & (df["l_quantity"] >= params.get("quantity3"))
        & (df["l_quantity"] <= params.get("quantity3") + 10)
        & (df["p_size"] >= 1)
        & (df["p_size"] <= 15)
        & air
        & deliver
    )

    filtered = df[c1 | c2 | c3]
    import pandas as pd

    if len(filtered) == 0:
        return pd.DataFrame({"revenue": [None]})
    return pd.DataFrame({"revenue": [(filtered["l_extendedprice"] * (1 - filtered["l_discount"])).sum()]})


def q20_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q20")
    col, lit = ctx.col, ctx.lit
    color = params.get("color", "forest")
    start_date = params.get("start_date", date(1994, 1, 1))
    end_date = params.get("end_date", date(1995, 1, 1))
    nation = params.get("nation", "CANADA")

    ss = _strip_audit_columns(ctx.get_table("sat_supplier").filter(col("load_end_dts").is_null()))
    lsn = _strip_audit_columns(ctx.get_table("link_supplier_nation"))
    sn = _strip_audit_columns(ctx.get_table("sat_nation").filter(col("load_end_dts").is_null()))
    lps = _strip_audit_columns(ctx.get_table("link_part_supplier"))
    sps = _strip_audit_columns(ctx.get_table("sat_partsupp").filter(col("load_end_dts").is_null()))
    sp = _strip_audit_columns(ctx.get_table("sat_part").filter(col("load_end_dts").is_null()))
    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))

    color_parts = sp.filter(col("p_name").str.starts_with(color))

    li_qty = (
        ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
        .filter((col("l_shipdate") >= lit(start_date)) & (col("l_shipdate") < lit(end_date)))
        .group_by("hk_part", "hk_supplier")
        .agg(col("l_quantity").sum().alias("total_qty"))
    )

    excess = (
        lps.join(sps, left_on="hk_part_supplier", right_on="hk_part_supplier")
        .join(color_parts, left_on="hk_part", right_on="hk_part")
        .join(li_qty, left_on=["hk_part", "hk_supplier"], right_on=["hk_part", "hk_supplier"])
        .filter(col("ps_availqty") * lit(2) > col("total_qty"))
        .select("hk_supplier")
        .unique()
    )

    suppliers_in_nation = (
        ss.join(lsn, left_on="hk_supplier", right_on="hk_supplier")
        .join(sn, left_on="hk_nation", right_on="hk_nation")
        .filter(col("n_name") == lit(nation))
    )

    result = suppliers_in_nation.join(excess, left_on="hk_supplier", right_on="hk_supplier")
    return result.select("s_name", "s_address").sort("s_name")


def q20_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q20")
    color = params.get("color", "forest")
    start_date = params.get("start_date", date(1994, 1, 1))
    end_date = params.get("end_date", date(1995, 1, 1))
    nation = params.get("nation", "CANADA")

    ss = ctx.get_table("sat_supplier")
    ss = _strip_audit_columns_pandas(ss[ss["load_end_dts"].isna()])
    lsn = _strip_audit_columns_pandas(ctx.get_table("link_supplier_nation"))
    sn = ctx.get_table("sat_nation")
    sn = _strip_audit_columns_pandas(sn[sn["load_end_dts"].isna()])
    lps = _strip_audit_columns_pandas(ctx.get_table("link_part_supplier"))
    sps = ctx.get_table("sat_partsupp")
    sps = _strip_audit_columns_pandas(sps[sps["load_end_dts"].isna()])
    sp = ctx.get_table("sat_part")
    sp = _strip_audit_columns_pandas(sp[sp["load_end_dts"].isna()])
    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])

    color_parts = sp[sp["p_name"].str.startswith(color, na=False)]

    li = ll.merge(sl, on="hk_lineitem_link")
    li = li[(li["l_shipdate"] >= start_date) & (li["l_shipdate"] < end_date)]
    li_qty = li.groupby(["hk_part", "hk_supplier"])["l_quantity"].sum().reset_index()
    li_qty["half_qty"] = li_qty["l_quantity"] * 0.5

    ps = lps.merge(sps, on="hk_part_supplier").merge(color_parts[["hk_part"]], on="hk_part")
    ps = ps.merge(li_qty[["hk_part", "hk_supplier", "half_qty"]], on=["hk_part", "hk_supplier"])
    excess_suppliers = ps[ps["ps_availqty"] > ps["half_qty"]]["hk_supplier"].drop_duplicates()

    suppliers = ss.merge(lsn, on="hk_supplier").merge(sn, on="hk_nation")
    suppliers = suppliers[suppliers["n_name"] == nation]
    result = suppliers[suppliers["hk_supplier"].isin(excess_suppliers)]
    return result[["s_name", "s_address"]].sort_values("s_name")


def q21_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q21")
    col, lit = ctx.col, ctx.lit
    nation = params.get("nation", "SAUDI ARABIA")

    ss = _strip_audit_columns(ctx.get_table("sat_supplier").filter(col("load_end_dts").is_null()))
    ll = _strip_audit_columns(ctx.get_table("link_lineitem"))
    sl = _strip_audit_columns(ctx.get_table("sat_lineitem").filter(col("load_end_dts").is_null()))
    so = _strip_audit_columns(ctx.get_table("sat_order").filter(col("load_end_dts").is_null()))
    lsn = _strip_audit_columns(ctx.get_table("link_supplier_nation"))
    sn = _strip_audit_columns(ctx.get_table("sat_nation").filter(col("load_end_dts").is_null()))

    li = ll.join(sl, left_on="hk_lineitem_link", right_on="hk_lineitem_link")
    late_li = li.filter(col("l_receiptdate") > col("l_commitdate"))

    failed_orders = so.filter(col("o_orderstatus") == lit("F")).select("hk_order")

    supp_nation = (
        ss.join(lsn, left_on="hk_supplier", right_on="hk_supplier")
        .join(sn, left_on="hk_nation", right_on="hk_nation")
        .filter(col("n_name") == lit(nation))
    )

    l1 = late_li.join(failed_orders, left_on="hk_order", right_on="hk_order")

    order_suppliers = li.group_by("hk_order").agg(col("hk_supplier").n_unique().alias("n_suppliers"))
    order_late_suppliers = late_li.group_by("hk_order").agg(col("hk_supplier").n_unique().alias("n_late"))

    df = (
        l1.join(supp_nation, left_on="hk_supplier", right_on="hk_supplier")
        .join(order_suppliers, left_on="hk_order", right_on="hk_order")
        .filter(col("n_suppliers") > lit(1))
        .join(order_late_suppliers, left_on="hk_order", right_on="hk_order")
        .filter(col("n_late") == lit(1))
    )

    result = df.group_by("s_name").agg(col("hk_order").count().alias("numwait"))
    return result.sort([("numwait", "desc"), ("s_name", "asc")]).limit(100)


def q21_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q21")
    nation = params.get("nation", "SAUDI ARABIA")

    ss = ctx.get_table("sat_supplier")
    ss = _strip_audit_columns_pandas(ss[ss["load_end_dts"].isna()])
    ll = _strip_audit_columns_pandas(ctx.get_table("link_lineitem"))
    sl = ctx.get_table("sat_lineitem")
    sl = _strip_audit_columns_pandas(sl[sl["load_end_dts"].isna()])
    so = ctx.get_table("sat_order")
    so = _strip_audit_columns_pandas(so[so["load_end_dts"].isna()])
    lsn = _strip_audit_columns_pandas(ctx.get_table("link_supplier_nation"))
    sn = ctx.get_table("sat_nation")
    sn = _strip_audit_columns_pandas(sn[sn["load_end_dts"].isna()])

    li = ll.merge(sl, on="hk_lineitem_link")
    late_li = li[li["l_receiptdate"] > li["l_commitdate"]]
    failed_orders = so[so["o_orderstatus"] == "F"][["hk_order"]]

    supp_nation = ss.merge(lsn, on="hk_supplier").merge(sn, on="hk_nation")
    supp_nation = supp_nation[supp_nation["n_name"] == nation]

    l1 = late_li.merge(failed_orders, on="hk_order")

    order_suppliers = li.groupby("hk_order")["hk_supplier"].nunique().reset_index(name="n_suppliers")
    order_late = late_li.groupby("hk_order")["hk_supplier"].nunique().reset_index(name="n_late")

    df = l1.merge(supp_nation[["hk_supplier", "s_name"]], on="hk_supplier")
    df = df.merge(order_suppliers, on="hk_order")
    df = df[df["n_suppliers"] > 1]
    df = df.merge(order_late, on="hk_order")
    df = df[df["n_late"] == 1]

    result = df.groupby("s_name").size().reset_index(name="numwait")
    return result.sort_values(["numwait", "s_name"], ascending=[False, True]).head(100)


def q22_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q22")
    col, lit = ctx.col, ctx.lit
    codes = params.get("country_codes", ["13", "31", "23", "29", "30", "18", "17"])

    hc = _strip_audit_columns(ctx.get_table("hub_customer"))
    sc = _strip_audit_columns(ctx.get_table("sat_customer").filter(col("load_end_dts").is_null()))
    loc = _strip_audit_columns(ctx.get_table("link_order_customer"))

    customers = hc.join(sc, left_on="hk_customer", right_on="hk_customer")
    customers = customers.with_columns(col("c_phone").str.slice(0, 2).alias("cntrycode"))
    customers = customers.filter(col("cntrycode").is_in(codes))

    avg_bal = ctx.scalar(
        customers.filter(col("c_acctbal") > lit(0)).agg(col("c_acctbal").mean().alias("avg_bal")),
        "avg_bal",
    )

    has_orders = loc.select("hk_customer").unique()
    no_orders = customers.join(has_orders, left_on="hk_customer", right_on="hk_customer", how="anti")

    result = (
        no_orders.filter(col("c_acctbal") > lit(avg_bal))
        .group_by("cntrycode")
        .agg(
            col("hk_customer").count().alias("numcust"),
            col("c_acctbal").sum().alias("totacctbal"),
        )
    )
    return result.sort("cntrycode")


def q22_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q22")
    codes = params.get("country_codes", ["13", "31", "23", "29", "30", "18", "17"])

    hc = _strip_audit_columns_pandas(ctx.get_table("hub_customer"))
    sc = ctx.get_table("sat_customer")
    sc = _strip_audit_columns_pandas(sc[sc["load_end_dts"].isna()])
    loc = _strip_audit_columns_pandas(ctx.get_table("link_order_customer"))

    customers = hc.merge(sc, on="hk_customer")
    customers["cntrycode"] = customers["c_phone"].str[:2]
    customers = customers[customers["cntrycode"].isin(codes)]

    avg_bal = customers[customers["c_acctbal"] > 0]["c_acctbal"].mean()

    has_orders = set(loc["hk_customer"].unique())
    no_orders = customers[~customers["hk_customer"].isin(has_orders)]
    no_orders = no_orders[no_orders["c_acctbal"] > avg_bal]

    result = (
        no_orders.groupby("cntrycode")
        .agg(numcust=("hk_customer", "count"), totacctbal=("c_acctbal", "sum"))
        .reset_index()
    )
    return result.sort_values("cntrycode")


_CATEGORY_CODES = {
    "AG": QueryCategory.AGGREGATE,
    "AN": QueryCategory.ANALYTICAL,
    "FI": QueryCategory.FILTER,
    "GB": QueryCategory.GROUP_BY,
    "JO": QueryCategory.JOIN,
    "MJ": QueryCategory.MULTI_JOIN,
    "SO": QueryCategory.SORT,
    "SQ": QueryCategory.SUBQUERY,
}

_QUERY_METADATA = """Q1|Pricing Summary Report|Pricing summary statistics for shipped lineitems (Data Vault)|AG,GB,FI|4
Q2|Minimum Cost Supplier|Find supplier with minimum cost for parts in region (Data Vault)|MJ,SQ,SO|100
Q3|Shipping Priority|Top 10 unshipped orders with highest value (Data Vault)|MJ,AG,SO|10
Q4|Order Priority Checking|Orders by priority with late lineitems (Data Vault)|JO,AG,SQ|5
Q5|Local Supplier Volume|Revenue from orders in same nation within region (Data Vault)|MJ,AG,FI|
Q6|Forecasting Revenue Change|Revenue increase from eliminating discounts (Data Vault)|AG,FI|1
Q7|Volume Shipping|Value of goods shipped between nations (Data Vault)|MJ,AG,FI|
Q8|National Market Share|Market share of a nation within a region (Data Vault)|MJ,AG,AN|
Q9|Product Type Profit Measure|Profit on a given line of parts (Data Vault)|MJ,AG,FI|
Q10|Returned Item Reporting|Customers with returned parts and revenue impact (Data Vault)|MJ,AG,SO|20
Q11|Important Stock Identification|Find most important stock in a nation (Data Vault)|JO,AG,SQ|
Q12|Shipping Modes and Order Priority|Effect of shipping modes on order priority (Data Vault)|JO,AG,FI|2
Q13|Customer Distribution|Distribution of customers by order count (Data Vault)|JO,AG,SQ|
Q14|Promotion Effect|Effect of promotions on revenue (Data Vault)|JO,AG,FI|1
Q15|Top Supplier|Determine top supplier based on revenue (Data Vault)|JO,AG,SQ|
Q16|Parts/Supplier Relationship|Count suppliers per part, excluding complaints (Data Vault)|JO,AG,SQ|
Q17|Small-Quantity-Order Revenue|Revenue from eliminating small quantity orders (Data Vault)|JO,AG,SQ|1
Q18|Large Volume Customer|Customers with large orders (Data Vault)|MJ,AG,SQ|100
Q19|Discounted Revenue|Revenue for parts with specific conditions (Data Vault)|JO,AG,FI|1
Q20|Potential Part Promotion|Suppliers with excess inventory of parts (Data Vault)|MJ,SQ,FI|
Q21|Suppliers Who Kept Orders Waiting|Suppliers who delayed orders they could fill (Data Vault)|MJ,AG,SQ|100
Q22|Global Sales Opportunity|Identify customers likely to make purchases (Data Vault)|AG,SQ,FI|
"""


def _impl_for(query_id: str, family: str) -> Any:
    return globals()[f"q{query_id[1:]}_{family}_impl"]


def _expected_row_count(value: str) -> int | None:
    return int(value) if value else None


def _register_all_queries() -> None:
    for query_id, query_name, description, category_codes, expected_rows in reader(
        _QUERY_METADATA.splitlines(), delimiter="|"
    ):
        register_query(
            DataFrameQuery(
                query_id=query_id,
                query_name=query_name,
                description=description,
                categories=[_CATEGORY_CODES[code] for code in category_codes.split(",")],
                expression_impl=_impl_for(query_id, "expression"),
                pandas_impl=_impl_for(query_id, "pandas"),
                expected_row_count=_expected_row_count(expected_rows),
            )
        )


_register_all_queries()
