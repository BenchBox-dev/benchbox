from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory, QueryRegistry

if TYPE_CHECKING:
    from benchbox.core.dataframe.context import DataFrameContext


REGISTRY = QueryRegistry("tpcds_obt")

OBT_TABLE = "tpcds_sales_returns_obt"

_AGG_FUNCS = ("count", "sum", "mean", "min", "max", "n_unique")

_FILTER_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}


@dataclass(frozen=True)
class _ObtQuerySpec:
    query_name: str
    description: str
    categories: tuple[QueryCategory, ...]
    sql_equivalent: str
    group_keys: tuple[str, ...] = ()
    aggregations: tuple[tuple[str, str, str], ...] = ()
    filters: tuple[tuple[str, str, Any], ...] = ()
    sort_keys: tuple[str, ...] = ()
    descending: tuple[bool, ...] = ()
    limit: int = 0

    def __post_init__(self) -> None:
        for _, _, func in self.aggregations:
            if func not in _AGG_FUNCS:
                raise ValueError(f"Unknown aggregation func {func!r} in {self.query_name}")
        for _, op, _ in self.filters:
            if op not in _FILTER_OPS:
                raise ValueError(f"Unknown filter op {op!r} in {self.query_name}")
        if self.descending and len(self.descending) != len(self.sort_keys):
            raise ValueError(f"descending flags must match sort_keys in {self.query_name}")
        if self.limit < 0:
            raise ValueError(f"limit must be >= 0 in {self.query_name}")


def _make_expression_impl(spec: _ObtQuerySpec) -> Callable[[DataFrameContext], Any]:

    def impl(ctx: DataFrameContext) -> Any:
        table = ctx.get_table(OBT_TABLE)
        for column, op, value in spec.filters:
            table = table.filter(_FILTER_OPS[op](ctx.col(column), ctx.lit(value)))
        aggregates = [getattr(ctx.col(column), func)().alias(alias) for alias, column, func in spec.aggregations]
        if spec.group_keys:
            result = table.group_by(*spec.group_keys).agg(*aggregates)
        else:
            result = table.select(*aggregates)
        if spec.sort_keys:
            descending = list(spec.descending) if spec.descending else [False] * len(spec.sort_keys)
            result = result.sort(list(spec.sort_keys), descending=descending)
        if spec.limit:
            result = result.limit(spec.limit)
        return result

    return impl


def _make_pandas_impl(spec: _ObtQuerySpec) -> Callable[[DataFrameContext], Any]:

    def impl(ctx: DataFrameContext) -> Any:
        import pandas as pd

        table = ctx.get_table(OBT_TABLE)
        for column, op, value in spec.filters:
            mask = _FILTER_OPS[op](table[column], value)
            table = table[mask]
        if spec.group_keys:
            named_aggs = {
                alias: (column, "nunique" if func == "n_unique" else func) for alias, column, func in spec.aggregations
            }
            result = table.groupby(list(spec.group_keys), as_index=False, dropna=False).agg(**named_aggs)
        else:
            result = pd.DataFrame(
                {alias: [_pandas_scalar(table, column, func)] for alias, column, func in spec.aggregations}
            )
        if spec.sort_keys:
            descending = list(spec.descending) if spec.descending else [False] * len(spec.sort_keys)
            result = result.sort_values(list(spec.sort_keys), ascending=[not flag for flag in descending])
        if spec.limit:
            result = result.head(spec.limit)
        return _materialize(result)

    return impl


def _materialize(value: Any) -> Any:
    compute = getattr(value, "compute", None)
    if callable(compute):
        return compute()
    return value


def _pandas_scalar(table: Any, column: str, func: str) -> Any:
    if func == "count":
        return int(_materialize(table[column].count()))
    values = table[column]
    if func == "n_unique":
        return int(_materialize(values.nunique()))
    return float(_materialize(getattr(values, func)()))


def _register_spec(spec: _ObtQuerySpec) -> None:
    REGISTRY.register(
        DataFrameQuery(
            query_id=spec.query_name,
            query_name=spec.query_name,
            description=spec.description,
            categories=list(spec.categories),
            expression_impl=_make_expression_impl(spec),
            pandas_impl=_make_pandas_impl(spec),
            sql_equivalent=spec.sql_equivalent,
        )
    )


