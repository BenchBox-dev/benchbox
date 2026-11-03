from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pyarrow as pa
from datafusion import col, functions as f, lit
from datafusion.expr import GroupingSet, Window, WindowFrame

if TYPE_CHECKING:
    from benchbox.core.dataframe.context import DataFrameContext


def _table(ctx: DataFrameContext, name: str) -> Any:
    return ctx.get_table(name).native


def _in_dates(column: str, start: date, end: date | None = None) -> Any:
    condition = col(column) >= lit(start)
    return condition if end is None else condition & (col(column) < lit(end))


def _wrap(ctx: DataFrameContext, frame: Any, anchor: str) -> Any:
    source = ctx.get_table(anchor)
    return type(source)(frame, source._adapter)


def window_multiple_orderings(ctx: DataFrameContext) -> Any:
    partition = [col("l_orderkey")]
    frame = (
        _table(ctx, "lineitem")
        .filter(col("l_orderkey") <= lit(10000))
        .select(
            col("l_orderkey"),
            col("l_partkey"),
            col("l_quantity"),
            col("l_extendedprice"),
            f.dense_rank(partition_by=partition, order_by=[col("l_extendedprice").sort(ascending=False)]).alias(
                "price_rank"
            ),
            f.percent_rank(partition_by=partition, order_by=[col("l_quantity").sort()]).alias("quantity_percentile"),
            f.cume_dist(partition_by=partition, order_by=[col("l_extendedprice").sort()]).alias("price_distribution"),
        )
        .sort(col("l_orderkey").sort(), col("price_rank").sort())
    )
    return _wrap(ctx, frame, "lineitem")


def window_moving_frame(ctx: DataFrameContext) -> Any:
    ordered = [col("o_orderdate").cast(pa.int32()).sort()]
    frame = (
        _table(ctx, "orders")
        .filter(_in_dates("o_orderdate", date(1995, 1, 1), date(1996, 1, 1)))
        .select(
            col("o_orderkey"),
            col("o_orderdate"),
            col("o_totalprice"),
            f.avg(col("o_totalprice"))
            .over(
                Window(
                    order_by=[col("o_orderdate").sort(), col("o_orderkey").sort()],
                    window_frame=WindowFrame("rows", 5, 0),
                )
            )
            .alias("moving_avg_6_orders"),
            f.sum(col("o_totalprice"))
            .over(
                Window(
                    order_by=ordered,
                    window_frame=WindowFrame("range", pa.scalar(30, pa.int32()), pa.scalar(0, pa.int32())),
                )
            )
            .alias("monthly_running_total"),
        )
        .sort(col("o_orderdate").sort(), col("o_orderkey").sort())
    )
    return _wrap(ctx, frame, "orders")


def statistical_correlation(ctx: DataFrameContext) -> Any:
    frame = (
        _table(ctx, "lineitem")
        .filter(_in_dates("l_shipdate", date(1995, 1, 1), date(1996, 1, 1)))
        .aggregate(
            [],
            [
                f.corr(col("l_quantity"), col("l_extendedprice")).alias("qty_price_correlation"),
                f.covar_pop(col("l_quantity"), col("l_discount")).alias("qty_discount_covariance"),
                f.covar_samp(col("l_tax"), col("l_extendedprice")).alias("tax_price_covariance"),
                f.regr_slope(col("l_extendedprice"), col("l_quantity")).alias("price_qty_slope"),
                f.regr_intercept(col("l_extendedprice"), col("l_quantity")).alias("price_qty_intercept"),
                f.regr_r2(col("l_extendedprice"), col("l_quantity")).alias("regression_r_squared"),
            ],
        )
    )
    return _wrap(ctx, frame, "lineitem")


def statistical_variance(ctx: DataFrameContext) -> Any:
    price = col("o_totalprice")
    frame = (
        _table(ctx, "orders")
        .filter(_in_dates("o_orderdate", date(1995, 1, 1)))
        .aggregate(
            [col("o_orderpriority")],
            [
                f.count(lit(1)).alias("order_count"),
                f.avg(price).alias("avg_price"),
                f.var_samp(price).alias("price_variance"),
                f.stddev(price).alias("price_stddev"),
                f.stddev_pop(price).alias("price_stddev_pop"),
                f.stddev(price).alias("price_stddev_samp"),
            ],
        )
    )
    return _wrap(ctx, frame, "orders")


def _order_region_join(ctx: DataFrameContext, orders: Any) -> Any:
    return (
        orders.join(_table(ctx, "customer"), left_on="o_custkey", right_on="c_custkey")
        .join(_table(ctx, "nation"), left_on="c_nationkey", right_on="n_nationkey")
        .join(_table(ctx, "region"), left_on="n_regionkey", right_on="r_regionkey")
    )


def olap_cube_analysis(ctx: DataFrameContext) -> Any:
    orders = _table(ctx, "orders").filter(_in_dates("o_orderdate", date(1995, 1, 1), date(1997, 1, 1)))
    dims = [
        col("n_name").alias("nation"),
        col("r_name").alias("region"),
        f.date_part(lit("year"), col("o_orderdate")).cast(pa.int64()).alias("order_year"),
        f.date_part(lit("quarter"), col("o_orderdate")).cast(pa.int64()).alias("order_quarter"),
    ]
    frame = _order_region_join(ctx, orders).aggregate(
        [GroupingSet.cube(*dims)],
        [
            f.count(lit(1)).alias("order_count"),
            f.sum(col("o_totalprice")).alias("total_revenue"),
            f.avg(col("o_totalprice")).alias("avg_order_value"),
        ],
    )
    return _wrap(ctx, frame, "orders")


def olap_rollup_analysis(ctx: DataFrameContext) -> Any:
    orders = _table(ctx, "orders").filter(_in_dates("o_orderdate", date(1995, 1, 1)))
    dims = [
        col("r_name").alias("region"),
        col("n_name").alias("nation"),
        col("c_mktsegment").alias("market_segment"),
    ]
    frame = (
        _order_region_join(ctx, orders)
        .aggregate(
            [GroupingSet.rollup(*dims)],
            [
                f.count(col("c_custkey"), distinct=True).alias("customer_count"),
                f.count(lit(1)).alias("order_count"),
                f.sum(col("o_totalprice")).alias("total_revenue"),
                f.avg(col("o_totalprice")).alias("avg_order_value"),
            ],
        )
        .sort(
            col("region").sort(nulls_first=False),
            col("nation").sort(nulls_first=False),
            col("market_segment").sort(nulls_first=False),
        )
    )
    return _wrap(ctx, frame, "orders")


def timeseries_trend_analysis(ctx: DataFrameContext) -> Any:
    month = f.date_trunc(lit("month"), col("o_orderdate"))
    monthly = (
        _table(ctx, "orders")
        .filter(_in_dates("o_orderdate", date(1995, 1, 1), date(1997, 1, 1)))
        .aggregate(
            [month.alias("order_month")],
            [
                f.count(lit(1)).alias("order_count"),
                f.sum(col("o_totalprice")).alias("monthly_revenue"),
                f.avg(col("o_totalprice")).alias("avg_order_value"),
            ],
        )
        .with_column("month_epoch", f.date_part(lit("epoch"), col("order_month")))
    )
    lagged = monthly.select(
        col("order_month"),
        col("order_count"),
        col("monthly_revenue"),
        col("avg_order_value"),
        col("month_epoch"),
        f.lag(col("monthly_revenue"), 1, order_by=[col("order_month").sort()]).alias("prev_month_revenue"),
    )
    previous = col("prev_month_revenue")
    frame = lagged.select(
        col("order_month"),
        col("order_count"),
        col("monthly_revenue"),
        col("avg_order_value"),
        f.regr_slope(col("monthly_revenue"), col("month_epoch")).over(Window()).alias("revenue_trend_slope"),
        previous,
        ((col("monthly_revenue") - previous) / f.nullif(previous, lit(0)) * lit(100)).alias("mom_growth_pct"),
    ).sort(col("order_month").sort())
    return _wrap(ctx, frame, "orders")


def optimizer_common_subexpression(ctx: DataFrameContext) -> Any:
    revenue = col("l_quantity") * col("l_extendedprice") * (lit(1) - col("l_discount")) * (lit(1) + col("l_tax"))
    frame = (
        _table(ctx, "lineitem")
        .filter(_in_dates("l_shipdate", date(1995, 1, 1), date(1996, 1, 1)))
        .with_column("revenue_with_tax", revenue)
        .filter(col("revenue_with_tax") > lit(1000))
        .select(
            col("l_orderkey"),
            col("l_partkey"),
            col("l_suppkey"),
            col("l_linenumber"),
            col("revenue_with_tax"),
            f.case(lit(True))
            .when(col("revenue_with_tax") > lit(50000), lit("High Value"))
            .when(col("revenue_with_tax") > lit(10000), lit("Medium Value"))
            .otherwise(lit("Low Value"))
            .alias("value_category"),
            f.round(col("revenue_with_tax"), lit(2)).alias("rounded_revenue"),
        )
        .sort(
            col("revenue_with_tax").sort(ascending=False),
            col("l_orderkey").sort(),
            col("l_linenumber").sort(),
        )
        .limit(5000)
    )
    return _wrap(ctx, frame, "lineitem")


def struct_construction(ctx: DataFrameContext) -> Any:
    frame = (
        _table(ctx, "customer")
        .filter(col("c_nationkey") == lit(1))
        .sort(col("c_custkey").sort())
        .select(
            col("c_custkey"),
            f.named_struct(
                [("c_name", col("c_name")), ("c_address", col("c_address")), ("c_phone", col("c_phone"))]
            ).alias("contact_info"),
            col("c_acctbal"),
        )
        .limit(100)
    )
    return _wrap(ctx, frame, "customer")