_QUERY_SPECS: tuple[_ObtQuerySpec, ...] = (
    _ObtQuerySpec(
        query_name="obt_row_count",
        description="Total row count for OBT table",
        categories=(QueryCategory.AGGREGATE,),
        aggregations=(("row_count", "sale_id", "count"),),
        sql_equivalent="SELECT COUNT(*) AS row_count FROM tpcds_sales_returns_obt",
    ),
    _ObtQuerySpec(
        query_name="obt_channel_distribution",
        description="Count sales rows by channel",
        categories=(QueryCategory.AGGREGATE, QueryCategory.GROUP_BY),
        group_keys=("channel",),
        aggregations=(("sales_count", "sale_id", "count"),),
        sort_keys=("channel",),
        sql_equivalent=(
            "SELECT channel, COUNT(sale_id) AS sales_count "
            "FROM tpcds_sales_returns_obt GROUP BY channel ORDER BY channel"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_returns_summary",
        description="Summarize returned sales and amount",
        categories=(QueryCategory.AGGREGATE, QueryCategory.FILTER),
        filters=(("has_return", "==", "Y"),),
        aggregations=(
            ("returned_sales", "sale_id", "count"),
            ("total_return_amount", "return_amount", "sum"),
        ),
        sql_equivalent=(
            "SELECT COUNT(sale_id) AS returned_sales, SUM(return_amount) AS total_return_amount "
            "FROM tpcds_sales_returns_obt WHERE has_return = 'Y'"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_channel_revenue",
        description="Total net revenue by channel",
        categories=(QueryCategory.AGGREGATE, QueryCategory.GROUP_BY),
        group_keys=("channel",),
        aggregations=(("revenue", "net_paid", "sum"),),
        sort_keys=("channel",),
        sql_equivalent=(
            "SELECT channel, SUM(net_paid) AS revenue FROM tpcds_sales_returns_obt GROUP BY channel ORDER BY channel"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_top_items_by_quantity",
        description="Top items by units sold",
        categories=(QueryCategory.AGGREGATE, QueryCategory.GROUP_BY, QueryCategory.SORT),
        group_keys=("item_sk",),
        aggregations=(("total_quantity", "quantity", "sum"),),
        sort_keys=("total_quantity", "item_sk"),
        descending=(True, False),
        limit=10,
        sql_equivalent=(
            "SELECT item_sk, SUM(quantity) AS total_quantity "
            "FROM tpcds_sales_returns_obt GROUP BY item_sk "
            "ORDER BY total_quantity DESC, item_sk ASC LIMIT 10"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_avg_ticket_by_channel",
        description="Average ticket and sales count by channel",
        categories=(QueryCategory.AGGREGATE, QueryCategory.GROUP_BY),
        group_keys=("channel",),
        aggregations=(
            ("avg_ticket", "net_paid", "mean"),
            ("sales", "sale_id", "count"),
        ),
        sort_keys=("channel",),
        sql_equivalent=(
            "SELECT channel, AVG(net_paid) AS avg_ticket, COUNT(sale_id) AS sales "
            "FROM tpcds_sales_returns_obt GROUP BY channel ORDER BY channel"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_discounted_sales",
        description="Revenue and discount totals for discounted sales",
        categories=(QueryCategory.AGGREGATE, QueryCategory.FILTER),
        filters=(("ext_discount_amt", ">", 0),),
        aggregations=(
            ("discounted_sales", "sale_id", "count"),
            ("discounted_revenue", "net_paid", "sum"),
            ("total_discount", "ext_discount_amt", "sum"),
        ),
        sql_equivalent=(
            "SELECT COUNT(sale_id) AS discounted_sales, SUM(net_paid) AS discounted_revenue, "
            "SUM(ext_discount_amt) AS total_discount "
            "FROM tpcds_sales_returns_obt WHERE ext_discount_amt > 0"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_profit_by_channel",
        description="Total net profit by channel",
        categories=(QueryCategory.AGGREGATE, QueryCategory.GROUP_BY),
        group_keys=("channel",),
        aggregations=(("total_profit", "net_profit", "sum"),),
        sort_keys=("channel",),
        sql_equivalent=(
            "SELECT channel, SUM(net_profit) AS total_profit "
            "FROM tpcds_sales_returns_obt GROUP BY channel ORDER BY channel"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_sales_by_channel_return_flag",
        description="Sales counts by channel and return flag",
        categories=(QueryCategory.AGGREGATE, QueryCategory.GROUP_BY),
        group_keys=("channel", "has_return"),
        aggregations=(("sales_count", "sale_id", "count"),),
        sort_keys=("channel", "has_return"),
        sql_equivalent=(
            "SELECT channel, has_return, COUNT(sale_id) AS sales_count "
            "FROM tpcds_sales_returns_obt GROUP BY channel, has_return "
            "ORDER BY channel, has_return"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_high_value_sales",
        description="Count and revenue of high-value sales",
        categories=(QueryCategory.AGGREGATE, QueryCategory.FILTER),
        filters=(("net_paid", ">=", 200),),
        aggregations=(
            ("high_value_sales", "sale_id", "count"),
            ("high_value_revenue", "net_paid", "sum"),
        ),
        sql_equivalent=(
            "SELECT COUNT(sale_id) AS high_value_sales, COALESCE(SUM(net_paid), 0) AS high_value_revenue "
            "FROM tpcds_sales_returns_obt WHERE net_paid >= 200"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_item_channel_quantity",
        description="Units sold by channel and item",
        categories=(QueryCategory.AGGREGATE, QueryCategory.GROUP_BY),
        group_keys=("channel", "item_sk"),
        aggregations=(("total_quantity", "quantity", "sum"),),
        sort_keys=("channel", "item_sk"),
        sql_equivalent=(
            "SELECT channel, item_sk, SUM(quantity) AS total_quantity "
            "FROM tpcds_sales_returns_obt GROUP BY channel, item_sk "
            "ORDER BY channel, item_sk"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_ticket_extremes",
        description="Minimum, maximum, and average ticket",
        categories=(QueryCategory.AGGREGATE,),
        aggregations=(
            ("min_ticket", "net_paid", "min"),
            ("max_ticket", "net_paid", "max"),
            ("avg_ticket", "net_paid", "mean"),
        ),
        sql_equivalent=(
            "SELECT MIN(net_paid) AS min_ticket, MAX(net_paid) AS max_ticket, "
            "AVG(net_paid) AS avg_ticket FROM tpcds_sales_returns_obt"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_distinct_items",
        description="Count of distinct items sold",
        categories=(QueryCategory.AGGREGATE,),
        aggregations=(("distinct_items", "item_sk", "n_unique"),),
        sql_equivalent="SELECT COUNT(DISTINCT item_sk) AS distinct_items FROM tpcds_sales_returns_obt",
    ),
    _ObtQuerySpec(
        query_name="obt_bulk_sales",
        description="Multi-unit sales volume and revenue by channel",
        categories=(QueryCategory.AGGREGATE, QueryCategory.GROUP_BY, QueryCategory.FILTER),
        filters=(("quantity", ">", 1),),
        group_keys=("channel",),
        aggregations=(
            ("bulk_units", "quantity", "sum"),
            ("bulk_revenue", "net_paid", "sum"),
        ),
        sort_keys=("channel",),
        sql_equivalent=(
            "SELECT channel, SUM(quantity) AS bulk_units, SUM(net_paid) AS bulk_revenue "
            "FROM tpcds_sales_returns_obt WHERE quantity > 1 "
            "GROUP BY channel ORDER BY channel"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_coupon_sales",
        description="Coupon usage count, value, and revenue",
        categories=(QueryCategory.AGGREGATE, QueryCategory.FILTER),
        filters=(("coupon_amt", ">", 0),),
        aggregations=(
            ("coupon_sales", "sale_id", "count"),
            ("total_coupons", "coupon_amt", "sum"),
            ("coupon_revenue", "net_paid", "sum"),
        ),
        sql_equivalent=(
            "SELECT COUNT(sale_id) AS coupon_sales, COALESCE(SUM(coupon_amt), 0) AS total_coupons, "
            "COALESCE(SUM(net_paid), 0) AS coupon_revenue "
            "FROM tpcds_sales_returns_obt WHERE coupon_amt > 0"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_store_summary",
        description="Store-channel sales, revenue, and profit",
        categories=(QueryCategory.AGGREGATE, QueryCategory.FILTER),
        filters=(("channel", "==", "store"),),
        aggregations=(
            ("store_sales", "sale_id", "count"),
            ("store_revenue", "net_paid", "sum"),
            ("store_profit", "net_profit", "sum"),
        ),
        sql_equivalent=(
            "SELECT COUNT(sale_id) AS store_sales, COALESCE(SUM(net_paid), 0) AS store_revenue, "
            "COALESCE(SUM(net_profit), 0) AS store_profit "
            "FROM tpcds_sales_returns_obt WHERE channel = 'store'"
        ),
    ),
    _ObtQuerySpec(
        query_name="obt_overall_totals",
        description="Overall sales, revenue, and profit totals",
        categories=(QueryCategory.AGGREGATE,),
        aggregations=(
            ("total_sales", "sale_id", "count"),
            ("total_revenue", "net_paid", "sum"),
            ("total_revenue_inc_tax", "net_paid_inc_tax", "sum"),
            ("total_profit", "net_profit", "sum"),
        ),
        sql_equivalent=(
            "SELECT COUNT(sale_id) AS total_sales, COALESCE(SUM(net_paid), 0) AS total_revenue, "
            "COALESCE(SUM(net_paid_inc_tax), 0) AS total_revenue_inc_tax, "
            "COALESCE(SUM(net_profit), 0) AS total_profit "
            "FROM tpcds_sales_returns_obt"
        ),
    ),
)


def _register_queries() -> None:
    for spec in _QUERY_SPECS:
        _register_spec(spec)


_register_queries()


def get_dataframe_queries() -> list[DataFrameQuery]:
    return REGISTRY.get_all_queries()
