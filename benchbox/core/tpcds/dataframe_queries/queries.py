# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from csv import reader
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import yaml

from benchbox.core.dataframe.compat import _to_list
from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory

from .parameters import get_parameters
from .registry import configure_query_loader

_FilterSpec = tuple[str, str | None, Any]


class QueryImpl(Protocol):
    def __call__(self, ctx: DataFrameContext) -> Any: ...


_GENERATED_IMPLS: dict[str, QueryImpl] = {}


def _register_generated_impl(impl: QueryImpl) -> QueryImpl:
    _GENERATED_IMPLS[impl.__name__] = impl
    return impl


def __getattr__(name: str) -> QueryImpl:
    if name in _GENERATED_IMPLS:
        return _GENERATED_IMPLS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _tables(ctx: DataFrameContext, *names: str) -> tuple[Any, ...]:
    return tuple(ctx.get_table(name) for name in names)


def _none_for_null(frame: Any, columns: list[str]) -> Any:
    if hasattr(frame, "npartitions"):
        return frame.assign(
            **{column: frame[column].astype(object).where(~frame[column].isna(), None) for column in columns}
        )
    frame = frame.copy()
    for column in columns:
        nulls = frame[column].isna()
        if nulls.any():
            frame[column] = frame[column].astype(object).where(~nulls, None)
    return frame


def _sum_or_null_expression(ctx: DataFrameContext, value: Any) -> Any:
    return ctx.when(value.count() > ctx.lit(0)).then(value.sum()).otherwise(ctx.lit(None))


def _grouped_pandas_aggregates(
    frame: Any, keys: str | list[str], aggregates: dict[str, tuple[str, str]], *, dropna: bool = True
) -> Any:
    non_null = {alias: f"_{alias}_non_null" for alias, (_, method) in aggregates.items() if method == "sum"}
    counts = {count: (aggregates[alias][0], "count") for alias, count in non_null.items()}
    result = frame.groupby(keys, as_index=False, dropna=dropna).agg(**aggregates, **counts)
    for alias, count in non_null.items():
        result[alias] = result[alias].where(result[count] > 0)
    return result.drop(columns=list(counts))


def _round_half_up_expression(expr: Any, decimals: int) -> Any:
    scale = 10**decimals
    return (expr * scale + 0.5).floor() / scale


def _round_half_up_pandas(values: Any, decimals: int) -> Any:
    scale = 10**decimals
    return ((values * scale + 0.5) // 1) / scale


def _filter_value(params: Any, param_name: str | None, default: Any) -> Any:
    return params.get(param_name, default) if param_name else default


def _equal_filter_expression(ctx: DataFrameContext, params: Any, filters: tuple[_FilterSpec, ...]) -> Any:
    col = ctx.col
    lit = ctx.lit
    predicate = None
    for column, param_name, default in filters:
        clause = col(column) == lit(_filter_value(params, param_name, default))
        predicate = clause if predicate is None else predicate & clause
    return predicate


def _equal_filter_pandas(frame: Any, params: Any, filters: tuple[_FilterSpec, ...]) -> Any:
    mask = None
    for column, param_name, default in filters:
        clause = frame[column] == _filter_value(params, param_name, default)
        mask = clause if mask is None else mask & clause
    return frame if mask is None else frame[mask]


def _date_item_sales_expression(
    ctx: DataFrameContext,
    query_id: int,
    filters: tuple[_FilterSpec, ...],
    group_by: tuple[str, ...],
    value_col: str,
    alias: str,
    sort_by: tuple[str, ...],
    descending: tuple[bool, ...],
    value_param: str | None = None,
) -> Any:
    params = get_parameters(query_id)
    value_col = _filter_value(params, value_param, value_col)
    date_dim, store_sales, item = _tables(ctx, "date_dim", "store_sales", "item")
    filtered = (
        date_dim.join(store_sales, left_on="d_date_sk", right_on="ss_sold_date_sk")
        .join(item, left_on="ss_item_sk", right_on="i_item_sk")
        .filter(_equal_filter_expression(ctx, params, filters))
    )
    value = ctx.col(value_col)
    total = ctx.when(value.count() > ctx.lit(0)).then(value.sum()).otherwise(ctx.lit(None)).alias(alias)
    grouped = filtered.group_by(*group_by).agg(total)
    return _sort_null_largest_expression(ctx, grouped, list(sort_by), list(descending)).limit(100)


def _date_item_sales_pandas(
    ctx: DataFrameContext,
    query_id: int,
    filters: tuple[_FilterSpec, ...],
    group_by: tuple[str, ...],
    value_col: str,
    alias: str,
    sort_by: tuple[str, ...],
    descending: tuple[bool, ...],
    value_param: str | None = None,
) -> Any:
    params = get_parameters(query_id)
    value_col = _filter_value(params, value_param, value_col)
    merged = ctx.get_table("date_dim").merge(
        ctx.get_table("store_sales"), left_on="d_date_sk", right_on="ss_sold_date_sk"
    )
    merged = merged.merge(ctx.get_table("item"), left_on="ss_item_sk", right_on="i_item_sk")
    grouped = (
        _equal_filter_pandas(merged, params, filters)
        .groupby(list(group_by), as_index=False, dropna=False)
        .agg(**{alias: (value_col, "sum"), "_non_null": (value_col, "count")})
    )
    grouped[alias] = grouped[alias].where(grouped["_non_null"] > 0)
    grouped = grouped.drop(columns="_non_null")
    ordered = _sort_null_largest_pandas(grouped, list(sort_by), list(descending)).head(100)
    return _none_for_null(ordered, [*group_by, alias])


def _manufacturer_month_expression(
    ctx: DataFrameContext,
    query_id: int,
    value_col: str,
    alias: str,
    id_col: str,
    period_col: str,
    avg_alias: str,
    categories_a: tuple[str, ...],
    classes_a: tuple[str, ...],
    brands_a: tuple[str, ...],
    categories_b: tuple[str, ...],
    classes_b: tuple[str, ...],
    brands_b: tuple[str, ...],
    sort_by: tuple[str, ...],
    dms_default: int = 1212,
) -> Any:
    params = get_parameters(query_id)
    dms = params.get("dms", dms_default)
    col = ctx.col
    lit = ctx.lit
    in_a = (
        col("i_category").is_in(list(categories_a))
        & col("i_class").is_in(list(classes_a))
        & col("i_brand").is_in(list(brands_a))
    )
    in_b = (
        col("i_category").is_in(list(categories_b))
        & col("i_class").is_in(list(classes_b))
        & col("i_brand").is_in(list(brands_b))
    )
    grouped = (
        ctx.get_table("store_sales")
        .join(ctx.get_table("item"), left_on="ss_item_sk", right_on="i_item_sk")
        .join(ctx.get_table("date_dim"), left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(ctx.get_table("store"), left_on="ss_store_sk", right_on="s_store_sk")
        .filter(col("d_month_seq").is_in(list(range(dms, dms + 12))) & (in_a | in_b))
        .group_by(id_col, period_col)
        .agg(_sum_or_null_expression(ctx, col(value_col)).alias(alias))
    )
    with_avg = grouped.with_columns(ctx.window_avg(alias, partition_by=[id_col]).alias(avg_alias))
    return _sort_null_largest_expression(
        ctx,
        with_avg.filter(
            (col(avg_alias) > lit(0)) & ((col(alias) - col(avg_alias)).abs() / col(avg_alias) > lit(0.1))
        ).select([id_col, alias, avg_alias]),
        list(sort_by),
    ).limit(100)


def _manufacturer_month_pandas(
    ctx: DataFrameContext,
    query_id: int,
    value_col: str,
    alias: str,
    id_col: str,
    period_col: str,
    avg_alias: str,
    categories_a: tuple[str, ...],
    classes_a: tuple[str, ...],
    brands_a: tuple[str, ...],
    categories_b: tuple[str, ...],
    classes_b: tuple[str, ...],
    brands_b: tuple[str, ...],
    sort_by: tuple[str, ...],
    dms_default: int = 1212,
) -> Any:
    params = get_parameters(query_id)
    dms = params.get("dms", dms_default)
    merged = ctx.get_table("store_sales").merge(ctx.get_table("item"), left_on="ss_item_sk", right_on="i_item_sk")
    merged = merged.merge(ctx.get_table("date_dim"), left_on="ss_sold_date_sk", right_on="d_date_sk")
    merged = merged.merge(ctx.get_table("store"), left_on="ss_store_sk", right_on="s_store_sk")
    in_a = (
        (merged["i_category"].isin(list(categories_a)))
        & (merged["i_class"].isin(list(classes_a)))
        & (merged["i_brand"].isin(list(brands_a)))
    )
    in_b = (
        (merged["i_category"].isin(list(categories_b)))
        & (merged["i_class"].isin(list(classes_b)))
        & (merged["i_brand"].isin(list(brands_b)))
    )
    filtered = merged[(merged["d_month_seq"].isin(list(range(dms, dms + 12)))) & (in_a | in_b)]
    grouped = _grouped_pandas_aggregates(filtered, [id_col, period_col], {alias: (value_col, "sum")}, dropna=False)
    grouped[avg_alias] = grouped.groupby(id_col, dropna=False)[alias].transform("mean")
    kept = grouped[(grouped[avg_alias] > 0) & ((grouped[alias] - grouped[avg_alias]).abs() / grouped[avg_alias] > 0.1)]
    return _none_for_null(
        kept[[id_col, alias, avg_alias]].sort_values(list(sort_by)).head(100), [id_col, alias, avg_alias]
    )


def _item_category_sales_expression(
    ctx: DataFrameContext,
    query_id: int,
    sales_table: str,
    item_key: str,
    date_key: str,
    value_col: str,
    category_param: str,
    sales_date_default: str,
    group_by: tuple[str, ...],
    sort_by: tuple[str, ...],
    limit: int | None = 100,
) -> Any:
    params = get_parameters(query_id)
    categories = params.get(category_param, ["Sports", "Books", "Home"])
    start_date, end_date = _sales_date_window(query_id, sales_date_default)
    col = ctx.col
    lit = ctx.lit
    grouped = (
        ctx.get_table(sales_table)
        .join(ctx.get_table("item"), left_on=item_key, right_on="i_item_sk")
        .join(ctx.get_table("date_dim"), left_on=date_key, right_on="d_date_sk")
        .filter(
            col("i_category").is_in(categories) & (col("d_date") >= lit(start_date)) & (col("d_date") <= lit(end_date))
        )
        .group_by(*group_by)
        .agg(
            ctx.when(col(value_col).count() > lit(0))
            .then(col(value_col).sum())
            .otherwise(lit(None))
            .alias("itemrevenue")
        )
    )
    with_ratio = grouped.with_columns(
        (col("itemrevenue") * 100 / ctx.window_sum("itemrevenue", partition_by=["i_class"])).alias("revenueratio")
    )
    ranked = _sort_null_largest_expression(ctx, with_ratio, list(sort_by), [False] * len(sort_by))
    return ranked if limit is None else ranked.limit(limit)


def _item_category_sales_pandas(
    ctx: DataFrameContext,
    query_id: int,
    sales_table: str,
    item_key: str,
    date_key: str,
    value_col: str,
    category_param: str,
    sales_date_default: str,
    group_by: tuple[str, ...],
    sort_by: tuple[str, ...],
    limit: int | None = 100,
) -> Any:
    params = get_parameters(query_id)
    categories = params.get(category_param, ["Sports", "Books", "Home"])
    start_date, end_date = _sales_date_window(query_id, sales_date_default)
    merged = ctx.get_table(sales_table).merge(ctx.get_table("item"), left_on=item_key, right_on="i_item_sk")
    merged = merged.merge(ctx.get_table("date_dim"), left_on=date_key, right_on="d_date_sk")
    filtered = merged[
        (merged["i_category"].isin(categories)) & (merged["d_date"] >= start_date) & (merged["d_date"] <= end_date)
    ]
    grouped = filtered.groupby(list(group_by), as_index=False, dropna=False).agg(
        itemrevenue=(value_col, "sum"), priced=(value_col, "count")
    )
    grouped["itemrevenue"] = grouped["itemrevenue"].where(grouped["priced"] > 0)
    grouped = grouped.drop(columns=["priced"])
    grouped["revenueratio"] = (
        grouped["itemrevenue"] * 100 / grouped.groupby("i_class", dropna=False)["itemrevenue"].transform("sum")
    )
    ordered = grouped.sort_values(list(sort_by), na_position="last")
    result = _none_for_null(ordered if limit is None else ordered.head(limit), list(group_by))
    null_groups = result["itemrevenue"].isna()
    for column in ("itemrevenue", "revenueratio"):
        result[column] = result[column].astype(object).where(~null_groups, None)
    return result


def _excess_discount_expression(
    ctx: DataFrameContext,
    query_id: int,
    sales_table: str,
    item_key: str,
    date_key: str,
    discount_col: str,
    manufact_id: int,
    sales_date_default: str = "1998-03-18",
) -> Any:
    manufact_id = get_parameters(query_id).get("manufact_id", manufact_id)
    start_date, end_date = _sales_date_window(query_id, sales_date_default, days=90)
    sales = ctx.get_table(sales_table)
    date_dim = ctx.get_table("date_dim")
    col = ctx.col
    lit = ctx.lit
    avg_discount = (
        sales.join(date_dim, left_on=date_key, right_on="d_date_sk")
        .filter((col("d_date") >= lit(start_date)) & (col("d_date") <= lit(end_date)))
        .group_by(item_key)
        .agg(col(discount_col).mean().alias("avg_discount"))
        .select(col(item_key).alias("avg_item_sk"), col("avg_discount"))
    )
    return (
        sales.join(ctx.get_table("item"), left_on=item_key, right_on="i_item_sk")
        .join(date_dim, left_on=date_key, right_on="d_date_sk")
        .join(avg_discount, left_on=item_key, right_on="avg_item_sk")
        .filter(
            (col("i_manufact_id") == lit(manufact_id))
            & (col("d_date") >= lit(start_date))
            & (col("d_date") <= lit(end_date))
            & (col(discount_col) > lit(1.3) * col("avg_discount"))
        )
        .select(
            col(discount_col).sum().alias("excess_discount_amount"),
            col(discount_col).count().alias("n"),
        )
        .select(
            ctx.when(col("n") > lit(0))
            .then(col("excess_discount_amount"))
            .otherwise(lit(None))
            .alias("excess_discount_amount")
        )
    )


def _excess_discount_pandas(
    ctx: DataFrameContext,
    query_id: int,
    sales_table: str,
    item_key: str,
    date_key: str,
    discount_col: str,
    manufact_id: int,
    sales_date_default: str = "1998-03-18",
) -> Any:
    import pandas as pd

    manufact_id = get_parameters(query_id).get("manufact_id", manufact_id)
    start_date, end_date = _sales_date_window(query_id, sales_date_default, days=90)
    sales = ctx.get_table(sales_table)
    date_dim = ctx.get_table("date_dim").copy()
    if len(date_dim) > 0 and hasattr(date_dim["d_date"].iloc[0], "date"):
        date_dim["d_date"] = pd.to_datetime(date_dim["d_date"]).dt.date
    merged_for_avg = sales.merge(date_dim, left_on=date_key, right_on="d_date_sk")
    in_window = merged_for_avg[(merged_for_avg["d_date"] >= start_date) & (merged_for_avg["d_date"] <= end_date)]
    avg_discount = in_window.groupby(item_key)[discount_col].mean().rename("avg_discount").reset_index()
    merged = sales.merge(ctx.get_table("item"), left_on=item_key, right_on="i_item_sk")
    merged = merged.merge(date_dim, left_on=date_key, right_on="d_date_sk")
    merged = merged.merge(avg_discount, on=item_key)
    filtered = merged[
        (merged["i_manufact_id"] == manufact_id)
        & (merged["d_date"] >= start_date)
        & (merged["d_date"] <= end_date)
        & (merged[discount_col] > 1.3 * merged["avg_discount"])
    ]
    if len(filtered) == 0:
        return pd.DataFrame({"excess_discount_amount": [None]})
    return pd.DataFrame({"excess_discount_amount": [filtered[discount_col].sum()]})


def _item_filter_expression(item: Any, col: Any, column: str, value: Any) -> Any:
    return item.filter(col(column).is_in(value) if isinstance(value, list) else col(column) == value)


def _item_filter_pandas(item: Any, column: str, value: Any) -> Any:
    return item[item[column].isin(value) if isinstance(value, list) else item[column] == value]


def _three_channel_item_sales_expression(
    ctx: DataFrameContext,
    query_id: int,
    filter_column: str,
    filter_param: str,
    filter_default: Any,
    month_default: int,
    sort_by: tuple[str, str],
) -> Any:
    col = ctx.col
    params = get_parameters(query_id)
    year = params.get("year", 1998)
    month = params.get("month", month_default)
    filter_value = params.get(filter_param, filter_default)
    item = ctx.get_table("item")
    item_ids = _item_filter_expression(item, col, filter_column, filter_value).select("i_item_id").unique()
    date_filter = ctx.get_table("date_dim").filter((col("d_year") == year) & (col("d_moy") == month))
    addr_filter = ctx.get_table("customer_address").filter(col("ca_gmt_offset") == params.get("gmt_offset", -5))

    def channel(table: str, item_key: str, date_key: str, addr_key: str, value_col: str) -> Any:
        return (
            ctx.get_table(table)
            .join(item, left_on=item_key, right_on="i_item_sk", how="inner")
            .join(date_filter, left_on=date_key, right_on="d_date_sk", how="inner")
            .join(addr_filter, left_on=addr_key, right_on="ca_address_sk", how="inner")
            .join(item_ids, on="i_item_id", how="semi")
            .group_by("i_item_id")
            .agg(_sum_or_null_expression(ctx, col(value_col)).alias("total_sales"))
        )

    combined = ctx.concat(
        [
            channel("store_sales", "ss_item_sk", "ss_sold_date_sk", "ss_addr_sk", "ss_ext_sales_price"),
            channel("catalog_sales", "cs_item_sk", "cs_sold_date_sk", "cs_bill_addr_sk", "cs_ext_sales_price"),
            channel("web_sales", "ws_item_sk", "ws_sold_date_sk", "ws_bill_addr_sk", "ws_ext_sales_price"),
        ]
    )
    return _sort_null_largest_expression(
        ctx,
        combined.group_by("i_item_id").agg(_sum_or_null_expression(ctx, col("total_sales")).alias("total_sales")),
        list(sort_by),
    ).head(100)


def _three_channel_item_sales_pandas(
    ctx: DataFrameContext,
    query_id: int,
    filter_column: str,
    filter_param: str,
    filter_default: Any,
    month_default: int,
    sort_by: tuple[str, str],
) -> Any:
    params = get_parameters(query_id)
    year = params.get("year", 1998)
    month = params.get("month", month_default)
    filter_value = params.get(filter_param, filter_default)
    item = ctx.get_table("item")
    item_ids = _item_filter_pandas(item, filter_column, filter_value)["i_item_id"].unique()
    date_filter = ctx.get_table("date_dim")
    date_filter = date_filter[(date_filter["d_year"] == year) & (date_filter["d_moy"] == month)]
    addr_filter = ctx.get_table("customer_address")
    addr_filter = addr_filter[addr_filter["ca_gmt_offset"] == params.get("gmt_offset", -5)]

    def channel(table: str, item_key: str, date_key: str, addr_key: str, value_col: str) -> Any:
        return _grouped_pandas_aggregates(
            ctx.get_table(table)
            .merge(item[item["i_item_id"].isin(item_ids)], left_on=item_key, right_on="i_item_sk", how="inner")
            .merge(date_filter, left_on=date_key, right_on="d_date_sk", how="inner")
            .merge(addr_filter, left_on=addr_key, right_on="ca_address_sk", how="inner"),
            "i_item_id",
            {"total_sales": (value_col, "sum")},
        )

    combined = ctx.concat(
        [
            channel("store_sales", "ss_item_sk", "ss_sold_date_sk", "ss_addr_sk", "ss_ext_sales_price"),
            channel("catalog_sales", "cs_item_sk", "cs_sold_date_sk", "cs_bill_addr_sk", "cs_ext_sales_price"),
            channel("web_sales", "ws_item_sk", "ws_sold_date_sk", "ws_bill_addr_sk", "ws_ext_sales_price"),
        ]
    )
    result = (
        _grouped_pandas_aggregates(combined, "i_item_id", {"total_sales": ("total_sales", "sum")})
        .sort_values(list(sort_by))
        .head(100)
    )
    return _none_for_null(result, list(result.columns))


def _sales_date_window(query_id: int, default: str, days: int = 30) -> tuple[Any, Any]:
    sales_date = get_parameters(query_id).get("sales_date", default)
    start_date = datetime.strptime(sales_date, "%Y-%m-%d").date() if isinstance(sales_date, str) else sales_date
    return start_date, start_date + timedelta(days=days)


def _date_window_expression(ctx: DataFrameContext, query_id: int, default: str, days: int = 30) -> Any:
    start_date, end_date = _sales_date_window(query_id, default, days)
    col = ctx.col
    lit = ctx.lit
    return ctx.get_table("date_dim").filter((col("d_date") >= lit(start_date)) & (col("d_date") <= lit(end_date)))


def _date_window_pandas(ctx: DataFrameContext, query_id: int, default: str, days: int = 30) -> Any:
    import pandas as pd

    start_date, end_date = _sales_date_window(query_id, default, days)
    date_dim = ctx.get_table("date_dim").copy()
    if len(date_dim) > 0 and hasattr(date_dim["d_date"].iloc[0], "date"):
        date_dim["d_date"] = pd.to_datetime(date_dim["d_date"]).dt.date
    return date_dim[(date_dim["d_date"] >= start_date) & (date_dim["d_date"] <= end_date)][["d_date_sk"]]


def _sales_returns_rollup_expression(ctx: DataFrameContext, combined: Any) -> Any:
    from .rollup_helper import expand_rollup_expression

    col = ctx.col
    return (
        expand_rollup_expression(
            combined,
            group_cols=["channel", "id"],
            agg_exprs=[
                col("sales").sum().alias("sales"),
                col("returns").sum().alias("returns"),
                col("profit").sum().alias("profit"),
            ],
            ctx=ctx,
            count_sources={"sales": "sales", "returns": "returns", "profit": "profit"},
        )
        .select(["channel", "id", "sales", "returns", "profit"])
        .sort(["channel", "id"], nulls_last=True)
        .limit(100)
    )


def _sales_returns_rollup_pandas(ctx: DataFrameContext, combined: Any) -> Any:
    from .rollup_helper import expand_rollup_pandas

    return (
        expand_rollup_pandas(
            combined,
            group_cols=["channel", "id"],
            agg_dict={
                "sales": ("sales", "sum"),
                "returns": ("returns", "sum"),
                "profit": ("profit", "sum"),
            },
            ctx=ctx,
            count_sources={"sales": "sales", "returns": "returns", "profit": "profit"},
        )[["channel", "id", "sales", "returns", "profit"]]
        .sort_values(["channel", "id"])
        .head(100)
    )


_Q49_CHANNEL_SPECS = (
    (
        "web_sales",
        "web_returns",
        ("ws_order_number", "ws_item_sk"),
        ("wr_order_number", "wr_item_sk"),
        "ws_sold_date_sk",
        "ws_item_sk",
        "wr_return_quantity",
        "wr_return_amt",
        "ws_net_paid",
        "ws_net_profit",
        "ws_quantity",
        "web",
    ),
    (
        "catalog_sales",
        "catalog_returns",
        ("cs_order_number", "cs_item_sk"),
        ("cr_order_number", "cr_item_sk"),
        "cs_sold_date_sk",
        "cs_item_sk",
        "cr_return_quantity",
        "cr_return_amount",
        "cs_net_paid",
        "cs_net_profit",
        "cs_quantity",
        "catalog",
    ),
    (
        "store_sales",
        "store_returns",
        ("ss_ticket_number", "ss_item_sk"),
        ("sr_ticket_number", "sr_item_sk"),
        "ss_sold_date_sk",
        "ss_item_sk",
        "sr_return_quantity",
        "sr_return_amt",
        "ss_net_paid",
        "ss_net_profit",
        "ss_quantity",
        "store",
    ),
)


def _three_channel_return_ratio_expression(ctx: DataFrameContext, query_id: int) -> Any:
    params = get_parameters(query_id)
    year = params.get("year", 2001)
    month = params.get("month", 12)
    col = ctx.col
    lit = ctx.lit
    date_filtered = ctx.get_table("date_dim").filter((col("d_year") == lit(year)) & (col("d_moy") == lit(month)))

    def channel(
        sales_table: str,
        returns_table: str,
        left_on: list[str],
        right_on: list[str],
        date_col: str,
        item_col: str,
        return_qty_col: str,
        return_amt_col: str,
        net_paid_col: str,
        net_profit_col: str,
        quantity_col: str,
        channel_name: str,
    ) -> Any:
        joined = (
            ctx.get_table(sales_table)
            .join(ctx.get_table(returns_table), left_on=list(left_on), right_on=list(right_on), how="left")
            .join(date_filtered, left_on=date_col, right_on="d_date_sk")
            .filter(
                (col(return_amt_col) > lit(10000))
                & (col(net_profit_col) > lit(1))
                & (col(net_paid_col) > lit(0))
                & (col(quantity_col) > lit(0))
            )
        )
        return (
            joined.group_by(item_col)
            .agg(
                [
                    (
                        col(return_qty_col).fill_null(0).sum().cast_float64()
                        / col(quantity_col).fill_null(0).sum().cast_float64()
                    ).alias("return_ratio"),
                    (
                        col(return_amt_col).fill_null(0).sum().cast_float64()
                        / col(net_paid_col).fill_null(0).sum().cast_float64()
                    ).alias("currency_ratio"),
                ]
            )
            .with_columns(
                [
                    col("return_ratio").rank(method="min").alias("return_rank"),
                    col("currency_ratio").rank(method="min").alias("currency_rank"),
                ]
            )
            .filter((col("return_rank") <= lit(10)) | (col("currency_rank") <= lit(10)))
            .with_columns(lit(channel_name).alias("channel"))
            .select(["channel", col(item_col).alias("item"), "return_ratio", "return_rank", "currency_rank"])
        )

    return (
        ctx.concat([channel(*spec) for spec in _Q49_CHANNEL_SPECS])
        .sort(["channel", "return_rank", "currency_rank", "item"])
        .limit(100)
    )


def _three_channel_return_ratio_pandas(ctx: DataFrameContext, query_id: int) -> Any:
    params = get_parameters(query_id)
    year = params.get("year", 2001)
    month = params.get("month", 12)
    date_filtered = ctx.get_table("date_dim")
    date_filtered = date_filtered[(date_filtered["d_year"] == year) & (date_filtered["d_moy"] == month)][["d_date_sk"]]

    def channel(
        sales_table: str,
        returns_table: str,
        left_on: list[str],
        right_on: list[str],
        date_col: str,
        item_col: str,
        return_qty_col: str,
        return_amt_col: str,
        net_paid_col: str,
        net_profit_col: str,
        quantity_col: str,
        channel_name: str,
    ) -> Any:
        joined = ctx.get_table(sales_table).merge(
            ctx.get_table(returns_table), left_on=list(left_on), right_on=list(right_on), how="left"
        )
        joined = joined.merge(date_filtered, left_on=date_col, right_on="d_date_sk")
        joined = joined[
            (joined[return_amt_col] > 10000)
            & (joined[net_profit_col] > 1)
            & (joined[net_paid_col] > 0)
            & (joined[quantity_col] > 0)
        ]
        grouped = joined.groupby(item_col, as_index=False).agg(
            return_qty_sum=(return_qty_col, lambda values: values.fillna(0).sum()),
            sales_qty_sum=(quantity_col, lambda values: values.fillna(0).sum()),
            return_amt_sum=(return_amt_col, lambda values: values.fillna(0).sum()),
            net_paid_sum=(net_paid_col, lambda values: values.fillna(0).sum()),
        )
        grouped["return_ratio"] = grouped["return_qty_sum"] / grouped["sales_qty_sum"]
        grouped["currency_ratio"] = grouped["return_amt_sum"] / grouped["net_paid_sum"]
        grouped["return_rank"] = grouped["return_ratio"].rank(method="min")
        grouped["currency_rank"] = grouped["currency_ratio"].rank(method="min")
        filtered = grouped[(grouped["return_rank"] <= 10) | (grouped["currency_rank"] <= 10)]
        filtered = filtered.assign(channel=channel_name)
        return filtered[["channel", item_col, "return_ratio", "return_rank", "currency_rank"]].rename(
            columns={item_col: "item"}
        )

    return (
        ctx.concat([channel(*spec) for spec in _Q49_CHANNEL_SPECS])
        .sort_values(["channel", "return_rank", "currency_rank", "item"])
        .head(100)
    )


def _q80_channel_expression(
    ctx: DataFrameContext,
    date_filtered: Any,
    item_filtered: Any,
    promo_filtered: Any,
    spec: tuple[str, ...],
) -> Any:
    (
        sales_table,
        returns_table,
        sales_item_col,
        sales_order_col,
        returns_item_col,
        returns_order_col,
        sales_date_col,
        dim_table,
        sales_dim_col,
        dim_key_col,
        dim_id_col,
        sales_promo_col,
        sales_value_col,
        returns_value_col,
        sales_profit_col,
        returns_loss_col,
        channel_name,
        id_prefix,
    ) = spec
    col = ctx.col
    lit = ctx.lit
    return (
        ctx.get_table(sales_table)
        .join(
            ctx.get_table(returns_table),
            left_on=[sales_item_col, sales_order_col],
            right_on=[returns_item_col, returns_order_col],
            how="left",
        )
        .join(date_filtered, left_on=sales_date_col, right_on="d_date_sk")
        .join(ctx.get_table(dim_table), left_on=sales_dim_col, right_on=dim_key_col)
        .join(item_filtered, left_on=sales_item_col, right_on="i_item_sk")
        .join(promo_filtered, left_on=sales_promo_col, right_on="p_promo_sk")
        .group_by(dim_id_col)
        .agg(
            [
                col(sales_value_col).sum().alias("sales"),
                col(returns_value_col).fill_null(0).sum().alias("returns"),
                (col(sales_profit_col) - col(returns_loss_col).fill_null(0)).sum().alias("profit"),
            ]
        )
        .with_columns([lit(channel_name).alias("channel"), (lit(id_prefix) + col(dim_id_col)).alias("id")])
        .select(["channel", "id", "sales", "returns", "profit"])
    )


def _q80_channel_pandas(
    ctx: DataFrameContext,
    date_filtered: Any,
    item_filtered: Any,
    promo_filtered: Any,
    spec: tuple[str, ...],
) -> Any:
    (
        sales_table,
        returns_table,
        sales_item_col,
        sales_order_col,
        returns_item_col,
        returns_order_col,
        sales_date_col,
        dim_table,
        sales_dim_col,
        dim_key_col,
        dim_id_col,
        sales_promo_col,
        sales_value_col,
        returns_value_col,
        sales_profit_col,
        returns_loss_col,
        channel_name,
        id_prefix,
    ) = spec
    joined = ctx.get_table(sales_table).merge(
        ctx.get_table(returns_table),
        left_on=[sales_item_col, sales_order_col],
        right_on=[returns_item_col, returns_order_col],
        how="left",
    )
    joined = joined.merge(date_filtered, left_on=sales_date_col, right_on="d_date_sk")
    joined = joined.merge(
        ctx.get_table(dim_table)[[dim_key_col, dim_id_col]], left_on=sales_dim_col, right_on=dim_key_col
    )
    joined = joined.merge(item_filtered, left_on=sales_item_col, right_on="i_item_sk")
    joined = joined.merge(promo_filtered, left_on=sales_promo_col, right_on="p_promo_sk")
    joined[returns_value_col] = joined[returns_value_col].fillna(0)
    joined[returns_loss_col] = joined[returns_loss_col].fillna(0)
    joined["profit_row"] = joined[sales_profit_col] - joined[returns_loss_col]
    result = joined.groupby(dim_id_col, as_index=False).agg(
        sales=(sales_value_col, "sum"),
        returns=(returns_value_col, "sum"),
        profit=("profit_row", "sum"),
    )
    result["channel"] = channel_name
    result["id"] = id_prefix + result[dim_id_col].astype(str)
    return result[["channel", "id", "sales", "returns", "profit"]]


_Q80_CHANNEL_SPECS: tuple[tuple[str, ...], ...] = (
    (
        "store_sales",
        "store_returns",
        "ss_item_sk",
        "ss_ticket_number",
        "sr_item_sk",
        "sr_ticket_number",
        "ss_sold_date_sk",
        "store",
        "ss_store_sk",
        "s_store_sk",
        "s_store_id",
        "ss_promo_sk",
        "ss_ext_sales_price",
        "sr_return_amt",
        "ss_net_profit",
        "sr_net_loss",
        "store channel",
        "store",
    ),
    (
        "catalog_sales",
        "catalog_returns",
        "cs_item_sk",
        "cs_order_number",
        "cr_item_sk",
        "cr_order_number",
        "cs_sold_date_sk",
        "catalog_page",
        "cs_catalog_page_sk",
        "cp_catalog_page_sk",
        "cp_catalog_page_id",
        "cs_promo_sk",
        "cs_ext_sales_price",
        "cr_return_amount",
        "cs_net_profit",
        "cr_net_loss",
        "catalog channel",
        "catalog_page",
    ),
    (
        "web_sales",
        "web_returns",
        "ws_item_sk",
        "ws_order_number",
        "wr_item_sk",
        "wr_order_number",
        "ws_sold_date_sk",
        "web_site",
        "ws_web_site_sk",
        "web_site_sk",
        "web_site_id",
        "ws_promo_sk",
        "ws_ext_sales_price",
        "wr_return_amt",
        "ws_net_profit",
        "wr_net_loss",
        "web channel",
        "web_site",
    ),
)


def _q77_expression_channel(
    ctx: DataFrameContext,
    date_filtered: Any,
    channel_name: str,
    sales_spec: tuple[str, ...],
    returns_spec: tuple[str, ...],
    join_how: str,
) -> Any:
    (
        sales_table,
        sales_date_col,
        sales_dim_table,
        sales_dim_left,
        sales_dim_right,
        sales_group,
        sales_col,
        profit_col,
    ) = sales_spec
    (
        returns_table,
        returns_date_col,
        returns_dim_table,
        returns_dim_left,
        returns_dim_right,
        returns_group,
        returns_col,
        loss_col,
    ) = returns_spec
    col = ctx.col
    lit = ctx.lit
    sales = ctx.get_table(sales_table).join(date_filtered, left_on=sales_date_col, right_on="d_date_sk")
    if sales_dim_table:
        sales = sales.join(ctx.get_table(sales_dim_table), left_on=sales_dim_left, right_on=sales_dim_right)
    sales = sales.group_by(sales_group).agg(
        [
            ctx.when(col(sales_col).count() > lit(0)).then(col(sales_col).sum()).otherwise(lit(None)).alias("sales"),
            ctx.when(col(profit_col).count() > lit(0)).then(col(profit_col).sum()).otherwise(lit(None)).alias("profit"),
        ]
    )
    returns = ctx.get_table(returns_table).join(date_filtered, left_on=returns_date_col, right_on="d_date_sk")
    if returns_dim_table:
        returns = returns.join(ctx.get_table(returns_dim_table), left_on=returns_dim_left, right_on=returns_dim_right)
    returns = returns.group_by(returns_group).agg(
        [
            ctx.when(col(returns_col).count() > lit(0))
            .then(col(returns_col).sum())
            .otherwise(lit(None))
            .alias("returns"),
            ctx.when(col(loss_col).count() > lit(0))
            .then(col(loss_col).sum())
            .otherwise(lit(None))
            .alias("profit_loss"),
        ]
    )
    join_kwargs: dict[str, Any] = {"how": join_how}
    if join_how != "cross":
        join_kwargs.update(left_on=sales_group, right_on=returns_group)
    joined = sales.join(returns, **join_kwargs)
    returns_expr = col("returns") if join_how == "cross" else col("returns").fill_null(0)
    loss_expr = col("profit_loss") if join_how == "cross" else col("profit_loss").fill_null(0)
    return joined.with_columns(
        [
            lit(channel_name).alias("channel"),
            col(sales_group).alias("id"),
            returns_expr.alias("returns"),
            (col("profit") - loss_expr).alias("profit_final"),
        ]
    ).select(["channel", "id", "sales", "returns", col("profit_final").alias("profit")])


def _q77_pandas_channel(
    ctx: DataFrameContext,
    date_filtered: Any,
    channel_name: str,
    sales_spec: tuple[str, ...],
    returns_spec: tuple[str, ...],
    join_how: str,
) -> Any:
    (
        sales_table,
        sales_date_col,
        sales_dim_table,
        sales_dim_left,
        sales_dim_right,
        sales_group,
        sales_col,
        profit_col,
    ) = sales_spec
    (
        returns_table,
        returns_date_col,
        returns_dim_table,
        returns_dim_left,
        returns_dim_right,
        returns_group,
        returns_col,
        loss_col,
    ) = returns_spec
    sales = ctx.get_table(sales_table).merge(date_filtered, left_on=sales_date_col, right_on="d_date_sk")
    if sales_dim_table:
        sales = sales.merge(
            ctx.get_table(sales_dim_table)[[sales_dim_right]], left_on=sales_dim_left, right_on=sales_dim_right
        )
    import pandas as _pd

    sales = sales.groupby(sales_group, as_index=False, dropna=False).agg(
        sales=(sales_col, "sum"),
        n_sales=(sales_col, "count"),
        profit=(profit_col, "sum"),
        n_profit=(profit_col, "count"),
    )
    sales["sales"] = _pd.Series(
        [value if count > 0 else None for value, count in zip(sales["sales"], sales["n_sales"])], dtype=object
    )
    sales["profit"] = _pd.Series(
        [value if count > 0 else None for value, count in zip(sales["profit"], sales["n_profit"])], dtype=object
    )
    returns = ctx.get_table(returns_table).merge(date_filtered, left_on=returns_date_col, right_on="d_date_sk")
    if returns_dim_table:
        returns = returns.merge(
            ctx.get_table(returns_dim_table)[[returns_dim_right]],
            left_on=returns_dim_left,
            right_on=returns_dim_right,
        )
    returns = returns.groupby(returns_group, as_index=False, dropna=False).agg(
        returns=(returns_col, "sum"),
        n_returns=(returns_col, "count"),
        profit_loss=(loss_col, "sum"),
        n_loss=(loss_col, "count"),
    )
    returns["returns"] = _pd.Series(
        [value if count > 0 else None for value, count in zip(returns["returns"], returns["n_returns"])], dtype=object
    )
    returns["profit_loss"] = _pd.Series(
        [value if count > 0 else None for value, count in zip(returns["profit_loss"], returns["n_loss"])],
        dtype=object,
    )
    if join_how == "cross":
        sales["_key"] = 1
        returns["_key"] = 1
        result = sales.merge(returns, on="_key").drop(columns=["_key"])
    else:
        result = sales.merge(returns, left_on=sales_group, right_on=returns_group, how=join_how)
        result["returns"] = result["returns"].where(result["returns"].notnull(), 0)
        result["profit_loss"] = result["profit_loss"].where(result["profit_loss"].notnull(), 0)
    result["profit"] = [
        None if profit is None or loss is None else profit - loss
        for profit, loss in zip(result["profit"], result["profit_loss"])
    ]
    result["channel"] = channel_name
    result[sales_group] = result[sales_group].astype(object).where(result[sales_group].notnull(), None)
    result["id"] = result[sales_group]
    return result[["channel", "id", "sales", "returns", "profit"]]


_Q77_EXPRESSION_CHANNEL_SPECS = (
    (
        "store channel",
        (
            "store_sales",
            "ss_sold_date_sk",
            "store",
            "ss_store_sk",
            "s_store_sk",
            "ss_store_sk",
            "ss_ext_sales_price",
            "ss_net_profit",
        ),
        (
            "store_returns",
            "sr_returned_date_sk",
            "store",
            "sr_store_sk",
            "s_store_sk",
            "sr_store_sk",
            "sr_return_amt",
            "sr_net_loss",
        ),
        "left",
    ),
    (
        "catalog channel",
        ("catalog_sales", "cs_sold_date_sk", "", "", "", "cs_call_center_sk", "cs_ext_sales_price", "cs_net_profit"),
        ("catalog_returns", "cr_returned_date_sk", "", "", "", "cr_call_center_sk", "cr_return_amount", "cr_net_loss"),
        "cross",
    ),
    (
        "web channel",
        (
            "web_sales",
            "ws_sold_date_sk",
            "web_page",
            "ws_web_page_sk",
            "wp_web_page_sk",
            "ws_web_page_sk",
            "ws_ext_sales_price",
            "ws_net_profit",
        ),
        (
            "web_returns",
            "wr_returned_date_sk",
            "web_page",
            "wr_web_page_sk",
            "wp_web_page_sk",
            "wr_web_page_sk",
            "wr_return_amt",
            "wr_net_loss",
        ),
        "left",
    ),
)
_Q77_PANDAS_CHANNEL_SPECS = (
    (
        "store channel",
        (
            "store_sales",
            "ss_sold_date_sk",
            "store",
            "ss_store_sk",
            "s_store_sk",
            "s_store_sk",
            "ss_ext_sales_price",
            "ss_net_profit",
        ),
        (
            "store_returns",
            "sr_returned_date_sk",
            "store",
            "sr_store_sk",
            "s_store_sk",
            "s_store_sk",
            "sr_return_amt",
            "sr_net_loss",
        ),
        "left",
    ),
    _Q77_EXPRESSION_CHANNEL_SPECS[1],
    (
        "web channel",
        (
            "web_sales",
            "ws_sold_date_sk",
            "web_page",
            "ws_web_page_sk",
            "wp_web_page_sk",
            "wp_web_page_sk",
            "ws_ext_sales_price",
            "ws_net_profit",
        ),
        (
            "web_returns",
            "wr_returned_date_sk",
            "web_page",
            "wr_web_page_sk",
            "wp_web_page_sk",
            "wp_web_page_sk",
            "wr_return_amt",
            "wr_net_loss",
        ),
        "left",
    ),
)


def _q58_balance_expression(ctx: DataFrameContext, result: Any) -> Any:
    col = ctx.col
    lit = ctx.lit
    revenues = ("ss_item_rev", "cs_item_rev", "ws_item_rev")
    predicate = None
    for left in revenues:
        for right in revenues:
            if left == right:
                continue
            clause = (col(left) >= col(right) * lit(0.9)) & (col(left) <= col(right) * lit(1.1))
            predicate = clause if predicate is None else predicate & clause
    return result.filter(predicate)


def _q58_balance_pandas(result: Any) -> Any:
    revenues = ("ss_item_rev", "cs_item_rev", "ws_item_rev")
    mask = True
    for left in revenues:
        for right in revenues:
            if left != right:
                mask = mask & (result[left] >= result[right] * 0.9) & (result[left] <= result[right] * 1.1)
    return result[mask]


def _promotion_sales_expression(
    ctx: DataFrameContext,
    query_id: int,
    sales_table: str,
    cdemo_key: str,
    date_key: str,
    item_key: str,
    promo_key: str,
    quantity_col: str,
    list_price_col: str,
    coupon_col: str,
    sales_price_col: str,
) -> Any:
    params = get_parameters(query_id)
    year = params.get("year", 2000)
    gender = params.get("gender", "M")
    marital_status = params.get("marital_status", "S")
    education = params.get("education", "College")
    col, lit = ctx.col, ctx.lit
    return (
        ctx.get_table(sales_table)
        .join(ctx.get_table("customer_demographics"), left_on=cdemo_key, right_on="cd_demo_sk")
        .join(ctx.get_table("date_dim"), left_on=date_key, right_on="d_date_sk")
        .join(ctx.get_table("item"), left_on=item_key, right_on="i_item_sk")
        .join(ctx.get_table("promotion"), left_on=promo_key, right_on="p_promo_sk")
        .filter(
            (col("cd_gender") == lit(gender))
            & (col("cd_marital_status") == lit(marital_status))
            & (col("cd_education_status") == lit(education))
            & ((col("p_channel_email") == lit("N")) | (col("p_channel_event") == lit("N")))
            & (col("d_year") == lit(year))
        )
        .group_by("i_item_id")
        .agg(
            col(quantity_col).mean().alias("agg1"),
            col(list_price_col).mean().alias("agg2"),
            col(coupon_col).mean().alias("agg3"),
            col(sales_price_col).mean().alias("agg4"),
        )
        .sort("i_item_id")
        .limit(100)
    )


def _promotion_sales_pandas(
    ctx: DataFrameContext,
    query_id: int,
    sales_table: str,
    cdemo_key: str,
    date_key: str,
    item_key: str,
    promo_key: str,
    quantity_col: str,
    list_price_col: str,
    coupon_col: str,
    sales_price_col: str,
) -> Any:
    params = get_parameters(query_id)
    year = params.get("year", 2000)
    gender = params.get("gender", "M")
    marital_status = params.get("marital_status", "S")
    education = params.get("education", "College")
    merged = ctx.get_table(sales_table).merge(
        ctx.get_table("customer_demographics"), left_on=cdemo_key, right_on="cd_demo_sk"
    )
    merged = merged.merge(ctx.get_table("date_dim"), left_on=date_key, right_on="d_date_sk")
    merged = merged.merge(ctx.get_table("item"), left_on=item_key, right_on="i_item_sk")
    merged = merged.merge(ctx.get_table("promotion"), left_on=promo_key, right_on="p_promo_sk")
    filtered = merged[
        (merged["cd_gender"] == gender)
        & (merged["cd_marital_status"] == marital_status)
        & (merged["cd_education_status"] == education)
        & ((merged["p_channel_email"] == "N") | (merged["p_channel_event"] == "N"))
        & (merged["d_year"] == year)
    ]
    result = (
        filtered.groupby(["i_item_id"], as_index=False)
        .agg(
            agg1=(quantity_col, "mean"),
            agg2=(list_price_col, "mean"),
            agg3=(coupon_col, "mean"),
            agg4=(sales_price_col, "mean"),
        )
        .sort_values(["i_item_id"])
        .head(100)
    )
    return _none_for_null(result, ["agg1", "agg2", "agg3", "agg4"])


def _web_multi_warehouse_orders_expression(ctx: DataFrameContext, web_sales: Any, optimized: bool) -> Any:
    col, lit = ctx.col, ctx.lit
    if optimized and getattr(ctx, "platform", "polars") != "datafusion":
        return (
            web_sales.group_by("ws_order_number")
            .agg(col("ws_warehouse_sk").n_unique().alias("num_warehouses"))
            .filter(col("num_warehouses") > lit(1))
            .select("ws_order_number")
        )
    order_warehouses = web_sales.select(["ws_order_number", "ws_warehouse_sk"]).unique()
    return (
        order_warehouses.join(
            order_warehouses.rename({"ws_warehouse_sk": "ws_warehouse_sk_2"}),
            on="ws_order_number",
        )
        .filter(col("ws_warehouse_sk") != col("ws_warehouse_sk_2"))
        .select(["ws_order_number"])
        .unique()
    )


def _web_multi_warehouse_expression(
    ctx: DataFrameContext,
    query_id: int,
    return_mode: str,
    optimized_multi_warehouse: bool,
) -> Any:
    params = get_parameters(query_id)
    year = params.get("year", 1999)
    month = params.get("month", 2)
    states = params.get("states", ["TX", "OR", "AZ"])
    col, lit = ctx.col, ctx.lit

    web_sales, web_returns, date_dim, customer_address, web_site = _tables(
        ctx, "web_sales", "web_returns", "date_dim", "customer_address", "web_site"
    )

    start_date = datetime(year, month, 1).date()
    end_date = start_date + timedelta(days=60)
    date_filtered = date_dim.filter((col("d_date") >= lit(start_date)) & (col("d_date") <= lit(end_date)))
    ca_filtered = customer_address.filter(col("ca_state").is_in(states))
    ws_filtered = web_site.filter(col("web_company_name") == lit("pri"))
    multi_warehouse_orders = _web_multi_warehouse_orders_expression(ctx, web_sales, optimized_multi_warehouse)

    if return_mode == "semi":
        returned_orders = (
            web_returns.select(["wr_order_number"])
            .join(multi_warehouse_orders, left_on="wr_order_number", right_on="ws_order_number")
            .select(["wr_order_number"])
            .unique()
        )
    else:
        returned_orders = web_returns.select(["wr_order_number"]).unique()

    result = (
        web_sales.join(date_filtered, left_on="ws_ship_date_sk", right_on="d_date_sk")
        .join(ca_filtered, left_on="ws_ship_addr_sk", right_on="ca_address_sk")
        .join(ws_filtered, left_on="ws_web_site_sk", right_on="web_site_sk")
        .join(multi_warehouse_orders, on="ws_order_number", how="semi")
        .join(returned_orders, left_on="ws_order_number", right_on="wr_order_number", how=return_mode)
    )
    tallied = result.select(
        [
            col("ws_order_number").n_unique().alias("order count"),
            ctx.sum("ws_ext_ship_cost").alias("ship_cost"),
            ctx.sum("ws_net_profit").alias("net_profit"),
            col("ws_order_number").count().alias("n"),
        ]
    )
    return tallied.select(
        [
            col("order count"),
            ctx.when(col("n") > lit(0)).then(col("ship_cost")).otherwise(lit(None)).alias("total shipping cost"),
            ctx.when(col("n") > lit(0)).then(col("net_profit")).otherwise(lit(None)).alias("total net profit"),
        ]
    )


def _web_multi_warehouse_pandas(
    ctx: DataFrameContext,
    query_id: int,
    return_mode: str,
    _optimized_multi_warehouse: bool,
) -> Any:
    import pandas as pd

    params = get_parameters(query_id)
    year = params.get("year", 1999)
    month = params.get("month", 2)
    states = params.get("states", ["TX", "OR", "AZ"])

    web_sales, web_returns, date_dim, customer_address, web_site = _tables(
        ctx, "web_sales", "web_returns", "date_dim", "customer_address", "web_site"
    )

    start_date = datetime(year, month, 1).date()
    end_date = start_date + timedelta(days=60)
    date_dim = date_dim.copy()
    if len(date_dim) > 0 and hasattr(date_dim["d_date"].iloc[0], "date"):
        date_dim["d_date"] = pd.to_datetime(date_dim["d_date"]).dt.date
    date_filtered = date_dim[(date_dim["d_date"] >= start_date) & (date_dim["d_date"] <= end_date)][["d_date_sk"]]
    ca_filtered = customer_address[customer_address["ca_state"].isin(states)]
    ws_filtered = web_site[web_site["web_company_name"] == "pri"]

    order_warehouses = web_sales[["ws_order_number", "ws_warehouse_sk"]].drop_duplicates()
    ow_count = ctx.groupby_size(order_warehouses, "ws_order_number", name="wh_count")
    multi_warehouse_orders = ow_count[ow_count["wh_count"] > 1][["ws_order_number"]]

    result = web_sales.merge(date_filtered, left_on="ws_ship_date_sk", right_on="d_date_sk")
    result = result.merge(ca_filtered[["ca_address_sk"]], left_on="ws_ship_addr_sk", right_on="ca_address_sk")
    result = result.merge(ws_filtered[["web_site_sk"]], left_on="ws_web_site_sk", right_on="web_site_sk")
    result = result.merge(multi_warehouse_orders, on="ws_order_number")

    returned_orders = web_returns[["wr_order_number"]].drop_duplicates()
    if return_mode == "anti":
        result = result.merge(
            returned_orders, left_on="ws_order_number", right_on="wr_order_number", how="left", indicator=True
        )
        result = result[result["_merge"] == "left_only"]
    else:
        returned_multi_wh = returned_orders.merge(
            multi_warehouse_orders,
            left_on="wr_order_number",
            right_on="ws_order_number",
        )[["wr_order_number"]]
        result = result.merge(returned_multi_wh, left_on="ws_order_number", right_on="wr_order_number")

    if len(result) == 0:
        return pd.DataFrame({"order count": [0], "total shipping cost": [None], "total net profit": [None]})
    return pd.DataFrame(
        {
            "order count": [result["ws_order_number"].nunique()],
            "total shipping cost": [result["ws_ext_ship_cost"].sum()],
            "total net profit": [result["ws_net_profit"].sum()],
        }
    )


def _inventory_item_expression(
    ctx: DataFrameContext,
    query_id: int,
    source_table: str,
    source_key: str,
    price_param_names: tuple[str, str],
    price_defaults: tuple[int, int],
    inventory_first: bool,
    manufact_param: str = "manufact_ids",
    manufact_default: tuple[int, ...] = (),
    sales_date_default: str = "2001-06-02",
) -> Any:
    params = get_parameters(query_id)
    start_date, end_date = _sales_date_window(query_id, sales_date_default, days=60)
    price_min = params.get(price_param_names[0], price_defaults[0])
    price_max = params.get(price_param_names[1], price_defaults[1])
    manufact_ids = params.get(manufact_param, list(manufact_default))
    col, lit = ctx.col, ctx.lit
    price_filter = (col("i_current_price") >= lit(price_min)) & (col("i_current_price") <= lit(price_max))
    date_filter = (col("d_date") >= lit(start_date)) & (col("d_date") <= lit(end_date))
    inventory_filter = (col("inv_quantity_on_hand") >= lit(100)) & (col("inv_quantity_on_hand") <= lit(500))
    manufact_filter = col("i_manufact_id").is_in(manufact_ids)
    return (
        ctx.get_table("item")
        .join(ctx.get_table("inventory"), left_on="i_item_sk", right_on="inv_item_sk")
        .join(ctx.get_table("date_dim"), left_on="inv_date_sk", right_on="d_date_sk")
        .join(ctx.get_table(source_table), left_on="i_item_sk", right_on=source_key)
        .filter(
            price_filter
            & manufact_filter
            & (inventory_filter & date_filter if inventory_first else date_filter & inventory_filter)
        )
        .select("i_item_id", "i_item_desc", "i_current_price")
        .unique()
        .sort("i_item_id")
        .limit(100)
    )


def _inventory_item_pandas(
    ctx: DataFrameContext,
    query_id: int,
    source_table: str,
    source_key: str,
    price_param_names: tuple[str, str],
    price_defaults: tuple[int, int],
    _inventory_first: bool,
    manufact_param: str = "manufact_ids",
    manufact_default: tuple[int, ...] = (),
    sales_date_default: str = "2001-06-02",
) -> Any:
    import pandas as pd

    params = get_parameters(query_id)
    start_date, end_date = _sales_date_window(query_id, sales_date_default, days=60)
    price_min = params.get(price_param_names[0], price_defaults[0])
    price_max = params.get(price_param_names[1], price_defaults[1])
    manufact_ids = params.get(manufact_param, list(manufact_default))

    item, inventory, date_dim = _tables(ctx, "item", "inventory", "date_dim")
    source = ctx.get_table(source_table)

    item_filtered = item[
        (item["i_current_price"] >= price_min)
        & (item["i_current_price"] <= price_max)
        & (item["i_manufact_id"].isin(manufact_ids))
    ]
    inv_filtered = inventory[(inventory["inv_quantity_on_hand"] >= 100) & (inventory["inv_quantity_on_hand"] <= 500)]
    date_dim = date_dim.copy()
    if len(date_dim) > 0 and hasattr(date_dim["d_date"].iloc[0], "date"):
        date_dim["d_date"] = pd.to_datetime(date_dim["d_date"]).dt.date
    date_filtered = date_dim[(date_dim["d_date"] >= start_date) & (date_dim["d_date"] <= end_date)]
    merged = item_filtered.merge(inv_filtered, left_on="i_item_sk", right_on="inv_item_sk")
    merged = merged.merge(date_filtered, left_on="inv_date_sk", right_on="d_date_sk")
    merged = merged[merged["i_item_sk"].isin(_to_list(source[source_key].unique()))]
    return (
        merged[["i_item_id", "i_item_desc", "i_current_price"]].drop_duplicates().sort_values(["i_item_id"]).head(100)
    )


def _load_helper_query_specs() -> list[dict[str, Any]]:
    with (Path(__file__).with_name("helper_query_specs.yaml")).open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    specs = payload.get("helper_queries", [])
    if not isinstance(specs, list):
        raise ValueError("TPC-DS helper query specs must be a list")
    return specs


_HELPER_QUERY_SPECS = _load_helper_query_specs()


def _make_helper_impl(query_id: int, family: str, helper: Any, helper_args: tuple[Any, ...]) -> QueryImpl:
    def impl(ctx: DataFrameContext) -> Any:
        return helper(ctx, query_id, *helper_args)

    impl.__name__ = f"q{query_id}_{family}_impl"
    impl.__qualname__ = impl.__name__
    return impl


for _spec in _HELPER_QUERY_SPECS:
    _qid = _spec["query_id"]
    _register_generated_impl(
        _make_helper_impl(_qid, "expression", globals()[_spec["expression_helper"]], _spec["args"])
    )
    _register_generated_impl(_make_helper_impl(_qid, "pandas", globals()[_spec["pandas_helper"]], _spec["args"]))


_JoinedAggCondition = tuple[Any, ...]
_JoinedAggValue = Any


def _load_query_specs() -> dict[str, list[dict[str, Any]]]:
    with (Path(__file__).with_name("query_specs.yaml")).open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("TPC-DS DataFrame query specs must be a mapping")
    return payload


def _resolve_joined_agg_value(params: Any, value: _JoinedAggValue) -> Any:
    if isinstance(value, dict) and "param" in value:
        resolved = params.get(value["param"], value.get("default"))
        offsets = value.get("offsets")
        return [resolved + offset for offset in offsets] if offsets is not None else resolved
    return value


def _combine_conditions(conditions: list[Any], operator: str) -> Any:
    if not conditions:
        return None
    predicate = conditions[0]
    for condition in conditions[1:]:
        predicate = predicate | condition if operator == "or" else predicate & condition
    return predicate


def _joined_agg_expr_condition(ctx: DataFrameContext, params: Any, condition: _JoinedAggCondition) -> Any:
    op = condition[0]
    col = ctx.col
    lit = ctx.lit
    if op in {"and", "or"}:
        return _combine_conditions(
            [_joined_agg_expr_condition(ctx, params, child) for child in condition[1:]],
            op,
        )
    column = condition[1]
    if op == "eq":
        return col(column) == lit(_resolve_joined_agg_value(params, condition[2]))
    if op == "gt":
        return col(column) > lit(_resolve_joined_agg_value(params, condition[2]))
    if op == "between":
        return col(column).is_between(
            _resolve_joined_agg_value(params, condition[2]),
            _resolve_joined_agg_value(params, condition[3]),
        )
    if op == "in":
        return col(column).is_in(list(_resolve_joined_agg_value(params, condition[2])))
    if op == "prefix_eq":
        return col(column).cast_string().str.slice(0, condition[2]) == lit(
            _resolve_joined_agg_value(params, condition[3])
        )
    if op == "substr_in":
        return (
            col(column)
            .cast_string()
            .str.slice(0, _resolve_joined_agg_value(params, condition[2]))
            .is_in([str(value) for value in list(_resolve_joined_agg_value(params, condition[3]))])
        )
    if op == "prefix_ne_cols":
        return col(column).cast_string().str.slice(0, condition[3]) != col(condition[2]).cast_string().str.slice(
            0, condition[3]
        )
    if op == "starts_with":
        return col(column).str.starts_with(_resolve_joined_agg_value(params, condition[2]))
    if op == "ratio_gt":
        return (col(condition[1]) / col(condition[2])) > lit(_resolve_joined_agg_value(params, condition[3]))
    raise ValueError(f"Unsupported joined aggregate condition: {op}")


def _joined_agg_pandas_condition(frame: Any, params: Any, condition: _JoinedAggCondition) -> Any:
    op = condition[0]
    if op in {"and", "or"}:
        return _combine_conditions(
            [_joined_agg_pandas_condition(frame, params, child) for child in condition[1:]],
            op,
        )
    column = condition[1]
    if op == "eq":
        return frame[column] == _resolve_joined_agg_value(params, condition[2])
    if op == "gt":
        return frame[column] > _resolve_joined_agg_value(params, condition[2])
    if op == "between":
        return (frame[column] >= _resolve_joined_agg_value(params, condition[2])) & (
            frame[column] <= _resolve_joined_agg_value(params, condition[3])
        )
    if op == "in":
        return frame[column].isin(list(_resolve_joined_agg_value(params, condition[2])))
    if op == "prefix_eq":
        return frame[column].astype(str).str[: condition[2]] == _resolve_joined_agg_value(params, condition[3])
    if op == "substr_in":
        length = _resolve_joined_agg_value(params, condition[2])
        values = [str(value) for value in list(_resolve_joined_agg_value(params, condition[3]))]
        return frame[column].astype(str).str[:length].isin(values)
    if op == "prefix_ne_cols":
        both_present = frame[column].notnull() & frame[condition[2]].notnull()
        differs = frame[column].astype(str).str[: condition[3]] != frame[condition[2]].astype(str).str[: condition[3]]
        return differs & both_present
    if op == "starts_with":
        return frame[column].str.startswith(_resolve_joined_agg_value(params, condition[2]))
    if op == "ratio_gt":
        return (frame[condition[1]] / frame[condition[2]]) > _resolve_joined_agg_value(params, condition[3])
    raise ValueError(f"Unsupported joined aggregate condition: {op}")


def _sort_null_largest_expression(
    ctx: DataFrameContext, frame: Any, columns: list[str], descending: list[bool] | None = None
) -> Any:
    descending = [False] * len(columns) if descending is None else descending
    flags = [f"_null_{index}" for index in range(len(columns))]
    original = frame.columns
    keyed = frame.with_columns(*(ctx.col(name).is_null().alias(flag) for name, flag in zip(columns, flags)))
    by = [name for pair in zip(flags, columns) for name in pair]
    flag_descending = [value for value in descending for _ in range(2)]
    return keyed.sort(by, descending=flag_descending).select(original)


def _sort_null_largest_pandas(frame: Any, columns: list[str], descending: list[bool]) -> Any:
    if all(descending):
        return frame.sort_values(columns, ascending=[False] * len(columns), na_position="first")
    if not any(descending):
        return frame.sort_values(columns, ascending=[True] * len(columns), na_position="last")
    flags = [f"_null_{index}" for index in range(len(columns))]
    keyed = frame.assign(**{flag: frame[name].isna() for name, flag in zip(columns, flags)})
    by = [name for pair in zip(flags, columns) for name in pair]
    ascending = [not value for value in descending for _ in range(2)]
    ordered = keyed.sort_values(by, ascending=ascending, na_position="last")
    if hasattr(frame, "npartitions"):
        return ordered.map_partitions(_drop_columns, flags, meta=frame._meta)
    return ordered.drop(columns=flags)


def _drop_columns(partition: Any, columns: list[str]) -> Any:
    return partition.drop(columns=columns)


def _joined_agg_expression_impl(ctx: DataFrameContext, spec: dict[str, Any]) -> Any:
    params = get_parameters(spec["query_id"])
    frame = ctx.get_table(spec["base"])
    for join in spec["joins"]:
        table_name, left_on, right_on, *rest = join
        kwargs = {"how": rest[0]} if rest else {}
        frame = frame.join(ctx.get_table(table_name), left_on=left_on, right_on=right_on, **kwargs)
    predicate = _joined_agg_expr_condition(ctx, params, ("and", *spec.get("filters", ())))
    if predicate is not None:
        frame = frame.filter(predicate)
    result = frame.group_by(*spec["group_by"]).agg(
        *(
            (
                _sum_or_null_expression(ctx, ctx.col(source)) if func == "sum" else getattr(ctx.col(source), func)()
            ).alias(alias)
            for alias, source, func in spec["aggs"]
        )
    )
    post_filter = spec.get("post_filter")
    if post_filter is not None:
        result = result.filter(_joined_agg_expr_condition(ctx, params, post_filter))
    result = _sort_null_largest_expression(
        ctx,
        result,
        list(spec["sort_by"]),
        list(spec.get("descending", (False,) * len(spec["sort_by"]))),
    )
    limit = spec.get("limit", 100)
    result = result if limit is None else result.limit(limit)
    return result.select(*spec["select"]) if "select" in spec else result


def _joined_agg_pandas_impl(ctx: DataFrameContext, spec: dict[str, Any]) -> Any:
    params = get_parameters(spec["query_id"])
    frame = ctx.get_table(spec["base"])
    for join in spec["joins"]:
        table_name, left_on, right_on, *rest = join
        kwargs = {"how": rest[0]} if rest else {}
        frame = frame.merge(ctx.get_table(table_name), left_on=left_on, right_on=right_on, **kwargs)
    predicate = _joined_agg_pandas_condition(frame, params, ("and", *spec.get("filters", ())))
    if predicate is not None:
        frame = frame[predicate]
    result = _grouped_pandas_aggregates(
        frame,
        list(spec["group_by"]),
        {alias: (source, func) for alias, source, func in spec["aggs"]},
        dropna=False,
    )
    result = _none_for_null(result, list(spec["group_by"]))
    post_filter = spec.get("post_filter")
    if post_filter is not None:
        result = result[_joined_agg_pandas_condition(result, params, post_filter)]
    descending = spec.get("descending", (False,) * len(spec["sort_by"]))
    result = _sort_null_largest_pandas(result, list(spec["sort_by"]), list(descending))
    limit = spec.get("limit", 100)
    result = result if limit is None else result.head(limit)
    result = result[list(spec["select"])] if "select" in spec else result
    return _none_for_null(result, list(result.columns))


def _make_joined_agg_impl(query_id: int, family: str, spec: dict[str, Any]) -> QueryImpl:
    engine = _joined_agg_expression_impl if family == "expression" else _joined_agg_pandas_impl

    def impl(ctx: DataFrameContext) -> Any:
        return engine(ctx, spec)

    impl.__name__ = f"q{query_id}_{family}_impl"
    impl.__qualname__ = impl.__name__
    return impl


_QUERY_SPECS = _load_query_specs()


def _state_average_returns_expression_impl(ctx: DataFrameContext, spec: dict[str, Any]) -> Any:
    params = get_parameters(spec["query_id"])
    year = params.get("year", spec["year_default"])
    state = params.get("state", spec["state_default"])
    col = ctx.col
    lit = ctx.lit

    returns = ctx.get_table(spec["return_table"])
    date_dim, customer_address, customer = _tables(ctx, "date_dim", "customer_address", "customer")

    ctr = (
        returns.join(date_dim, left_on=spec["return_date_key"], right_on="d_date_sk")
        .join(customer_address, left_on=spec["return_addr_key"], right_on="ca_address_sk")
        .filter(col("d_year") == lit(year))
        .group_by(
            col(spec["return_customer_key"]).alias("ctr_customer_sk"),
            col("ca_state").alias("ctr_state"),
        )
        .agg(
            ctx.when(col(spec["return_amount"]).count() > lit(0))
            .then(col(spec["return_amount"]).sum())
            .otherwise(lit(None))
            .alias("ctr_total_return")
        )
    )
    state_avg = ctr.group_by("ctr_state").agg(col("ctr_total_return").mean().alias("state_avg"))
    ca_filtered = customer_address.filter(col("ca_state") == lit(state))
    result = (
        ctr.join(state_avg, on="ctr_state")
        .filter(col("ctr_total_return") > col("state_avg") * 1.2)
        .join(customer, left_on="ctr_customer_sk", right_on="c_customer_sk")
        .join(ca_filtered, left_on="c_current_addr_sk", right_on="ca_address_sk")
    )
    select_exprs = [
        col(item["column"]).alias(item["alias"]) if isinstance(item, dict) else item
        for item in spec["expression_select"]
    ]
    return (
        result.select(*select_exprs)
        .sort(*[item["alias"] if isinstance(item, dict) else item for item in spec["expression_select"]])
        .limit(100)
    )


def _state_average_returns_pandas_impl(ctx: DataFrameContext, spec: dict[str, Any]) -> Any:
    params = get_parameters(spec["query_id"])
    year = params.get("year", spec["year_default"])
    state = params.get("state", spec["state_default"])

    returns = ctx.get_table(spec["return_table"])
    date_dim, customer_address, customer = _tables(ctx, "date_dim", "customer_address", "customer")

    merged = returns.merge(date_dim, left_on=spec["return_date_key"], right_on="d_date_sk")
    merged = merged.merge(customer_address, left_on=spec["return_addr_key"], right_on="ca_address_sk")
    merged = merged[merged["d_year"] == year]
    ctr = merged.groupby([spec["return_customer_key"], "ca_state"], as_index=False, dropna=False).agg(
        ctr_total_return=(spec["return_amount"], "sum"), returned_rows=(spec["return_amount"], "count")
    )
    ctr["ctr_total_return"] = ctr["ctr_total_return"].where(ctr["returned_rows"] > 0)
    ctr = ctr.drop(columns="returned_rows").rename(
        columns={spec["return_customer_key"]: "ctr_customer_sk", "ca_state": "ctr_state"}
    )
    state_avg = ctr.groupby("ctr_state", as_index=False).agg(state_avg=("ctr_total_return", "mean"))
    ctr_filtered = ctr.merge(state_avg, on="ctr_state")
    ctr_filtered = ctr_filtered[ctr_filtered["ctr_total_return"] > ctr_filtered["state_avg"] * 1.2]
    ca_filtered = customer_address[customer_address["ca_state"] == state]
    result = ctr_filtered.merge(customer, left_on="ctr_customer_sk", right_on="c_customer_sk")
    result = result.merge(ca_filtered, left_on="c_current_addr_sk", right_on="ca_address_sk")
    cols = [column for column in spec["pandas_select"] if column in result.columns]
    result = result[cols].sort_values(cols).head(100)
    return _none_for_null(result, list(result.columns))


def _make_state_average_returns_impl(query_id: int, family: str, spec: dict[str, Any]) -> QueryImpl:
    engine = _state_average_returns_expression_impl if family == "expression" else _state_average_returns_pandas_impl

    def impl(ctx: DataFrameContext) -> Any:
        return engine(ctx, spec)

    impl.__name__ = f"q{query_id}_{family}_impl"
    impl.__qualname__ = impl.__name__
    return impl


for _spec in _QUERY_SPECS["joined_aggregate"]:
    _query_id = _spec["query_id"]
    _register_generated_impl(_make_joined_agg_impl(_query_id, "expression", _spec))
    _register_generated_impl(_make_joined_agg_impl(_query_id, "pandas", _spec))

for _spec in _QUERY_SPECS["state_average_returns"]:
    _query_id = _spec["query_id"]
    _register_generated_impl(_make_state_average_returns_impl(_query_id, "expression", _spec))
    _register_generated_impl(_make_state_average_returns_impl(_query_id, "pandas", _spec))


_Q96_STORE_NAME = "ese"


def q96_expression_impl(ctx: DataFrameContext) -> Any:
    time_dim, store_sales, store, household_demographics = _tables(
        ctx, "time_dim", "store_sales", "store", "household_demographics"
    )
    col = ctx.col
    lit = ctx.lit

    params = get_parameters(96)
    t_hour = int(params.get("hour", 8))
    dep_count = int(params.get("dep_count", 5))
    store_name = _Q96_STORE_NAME

    return (
        store_sales.join(time_dim, left_on="ss_sold_time_sk", right_on="t_time_sk")
        .join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .join(household_demographics, left_on="ss_hdemo_sk", right_on="hd_demo_sk")
        .filter(
            (col("t_hour") == lit(t_hour))
            & (col("t_minute") >= lit(30))
            & (col("hd_dep_count") == lit(dep_count))
            & (col("s_store_name") == lit(store_name))
        )
        .select(col("ss_sold_time_sk").count().alias("count"))
    )


def q96_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    time_dim, store_sales, store, household_demographics = _tables(
        ctx, "time_dim", "store_sales", "store", "household_demographics"
    )

    params = get_parameters(96)
    t_hour = int(params.get("hour", 8))
    dep_count = int(params.get("dep_count", 5))
    store_name = _Q96_STORE_NAME

    merged = store_sales.merge(time_dim, left_on="ss_sold_time_sk", right_on="t_time_sk")
    merged = merged.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    merged = merged.merge(household_demographics, left_on="ss_hdemo_sk", right_on="hd_demo_sk")

    filtered = merged[
        (merged["t_hour"] == t_hour)
        & (merged["t_minute"] >= 30)
        & (merged["hd_dep_count"] == dep_count)
        & (merged["s_store_name"] == store_name)
    ]

    count = len(filtered)
    return pd.DataFrame({"count": [count]})


_Q25_FIRST_MONTH = 4
_Q25_AGGREGATES = {"sum": "sum", "min": "min", "max": "max", "avg": "mean", "stddev_samp": "std"}


def _q25_aggregate(name: str) -> str:
    try:
        return _Q25_AGGREGATES[name]
    except KeyError:
        raise ValueError(f"Q25 AGG must be one of {sorted(_Q25_AGGREGATES)}, got {name!r}") from None


def q25_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(25)
    year = params.get("year", 2000)
    agg = _q25_aggregate(params.get("agg", "sum"))
    month = _Q25_FIRST_MONTH

    store_sales, store_returns, catalog_sales, date_dim, store, item = _tables(
        ctx, "store_sales", "store_returns", "catalog_sales", "date_dim", "store", "item"
    )
    col = ctx.col
    lit = ctx.lit

    d1 = date_dim.filter((col("d_year") == lit(year)) & (col("d_moy") == lit(month)))
    d2 = date_dim.filter((col("d_year") == lit(year)) & (col("d_moy") >= lit(month)) & (col("d_moy") <= lit(month + 6)))
    d3 = date_dim.filter((col("d_year") == lit(year)) & (col("d_moy") >= lit(month)) & (col("d_moy") <= lit(month + 6)))

    joined_returns = (
        store_sales.join(item, left_on="ss_item_sk", right_on="i_item_sk")
        .join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .join(d1, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(
            store_returns,
            left_on=["ss_customer_sk", "ss_item_sk", "ss_ticket_number"],
            right_on=["sr_customer_sk", "sr_item_sk", "sr_ticket_number"],
        )
        .join(d2.select("d_date_sk"), left_on="sr_returned_date_sk", right_on="d_date_sk")
    )
    return _sort_null_largest_expression(
        ctx,
        joined_returns.join(
            catalog_sales,
            left_on=["ss_customer_sk", "ss_item_sk"],
            right_on=["cs_bill_customer_sk", "cs_item_sk"],
        )
        .join(d3.select("d_date_sk"), left_on="cs_sold_date_sk", right_on="d_date_sk")
        .group_by("i_item_id", "i_item_desc", "s_store_id", "s_store_name")
        .agg(
            *(
                (_sum_or_null_expression(ctx, col(source)) if agg == "sum" else getattr(col(source), agg)()).alias(
                    alias
                )
                for source, alias in (
                    ("ss_net_profit", "store_sales_profit"),
                    ("sr_net_loss", "store_returns_loss"),
                    ("cs_net_profit", "catalog_sales_profit"),
                )
            ),
        ),
        ["i_item_id", "i_item_desc", "s_store_id", "s_store_name"],
    ).limit(100)


def q25_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(25)
    year = params.get("year", 2000)
    agg = _q25_aggregate(params.get("agg", "sum"))
    month = _Q25_FIRST_MONTH

    store_sales, store_returns, catalog_sales, date_dim, store, item = _tables(
        ctx, "store_sales", "store_returns", "catalog_sales", "date_dim", "store", "item"
    )

    d1 = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] == month)]
    d2 = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] >= month) & (date_dim["d_moy"] <= month + 6)]
    d3 = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] >= month) & (date_dim["d_moy"] <= month + 6)]

    merged = store_sales.merge(item, left_on="ss_item_sk", right_on="i_item_sk")
    merged = merged.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    merged = merged.merge(d1[["d_date_sk"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    merged = merged.merge(
        store_returns,
        left_on=["ss_customer_sk", "ss_item_sk", "ss_ticket_number"],
        right_on=["sr_customer_sk", "sr_item_sk", "sr_ticket_number"],
    )
    merged = merged.merge(d2[["d_date_sk"]], left_on="sr_returned_date_sk", right_on="d_date_sk")
    merged = merged.merge(
        catalog_sales,
        left_on=["sr_customer_sk", "sr_item_sk"],
        right_on=["cs_bill_customer_sk", "cs_item_sk"],
    )
    merged = merged.merge(d3[["d_date_sk"]], left_on="cs_sold_date_sk", right_on="d_date_sk")

    result = (
        _grouped_pandas_aggregates(
            merged,
            ["i_item_id", "i_item_desc", "s_store_id", "s_store_name"],
            {
                "store_sales_profit": ("ss_net_profit", agg),
                "store_returns_loss": ("sr_net_loss", agg),
                "catalog_sales_profit": ("cs_net_profit", agg),
            },
            dropna=False,
        )
        .sort_values(["i_item_id", "i_item_desc", "s_store_id", "s_store_name"])
        .head(100)
    )
    return _none_for_null(result, list(result.columns))


def q43_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(43)
    gmt_offset = params.get("gmt_offset", -5.0)
    year = params.get("year", 1998)
    col = ctx.col
    lit = ctx.lit
    days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    aliases = ["sun_sales", "mon_sales", "tue_sales", "wed_sales", "thu_sales", "fri_sales", "sat_sales"]
    return _sort_null_largest_expression(
        ctx,
        ctx.get_table("date_dim")
        .join(ctx.get_table("store_sales"), left_on="d_date_sk", right_on="ss_sold_date_sk")
        .join(ctx.get_table("store"), left_on="ss_store_sk", right_on="s_store_sk")
        .filter((col("s_gmt_offset") == lit(gmt_offset)) & (col("d_year") == lit(year)))
        .group_by("s_store_name", "s_store_id")
        .agg(
            *(
                _sum_or_null_expression(
                    ctx, ctx.when(col("d_day_name") == lit(day)).then(col("ss_sales_price")).otherwise(lit(None))
                ).alias(alias)
                for day, alias in zip(days, aliases)
            )
        ),
        ["s_store_name", "s_store_id", *aliases],
    ).limit(100)


def q43_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(43)
    gmt_offset = params.get("gmt_offset", -5.0)
    year = params.get("year", 1998)

    date_dim, store_sales, store = _tables(ctx, "date_dim", "store_sales", "store")
    merged = date_dim.merge(store_sales, left_on="d_date_sk", right_on="ss_sold_date_sk")
    merged = merged.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    filtered = merged[(merged["s_gmt_offset"] == gmt_offset) & (merged["d_year"] == year)]

    days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    aliases = ["sun_sales", "mon_sales", "tue_sales", "wed_sales", "thu_sales", "fri_sales", "sat_sales"]
    for day, alias in zip(days, aliases):
        filtered[alias] = filtered["ss_sales_price"].where(filtered["d_day_name"] == day)
    result = (
        _grouped_pandas_aggregates(
            filtered, ["s_store_name", "s_store_id"], {alias: (alias, "sum") for alias in aliases}, dropna=False
        )
        .sort_values(["s_store_name", "s_store_id", *aliases])
        .head(100)
    )
    return _none_for_null(result, list(result.columns))


def _ticket_household_inner_expression(
    ctx: DataFrameContext,
    query_id: int,
    year_default: int,
    cities_default: list[str],
    dep_default: int,
    aggs: tuple[tuple[str, str], ...],
    dom_range: tuple[int, int] | None = None,
    dow_list: tuple[int, ...] | None = None,
) -> Any:
    params = get_parameters(query_id)
    year = params.get("year", year_default)
    years = [year + offset for offset in params.get("year_offsets", [0, 1, 2])]
    cities = params.get("cities", cities_default)
    dep_count = params.get("dep_count", dep_default)
    vehicle_count = params.get("vehicle_count", 3)
    col = ctx.col
    lit = ctx.lit
    date_filter = col("d_year").is_in(years)
    if dom_range is not None:
        date_filter = date_filter & col("d_dom").is_between(dom_range[0], dom_range[1])
    if dow_list is not None:
        date_filter = date_filter & col("d_dow").is_in(list(dow_list))
    return (
        ctx.get_table("store_sales")
        .join(ctx.get_table("date_dim"), left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(ctx.get_table("store"), left_on="ss_store_sk", right_on="s_store_sk")
        .join(ctx.get_table("household_demographics"), left_on="ss_hdemo_sk", right_on="hd_demo_sk")
        .join(ctx.get_table("customer_address"), left_on="ss_addr_sk", right_on="ca_address_sk")
        .filter(
            date_filter
            & col("s_city").is_in(cities)
            & ((col("hd_dep_count") == lit(dep_count)) | (col("hd_vehicle_count") == lit(vehicle_count)))
        )
        .group_by("ss_ticket_number", "ss_customer_sk", "ss_addr_sk", col("ca_city").alias("bought_city"))
        .agg(*(col(source).sum().alias(alias) for alias, source in aggs))
    )


def _ticket_household_outer_expression(
    ctx: DataFrameContext,
    inner: Any,
    select_cols: list[str],
    sort_cols: list[str],
) -> Any:
    col = ctx.col
    return (
        inner.join(ctx.get_table("customer"), left_on="ss_customer_sk", right_on="c_customer_sk")
        .join(
            ctx.get_table("customer_address"),
            left_on="c_current_addr_sk",
            right_on="ca_address_sk",
        )
        .filter(col("ca_city") != col("bought_city"))
        .select(*select_cols)
        .sort(sort_cols, nulls_last=True)
        .limit(100)
    )


def q46_expression_impl(ctx: DataFrameContext) -> Any:
    inner = _ticket_household_inner_expression(
        ctx,
        46,
        1999,
        ["Midway", "Fairview", "Fairview", "Fairview", "Fairview"],
        5,
        (("amt", "ss_coupon_amt"), ("profit", "ss_net_profit")),
        dow_list=(6, 0),
    )
    return _ticket_household_outer_expression(
        ctx,
        inner,
        ["c_last_name", "c_first_name", "ca_city", "bought_city", "ss_ticket_number", "amt", "profit"],
        ["c_last_name", "c_first_name", "ca_city", "bought_city", "ss_ticket_number"],
    )


def q46_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(46)
    year = params.get("year", 1999)
    years = [year + offset for offset in params.get("year_offsets", [0, 1, 2])]
    cities = params.get("cities", ["Midway", "Fairview", "Fairview", "Fairview", "Fairview"])
    dep_count = params.get("dep_count", 5)
    vehicle_count = params.get("vehicle_count", 3)
    dow = params.get("dow", [6, 0])

    store_sales, date_dim, store, household_demographics, customer_address, customer = _tables(
        ctx, "store_sales", "date_dim", "store", "household_demographics", "customer_address", "customer"
    )
    merged = store_sales.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
    merged = merged.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    merged = merged.merge(household_demographics, left_on="ss_hdemo_sk", right_on="hd_demo_sk")
    merged = merged.merge(customer_address, left_on="ss_addr_sk", right_on="ca_address_sk")
    filtered = merged[
        (merged["d_year"].isin(years))
        & (merged["d_dow"].isin(dow))
        & (merged["s_city"].isin(cities))
        & ((merged["hd_dep_count"] == dep_count) | (merged["hd_vehicle_count"] == vehicle_count))
    ]
    inner = (
        filtered.groupby(["ss_ticket_number", "ss_customer_sk", "ss_addr_sk", "ca_city"], as_index=False)
        .agg(amt=("ss_coupon_amt", "sum"), profit=("ss_net_profit", "sum"))
        .rename(columns={"ca_city": "bought_city"})
    )
    outer = inner.merge(customer, left_on="ss_customer_sk", right_on="c_customer_sk")
    outer = outer.merge(customer_address, left_on="c_current_addr_sk", right_on="ca_address_sk")
    outer = outer[
        (outer["ca_city"] != outer["bought_city"]) & outer["ca_city"].notnull() & outer["bought_city"].notnull()
    ]
    cols = ["c_last_name", "c_first_name", "ca_city", "bought_city", "ss_ticket_number", "amt", "profit"]
    result = outer[cols].sort_values(cols).head(100)
    return _none_for_null(result, [column for column in result.columns if result[column].dtype == object])


def q68_expression_impl(ctx: DataFrameContext) -> Any:
    inner = _ticket_household_inner_expression(
        ctx,
        68,
        1999,
        ["Midway", "Fairview"],
        5,
        (
            ("extended_price", "ss_ext_sales_price"),
            ("list_price", "ss_ext_list_price"),
            ("extended_tax", "ss_ext_tax"),
        ),
        dom_range=(1, 2),
    )
    return _ticket_household_outer_expression(
        ctx,
        inner,
        [
            "c_last_name",
            "c_first_name",
            "ca_city",
            "bought_city",
            "ss_ticket_number",
            "extended_price",
            "extended_tax",
            "list_price",
        ],
        ["c_last_name", "ss_ticket_number"],
    )


def q68_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(68)
    year = params.get("year", 1999)
    years = [year + offset for offset in params.get("year_offsets", [0, 1, 2])]
    cities = params.get("cities", ["Midway", "Fairview"])
    dep_count = params.get("dep_count", 5)
    vehicle_count = params.get("vehicle_count", 3)

    store_sales, date_dim, store, household_demographics, customer_address, customer = _tables(
        ctx, "store_sales", "date_dim", "store", "household_demographics", "customer_address", "customer"
    )
    merged = store_sales.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
    merged = merged.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    merged = merged.merge(household_demographics, left_on="ss_hdemo_sk", right_on="hd_demo_sk")
    merged = merged.merge(customer_address, left_on="ss_addr_sk", right_on="ca_address_sk")
    filtered = merged[
        (merged["d_year"].isin(years))
        & (merged["d_dom"] >= 1)
        & (merged["d_dom"] <= 2)
        & (merged["s_city"].isin(cities))
        & ((merged["hd_dep_count"] == dep_count) | (merged["hd_vehicle_count"] == vehicle_count))
    ]
    inner = (
        filtered.groupby(["ss_ticket_number", "ss_customer_sk", "ss_addr_sk", "ca_city"], as_index=False)
        .agg(
            extended_price=("ss_ext_sales_price", "sum"),
            list_price=("ss_ext_list_price", "sum"),
            extended_tax=("ss_ext_tax", "sum"),
        )
        .rename(columns={"ca_city": "bought_city"})
    )
    outer = inner.merge(customer, left_on="ss_customer_sk", right_on="c_customer_sk")
    outer = outer.merge(customer_address, left_on="c_current_addr_sk", right_on="ca_address_sk")
    outer = outer[
        (outer["ca_city"] != outer["bought_city"]) & outer["ca_city"].notnull() & outer["bought_city"].notnull()
    ]
    cols = [
        "c_last_name",
        "c_first_name",
        "ca_city",
        "bought_city",
        "ss_ticket_number",
        "extended_price",
        "extended_tax",
        "list_price",
    ]
    result = outer[cols].sort_values(["c_last_name", "ss_ticket_number"]).head(100)
    return _none_for_null(result, [column for column in result.columns if result[column].dtype == object])


def q79_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(79)
    year = params.get("year", 1998)
    years = [year, year + 1, year + 2]
    dep_count = params.get("dep_count", 8)
    vehicle_count = params.get("vehicle_count", 0)
    col = ctx.col
    lit = ctx.lit
    inner = (
        ctx.get_table("store_sales")
        .join(ctx.get_table("date_dim"), left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(ctx.get_table("store"), left_on="ss_store_sk", right_on="s_store_sk")
        .join(ctx.get_table("household_demographics"), left_on="ss_hdemo_sk", right_on="hd_demo_sk")
        .filter(
            col("d_year").is_in(years)
            & (col("d_dow") == lit(1))
            & ((col("hd_dep_count") == lit(dep_count)) | (col("hd_vehicle_count") > lit(vehicle_count)))
            & col("s_number_employees").is_between(200, 295)
        )
        .group_by("ss_ticket_number", "ss_customer_sk", "ss_addr_sk", col("s_city").alias("s_city"))
        .agg(
            col("ss_coupon_amt").sum().alias("amt"),
            col("ss_coupon_amt").count().alias("n_amt"),
            col("ss_net_profit").sum().alias("profit"),
            col("ss_net_profit").count().alias("n_profit"),
        )
    )
    return (
        inner.join(ctx.get_table("customer"), left_on="ss_customer_sk", right_on="c_customer_sk")
        .select(
            "c_last_name",
            "c_first_name",
            col("s_city").cast_string().str.slice(0, 30).alias("s_city"),
            "ss_ticket_number",
            ctx.when(col("n_amt") > lit(0)).then(col("amt")).otherwise(lit(None)).alias("amt"),
            ctx.when(col("n_profit") > lit(0)).then(col("profit")).otherwise(lit(None)).alias("profit"),
        )
        .sort(["c_last_name", "c_first_name", "s_city", "profit"], nulls_last=True)
        .limit(100)
    )


def q79_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(79)
    year = params.get("year", 1998)
    years = [year, year + 1, year + 2]
    dep_count = params.get("dep_count", 8)
    vehicle_count = params.get("vehicle_count", 0)

    store_sales, date_dim, store, household_demographics, customer = _tables(
        ctx, "store_sales", "date_dim", "store", "household_demographics", "customer"
    )
    merged = store_sales.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
    merged = merged.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    merged = merged.merge(household_demographics, left_on="ss_hdemo_sk", right_on="hd_demo_sk")
    filtered = merged[
        (merged["d_year"].isin(years))
        & (merged["d_dow"] == 1)
        & ((merged["hd_dep_count"] == dep_count) | (merged["hd_vehicle_count"] > vehicle_count))
        & (merged["s_number_employees"] >= 200)
        & (merged["s_number_employees"] <= 295)
    ]
    inner = filtered.groupby(
        ["ss_ticket_number", "ss_customer_sk", "ss_addr_sk", "s_city"], as_index=False, dropna=False
    ).agg(
        amt=("ss_coupon_amt", "sum"),
        n_amt=("ss_coupon_amt", "count"),
        profit=("ss_net_profit", "sum"),
        n_profit=("ss_net_profit", "count"),
    )
    import pandas as _pd

    inner["amt"] = _pd.Series(
        [value if count > 0 else None for value, count in zip(inner["amt"], inner["n_amt"])], dtype=object
    )
    inner["profit"] = _pd.Series(
        [value if count > 0 else None for value, count in zip(inner["profit"], inner["n_profit"])], dtype=object
    )
    outer = inner.merge(customer, left_on="ss_customer_sk", right_on="c_customer_sk")
    outer["s_city"] = outer["s_city"].astype(str).str[:30]
    cols = ["c_last_name", "c_first_name", "s_city", "ss_ticket_number", "amt", "profit"]
    result = outer[cols].sort_values(["c_last_name", "c_first_name", "s_city", "profit"]).head(100)
    return _none_for_null(result, [column for column in result.columns if result[column].dtype == object])


_Q50_STORE_COLS = [
    "s_store_name",
    "s_company_id",
    "s_street_number",
    "s_street_name",
    "s_street_type",
    "s_suite_number",
    "s_city",
    "s_county",
    "s_state",
    "s_zip",
]


def q50_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(50)
    year = params.get("year", 2000)
    month = params.get("month", 9)
    col = ctx.col
    lit = ctx.lit
    lag = col("sr_returned_date_sk") - col("ss_sold_date_sk")
    return (
        ctx.get_table("store_sales")
        .join(
            ctx.get_table("store_returns"),
            left_on=["ss_ticket_number", "ss_item_sk", "ss_customer_sk"],
            right_on=["sr_ticket_number", "sr_item_sk", "sr_customer_sk"],
        )
        .join(ctx.get_table("store"), left_on="ss_store_sk", right_on="s_store_sk")
        .join(ctx.get_table("date_dim").select("d_date_sk"), left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(
            ctx.get_table("date_dim").filter((col("d_year") == lit(year)) & (col("d_moy") == lit(month))),
            left_on="sr_returned_date_sk",
            right_on="d_date_sk",
        )
        .group_by(*_Q50_STORE_COLS)
        .agg(
            ctx.when(lag <= 30).then(1).otherwise(0).sum().alias("30 days"),
            ctx.when((lag > 30) & (lag <= 60)).then(1).otherwise(0).sum().alias("31-60 days"),
            ctx.when((lag > 60) & (lag <= 90)).then(1).otherwise(0).sum().alias("61-90 days"),
            ctx.when((lag > 90) & (lag <= 120)).then(1).otherwise(0).sum().alias("91-120 days"),
            ctx.when(lag > 120).then(1).otherwise(0).sum().alias(">120 days"),
        )
        .sort(_Q50_STORE_COLS, nulls_last=True)
        .limit(100)
    )


def q50_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(50)
    year = params.get("year", 2000)
    month = params.get("month", 9)

    store_sales, store_returns, store, date_dim = _tables(ctx, "store_sales", "store_returns", "store", "date_dim")
    merged = store_sales.merge(
        store_returns,
        left_on=["ss_ticket_number", "ss_item_sk", "ss_customer_sk"],
        right_on=["sr_ticket_number", "sr_item_sk", "sr_customer_sk"],
    )
    merged = merged.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    merged = merged.merge(date_dim[["d_date_sk"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    d2 = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] == month)]
    merged = merged.merge(d2[["d_date_sk"]], left_on="sr_returned_date_sk", right_on="d_date_sk")
    lag = merged["sr_returned_date_sk"] - merged["ss_sold_date_sk"]
    merged["30 days"] = (lag <= 30).astype(int)
    merged["31-60 days"] = ((lag > 30) & (lag <= 60)).astype(int)
    merged["61-90 days"] = ((lag > 60) & (lag <= 90)).astype(int)
    merged["91-120 days"] = ((lag > 90) & (lag <= 120)).astype(int)
    merged[">120 days"] = (lag > 120).astype(int)
    buckets = ["30 days", "31-60 days", "61-90 days", "91-120 days", ">120 days"]
    return (
        merged.groupby(_Q50_STORE_COLS, as_index=False, dropna=False)
        .agg(**{bucket: (bucket, "sum") for bucket in buckets})
        .sort_values(_Q50_STORE_COLS)
        .head(100)
    )


def q65_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(65)
    dms = params.get("dms", 1212)
    col = ctx.col
    lit = ctx.lit
    sales = ctx.get_table("store_sales").join(
        ctx.get_table("date_dim").filter(col("d_month_seq").is_between(dms, dms + 11)),
        left_on="ss_sold_date_sk",
        right_on="d_date_sk",
    )
    revenue = sales.group_by("ss_store_sk", "ss_item_sk").agg(
        _sum_or_null_expression(ctx, col("ss_sales_price")).alias("revenue")
    )
    store_avg = revenue.group_by("ss_store_sk").agg(col("revenue").mean().alias("ave"))
    flagged = (
        revenue.join(store_avg, on="ss_store_sk")
        .filter(col("revenue") <= lit(0.1) * col("ave"))
        .select("ss_store_sk", "ss_item_sk", "revenue")
    )
    return (
        flagged.join(ctx.get_table("store"), left_on="ss_store_sk", right_on="s_store_sk")
        .join(ctx.get_table("item"), left_on="ss_item_sk", right_on="i_item_sk")
        .select("s_store_name", "i_item_desc", "revenue", "i_current_price", "i_wholesale_cost", "i_brand")
        .sort(["s_store_name", "i_item_desc"], nulls_last=True)
        .limit(100)
    )


def q65_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(65)
    dms = params.get("dms", 1212)

    store_sales, date_dim, store, item = _tables(ctx, "store_sales", "date_dim", "store", "item")
    window = date_dim[(date_dim["d_month_seq"] >= dms) & (date_dim["d_month_seq"] <= dms + 11)]
    sales = store_sales.merge(window[["d_date_sk"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    revenue = _grouped_pandas_aggregates(
        sales, ["ss_store_sk", "ss_item_sk"], {"revenue": ("ss_sales_price", "sum")}, dropna=False
    )
    store_avg = revenue.groupby("ss_store_sk", as_index=False).agg(ave=("revenue", "mean"))
    flagged = revenue.merge(store_avg, on="ss_store_sk")
    flagged = flagged[flagged["revenue"] <= 0.1 * flagged["ave"]][["ss_store_sk", "ss_item_sk", "revenue"]]
    outer = flagged.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    outer = outer.merge(item, left_on="ss_item_sk", right_on="i_item_sk")
    cols = ["s_store_name", "i_item_desc", "revenue", "i_current_price", "i_wholesale_cost", "i_brand"]
    result = outer[cols].sort_values(["s_store_name", "i_item_desc"]).head(100)
    return _none_for_null(result, cols)


def _q89_groups(params: Any) -> tuple[tuple[list[str], list[str]], tuple[list[str], list[str]]]:
    categories = list(params.get("categories", ["Home", "Books", "Electronics", "Shoes", "Jewelry", "Men"]))
    classes = list(params.get("classes", ["wallpaper", "parenting", "musical", "womens", "birdal", "pants"]))
    if len(categories) != 6 or len(classes) != 6:
        raise ValueError(f"Q89 needs six categories and six classes, got {categories!r} and {classes!r}")
    return (categories[:3], classes[:3]), (categories[3:], classes[3:])


def q89_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(89)
    year = params.get("year", 2000)
    (categories_a, classes_a), (categories_b, classes_b) = _q89_groups(params)
    col = ctx.col
    lit = ctx.lit
    in_a = col("i_category").is_in(categories_a) & col("i_class").is_in(classes_a)
    in_b = col("i_category").is_in(categories_b) & col("i_class").is_in(classes_b)
    grouped = (
        ctx.get_table("item")
        .join(ctx.get_table("store_sales"), left_on="i_item_sk", right_on="ss_item_sk")
        .join(ctx.get_table("date_dim"), left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(ctx.get_table("store"), left_on="ss_store_sk", right_on="s_store_sk")
        .filter((col("d_year") == lit(year)) & (in_a | in_b))
        .group_by("i_category", "i_class", "i_brand", "s_store_name", "s_company_name", "d_moy")
        .agg(col("ss_sales_price").sum().alias("sum_sales"))
    )
    partition = ["i_category", "i_brand", "s_store_name", "s_company_name"]
    with_avg = grouped.with_columns(ctx.window_avg("sum_sales", partition_by=partition).alias("avg_monthly_sales"))
    deviation = (col("sum_sales") - col("avg_monthly_sales")).abs() / col("avg_monthly_sales")
    return (
        with_avg.filter((col("avg_monthly_sales") != lit(0)) & (deviation > lit(0.1)))
        .with_columns((col("sum_sales") - col("avg_monthly_sales")).alias("diff"))
        .sort(["diff", "s_store_name"], nulls_last=True)
        .select(
            "i_category",
            "i_class",
            "i_brand",
            "s_store_name",
            "s_company_name",
            "d_moy",
            "sum_sales",
            "avg_monthly_sales",
        )
        .limit(100)
    )


def q89_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(89)
    year = params.get("year", 2000)
    (categories_a, classes_a), (categories_b, classes_b) = _q89_groups(params)

    item, store_sales, date_dim, store = _tables(ctx, "item", "store_sales", "date_dim", "store")
    merged = item.merge(store_sales, left_on="i_item_sk", right_on="ss_item_sk")
    merged = merged.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
    merged = merged.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    in_a = (merged["i_category"].isin(categories_a)) & (merged["i_class"].isin(classes_a))
    in_b = (merged["i_category"].isin(categories_b)) & (merged["i_class"].isin(classes_b))
    filtered = merged[(merged["d_year"] == year) & (in_a | in_b)]
    grouped = filtered.groupby(
        ["i_category", "i_class", "i_brand", "s_store_name", "s_company_name", "d_moy"],
        as_index=False,
        dropna=False,
    ).agg(sum_sales=("ss_sales_price", "sum"))
    partition = ["i_category", "i_brand", "s_store_name", "s_company_name"]
    grouped["avg_monthly_sales"] = grouped.groupby(partition, dropna=False)["sum_sales"].transform("mean")
    deviation = (grouped["sum_sales"] - grouped["avg_monthly_sales"]).abs() / grouped["avg_monthly_sales"]
    kept = grouped[(grouped["avg_monthly_sales"] != 0) & (deviation > 0.1)].copy()
    kept["diff"] = kept["sum_sales"] - kept["avg_monthly_sales"]
    cols = [
        "i_category",
        "i_class",
        "i_brand",
        "s_store_name",
        "s_company_name",
        "d_moy",
        "sum_sales",
        "avg_monthly_sales",
    ]
    return kept.sort_values(["diff", "s_store_name"])[cols].head(100)


def q1_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(1)
    year = params.get("year", 2000)
    state = params.get("state", "TN")
    agg_field = params.get("agg_field")

    store_returns, date_dim, store, customer = _tables(ctx, "store_returns", "date_dim", "store", "customer")
    col = ctx.col
    lit = ctx.lit

    customer_total = (
        store_returns.join(date_dim, left_on="sr_returned_date_sk", right_on="d_date_sk")
        .filter(col("d_year") == lit(year))
        .group_by(col("sr_customer_sk").alias("ctr_customer_sk"), col("sr_store_sk").alias("ctr_store_sk"))
        .agg(
            ctx.when(col(agg_field).count() > lit(0))
            .then(col(agg_field).sum())
            .otherwise(lit(None))
            .alias("ctr_total_return")
        )
    )

    store_avg = customer_total.group_by("ctr_store_sk").agg(col("ctr_total_return").mean().alias("store_avg"))

    return (
        customer_total.join(store_avg, on="ctr_store_sk")
        .filter(col("ctr_total_return") > col("store_avg") * lit(1.2))
        .join(store.filter(col("s_state") == lit(state)), left_on="ctr_store_sk", right_on="s_store_sk")
        .join(customer, left_on="ctr_customer_sk", right_on="c_customer_sk")
        .select("c_customer_id")
        .sort("c_customer_id")
        .limit(100)
    )


def q1_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(1)
    year = params.get("year", 2000)
    state = params.get("state", "TN")
    agg_field = params.get("agg_field")

    store_returns, date_dim, store, customer = _tables(ctx, "store_returns", "date_dim", "store", "customer")

    merged = store_returns.merge(date_dim[["d_date_sk", "d_year"]], left_on="sr_returned_date_sk", right_on="d_date_sk")
    merged = merged[merged["d_year"] == year]
    customer_total = merged.groupby(["sr_customer_sk", "sr_store_sk"], as_index=False, dropna=False).agg(
        ctr_total_return=(agg_field, "sum"), priced=(agg_field, "count")
    )
    customer_total["ctr_total_return"] = customer_total["ctr_total_return"].where(customer_total["priced"] > 0)
    customer_total = customer_total.drop(columns=["priced"])
    customer_total = customer_total.rename(columns={"sr_customer_sk": "ctr_customer_sk", "sr_store_sk": "ctr_store_sk"})

    store_avg = customer_total.groupby("ctr_store_sk", as_index=False, dropna=False).agg(
        store_avg=("ctr_total_return", "mean")
    )

    result = customer_total.merge(store_avg, on="ctr_store_sk")
    result = result[result["ctr_total_return"] > result["store_avg"] * 1.2]
    result = result.merge(store[store["s_state"] == state], left_on="ctr_store_sk", right_on="s_store_sk")
    result = result.merge(customer, left_on="ctr_customer_sk", right_on="c_customer_sk")
    return result[["c_customer_id"]].sort_values("c_customer_id").head(100)


def q6_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(6)
    year = params.get("year", 2001)
    month = params.get("month", 1)

    customer_address, customer, store_sales, date_dim, item = _tables(
        ctx, "customer_address", "customer", "store_sales", "date_dim", "item"
    )
    col = ctx.col
    lit = ctx.lit

    category_avg = item.group_by("i_category").agg(col("i_current_price").mean().alias("category_avg_price"))
    item_with_avg = item.join(category_avg, on="i_category")

    return _sort_null_largest_expression(
        ctx,
        customer_address.join(customer, left_on="ca_address_sk", right_on="c_current_addr_sk")
        .join(store_sales, left_on="c_customer_sk", right_on="ss_customer_sk")
        .join(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(item_with_avg, left_on="ss_item_sk", right_on="i_item_sk")
        .filter(
            (col("d_year") == lit(year))
            & (col("d_moy") == lit(month))
            & (col("i_current_price") > lit(1.2) * col("category_avg_price"))
        )
        .group_by("ca_state")
        .agg(col("ss_sold_date_sk").count().alias("cnt"))
        .filter(col("cnt") >= lit(10))
        .select(col("ca_state").alias("state"), col("cnt")),
        ["cnt", "state"],
    ).limit(100)


def q6_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(6)
    year = params.get("year", 2001)
    month = params.get("month", 1)

    customer_address, customer, store_sales, date_dim, item = _tables(
        ctx, "customer_address", "customer", "store_sales", "date_dim", "item"
    )

    category_avg = (
        item.groupby("i_category", as_index=False)["i_current_price"]
        .mean()
        .rename(columns={"i_current_price": "category_avg_price"})
    )
    item_with_avg = item.merge(category_avg, on="i_category")

    merged = customer_address.merge(customer, left_on="ca_address_sk", right_on="c_current_addr_sk")
    merged = merged.merge(store_sales, left_on="c_customer_sk", right_on="ss_customer_sk")
    merged = merged.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
    merged = merged.merge(item_with_avg, left_on="ss_item_sk", right_on="i_item_sk")

    filtered = merged[
        (merged["d_year"] == year)
        & (merged["d_moy"] == month)
        & (merged["i_current_price"] > 1.2 * merged["category_avg_price"])
    ]

    grouped = filtered.groupby("ca_state", as_index=False, dropna=False).agg(cnt=("ss_sold_date_sk", "count"))

    result = grouped[grouped["cnt"] >= 10].rename(columns={"ca_state": "state"}).sort_values(["cnt", "state"]).head(100)
    return _none_for_null(result, list(result.columns))


def q72_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(72)
    year = params.get("year", 2001)
    buy_potential = params.get("buy_potential", "1001-5000")
    marital_status = params.get("marital_status", "M")

    catalog_sales, inventory, date_dim, item, warehouse, customer_demographics, household_demographics, promotion = (
        _tables(
            ctx,
            "catalog_sales",
            "inventory",
            "date_dim",
            "item",
            "warehouse",
            "customer_demographics",
            "household_demographics",
            "promotion",
        )
    )
    col = ctx.col
    lit = ctx.lit

    cs_with_d1 = catalog_sales.join(
        date_dim.select(
            col("d_date_sk").alias("d1_date_sk"),
            col("d_week_seq").alias("cs_week_seq"),
            col("d_year").alias("d1_year"),
            col("d_date").alias("d1_date"),
        ),
        left_on="cs_sold_date_sk",
        right_on="d1_date_sk",
    ).filter(col("d1_year") == lit(year))

    inv_with_d2 = inventory.join(
        date_dim.select(
            col("d_date_sk").alias("d2_date_sk"),
            col("d_week_seq").alias("inv_week_seq"),
        ),
        left_on="inv_date_sk",
        right_on="d2_date_sk",
    )

    grouped = (
        cs_with_d1.join(
            inv_with_d2,
            left_on=["cs_item_sk", "cs_week_seq"],
            right_on=["inv_item_sk", "inv_week_seq"],
        )
        .join(
            date_dim.select(
                col("d_date_sk").alias("d3_date_sk"),
                col("d_date").alias("d3_date"),
            ),
            left_on="cs_ship_date_sk",
            right_on="d3_date_sk",
        )
        .join(warehouse, left_on="inv_warehouse_sk", right_on="w_warehouse_sk")
        .join(item, left_on="cs_item_sk", right_on="i_item_sk")
        .join(customer_demographics, left_on="cs_bill_cdemo_sk", right_on="cd_demo_sk")
        .join(household_demographics, left_on="cs_bill_hdemo_sk", right_on="hd_demo_sk")
        .join(promotion, left_on="cs_promo_sk", right_on="p_promo_sk", how="left")
        .filter(
            (col("inv_quantity_on_hand") < col("cs_quantity"))
            & (col("d3_date") > ctx.date_add(col("d1_date"), 5))
            & (col("hd_buy_potential") == lit(buy_potential))
            & (col("cd_marital_status") == lit(marital_status))
        )
        .group_by("i_item_desc", "w_warehouse_name", "cs_week_seq")
        .agg(
            ctx.when(col("p_promo_id").is_null()).then(1).otherwise(0).sum().alias("no_promo"),
            ctx.when(col("p_promo_id").is_not_null()).then(1).otherwise(0).sum().alias("promo"),
            ctx.len().alias("total_cnt"),
        )
    )
    return _sort_null_largest_expression(
        ctx, grouped, ["total_cnt", "i_item_desc", "w_warehouse_name", "cs_week_seq"], [True, False, False, False]
    ).limit(100)


def q72_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    params = get_parameters(72)
    year = params.get("year", 2001)
    buy_potential = params.get("buy_potential", "1001-5000")
    marital_status = params.get("marital_status", "M")

    catalog_sales, inventory, date_dim, item, warehouse, customer_demographics, household_demographics, promotion = (
        _tables(
            ctx,
            "catalog_sales",
            "inventory",
            "date_dim",
            "item",
            "warehouse",
            "customer_demographics",
            "household_demographics",
            "promotion",
        )
    )

    d1 = date_dim[["d_date_sk", "d_week_seq", "d_year", "d_date"]].rename(
        columns={"d_date_sk": "d1_date_sk", "d_week_seq": "cs_week_seq", "d_year": "d1_year", "d_date": "d1_date"}
    )
    cs_with_d1 = catalog_sales.merge(d1, left_on="cs_sold_date_sk", right_on="d1_date_sk")
    cs_with_d1 = cs_with_d1[cs_with_d1["d1_year"] == year]

    d2 = date_dim[["d_date_sk", "d_week_seq"]].rename(columns={"d_date_sk": "d2_date_sk", "d_week_seq": "inv_week_seq"})
    inv_with_d2 = inventory.merge(d2, left_on="inv_date_sk", right_on="d2_date_sk")

    merged = cs_with_d1.merge(
        inv_with_d2,
        left_on=["cs_item_sk", "cs_week_seq"],
        right_on=["inv_item_sk", "inv_week_seq"],
    )

    d3 = date_dim[["d_date_sk", "d_date"]].rename(columns={"d_date_sk": "d3_date_sk", "d_date": "d3_date"})
    merged = merged.merge(d3, left_on="cs_ship_date_sk", right_on="d3_date_sk")

    merged = merged.merge(warehouse, left_on="inv_warehouse_sk", right_on="w_warehouse_sk")
    merged = merged.merge(item, left_on="cs_item_sk", right_on="i_item_sk")
    merged = merged.merge(customer_demographics, left_on="cs_bill_cdemo_sk", right_on="cd_demo_sk")
    merged = merged.merge(household_demographics, left_on="cs_bill_hdemo_sk", right_on="hd_demo_sk")
    merged = merged.merge(promotion, left_on="cs_promo_sk", right_on="p_promo_sk", how="left")

    d1 = pd.to_datetime(merged["d1_date"]).dt.date
    d3 = pd.to_datetime(merged["d3_date"]).dt.date
    filtered = merged[
        (merged["inv_quantity_on_hand"] < merged["cs_quantity"])
        & (d3 > d1 + pd.to_timedelta(5, unit="D"))
        & (merged["hd_buy_potential"] == buy_potential)
        & (merged["cd_marital_status"] == marital_status)
    ]

    filtered = filtered.copy()
    filtered["no_promo"] = filtered["p_promo_id"].isna().astype(int)
    filtered["promo"] = filtered["p_promo_id"].notnull().astype(int)

    grouped = filtered.groupby(["i_item_desc", "w_warehouse_name", "cs_week_seq"], as_index=False, dropna=False).agg(
        no_promo=("no_promo", "sum"), promo=("promo", "sum"), total_cnt=("i_item_desc", "size")
    )
    result = _sort_null_largest_pandas(
        grouped, ["total_cnt", "i_item_desc", "w_warehouse_name", "cs_week_seq"], [True, False, False, False]
    ).head(100)
    return _none_for_null(result, list(result.columns))


def q62_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(62)
    dms = params.get("dms", 1212)

    web_sales, warehouse, ship_mode, web_site, date_dim = _tables(
        ctx, "web_sales", "warehouse", "ship_mode", "web_site", "date_dim"
    )
    col = ctx.col

    return (
        web_sales.join(date_dim, left_on="ws_ship_date_sk", right_on="d_date_sk")
        .join(warehouse, left_on="ws_warehouse_sk", right_on="w_warehouse_sk")
        .join(ship_mode, left_on="ws_ship_mode_sk", right_on="sm_ship_mode_sk")
        .join(web_site, left_on="ws_web_site_sk", right_on="web_site_sk")
        .filter(col("d_month_seq").is_between(dms, dms + 11))
        .with_columns((col("ws_ship_date_sk") - col("ws_sold_date_sk")).alias("delivery_days"))
        .group_by(
            col("w_warehouse_name").str.slice(0, 20).alias("warehouse_name"),
            col("sm_type"),
            col("web_name"),
        )
        .agg(
            ctx.when(col("delivery_days") <= 30).then(1).otherwise(0).sum().alias("30_days"),
            ctx.when((col("delivery_days") > 30) & (col("delivery_days") <= 60))
            .then(1)
            .otherwise(0)
            .sum()
            .alias("31_60_days"),
            ctx.when((col("delivery_days") > 60) & (col("delivery_days") <= 90))
            .then(1)
            .otherwise(0)
            .sum()
            .alias("61_90_days"),
            ctx.when((col("delivery_days") > 90) & (col("delivery_days") <= 120))
            .then(1)
            .otherwise(0)
            .sum()
            .alias("91_120_days"),
            ctx.when(col("delivery_days") > 120).then(1).otherwise(0).sum().alias("gt_120_days"),
        )
        .sort("warehouse_name", "sm_type", "web_name", nulls_last=True)
        .limit(100)
    )


def q62_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(62)
    dms = params.get("dms", 1212)

    web_sales, warehouse, ship_mode, web_site, date_dim = _tables(
        ctx, "web_sales", "warehouse", "ship_mode", "web_site", "date_dim"
    )

    merged = web_sales.merge(date_dim, left_on="ws_ship_date_sk", right_on="d_date_sk")
    merged = merged.merge(warehouse, left_on="ws_warehouse_sk", right_on="w_warehouse_sk")
    merged = merged.merge(ship_mode, left_on="ws_ship_mode_sk", right_on="sm_ship_mode_sk")
    merged = merged.merge(web_site, left_on="ws_web_site_sk", right_on="web_site_sk")

    filtered = merged[(merged["d_month_seq"] >= dms) & (merged["d_month_seq"] <= dms + 11)]

    filtered = filtered.copy()
    filtered["delivery_days"] = filtered["ws_ship_date_sk"] - filtered["ws_sold_date_sk"]
    filtered["warehouse_name"] = filtered["w_warehouse_name"].str[:20]

    filtered["30_days"] = (filtered["delivery_days"] <= 30).astype(int)
    filtered["31_60_days"] = ((filtered["delivery_days"] > 30) & (filtered["delivery_days"] <= 60)).astype(int)
    filtered["61_90_days"] = ((filtered["delivery_days"] > 60) & (filtered["delivery_days"] <= 90)).astype(int)
    filtered["91_120_days"] = ((filtered["delivery_days"] > 90) & (filtered["delivery_days"] <= 120)).astype(int)
    filtered["gt_120_days"] = (filtered["delivery_days"] > 120).astype(int)

    result = (
        filtered.groupby(["warehouse_name", "sm_type", "web_name"], as_index=False, dropna=False)
        .agg(
            {
                "30_days": "sum",
                "31_60_days": "sum",
                "61_90_days": "sum",
                "91_120_days": "sum",
                "gt_120_days": "sum",
            }
        )
        .sort_values(["warehouse_name", "sm_type", "web_name"], na_position="last")
        .head(100)
    )
    return _none_for_null(result, ["warehouse_name", "sm_type", "web_name"])


def q99_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(99)
    dms = params.get("dms", 1212)

    catalog_sales, warehouse, ship_mode, call_center, date_dim = _tables(
        ctx, "catalog_sales", "warehouse", "ship_mode", "call_center", "date_dim"
    )
    col = ctx.col

    return (
        catalog_sales.join(date_dim, left_on="cs_ship_date_sk", right_on="d_date_sk")
        .join(warehouse, left_on="cs_warehouse_sk", right_on="w_warehouse_sk")
        .join(ship_mode, left_on="cs_ship_mode_sk", right_on="sm_ship_mode_sk")
        .join(call_center, left_on="cs_call_center_sk", right_on="cc_call_center_sk")
        .filter(col("d_month_seq").is_between(dms, dms + 11))
        .with_columns((col("cs_ship_date_sk") - col("cs_sold_date_sk")).alias("delivery_days"))
        .group_by(
            col("w_warehouse_name").str.slice(0, 20).alias("warehouse_name"),
            col("sm_type"),
            col("cc_name"),
        )
        .agg(
            ctx.when(col("delivery_days") <= 30).then(1).otherwise(0).sum().alias("30_days"),
            ctx.when((col("delivery_days") > 30) & (col("delivery_days") <= 60))
            .then(1)
            .otherwise(0)
            .sum()
            .alias("31_60_days"),
            ctx.when((col("delivery_days") > 60) & (col("delivery_days") <= 90))
            .then(1)
            .otherwise(0)
            .sum()
            .alias("61_90_days"),
            ctx.when((col("delivery_days") > 90) & (col("delivery_days") <= 120))
            .then(1)
            .otherwise(0)
            .sum()
            .alias("91_120_days"),
            ctx.when(col("delivery_days") > 120).then(1).otherwise(0).sum().alias("gt_120_days"),
        )
        .sort("warehouse_name", "sm_type", "cc_name", nulls_last=True)
        .limit(100)
    )


def q99_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(99)
    dms = params.get("dms", 1212)

    catalog_sales, warehouse, ship_mode, call_center, date_dim = _tables(
        ctx, "catalog_sales", "warehouse", "ship_mode", "call_center", "date_dim"
    )

    merged = catalog_sales.merge(date_dim, left_on="cs_ship_date_sk", right_on="d_date_sk")
    merged = merged.merge(warehouse, left_on="cs_warehouse_sk", right_on="w_warehouse_sk")
    merged = merged.merge(ship_mode, left_on="cs_ship_mode_sk", right_on="sm_ship_mode_sk")
    merged = merged.merge(call_center, left_on="cs_call_center_sk", right_on="cc_call_center_sk")

    filtered = merged[(merged["d_month_seq"] >= dms) & (merged["d_month_seq"] <= dms + 11)]

    filtered = filtered.copy()
    filtered["delivery_days"] = filtered["cs_ship_date_sk"] - filtered["cs_sold_date_sk"]
    filtered["warehouse_name"] = filtered["w_warehouse_name"].str[:20]

    filtered["30_days"] = (filtered["delivery_days"] <= 30).astype(int)
    filtered["31_60_days"] = ((filtered["delivery_days"] > 30) & (filtered["delivery_days"] <= 60)).astype(int)
    filtered["61_90_days"] = ((filtered["delivery_days"] > 60) & (filtered["delivery_days"] <= 90)).astype(int)
    filtered["91_120_days"] = ((filtered["delivery_days"] > 90) & (filtered["delivery_days"] <= 120)).astype(int)
    filtered["gt_120_days"] = (filtered["delivery_days"] > 120).astype(int)

    result = (
        filtered.groupby(["warehouse_name", "sm_type", "cc_name"], as_index=False, dropna=False)
        .agg(
            {
                "30_days": "sum",
                "31_60_days": "sum",
                "61_90_days": "sum",
                "91_120_days": "sum",
                "gt_120_days": "sum",
            }
        )
        .sort_values(["warehouse_name", "sm_type", "cc_name"], na_position="last")
        .head(100)
    )
    return _none_for_null(result, ["warehouse_name", "sm_type", "cc_name"])


def q13_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(13)
    year = params.get("year", 2001)
    demo1_marital = params.get("demo1_marital", "D")
    demo1_education = params.get("demo1_education", "2 yr Degree")
    demo2_marital = params.get("demo2_marital", "S")
    demo2_education = params.get("demo2_education", "Secondary")
    demo3_marital = params.get("demo3_marital", "W")
    demo3_education = params.get("demo3_education", "Advanced Degree")
    states1 = params.get("states1", ["CO", "IL", "MN"])
    states2 = params.get("states2", ["OH", "MT", "NM"])
    states3 = params.get("states3", ["TX", "MO", "MI"])

    store_sales, store, customer_demographics, household_demographics, customer_address, date_dim = _tables(
        ctx, "store_sales", "store", "customer_demographics", "household_demographics", "customer_address", "date_dim"
    )
    col = ctx.col
    lit = ctx.lit

    joined = (
        store_sales.join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .join(customer_demographics, left_on="ss_cdemo_sk", right_on="cd_demo_sk")
        .join(household_demographics, left_on="ss_hdemo_sk", right_on="hd_demo_sk")
        .join(customer_address, left_on="ss_addr_sk", right_on="ca_address_sk")
        .join(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
    )
    predicate = (col("d_year") == lit(year)) & (
        (
            (
                (col("cd_marital_status") == lit(demo1_marital))
                & (col("cd_education_status") == lit(demo1_education))
                & col("ss_sales_price").is_between(100.0, 150.0)
                & (col("hd_dep_count") == lit(3))
            )
            | (
                (col("cd_marital_status") == lit(demo2_marital))
                & (col("cd_education_status") == lit(demo2_education))
                & col("ss_sales_price").is_between(50.0, 100.0)
                & (col("hd_dep_count") == lit(1))
            )
            | (
                (col("cd_marital_status") == lit(demo3_marital))
                & (col("cd_education_status") == lit(demo3_education))
                & col("ss_sales_price").is_between(150.0, 200.0)
                & (col("hd_dep_count") == lit(1))
            )
        )
        & (
            (
                (col("ca_country") == lit("United States"))
                & col("ca_state").is_in(states1)
                & col("ss_net_profit").is_between(100, 200)
            )
            | (
                (col("ca_country") == lit("United States"))
                & col("ca_state").is_in(states2)
                & col("ss_net_profit").is_between(150, 300)
            )
            | (
                (col("ca_country") == lit("United States"))
                & col("ca_state").is_in(states3)
                & col("ss_net_profit").is_between(50, 250)
            )
        )
    )
    filtered = joined.filter(predicate)
    tallied = filtered.select(
        col("ss_quantity").mean().alias("avg_ss_quantity"),
        col("ss_ext_sales_price").mean().alias("avg_ss_ext_sales_price"),
        col("ss_ext_wholesale_cost").mean().alias("avg_ss_ext_wholesale_cost"),
        col("ss_ext_wholesale_cost").sum().alias("sum_ss_ext_wholesale_cost"),
        col("ss_quantity").count().alias("n"),
    )
    return tallied.select(
        ctx.when(col("n") > lit(0)).then(col("avg_ss_quantity")).otherwise(lit(None)).alias("avg_ss_quantity"),
        ctx.when(col("n") > lit(0))
        .then(col("avg_ss_ext_sales_price"))
        .otherwise(lit(None))
        .alias("avg_ss_ext_sales_price"),
        ctx.when(col("n") > lit(0))
        .then(col("avg_ss_ext_wholesale_cost"))
        .otherwise(lit(None))
        .alias("avg_ss_ext_wholesale_cost"),
        ctx.when(col("n") > lit(0))
        .then(col("sum_ss_ext_wholesale_cost"))
        .otherwise(lit(None))
        .alias("sum_ss_ext_wholesale_cost"),
    )


def q13_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    params = get_parameters(13)
    year = params.get("year", 2001)
    demo1_marital = params.get("demo1_marital", "D")
    demo1_education = params.get("demo1_education", "2 yr Degree")
    demo2_marital = params.get("demo2_marital", "S")
    demo2_education = params.get("demo2_education", "Secondary")
    demo3_marital = params.get("demo3_marital", "W")
    demo3_education = params.get("demo3_education", "Advanced Degree")
    states1 = params.get("states1", ["CO", "IL", "MN"])
    states2 = params.get("states2", ["OH", "MT", "NM"])
    states3 = params.get("states3", ["TX", "MO", "MI"])

    store_sales, store, customer_demographics, household_demographics, customer_address, date_dim = _tables(
        ctx, "store_sales", "store", "customer_demographics", "household_demographics", "customer_address", "date_dim"
    )

    merged = store_sales.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    merged = merged.merge(customer_demographics, left_on="ss_cdemo_sk", right_on="cd_demo_sk")
    merged = merged.merge(household_demographics, left_on="ss_hdemo_sk", right_on="hd_demo_sk")
    merged = merged.merge(customer_address, left_on="ss_addr_sk", right_on="ca_address_sk")
    merged = merged.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")

    demo_cond1 = (
        (merged["cd_marital_status"] == demo1_marital)
        & (merged["cd_education_status"] == demo1_education)
        & (merged["ss_sales_price"] >= 100.0)
        & (merged["ss_sales_price"] <= 150.0)
        & (merged["hd_dep_count"] == 3)
    )
    demo_cond2 = (
        (merged["cd_marital_status"] == demo2_marital)
        & (merged["cd_education_status"] == demo2_education)
        & (merged["ss_sales_price"] >= 50.0)
        & (merged["ss_sales_price"] <= 100.0)
        & (merged["hd_dep_count"] == 1)
    )
    demo_cond3 = (
        (merged["cd_marital_status"] == demo3_marital)
        & (merged["cd_education_status"] == demo3_education)
        & (merged["ss_sales_price"] >= 150.0)
        & (merged["ss_sales_price"] <= 200.0)
        & (merged["hd_dep_count"] == 1)
    )

    addr_cond1 = (
        (merged["ca_country"] == "United States")
        & merged["ca_state"].isin(states1)
        & (merged["ss_net_profit"] >= 100)
        & (merged["ss_net_profit"] <= 200)
    )
    addr_cond2 = (
        (merged["ca_country"] == "United States")
        & merged["ca_state"].isin(states2)
        & (merged["ss_net_profit"] >= 150)
        & (merged["ss_net_profit"] <= 300)
    )
    addr_cond3 = (
        (merged["ca_country"] == "United States")
        & merged["ca_state"].isin(states3)
        & (merged["ss_net_profit"] >= 50)
        & (merged["ss_net_profit"] <= 250)
    )

    filtered = merged[
        (merged["d_year"] == year) & (demo_cond1 | demo_cond2 | demo_cond3) & (addr_cond1 | addr_cond2 | addr_cond3)
    ]

    if len(filtered) == 0:
        return pd.DataFrame(
            {
                "avg_ss_quantity": [None],
                "avg_ss_ext_sales_price": [None],
                "avg_ss_ext_wholesale_cost": [None],
                "sum_ss_ext_wholesale_cost": [None],
            }
        )
    return pd.DataFrame(
        {
            "avg_ss_quantity": [filtered["ss_quantity"].mean()],
            "avg_ss_ext_sales_price": [filtered["ss_ext_sales_price"].mean()],
            "avg_ss_ext_wholesale_cost": [filtered["ss_ext_wholesale_cost"].mean()],
            "sum_ss_ext_wholesale_cost": [filtered["ss_ext_wholesale_cost"].sum()],
        }
    )


def q48_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(48)
    year = params.get("year", 1998)
    demo1_marital = params.get("demo1_marital", "M")
    demo1_education = params.get("demo1_education", "4 yr Degree")
    demo2_marital = params.get("demo2_marital", "D")
    demo2_education = params.get("demo2_education", "Primary")
    demo3_marital = params.get("demo3_marital", "U")
    demo3_education = params.get("demo3_education", "Advanced Degree")
    states1 = params.get("states1", ["KY", "GA", "NM"])
    states2 = params.get("states2", ["MT", "OR", "IN"])
    states3 = params.get("states3", ["WI", "MO", "WV"])

    store_sales, store, customer_demographics, customer_address, date_dim = _tables(
        ctx, "store_sales", "store", "customer_demographics", "customer_address", "date_dim"
    )
    col = ctx.col
    lit = ctx.lit

    return (
        store_sales.join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .join(customer_demographics, left_on="ss_cdemo_sk", right_on="cd_demo_sk")
        .join(customer_address, left_on="ss_addr_sk", right_on="ca_address_sk")
        .join(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .filter(
            (col("d_year") == lit(year))
            & (
                (
                    (col("cd_marital_status") == lit(demo1_marital))
                    & (col("cd_education_status") == lit(demo1_education))
                    & col("ss_sales_price").is_between(100.0, 150.0)
                )
                | (
                    (col("cd_marital_status") == lit(demo2_marital))
                    & (col("cd_education_status") == lit(demo2_education))
                    & col("ss_sales_price").is_between(50.0, 100.0)
                )
                | (
                    (col("cd_marital_status") == lit(demo3_marital))
                    & (col("cd_education_status") == lit(demo3_education))
                    & col("ss_sales_price").is_between(150.0, 200.0)
                )
            )
            & (
                (
                    (col("ca_country") == lit("United States"))
                    & col("ca_state").is_in(states1)
                    & col("ss_net_profit").is_between(0, 2000)
                )
                | (
                    (col("ca_country") == lit("United States"))
                    & col("ca_state").is_in(states2)
                    & col("ss_net_profit").is_between(150, 3000)
                )
                | (
                    (col("ca_country") == lit("United States"))
                    & col("ca_state").is_in(states3)
                    & col("ss_net_profit").is_between(50, 25000)
                )
            )
        )
        .select(col("ss_quantity").sum().alias("sum_ss_quantity"))
    )


def q48_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    params = get_parameters(48)
    year = params.get("year", 1998)
    demo1_marital = params.get("demo1_marital", "M")
    demo1_education = params.get("demo1_education", "4 yr Degree")
    demo2_marital = params.get("demo2_marital", "D")
    demo2_education = params.get("demo2_education", "Primary")
    demo3_marital = params.get("demo3_marital", "U")
    demo3_education = params.get("demo3_education", "Advanced Degree")
    states1 = params.get("states1", ["KY", "GA", "NM"])
    states2 = params.get("states2", ["MT", "OR", "IN"])
    states3 = params.get("states3", ["WI", "MO", "WV"])

    store_sales, store, customer_demographics, customer_address, date_dim = _tables(
        ctx, "store_sales", "store", "customer_demographics", "customer_address", "date_dim"
    )

    merged = store_sales.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    merged = merged.merge(customer_demographics, left_on="ss_cdemo_sk", right_on="cd_demo_sk")
    merged = merged.merge(customer_address, left_on="ss_addr_sk", right_on="ca_address_sk")
    merged = merged.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")

    demo_cond1 = (
        (merged["cd_marital_status"] == demo1_marital)
        & (merged["cd_education_status"] == demo1_education)
        & (merged["ss_sales_price"] >= 100.0)
        & (merged["ss_sales_price"] <= 150.0)
    )
    demo_cond2 = (
        (merged["cd_marital_status"] == demo2_marital)
        & (merged["cd_education_status"] == demo2_education)
        & (merged["ss_sales_price"] >= 50.0)
        & (merged["ss_sales_price"] <= 100.0)
    )
    demo_cond3 = (
        (merged["cd_marital_status"] == demo3_marital)
        & (merged["cd_education_status"] == demo3_education)
        & (merged["ss_sales_price"] >= 150.0)
        & (merged["ss_sales_price"] <= 200.0)
    )

    addr_cond1 = (
        (merged["ca_country"] == "United States")
        & merged["ca_state"].isin(states1)
        & (merged["ss_net_profit"] >= 0)
        & (merged["ss_net_profit"] <= 2000)
    )
    addr_cond2 = (
        (merged["ca_country"] == "United States")
        & merged["ca_state"].isin(states2)
        & (merged["ss_net_profit"] >= 150)
        & (merged["ss_net_profit"] <= 3000)
    )
    addr_cond3 = (
        (merged["ca_country"] == "United States")
        & merged["ca_state"].isin(states3)
        & (merged["ss_net_profit"] >= 50)
        & (merged["ss_net_profit"] <= 25000)
    )

    filtered = merged[
        (merged["d_year"] == year) & (demo_cond1 | demo_cond2 | demo_cond3) & (addr_cond1 | addr_cond2 | addr_cond3)
    ]

    return pd.DataFrame({"sum_ss_quantity": [filtered["ss_quantity"].sum()]})


def q34_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(34)
    year = params.get("year", 1998)
    counties = params.get(
        "counties",
        [
            "Williamson County",
            "Williamson County",
            "Williamson County",
            "Williamson County",
            "Williamson County",
            "Williamson County",
            "Williamson County",
            "Williamson County",
        ],
    )
    buy_potential_1 = params.get("buy_potential_1", ">10000")
    buy_potential_2 = params.get("buy_potential_2", "Unknown")

    store_sales, date_dim, store, household_demographics, customer = _tables(
        ctx, "store_sales", "date_dim", "store", "household_demographics", "customer"
    )
    col = ctx.col
    lit = ctx.lit

    ticket_agg = (
        store_sales.join(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .join(household_demographics, left_on="ss_hdemo_sk", right_on="hd_demo_sk")
        .filter(
            (
                (col("d_dom") >= lit(1)) & (col("d_dom") <= lit(3))
                | (col("d_dom") >= lit(25)) & (col("d_dom") <= lit(28))
            )
            & ((col("hd_buy_potential") == lit(buy_potential_1)) | (col("hd_buy_potential") == lit(buy_potential_2)))
            & (col("hd_vehicle_count") > lit(0))
            & ((col("hd_dep_count") / col("hd_vehicle_count")) > lit(1.2))
            & (col("d_year").is_in([year, year + 1, year + 2]))
            & col("s_county").is_in(counties)
        )
        .group_by("ss_ticket_number", "ss_customer_sk")
        .agg(col("ss_ticket_number").count().alias("cnt"))
        .filter((col("cnt") >= lit(15)) & (col("cnt") <= lit(20)))
    )

    result = ticket_agg.join(customer, left_on="ss_customer_sk", right_on="c_customer_sk").select(
        col("c_last_name"),
        col("c_first_name"),
        col("c_salutation"),
        col("c_preferred_cust_flag"),
        col("ss_ticket_number"),
        col("cnt"),
    )
    return _sort_null_largest_expression(
        ctx,
        result,
        ["c_last_name", "c_first_name", "c_salutation", "c_preferred_cust_flag", "ss_ticket_number"],
        [False, False, False, True, False],
    )


def q34_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(34)
    year = params.get("year", 1998)
    counties = params.get(
        "counties",
        [
            "Williamson County",
            "Williamson County",
            "Williamson County",
            "Williamson County",
            "Williamson County",
            "Williamson County",
            "Williamson County",
            "Williamson County",
        ],
    )
    buy_potential_1 = params.get("buy_potential_1", ">10000")
    buy_potential_2 = params.get("buy_potential_2", "Unknown")

    store_sales, date_dim, store, household_demographics, customer = _tables(
        ctx, "store_sales", "date_dim", "store", "household_demographics", "customer"
    )

    merged = store_sales.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
    merged = merged.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    merged = merged.merge(household_demographics, left_on="ss_hdemo_sk", right_on="hd_demo_sk")

    dom_cond = ((merged["d_dom"] >= 1) & (merged["d_dom"] <= 3)) | ((merged["d_dom"] >= 25) & (merged["d_dom"] <= 28))
    bp_cond = (merged["hd_buy_potential"] == buy_potential_1) | (merged["hd_buy_potential"] == buy_potential_2)
    vehicle_cond = (merged["hd_vehicle_count"] > 0) & (merged["hd_dep_count"] / merged["hd_vehicle_count"] > 1.2)
    year_cond = merged["d_year"].isin([year, year + 1, year + 2])
    county_cond = merged["s_county"].isin(counties)

    filtered = merged[dom_cond & bp_cond & vehicle_cond & year_cond & county_cond]

    ticket_agg = ctx.groupby_size(filtered, ["ss_ticket_number", "ss_customer_sk"], name="cnt")

    ticket_filtered = ticket_agg[(ticket_agg["cnt"] >= 15) & (ticket_agg["cnt"] <= 20)]

    result = ticket_filtered.merge(customer, left_on="ss_customer_sk", right_on="c_customer_sk")
    result = _none_for_null(result, ["c_last_name", "c_first_name", "c_salutation", "c_preferred_cust_flag"])

    result = result[["c_last_name", "c_first_name", "c_salutation", "c_preferred_cust_flag", "ss_ticket_number", "cnt"]]
    return _sort_null_largest_pandas(
        result,
        ["c_last_name", "c_first_name", "c_salutation", "c_preferred_cust_flag", "ss_ticket_number"],
        [False, False, False, True, False],
    )


_Q45_ZIP_CODES = ["85669", "86197", "88274", "83405", "86475", "85392", "85460", "80348", "81792"]
_Q45_ITEM_SKS = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]


def q45_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(45)
    year = params.get("year", 2000)
    qoy = params.get("qoy", 2)
    group_column = params.get("gbobc", "ca_county")
    zip_codes = _Q45_ZIP_CODES
    item_sks = _Q45_ITEM_SKS

    web_sales, customer, customer_address, date_dim, item = _tables(
        ctx, "web_sales", "customer", "customer_address", "date_dim", "item"
    )
    col = ctx.col
    lit = ctx.lit

    item_ids_list = (
        item.filter(col("i_item_sk").is_in(item_sks)).select("i_item_id").unique().collect_column_as_list("i_item_id")
    )

    grouped = (
        web_sales.join(customer, left_on="ws_bill_customer_sk", right_on="c_customer_sk")
        .join(customer_address, left_on="c_current_addr_sk", right_on="ca_address_sk")
        .join(item, left_on="ws_item_sk", right_on="i_item_sk")
        .join(date_dim, left_on="ws_sold_date_sk", right_on="d_date_sk")
        .filter(
            (col("d_qoy") == lit(qoy))
            & (col("d_year") == lit(year))
            & (col("ca_zip").cast_string().str.slice(0, 5).is_in(zip_codes) | col("i_item_id").is_in(item_ids_list))
        )
        .group_by(col("ca_zip"), col(group_column))
        .agg(col("ws_sales_price").sum().alias("sum_ws_sales_price"))
    )
    return _sort_null_largest_expression(ctx, grouped, ["ca_zip", group_column], [False, False]).limit(100)


def q45_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(45)
    year = params.get("year", 2000)
    qoy = params.get("qoy", 2)
    group_column = params.get("gbobc", "ca_county")
    zip_codes = _Q45_ZIP_CODES
    item_sks = _Q45_ITEM_SKS

    web_sales, customer, customer_address, date_dim, item = _tables(
        ctx, "web_sales", "customer", "customer_address", "date_dim", "item"
    )

    item_ids = _to_list(item[item["i_item_sk"].isin(item_sks)]["i_item_id"].unique())

    merged = web_sales.merge(customer, left_on="ws_bill_customer_sk", right_on="c_customer_sk")
    merged = merged.merge(customer_address, left_on="c_current_addr_sk", right_on="ca_address_sk")
    merged = merged.merge(item, left_on="ws_item_sk", right_on="i_item_sk")
    merged = merged.merge(date_dim, left_on="ws_sold_date_sk", right_on="d_date_sk")

    filtered = merged[
        (merged["d_qoy"] == qoy)
        & (merged["d_year"] == year)
        & (merged["ca_zip"].str[:5].isin(zip_codes) | merged["i_item_id"].isin(item_ids))
    ]

    grouped = filtered.groupby(["ca_zip", group_column], as_index=False, dropna=False).agg(
        sum_ws_sales_price=("ws_sales_price", "sum")
    )
    ordered = _sort_null_largest_pandas(grouped, ["ca_zip", group_column], [False, False]).head(100)
    return _none_for_null(ordered, ["ca_zip", group_column])


def _q90_params() -> tuple[int, int, int, int, int]:
    params = get_parameters(90)
    return (
        params.get("hour_am", 8),
        params.get("hour_pm", 19),
        params.get("dep_count", 8),
        5000,
        5200,
    )


def q90_expression_impl(ctx: DataFrameContext) -> Any:
    hour_am, hour_pm, dep_count, char_min, char_max = _q90_params()
    col = ctx.col
    lit = ctx.lit
    base = (
        ctx.get_table("web_sales")
        .join(ctx.get_table("time_dim"), left_on="ws_sold_time_sk", right_on="t_time_sk")
        .join(ctx.get_table("household_demographics"), left_on="ws_ship_hdemo_sk", right_on="hd_demo_sk")
        .join(ctx.get_table("web_page"), left_on="ws_web_page_sk", right_on="wp_web_page_sk")
        .filter((col("hd_dep_count") == lit(dep_count)) & col("wp_char_count").is_between(char_min, char_max))
    )
    am_val = base.filter(col("t_hour").is_between(hour_am, hour_am + 1)).select(ctx.count().alias("amc")).scalar(0, 0)
    pm_val = base.filter(col("t_hour").is_between(hour_pm, hour_pm + 1)).select(ctx.count().alias("pmc")).scalar(0, 0)
    return ctx.create_dataframe({"am_pm_ratio": [am_val / pm_val if pm_val and pm_val > 0 else None]})


def q90_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    hour_am, hour_pm, dep_count, char_min, char_max = _q90_params()
    base = ctx.get_table("web_sales").merge(ctx.get_table("time_dim"), left_on="ws_sold_time_sk", right_on="t_time_sk")
    base = base.merge(ctx.get_table("household_demographics"), left_on="ws_ship_hdemo_sk", right_on="hd_demo_sk")
    base = base.merge(ctx.get_table("web_page"), left_on="ws_web_page_sk", right_on="wp_web_page_sk")
    base = base[
        (base["hd_dep_count"] == dep_count) & (base["wp_char_count"] >= char_min) & (base["wp_char_count"] <= char_max)
    ]
    am_count = len(base[(base["t_hour"] >= hour_am) & (base["t_hour"] <= hour_am + 1)])
    pm_count = len(base[(base["t_hour"] >= hour_pm) & (base["t_hour"] <= hour_pm + 1)])
    return pd.DataFrame({"am_pm_ratio": [am_count / pm_count if pm_count > 0 else None]})


def q83_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(83)
    return_dates = params.get("dates", ["1998-01-02", "1998-10-15", "1998-11-10"])

    col = ctx.col
    lit = ctx.lit

    store_returns, catalog_returns, web_returns, item, date_dim = _tables(
        ctx, "store_returns", "catalog_returns", "web_returns", "item", "date_dim"
    )

    lit = ctx.lit

    date_filter = lit(False)
    for dt in return_dates:
        date_filter = date_filter | (col("d_date").cast_string().str.starts_with(dt))

    target_weeks = date_dim.filter(date_filter).select(col("d_week_seq")).unique()

    valid_dates = date_dim.join(target_weeks, on="d_week_seq").select(col("d_date_sk")).unique()

    sr_items = (
        store_returns.join(item, left_on="sr_item_sk", right_on="i_item_sk")
        .join(valid_dates, left_on="sr_returned_date_sk", right_on="d_date_sk")
        .group_by(col("i_item_id").alias("item_id"))
        .agg(col("sr_return_quantity").sum().alias("sr_item_qty"))
    )

    cr_items = (
        catalog_returns.join(item, left_on="cr_item_sk", right_on="i_item_sk")
        .join(valid_dates, left_on="cr_returned_date_sk", right_on="d_date_sk")
        .group_by(col("i_item_id").alias("item_id"))
        .agg(col("cr_return_quantity").sum().alias("cr_item_qty"))
    )

    wr_items = (
        web_returns.join(item, left_on="wr_item_sk", right_on="i_item_sk")
        .join(valid_dates, left_on="wr_returned_date_sk", right_on="d_date_sk")
        .group_by(col("i_item_id").alias("item_id"))
        .agg(col("wr_return_quantity").sum().alias("wr_item_qty"))
    )

    return (
        sr_items.join(cr_items, on="item_id")
        .join(wr_items, on="item_id")
        .with_columns((col("sr_item_qty") + col("cr_item_qty") + col("wr_item_qty")).alias("total_qty"))
        .with_columns(
            ((col("sr_item_qty") / col("total_qty") / lit(3.0)) * lit(100.0)).alias("sr_dev"),
            ((col("cr_item_qty") / col("total_qty") / lit(3.0)) * lit(100.0)).alias("cr_dev"),
            ((col("wr_item_qty") / col("total_qty") / lit(3.0)) * lit(100.0)).alias("wr_dev"),
            (col("total_qty") / lit(3.0)).alias("average"),
        )
        .select(
            col("item_id"),
            col("sr_item_qty"),
            col("sr_dev"),
            col("cr_item_qty"),
            col("cr_dev"),
            col("wr_item_qty"),
            col("wr_dev"),
            col("average"),
        )
        .sort(["item_id", "sr_item_qty"])
        .head(100)
    )


def q83_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(83)
    return_dates = params.get("dates", ["1998-01-02", "1998-10-15", "1998-11-10"])

    store_returns, catalog_returns, web_returns, item, date_dim = _tables(
        ctx, "store_returns", "catalog_returns", "web_returns", "item", "date_dim"
    )

    date_dim["d_date_str"] = date_dim["d_date"].astype(str)
    target_weeks = _to_list(date_dim[date_dim["d_date_str"].str[:10].isin(return_dates)]["d_week_seq"].unique())

    valid_date_sks = _to_list(date_dim[date_dim["d_week_seq"].isin(target_weeks)]["d_date_sk"].unique())

    sr_merged = store_returns.merge(item, left_on="sr_item_sk", right_on="i_item_sk")
    sr_merged = sr_merged[sr_merged["sr_returned_date_sk"].isin(valid_date_sks)]
    sr_items = sr_merged.groupby("i_item_id", as_index=False).agg(sr_item_qty=("sr_return_quantity", "sum"))
    sr_items = sr_items.rename(columns={"i_item_id": "item_id"})

    cr_merged = catalog_returns.merge(item, left_on="cr_item_sk", right_on="i_item_sk")
    cr_merged = cr_merged[cr_merged["cr_returned_date_sk"].isin(valid_date_sks)]
    cr_items = cr_merged.groupby("i_item_id", as_index=False).agg(cr_item_qty=("cr_return_quantity", "sum"))
    cr_items = cr_items.rename(columns={"i_item_id": "item_id"})

    wr_merged = web_returns.merge(item, left_on="wr_item_sk", right_on="i_item_sk")
    wr_merged = wr_merged[wr_merged["wr_returned_date_sk"].isin(valid_date_sks)]
    wr_items = wr_merged.groupby("i_item_id", as_index=False).agg(wr_item_qty=("wr_return_quantity", "sum"))
    wr_items = wr_items.rename(columns={"i_item_id": "item_id"})

    result = sr_items.merge(cr_items, on="item_id").merge(wr_items, on="item_id")
    result["total_qty"] = result["sr_item_qty"] + result["cr_item_qty"] + result["wr_item_qty"]
    result["sr_dev"] = (result["sr_item_qty"] / result["total_qty"] / 3.0) * 100.0
    result["cr_dev"] = (result["cr_item_qty"] / result["total_qty"] / 3.0) * 100.0
    result["wr_dev"] = (result["wr_item_qty"] / result["total_qty"] / 3.0) * 100.0
    result["average"] = result["total_qty"] / 3.0

    result = result[["item_id", "sr_item_qty", "sr_dev", "cr_item_qty", "cr_dev", "wr_item_qty", "wr_dev", "average"]]
    return result.sort_values(["item_id", "sr_item_qty"]).head(100)


_Q41_GROUPS = (
    ("Women", 0, 0, 0),
    ("Women", 2, 2, 2),
    ("Men", 4, 4, 4),
    ("Men", 6, 6, 0),
    ("Women", 8, 8, 0),
    ("Women", 10, 10, 2),
    ("Men", 12, 12, 4),
    ("Men", 14, 14, 0),
)


def _q41_parameters(params: Any) -> tuple[int, list[str], list[str], list[str]]:
    manufact = params.get("manufact", 742)
    colors = list(params.get("colors", []))
    units = list(params.get("units", []))
    sizes = list(params.get("sizes", []))
    if len(colors) != 16 or len(units) != 16 or len(sizes) != 6:
        raise ValueError(f"Q41 needs 16 colors, 16 units and 6 sizes, got {len(colors)}, {len(units)} and {len(sizes)}")
    return manufact, colors, units, sizes


def q41_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(41)
    manufact, colors, units, sizes = _q41_parameters(params)

    col = ctx.col
    lit = ctx.lit

    item = ctx.get_table("item")

    i1 = item.filter((col("i_manufact_id") >= lit(manufact)) & (col("i_manufact_id") <= lit(manufact + 40)))

    condition = None
    for category, color, unit, size in _Q41_GROUPS:
        group = (
            (col("i_category") == lit(category))
            & col("i_color").is_in(colors[color : color + 2])
            & col("i_units").is_in(units[unit : unit + 2])
            & col("i_size").is_in(sizes[size : size + 2])
        )
        condition = group if condition is None else condition | group

    matching_manufacts = item.filter(condition & col("i_manufact").is_not_null()).select("i_manufact").unique()

    return (
        i1.join(matching_manufacts, on="i_manufact")
        .select(col("i_product_name"))
        .unique()
        .sort("i_product_name", nulls_last=True)
        .head(100)
    )


def q41_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(41)
    manufact, colors, units, sizes = _q41_parameters(params)

    item = ctx.get_table("item")

    i1 = item[(item["i_manufact_id"] >= manufact) & (item["i_manufact_id"] <= manufact + 40)]

    condition = None
    for category, color, unit, size in _Q41_GROUPS:
        group = (
            (item["i_category"] == category)
            & item["i_color"].isin(colors[color : color + 2])
            & item["i_units"].isin(units[unit : unit + 2])
            & item["i_size"].isin(sizes[size : size + 2])
        )
        condition = group if condition is None else condition | group

    matching_manufacts = _to_list(item[condition & item["i_manufact"].notnull()]["i_manufact"].unique())

    result = i1[i1["i_manufact"].isin(matching_manufacts)][["i_product_name"]].drop_duplicates()
    return result.sort_values("i_product_name").head(100)


def _sql_rank_expression(ctx: DataFrameContext, column: str, partition_by: list[str], *, descending: bool) -> Any:
    value = ctx.col(column)
    rank = value.rank(method="min", descending=descending).over(partition_by)
    if ctx.platform != "Polars":
        return rank
    if descending:
        null_count = value.is_null().sum().over(partition_by)
        return ctx.when(value.is_null()).then(1).otherwise(rank + null_count)
    nonnull_count = value.count().over(partition_by)
    return ctx.when(value.is_null()).then(nonnull_count + 1).otherwise(rank)


def q86_expression_impl(ctx: DataFrameContext) -> Any:
    from benchbox.core.tpcds.dataframe_queries.rollup_helper import (
        expand_rollup_expression,
        lochierarchy_expression,
    )

    params = get_parameters(86)
    dms = params.get("dms", 1212)

    col = ctx.col
    lit = ctx.lit

    web_sales, date_dim, item = _tables(ctx, "web_sales", "date_dim", "item")

    base = (
        web_sales.join(date_dim, left_on="ws_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="ws_item_sk", right_on="i_item_sk")
        .filter(col("d_month_seq").is_between(dms, dms + 11))
    )

    agg_exprs = [col("ws_net_paid").sum().alias("total_sum")]

    rollup_result = expand_rollup_expression(
        base,
        group_cols=["i_category", "i_class"],
        agg_exprs=agg_exprs,
        ctx=ctx,
        count_sources={"total_sum": "ws_net_paid"},
    )

    lochierarchy_expr = lochierarchy_expression("grouping_id", 2, ctx=ctx)
    result = rollup_result.with_columns(lochierarchy_expr.alias("lochierarchy"))

    result = result.with_columns(
        ctx.when(col("grouping_id") & 1 == 0).then(col("i_category")).otherwise(lit(None)).alias("partition_key")
    )

    result = result.with_columns(
        _sql_rank_expression(ctx, "total_sum", ["lochierarchy", "partition_key"], descending=True).alias(
            "rank_within_parent"
        )
    )

    return _sort_rollup_hierarchy_expression(ctx, result, "total_sum")


def q86_pandas_impl(ctx: DataFrameContext) -> Any:
    from benchbox.core.tpcds.dataframe_queries.rollup_helper import expand_rollup_pandas

    params = get_parameters(86)
    dms = params.get("dms", 1212)

    web_sales, date_dim, item = _tables(ctx, "web_sales", "date_dim", "item")

    base = web_sales.merge(date_dim, left_on="ws_sold_date_sk", right_on="d_date_sk")
    base = base.merge(item, left_on="ws_item_sk", right_on="i_item_sk")
    base = base[(base["d_month_seq"] >= dms) & (base["d_month_seq"] <= dms + 11)]

    agg_dict = {"total_sum": ("ws_net_paid", "sum")}
    rollup_result = expand_rollup_pandas(
        base,
        group_cols=["i_category", "i_class"],
        agg_dict=agg_dict,
        ctx=ctx,
        count_sources={"total_sum": "ws_net_paid"},
    )

    rollup_result["lochierarchy"] = rollup_result["grouping_id"].apply(lambda x: bin(x).count("1"))

    rollup_result["partition_key"] = rollup_result.apply(
        lambda r: r["i_category"] if (r["grouping_id"] & 1) == 0 else None, axis=1
    )

    rollup_result["rank_within_parent"] = rollup_result.groupby(["lochierarchy", "partition_key"], dropna=False)[
        "total_sum"
    ].rank(method="min", ascending=False, na_option="top")

    return _sort_rollup_hierarchy_pandas(rollup_result, "total_sum")


def _sort_rollup_hierarchy_expression(ctx: DataFrameContext, result: Any, measure: str) -> Any:
    col = ctx.col
    ordered = result.with_columns(
        ctx.when(col("lochierarchy") == 0).then(col("i_category")).otherwise(ctx.lit(None)).alias("order_category")
    )
    return (
        _sort_null_largest_expression(
            ctx, ordered, ["lochierarchy", "order_category", "rank_within_parent"], [True, False, False]
        )
        .select(col(measure), col("i_category"), col("i_class"), col("lochierarchy"), col("rank_within_parent"))
        .head(100)
    )


def _sort_rollup_hierarchy_pandas(rollup_result: Any, measure: str) -> Any:
    columns = [measure, "i_category", "i_class", "lochierarchy", "rank_within_parent"]
    result = rollup_result[columns].assign(
        order_category=rollup_result["i_category"].where(rollup_result["lochierarchy"] == 0, "")
    )
    ordered = _sort_null_largest_pandas(
        result, ["lochierarchy", "order_category", "rank_within_parent"], [True, False, False]
    )
    return ordered.drop(columns=["order_category"]).head(100)


def q36_expression_impl(ctx: DataFrameContext) -> Any:
    from benchbox.core.tpcds.dataframe_queries.rollup_helper import (
        expand_rollup_expression,
        lochierarchy_expression,
    )

    params = get_parameters(36)
    year = params.get("year", 2000)
    states = params.get("states", ["TN"])

    col = ctx.col
    lit = ctx.lit

    store_sales, date_dim, item, store = _tables(ctx, "store_sales", "date_dim", "item", "store")

    base = (
        store_sales.join(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="ss_item_sk", right_on="i_item_sk")
        .join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .filter((col("d_year") == lit(year)) & col("s_state").is_in(states))
    )

    agg_exprs = [
        col("ss_net_profit").sum().alias("sum_profit"),
        col("ss_ext_sales_price").sum().alias("sum_sales"),
    ]

    rollup_result = expand_rollup_expression(
        base,
        group_cols=["i_category", "i_class"],
        agg_exprs=agg_exprs,
        ctx=ctx,
        count_sources={"sum_profit": "ss_net_profit", "sum_sales": "ss_ext_sales_price"},
    )

    profit, sales = col("sum_profit"), col("sum_sales")
    zero_sales_margin = (
        ctx.when(profit == lit(0))
        .then(lit(float("nan")))
        .when(profit > lit(0))
        .then(lit(float("inf")))
        .otherwise(lit(float("-inf")))
    )
    margin = (
        ctx.when(profit.is_null() | sales.is_null())
        .then(lit(None))
        .when(sales == lit(0))
        .then(zero_sales_margin)
        .otherwise(profit / sales)
    )
    result = rollup_result.with_columns(margin.alias("gross_margin"))

    lochierarchy_expr = lochierarchy_expression("grouping_id", 2, ctx=ctx)
    result = result.with_columns(lochierarchy_expr.alias("lochierarchy"))

    result = result.with_columns(
        ctx.when(col("grouping_id") & 1 == 0).then(col("i_category")).otherwise(lit(None)).alias("partition_key")
    )

    result = result.with_columns(
        _sql_rank_expression(ctx, "gross_margin", ["lochierarchy", "partition_key"], descending=False).alias(
            "rank_within_parent"
        )
    )

    return _sort_rollup_hierarchy_expression(ctx, result, "gross_margin")


def q36_pandas_impl(ctx: DataFrameContext) -> Any:
    from benchbox.core.tpcds.dataframe_queries.rollup_helper import expand_rollup_pandas

    params = get_parameters(36)
    year = params.get("year", 2000)
    states = params.get("states", ["TN"])

    store_sales, date_dim, item, store = _tables(ctx, "store_sales", "date_dim", "item", "store")

    base = store_sales.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
    base = base.merge(item, left_on="ss_item_sk", right_on="i_item_sk")
    base = base.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    base = base[(base["d_year"] == year) & base["s_state"].isin(states)]

    agg_dict = {
        "sum_profit": ("ss_net_profit", "sum"),
        "sum_sales": ("ss_ext_sales_price", "sum"),
    }
    rollup_result = expand_rollup_pandas(
        base,
        group_cols=["i_category", "i_class"],
        agg_dict=agg_dict,
        ctx=ctx,
        count_sources={"sum_profit": "ss_net_profit", "sum_sales": "ss_ext_sales_price"},
    )

    margin = rollup_result["sum_profit"].astype(float) / rollup_result["sum_sales"].astype(float)
    valid_sums = rollup_result["sum_profit"].notnull() & rollup_result["sum_sales"].notnull()
    rollup_result["gross_margin"] = margin.astype(object).where(valid_sums, None)
    rollup_result["nan_margin"] = margin.isna() & valid_sums

    rollup_result["lochierarchy"] = rollup_result["grouping_id"].apply(lambda x: bin(x).count("1"))

    rollup_result["partition_key"] = rollup_result.apply(
        lambda r: r["i_category"] if (r["grouping_id"] & 1) == 0 else None, axis=1
    )

    rollup_result["rank_within_parent"] = rollup_result.groupby(["lochierarchy", "partition_key"], dropna=False)[
        "gross_margin"
    ].rank(method="min", ascending=True, na_option="bottom")
    null_offset = (
        rollup_result.groupby(["lochierarchy", "partition_key"], dropna=False)["nan_margin"]
        .transform("sum")
        .where(~valid_sums, 0)
    )
    rollup_result["rank_within_parent"] += null_offset

    return _sort_rollup_hierarchy_pandas(rollup_result, "gross_margin")


def _q51_running_total(ctx: DataFrameContext, item_key: str) -> Any:
    col, lit = ctx.col, ctx.lit
    seen = ctx.when(col("daily_sales").is_not_null()).then(1).otherwise(0).cum_sum().over([item_key], order_by="d_date")
    total = col("daily_sales").fill_null(0).cum_sum().over([item_key], order_by="d_date")
    return ctx.when(seen > lit(0)).then(total).otherwise(lit(None))


def _q51_merge_channels(ctx: DataFrameContext, web: Any, store: Any) -> Any:
    col, lit = ctx.col, ctx.lit

    def channel(frame: Any, name: str, null_date_channel: int) -> Any:
        other_name = "store_sales" if name == "web_sales" else "web_sales"
        return frame.select(
            col("item_sk").alias("item_sk_final"),
            col("d_date").alias("d_date_final"),
            col("cume_sales").alias(name),
            ctx.when(lit(False)).then(col("cume_sales")).otherwise(lit(None)).alias(other_name),
            ctx.when(col("d_date").is_null()).then(null_date_channel).otherwise(0).alias("null_date_channel"),
        ).select("item_sk_final", "d_date_final", "web_sales", "store_sales", "null_date_channel")

    return (
        ctx.concat([channel(web, "web_sales", 1), channel(store, "store_sales", 2)])
        .group_by(["item_sk_final", "d_date_final", "null_date_channel"])
        .agg(col("web_sales").max().alias("web_sales"), col("store_sales").max().alias("store_sales"))
        .drop("null_date_channel")
    )


def q51_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(51)
    dms = params.get("dms", 1212)

    col = ctx.col
    lit = ctx.lit

    web_sales, store_sales, date_dim = _tables(ctx, "web_sales", "store_sales", "date_dim")

    dates = date_dim.filter(col("d_month_seq").is_between(dms, dms + 11))

    web_base = (
        web_sales.filter(col("ws_item_sk").is_not_null())
        .join(dates, left_on="ws_sold_date_sk", right_on="d_date_sk")
        .group_by(["ws_item_sk", "d_date"])
        .agg(
            col("ws_sales_price").count().alias("daily_count"),
            col("ws_sales_price").sum().alias("daily_sales"),
        )
        .with_columns(
            ctx.when(col("daily_count") > lit(0)).then(col("daily_sales")).otherwise(lit(None)).alias("daily_sales")
        )
    )

    web_v1 = (
        web_base.sort(["ws_item_sk", "d_date"])
        .with_columns(_q51_running_total(ctx, "ws_item_sk").alias("cume_sales"))
        .select(
            col("ws_item_sk").alias("item_sk"),
            col("d_date"),
            col("cume_sales"),
        )
    )

    store_base = (
        store_sales.filter(col("ss_item_sk").is_not_null())
        .join(dates, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .group_by(["ss_item_sk", "d_date"])
        .agg(
            col("ss_sales_price").count().alias("daily_count"),
            col("ss_sales_price").sum().alias("daily_sales"),
        )
        .with_columns(
            ctx.when(col("daily_count") > lit(0)).then(col("daily_sales")).otherwise(lit(None)).alias("daily_sales")
        )
    )

    store_v1 = (
        store_base.sort(["ss_item_sk", "d_date"])
        .with_columns(_q51_running_total(ctx, "ss_item_sk").alias("cume_sales"))
        .select(
            col("ss_item_sk").alias("item_sk"),
            col("d_date"),
            col("cume_sales"),
        )
    )

    joined = _q51_merge_channels(ctx, web_v1, store_v1).sort(["item_sk_final", "d_date_final"])

    web_cumulative = (
        col("web_sales").fill_null(float("-inf")).cum_max().over(["item_sk_final"], order_by="d_date_final")
    )
    store_cumulative = (
        col("store_sales").fill_null(float("-inf")).cum_max().over(["item_sk_final"], order_by="d_date_final")
    )
    result = joined.with_columns(
        ctx.when(web_cumulative == lit(float("-inf"))).then(None).otherwise(web_cumulative).alias("web_cumulative"),
        ctx.when(store_cumulative == lit(float("-inf")))
        .then(None)
        .otherwise(store_cumulative)
        .alias("store_cumulative"),
    )

    return (
        result.filter(col("web_cumulative") > col("store_cumulative"))
        .select(
            col("item_sk_final").alias("item_sk"),
            col("d_date_final").alias("d_date"),
            col("web_sales"),
            col("store_sales"),
            col("web_cumulative"),
            col("store_cumulative"),
        )
        .sort(["item_sk", "d_date"])
        .head(100)
    )


def _q51_running_total_pandas(base: Any, item_key: str) -> Any:
    return base.groupby(item_key)["daily_sales"].cumsum().groupby(base[item_key], sort=False).ffill()


def q51_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(51)
    dms = params.get("dms", 1212)

    web_sales, store_sales, date_dim = _tables(ctx, "web_sales", "store_sales", "date_dim")

    dates = date_dim[(date_dim["d_month_seq"] >= dms) & (date_dim["d_month_seq"] <= dms + 11)]

    web_merged = web_sales[web_sales["ws_item_sk"].notnull()].merge(
        dates[["d_date_sk", "d_date"]], left_on="ws_sold_date_sk", right_on="d_date_sk"
    )
    web_base = web_merged.groupby(["ws_item_sk", "d_date"], as_index=False).agg(
        daily_sales=("ws_sales_price", lambda values: values.sum(min_count=1))
    )
    web_base = web_base.sort_values(["ws_item_sk", "d_date"])
    web_base["cume_sales"] = _q51_running_total_pandas(web_base, "ws_item_sk")
    web_v1 = web_base[["ws_item_sk", "d_date", "cume_sales"]].rename(columns={"ws_item_sk": "item_sk"})

    store_merged = store_sales[store_sales["ss_item_sk"].notnull()].merge(
        dates[["d_date_sk", "d_date"]], left_on="ss_sold_date_sk", right_on="d_date_sk"
    )
    store_base = store_merged.groupby(["ss_item_sk", "d_date"], as_index=False).agg(
        daily_sales=("ss_sales_price", lambda values: values.sum(min_count=1))
    )
    store_base = store_base.sort_values(["ss_item_sk", "d_date"])
    store_base["cume_sales"] = _q51_running_total_pandas(store_base, "ss_item_sk")
    store_v1 = store_base[["ss_item_sk", "d_date", "cume_sales"]].rename(columns={"ss_item_sk": "item_sk"})

    joined = web_v1.merge(store_v1, on=["item_sk", "d_date"], how="outer", suffixes=("_web", "_store"))
    joined["web_sales"] = joined["cume_sales_web"]
    joined["store_sales"] = joined["cume_sales_store"]

    joined = joined.sort_values(["item_sk", "d_date"])
    joined["web_cumulative"] = (
        joined.groupby("item_sk")["web_sales"].cummax().groupby(joined["item_sk"], sort=False).ffill()
    )
    joined["store_cumulative"] = (
        joined.groupby("item_sk")["store_sales"].cummax().groupby(joined["item_sk"], sort=False).ffill()
    )

    result = joined[joined["web_cumulative"] > joined["store_cumulative"]]
    result = result[["item_sk", "d_date", "web_sales", "store_sales", "web_cumulative", "store_cumulative"]]
    return result.sort_values(["item_sk", "d_date"]).head(100).astype(object).where(lambda frame: frame.notnull(), None)


_ROLLING_MEASURES = ("avg_monthly_sales", "sum_sales", "psum", "nsum")
_ROLLING_PERIOD_COLUMNS = ("d_year", "d_moy")


def _rolling_select_list(text: str, allowed: tuple[str, ...], what: str) -> list[str]:
    columns = [part.strip().removeprefix("v1.") for part in text.split(",") if part.strip()]
    unknown = [column for column in columns if column not in allowed]
    if unknown or not columns:
        raise ValueError(f"{what} must name columns among {list(allowed)}, got {text!r}")
    return columns


def _rolling_average_parameters(
    params: Any, partition_keys: list[str], select_one_default: str
) -> tuple[int, list[str], str]:
    year = params.get("year", 2000)
    select_one = _rolling_select_list(params.get("select_one", select_one_default), tuple(partition_keys), "SELECTONE")
    select_two = _rolling_select_list(
        params.get("select_two", ",v1.d_year, v1.d_moy"), _ROLLING_PERIOD_COLUMNS, "SELECTTWO"
    )
    order_by = params.get("order_by", "nsum")
    order_columns = [*partition_keys, *_ROLLING_MEASURES]
    if order_by not in order_columns:
        raise ValueError(f"ORDERBY must be one of {order_columns}, got {order_by!r}")
    return year, [*select_one, *select_two, *_ROLLING_MEASURES], order_by


def _rolling_average_expression_impl(
    ctx: DataFrameContext,
    *,
    query_id: int,
    sales_table: str,
    date_sk_col: str,
    item_sk_col: str,
    sales_price_col: str,
    channel_table: str,
    channel_join_key_left: str,
    channel_join_key_right: str,
    channel_cols: list[str],
    select_one_default: str,
) -> Any:
    partition_keys = ["i_category", "i_brand", *channel_cols]
    year, output_cols, order_by = _rolling_average_parameters(
        get_parameters(query_id), partition_keys, select_one_default
    )
    col = ctx.col
    lit = ctx.lit
    sales = ctx.get_table(sales_table)
    date_dim, item = _tables(ctx, "date_dim", "item")
    channel = ctx.get_table(channel_table)
    dates = date_dim.filter(
        (col("d_year") == lit(year))
        | ((col("d_year") == lit(year - 1)) & (col("d_moy") == 12))
        | ((col("d_year") == lit(year + 1)) & (col("d_moy") == 1))
    )
    base = (
        sales.join(dates, left_on=date_sk_col, right_on="d_date_sk")
        .join(item, left_on=item_sk_col, right_on="i_item_sk")
        .join(channel, left_on=channel_join_key_left, right_on=channel_join_key_right)
        .group_by([*partition_keys, "d_year", "d_moy"])
        .agg(_sum_or_null_expression(ctx, col(sales_price_col)).alias("sum_sales"))
    )
    v1 = base.with_columns(
        col("sum_sales").mean().over([*partition_keys, "d_year"]).alias("avg_monthly_sales"),
        ((col("d_year") * 100) + col("d_moy")).rank(method="ordinal").over(partition_keys).alias("rn"),
    )
    lag_select = [col(key) for key in partition_keys] + [col("rn"), col("sum_sales").alias("psum")]
    lead_select = [col(key) for key in partition_keys] + [col("rn"), col("sum_sales").alias("nsum")]
    result = (
        v1.join(v1.select(lag_select), on=partition_keys, suffix="_lag")
        .filter(col("rn") == col("rn_lag") + 1)
        .join(v1.select(lead_select), on=partition_keys, suffix="_lead")
        .filter(col("rn") == col("rn_lead") - 1)
        .filter(
            (col("d_year") == lit(year))
            & (col("avg_monthly_sales") > 0)
            & ((col("sum_sales") - col("avg_monthly_sales")).abs() / col("avg_monthly_sales") > 0.1)
        )
    )
    return (
        _sort_null_largest_expression(
            ctx,
            result.select(
                [col(key) for key in partition_keys]
                + [
                    col("d_year"),
                    col("d_moy"),
                    col("avg_monthly_sales"),
                    col("sum_sales"),
                    col("psum"),
                    col("nsum"),
                ]
            ).with_columns((col("sum_sales") - col("avg_monthly_sales")).alias("diff")),
            ["diff", order_by],
        )
        .select([col(name) for name in output_cols])
        .head(100)
    )


def q47_expression_impl(ctx: DataFrameContext) -> Any:
    return _rolling_average_expression_impl(
        ctx,
        query_id=47,
        sales_table="store_sales",
        date_sk_col="ss_sold_date_sk",
        item_sk_col="ss_item_sk",
        sales_price_col="ss_sales_price",
        channel_table="store",
        channel_join_key_left="ss_store_sk",
        channel_join_key_right="s_store_sk",
        channel_cols=["s_store_name", "s_company_name"],
        select_one_default="v1.i_category, v1.i_brand",
    )


def _rolling_average_pandas_impl(
    ctx: DataFrameContext,
    *,
    query_id: int,
    sales_table: str,
    date_sk_col: str,
    item_sk_col: str,
    sales_price_col: str,
    channel_table: str,
    channel_join_key_left: str,
    channel_join_key_right: str,
    channel_cols: list[str],
    select_one_default: str,
) -> Any:
    partition_keys = ["i_category", "i_brand", *channel_cols]
    year, output_cols, order_by = _rolling_average_parameters(
        get_parameters(query_id), partition_keys, select_one_default
    )

    sales = ctx.get_table(sales_table)
    date_dim, item = _tables(ctx, "date_dim", "item")
    channel = ctx.get_table(channel_table)

    dates = date_dim[
        (date_dim["d_year"] == year)
        | ((date_dim["d_year"] == year - 1) & (date_dim["d_moy"] == 12))
        | ((date_dim["d_year"] == year + 1) & (date_dim["d_moy"] == 1))
    ]

    group_keys = ["i_category", "i_brand", *channel_cols, "d_year", "d_moy"]

    base = sales.merge(dates, left_on=date_sk_col, right_on="d_date_sk")
    base = base.merge(item, left_on=item_sk_col, right_on="i_item_sk")
    base = base.merge(channel, left_on=channel_join_key_left, right_on=channel_join_key_right)
    base = _grouped_pandas_aggregates(base, group_keys, {"sum_sales": (sales_price_col, "sum")})

    base["year_month"] = base["d_year"] * 100 + base["d_moy"]
    base = base.sort_values([*partition_keys, "year_month"])

    base["rn"] = base.groupby(partition_keys).cumcount() + 1

    base["avg_monthly_sales"] = base.groupby([*partition_keys, "d_year"])["sum_sales"].transform("mean")

    v1 = base.copy()
    lag_cols = [*partition_keys, "rn", "sum_sales"]
    v1_lag = v1[lag_cols].copy()
    v1_lag = v1_lag.rename(columns={"sum_sales": "psum", "rn": "rn_lag"})
    v1_lag["rn_lag"] = v1_lag["rn_lag"] + 1

    v1_lead = v1[lag_cols].copy()
    v1_lead = v1_lead.rename(columns={"sum_sales": "nsum", "rn": "rn_lead"})
    v1_lead["rn_lead"] = v1_lead["rn_lead"] - 1

    merge_keys = [*partition_keys, "rn"]
    result = v1.merge(
        v1_lag,
        left_on=merge_keys,
        right_on=[*partition_keys, "rn_lag"],
    )
    result = result.merge(
        v1_lead,
        left_on=merge_keys,
        right_on=[*partition_keys, "rn_lead"],
    )

    result = result[
        (result["d_year"] == year)
        & (result["avg_monthly_sales"] > 0)
        & (abs(result["sum_sales"] - result["avg_monthly_sales"]) / result["avg_monthly_sales"] > 0.1)
    ]

    result["diff"] = result["sum_sales"] - result["avg_monthly_sales"]
    result = result.sort_values(["diff", order_by]).head(100)
    result = result[output_cols]
    return _none_for_null(result, list(result.columns))


def q47_pandas_impl(ctx: DataFrameContext) -> Any:
    return _rolling_average_pandas_impl(
        ctx,
        query_id=47,
        sales_table="store_sales",
        date_sk_col="ss_sold_date_sk",
        item_sk_col="ss_item_sk",
        sales_price_col="ss_sales_price",
        channel_table="store",
        channel_join_key_left="ss_store_sk",
        channel_join_key_right="s_store_sk",
        channel_cols=["s_store_name", "s_company_name"],
        select_one_default="v1.i_category, v1.i_brand",
    )


def q57_expression_impl(ctx: DataFrameContext) -> Any:
    return _rolling_average_expression_impl(
        ctx,
        query_id=57,
        sales_table="catalog_sales",
        date_sk_col="cs_sold_date_sk",
        item_sk_col="cs_item_sk",
        sales_price_col="cs_sales_price",
        channel_table="call_center",
        channel_join_key_left="cs_call_center_sk",
        channel_join_key_right="cc_call_center_sk",
        channel_cols=["cc_name"],
        select_one_default="v1.cc_name",
    )


def q57_pandas_impl(ctx: DataFrameContext) -> Any:
    return _rolling_average_pandas_impl(
        ctx,
        query_id=57,
        sales_table="catalog_sales",
        date_sk_col="cs_sold_date_sk",
        item_sk_col="cs_item_sk",
        sales_price_col="cs_sales_price",
        channel_table="call_center",
        channel_join_key_left="cs_call_center_sk",
        channel_join_key_right="cc_call_center_sk",
        channel_cols=["cc_name"],
        select_one_default="v1.cc_name",
    )


def q67_expression_impl(ctx: DataFrameContext) -> Any:
    from benchbox.core.tpcds.dataframe_queries.rollup_helper import expand_rollup_expression

    params = get_parameters(67)
    dms = params.get("dms", 1212)

    col = ctx.col
    lit = ctx.lit

    store_sales, date_dim, item, store = _tables(ctx, "store_sales", "date_dim", "item", "store")

    base = (
        store_sales.join(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="ss_item_sk", right_on="i_item_sk")
        .join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .filter(col("d_month_seq").is_between(dms, dms + 11))
        .with_columns(
            ctx.coalesce((col("ss_sales_price") * lit(100)).round(0).cast_int64() * col("ss_quantity"), lit(0)).alias(
                "sales_amount_cents"
            )
        )
    )

    group_cols = [
        "i_category",
        "i_class",
        "i_brand",
        "i_product_name",
        "d_year",
        "d_qoy",
        "d_moy",
        "s_store_id",
    ]

    agg_exprs = [col("sales_amount_cents").sum().alias("sumsales_cents")]

    rollup_result = expand_rollup_expression(
        base, group_cols, agg_exprs, ctx, count_sources={"sumsales_cents": "sales_amount_cents"}
    )

    result = rollup_result.with_columns(
        _sql_rank_expression(ctx, "sumsales_cents", ["i_category"], descending=True).alias("rk")
    ).with_columns((col("sumsales_cents").cast_float64() / lit(100)).alias("sumsales"))

    return (
        result.filter(col("rk") <= 100)
        .select(
            col("i_category"),
            col("i_class"),
            col("i_brand"),
            col("i_product_name"),
            col("d_year"),
            col("d_qoy"),
            col("d_moy"),
            col("s_store_id"),
            col("sumsales"),
            col("rk"),
        )
        .sort(
            [
                "i_category",
                "i_class",
                "i_brand",
                "i_product_name",
                "d_year",
                "d_qoy",
                "d_moy",
                "s_store_id",
                "sumsales",
                "rk",
            ],
            nulls_last=True,
        )
        .head(100)
    )


def q67_pandas_impl(ctx: DataFrameContext) -> Any:
    from benchbox.core.tpcds.dataframe_queries.rollup_helper import expand_rollup_pandas

    params = get_parameters(67)
    dms = params.get("dms", 1212)

    store_sales, date_dim, item, store = _tables(ctx, "store_sales", "date_dim", "item", "store")

    base = store_sales.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")
    base = base.merge(item, left_on="ss_item_sk", right_on="i_item_sk")
    base = base.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    base = base[(base["d_month_seq"] >= dms) & (base["d_month_seq"] <= dms + 11)]
    base["sales_amount_cents"] = (
        (base["ss_sales_price"] * 100).round().astype("Int64") * base["ss_quantity"].astype("Int64")
    ).fillna(0)

    group_cols = [
        "i_category",
        "i_class",
        "i_brand",
        "i_product_name",
        "d_year",
        "d_qoy",
        "d_moy",
        "s_store_id",
    ]
    agg_dict = {"sumsales_cents": ("sales_amount_cents", "sum")}
    rollup_result = expand_rollup_pandas(
        base, group_cols, agg_dict, ctx, count_sources={"sumsales_cents": "sales_amount_cents"}
    )

    rollup_result["rk"] = rollup_result.groupby("i_category", dropna=False)["sumsales_cents"].rank(
        method="min", ascending=False, na_option="top"
    )

    rollup_result["sumsales"] = rollup_result["sumsales_cents"].astype("Float64") / 100

    result = rollup_result[rollup_result["rk"] <= 100]
    result = result[
        [
            "i_category",
            "i_class",
            "i_brand",
            "i_product_name",
            "d_year",
            "d_qoy",
            "d_moy",
            "s_store_id",
            "sumsales",
            "rk",
        ]
    ]
    return result.sort_values(
        [
            "i_category",
            "i_class",
            "i_brand",
            "i_product_name",
            "d_year",
            "d_qoy",
            "d_moy",
            "s_store_id",
            "sumsales",
            "rk",
        ]
    ).head(100)


def q70_expression_impl(ctx: DataFrameContext) -> Any:
    from benchbox.core.tpcds.dataframe_queries.rollup_helper import (
        expand_rollup_expression,
        lochierarchy_expression,
    )

    params = get_parameters(70)
    dms = params.get("dms", 1212)

    col = ctx.col
    lit = ctx.lit

    store_sales, date_dim, store = _tables(ctx, "store_sales", "date_dim", "store")

    dates = date_dim.filter(col("d_month_seq").is_between(dms, dms + 11))

    base = (
        store_sales.join(dates, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .filter(col("s_state").is_not_null())
    )

    agg_exprs = [col("ss_net_profit").sum().alias("total_sum")]
    rollup_result = expand_rollup_expression(
        base, ["s_state", "s_county"], agg_exprs, ctx, count_sources={"total_sum": "ss_net_profit"}
    )
    rollup_result = rollup_result.with_columns(col("total_sum").round(2).alias("total_sum"))

    lochierarchy_expr = lochierarchy_expression("grouping_id", 2, ctx=ctx)
    result = rollup_result.with_columns(lochierarchy_expr.alias("lochierarchy"))

    result = result.with_columns(
        ctx.when(col("grouping_id") & 1 == 0).then(col("s_state")).otherwise(lit(None)).alias("partition_key")
    )

    result = result.with_columns(
        _sql_rank_expression(ctx, "total_sum", ["lochierarchy", "partition_key"], descending=True).alias(
            "rank_within_parent"
        )
    )

    result = result.with_columns(
        ctx.when(col("lochierarchy") == 0).then(col("s_state")).otherwise(lit(None)).alias("order_state")
    )

    return (
        _sort_null_largest_expression(
            ctx, result, ["lochierarchy", "order_state", "rank_within_parent"], [True, False, False]
        )
        .select(
            col("total_sum"),
            col("s_state"),
            col("s_county"),
            col("lochierarchy"),
            col("rank_within_parent"),
        )
        .head(100)
    )


def q70_pandas_impl(ctx: DataFrameContext) -> Any:
    from benchbox.core.tpcds.dataframe_queries.rollup_helper import expand_rollup_pandas

    params = get_parameters(70)
    dms = params.get("dms", 1212)

    store_sales, date_dim, store = _tables(ctx, "store_sales", "date_dim", "store")

    dates = date_dim[(date_dim["d_month_seq"] >= dms) & (date_dim["d_month_seq"] <= dms + 11)]

    merged = store_sales.merge(dates[["d_date_sk"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    merged = merged.merge(store[["s_store_sk", "s_state", "s_county"]], left_on="ss_store_sk", right_on="s_store_sk")

    base = merged[merged["s_state"].notnull()]

    agg_dict = {"total_sum": ("ss_net_profit", "sum")}
    rollup_result = expand_rollup_pandas(
        base, ["s_state", "s_county"], agg_dict, ctx, count_sources={"total_sum": "ss_net_profit"}
    )
    totals = rollup_result["total_sum"]
    rollup_result["total_sum"] = totals.astype(float).round(2).astype(object).where(totals.notnull(), None)

    rollup_result["lochierarchy"] = rollup_result["grouping_id"].apply(lambda x: bin(x).count("1"))

    rollup_result["partition_key"] = rollup_result.apply(
        lambda r: r["s_state"] if (r["grouping_id"] & 1) == 0 else None, axis=1
    )

    rollup_result["rank_within_parent"] = rollup_result.groupby(["lochierarchy", "partition_key"], dropna=False)[
        "total_sum"
    ].rank(method="min", ascending=False, na_option="top")

    rollup_result["order_state"] = rollup_result["s_state"].where(rollup_result["lochierarchy"] == 0, "")

    result = rollup_result[["total_sum", "s_state", "s_county", "lochierarchy", "rank_within_parent", "order_state"]]
    ordered = _sort_null_largest_pandas(
        result, ["lochierarchy", "order_state", "rank_within_parent"], [True, False, False]
    )
    return ordered.drop(columns=["order_state"]).head(100)


def q2_expression_impl(ctx: DataFrameContext) -> Any:
    col = ctx.col
    lit = ctx.lit

    params = get_parameters(2)
    year = params.get("year", 1998)

    web_sales, catalog_sales, date_dim = _tables(ctx, "web_sales", "catalog_sales", "date_dim")

    ws = web_sales.select(
        [
            col("ws_sold_date_sk").alias("sold_date_sk"),
            col("ws_ext_sales_price").alias("sales_price"),
        ]
    )
    cs = catalog_sales.select(
        [
            col("cs_sold_date_sk").alias("sold_date_sk"),
            col("cs_ext_sales_price").alias("sales_price"),
        ]
    )
    wscs = ctx.concat([ws, cs])

    joined = wscs.join(
        date_dim,
        left_on="sold_date_sk",
        right_on="d_date_sk",
        how="inner",
    )

    wswscs = joined.group_by("d_week_seq").agg(
        [
            ctx.when(col("d_day_name") == "Sunday").then(col("sales_price")).otherwise(None).sum().alias("sun_sales"),
            ctx.when(col("d_day_name") == "Monday").then(col("sales_price")).otherwise(None).sum().alias("mon_sales"),
            ctx.when(col("d_day_name") == "Tuesday").then(col("sales_price")).otherwise(None).sum().alias("tue_sales"),
            ctx.when(col("d_day_name") == "Wednesday")
            .then(col("sales_price"))
            .otherwise(None)
            .sum()
            .alias("wed_sales"),
            ctx.when(col("d_day_name") == "Thursday").then(col("sales_price")).otherwise(None).sum().alias("thu_sales"),
            ctx.when(col("d_day_name") == "Friday").then(col("sales_price")).otherwise(None).sum().alias("fri_sales"),
            ctx.when(col("d_day_name") == "Saturday").then(col("sales_price")).otherwise(None).sum().alias("sat_sales"),
            *[
                ctx.when(col("d_day_name") == day).then(col("sales_price")).otherwise(None).count().alias(f"n_{alias}")
                for day, alias in [
                    ("Sunday", "sun_sales"),
                    ("Monday", "mon_sales"),
                    ("Tuesday", "tue_sales"),
                    ("Wednesday", "wed_sales"),
                    ("Thursday", "thu_sales"),
                    ("Friday", "fri_sales"),
                    ("Saturday", "sat_sales"),
                ]
            ],
        ]
    )
    for _alias in ["sun_sales", "mon_sales", "tue_sales", "wed_sales", "thu_sales", "fri_sales", "sat_sales"]:
        wswscs = wswscs.with_columns(
            ctx.when(col(f"n_{_alias}") > lit(0)).then(col(_alias)).otherwise(None).alias(_alias)
        )

    date_weeks = date_dim.filter(col("d_year") == year).select(["d_week_seq"])
    y1 = wswscs.join(date_weeks, on="d_week_seq", how="inner").select(
        [
            col("d_week_seq").alias("d_week_seq1"),
            col("sun_sales").alias("sun_sales1"),
            col("mon_sales").alias("mon_sales1"),
            col("tue_sales").alias("tue_sales1"),
            col("wed_sales").alias("wed_sales1"),
            col("thu_sales").alias("thu_sales1"),
            col("fri_sales").alias("fri_sales1"),
            col("sat_sales").alias("sat_sales1"),
        ]
    )

    date_weeks_next = date_dim.filter(col("d_year") == year + 1).select(["d_week_seq"])
    y2 = wswscs.join(date_weeks_next, on="d_week_seq", how="inner").select(
        [
            col("d_week_seq").alias("d_week_seq2"),
            col("sun_sales").alias("sun_sales2"),
            col("mon_sales").alias("mon_sales2"),
            col("tue_sales").alias("tue_sales2"),
            col("wed_sales").alias("wed_sales2"),
            col("thu_sales").alias("thu_sales2"),
            col("fri_sales").alias("fri_sales2"),
            col("sat_sales").alias("sat_sales2"),
        ]
    )

    return (
        y1.join(
            y2,
            left_on="d_week_seq1",
            right_on=col("d_week_seq2") - 53,
            how="inner",
        )
        .select(
            [
                col("d_week_seq1"),
                (col("sun_sales1") / col("sun_sales2")).round(2).alias("sun_ratio"),
                (col("mon_sales1") / col("mon_sales2")).round(2).alias("mon_ratio"),
                (col("tue_sales1") / col("tue_sales2")).round(2).alias("tue_ratio"),
                (col("wed_sales1") / col("wed_sales2")).round(2).alias("wed_ratio"),
                (col("thu_sales1") / col("thu_sales2")).round(2).alias("thu_ratio"),
                (col("fri_sales1") / col("fri_sales2")).round(2).alias("fri_ratio"),
                (col("sat_sales1") / col("sat_sales2")).round(2).alias("sat_ratio"),
            ]
        )
        .sort("d_week_seq1")
    )


def q2_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(2)
    year = params.get("year", 1998)

    web_sales, catalog_sales, date_dim = _tables(ctx, "web_sales", "catalog_sales", "date_dim")

    ws = web_sales[["ws_sold_date_sk", "ws_ext_sales_price"]].rename(
        columns={"ws_sold_date_sk": "sold_date_sk", "ws_ext_sales_price": "sales_price"}
    )
    cs = catalog_sales[["cs_sold_date_sk", "cs_ext_sales_price"]].rename(
        columns={"cs_sold_date_sk": "sold_date_sk", "cs_ext_sales_price": "sales_price"}
    )
    wscs = ctx.concat([ws, cs])

    joined = wscs.merge(date_dim, left_on="sold_date_sk", right_on="d_date_sk", how="inner")

    days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    day_cols = ["sun_sales", "mon_sales", "tue_sales", "wed_sales", "thu_sales", "fri_sales", "sat_sales"]

    for day, day_col in zip(days, day_cols):
        mask = joined["d_day_name"] == day
        joined[day_col] = joined["sales_price"].where(mask)
        joined[f"n_{day_col}"] = mask.astype(int)

    agg_dict = dict.fromkeys(day_cols, "sum")
    agg_dict.update({f"n_{day_col}": "sum" for day_col in day_cols})
    wswscs = ctx.groupby_agg(joined, "d_week_seq", agg_dict, as_index=False)
    for day_col in day_cols:
        wswscs[day_col] = wswscs[day_col].where(wswscs[f"n_{day_col}"] > 0)
    wswscs = wswscs.drop(columns=[f"n_{day_col}" for day_col in day_cols])

    year_weeks = date_dim[date_dim["d_year"] == year][["d_week_seq"]]
    next_year_weeks = date_dim[date_dim["d_year"] == year + 1][["d_week_seq"]]

    y1 = wswscs.merge(year_weeks, on="d_week_seq", how="inner").copy()
    y1.columns = ["d_week_seq1"] + [f"{c}1" for c in day_cols]

    y2 = wswscs.merge(next_year_weeks, on="d_week_seq", how="inner").copy()
    y2.columns = ["d_week_seq2"] + [f"{c}2" for c in day_cols]

    y2["join_key"] = y2["d_week_seq2"] - 53
    result = y1.merge(y2, left_on="d_week_seq1", right_on="join_key", how="inner")

    for day_col in day_cols:
        result[f"{day_col[:3]}_ratio"] = (result[f"{day_col}1"] / result[f"{day_col}2"]).round(2)

    result = result[
        ["d_week_seq1", "sun_ratio", "mon_ratio", "tue_ratio", "wed_ratio", "thu_ratio", "fri_ratio", "sat_ratio"]
    ]
    result = result.sort_values("d_week_seq1")
    return _none_for_null(
        result, ["sun_ratio", "mon_ratio", "tue_ratio", "wed_ratio", "thu_ratio", "fri_ratio", "sat_ratio"]
    )


_Q31_ORDER_COLUMNS = (
    "ca_county",
    "d_year",
    "web_q1_q2_increase",
    "store_q1_q2_increase",
    "web_q2_q3_increase",
    "store_q2_q3_increase",
)


def _q31_order_column(order_by: str) -> str:
    column = order_by.rpartition(".")[2]
    if column not in _Q31_ORDER_COLUMNS:
        raise ValueError(f"Q31 ORDER BY must be one of {list(_Q31_ORDER_COLUMNS)}, got {order_by!r}")
    return column


def q31_expression_impl(ctx: DataFrameContext) -> Any:
    col = ctx.col
    lit = ctx.lit

    params = get_parameters(31)
    year = params.get("year", 2000)
    order_by = _q31_order_column(params.get("order_by", "ss1.ca_county"))

    store_sales, web_sales, date_dim, customer_address = _tables(
        ctx, "store_sales", "web_sales", "date_dim", "customer_address"
    )

    ss = (
        store_sales.join(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk", how="inner")
        .join(customer_address, left_on="ss_addr_sk", right_on="ca_address_sk", how="inner")
        .group_by(["ca_county", "d_qoy", "d_year"])
        .agg(col("ss_ext_sales_price").sum().alias("store_sales"))
    )

    ws = (
        web_sales.join(date_dim, left_on="ws_sold_date_sk", right_on="d_date_sk", how="inner")
        .join(customer_address, left_on="ws_bill_addr_sk", right_on="ca_address_sk", how="inner")
        .group_by(["ca_county", "d_qoy", "d_year"])
        .agg(col("ws_ext_sales_price").sum().alias("web_sales"))
    )

    ss1 = ss.filter((col("d_qoy") == 1) & (col("d_year") == year))
    ss2 = ss.filter((col("d_qoy") == 2) & (col("d_year") == year))
    ss3 = ss.filter((col("d_qoy") == 3) & (col("d_year") == year))

    ws1 = ws.filter((col("d_qoy") == 1) & (col("d_year") == year))
    ws2 = ws.filter((col("d_qoy") == 2) & (col("d_year") == year))
    ws3 = ws.filter((col("d_qoy") == 3) & (col("d_year") == year))

    result = (
        ss1.select([col("ca_county"), col("store_sales").alias("ss1_sales")])
        .join(
            ss2.select([col("ca_county"), col("store_sales").alias("ss2_sales")]),
            on="ca_county",
            how="inner",
        )
        .join(
            ss3.select([col("ca_county"), col("store_sales").alias("ss3_sales")]),
            on="ca_county",
            how="inner",
        )
        .join(
            ws1.select([col("ca_county"), col("web_sales").alias("ws1_sales")]),
            on="ca_county",
            how="inner",
        )
        .join(
            ws2.select([col("ca_county"), col("web_sales").alias("ws2_sales")]),
            on="ca_county",
            how="inner",
        )
        .join(
            ws3.select([col("ca_county"), col("web_sales").alias("ws3_sales")]),
            on="ca_county",
            how="inner",
        )
    )

    result = result.with_columns(
        [
            lit(year).alias("d_year"),
            (col("ws2_sales") / col("ws1_sales")).alias("web_q1_q2_increase"),
            (col("ss2_sales") / col("ss1_sales")).alias("store_q1_q2_increase"),
            (col("ws3_sales") / col("ws2_sales")).alias("web_q2_q3_increase"),
            (col("ss3_sales") / col("ss2_sales")).alias("store_q2_q3_increase"),
        ]
    )

    result = result.filter(
        (
            ctx.when(col("ws1_sales") > 0).then(col("ws2_sales") / col("ws1_sales")).otherwise(None)
            > ctx.when(col("ss1_sales") > 0).then(col("ss2_sales") / col("ss1_sales")).otherwise(None)
        )
        & (
            ctx.when(col("ws2_sales") > 0).then(col("ws3_sales") / col("ws2_sales")).otherwise(None)
            > ctx.when(col("ss2_sales") > 0).then(col("ss3_sales") / col("ss2_sales")).otherwise(None)
        )
    )

    return result.select(
        [
            col("ca_county"),
            col("d_year"),
            col("web_q1_q2_increase"),
            col("store_q1_q2_increase"),
            col("web_q2_q3_increase"),
            col("store_q2_q3_increase"),
        ]
    ).sort(order_by)


def q31_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(31)
    year = params.get("year", 2000)
    order_by = _q31_order_column(params.get("order_by", "ss1.ca_county"))

    store_sales, web_sales, date_dim, customer_address = _tables(
        ctx, "store_sales", "web_sales", "date_dim", "customer_address"
    )

    ss = (
        store_sales.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk", how="inner")
        .merge(customer_address, left_on="ss_addr_sk", right_on="ca_address_sk", how="inner")
        .groupby(["ca_county", "d_qoy", "d_year"], as_index=False)
        .agg(store_sales=("ss_ext_sales_price", "sum"))
    )

    ws = (
        web_sales.merge(date_dim, left_on="ws_sold_date_sk", right_on="d_date_sk", how="inner")
        .merge(customer_address, left_on="ws_bill_addr_sk", right_on="ca_address_sk", how="inner")
        .groupby(["ca_county", "d_qoy", "d_year"], as_index=False)
        .agg(web_sales=("ws_ext_sales_price", "sum"))
    )

    ss1 = ss[(ss["d_qoy"] == 1) & (ss["d_year"] == year)][["ca_county", "store_sales"]].rename(
        columns={"store_sales": "ss1_sales"}
    )
    ss2 = ss[(ss["d_qoy"] == 2) & (ss["d_year"] == year)][["ca_county", "store_sales"]].rename(
        columns={"store_sales": "ss2_sales"}
    )
    ss3 = ss[(ss["d_qoy"] == 3) & (ss["d_year"] == year)][["ca_county", "store_sales"]].rename(
        columns={"store_sales": "ss3_sales"}
    )

    ws1 = ws[(ws["d_qoy"] == 1) & (ws["d_year"] == year)][["ca_county", "web_sales"]].rename(
        columns={"web_sales": "ws1_sales"}
    )
    ws2 = ws[(ws["d_qoy"] == 2) & (ws["d_year"] == year)][["ca_county", "web_sales"]].rename(
        columns={"web_sales": "ws2_sales"}
    )
    ws3 = ws[(ws["d_qoy"] == 3) & (ws["d_year"] == year)][["ca_county", "web_sales"]].rename(
        columns={"web_sales": "ws3_sales"}
    )

    result = (
        ss1.merge(ss2, on="ca_county", how="inner")
        .merge(ss3, on="ca_county", how="inner")
        .merge(ws1, on="ca_county", how="inner")
        .merge(ws2, on="ca_county", how="inner")
        .merge(ws3, on="ca_county", how="inner")
    )

    result["d_year"] = year
    result["web_q1_q2_increase"] = result["ws2_sales"] / result["ws1_sales"]
    result["store_q1_q2_increase"] = result["ss2_sales"] / result["ss1_sales"]
    result["web_q2_q3_increase"] = result["ws3_sales"] / result["ws2_sales"]
    result["store_q2_q3_increase"] = result["ss3_sales"] / result["ss2_sales"]

    result["web_q1_q2_increase"] = result["ws2_sales"].div(result["ws1_sales"]).where(result["ws1_sales"] > 0)
    result["store_q1_q2_increase"] = result["ss2_sales"].div(result["ss1_sales"]).where(result["ss1_sales"] > 0)
    result["web_q2_q3_increase"] = result["ws3_sales"].div(result["ws2_sales"]).where(result["ws2_sales"] > 0)
    result["store_q2_q3_increase"] = result["ss3_sales"].div(result["ss2_sales"]).where(result["ss2_sales"] > 0)
    result = result.loc[
        (result["ws1_sales"] > 0)
        & (result["ss1_sales"] > 0)
        & (result["ws2_sales"] > 0)
        & (result["ss2_sales"] > 0)
        & (result["web_q1_q2_increase"] > result["store_q1_q2_increase"])
        & (result["web_q2_q3_increase"] > result["store_q2_q3_increase"])
    ]

    return result[
        [
            "ca_county",
            "d_year",
            "web_q1_q2_increase",
            "store_q1_q2_increase",
            "web_q2_q3_increase",
            "store_q2_q3_increase",
        ]
    ].sort_values(order_by)


def q33_expression_impl(ctx: DataFrameContext) -> Any:
    col = ctx.col

    params = get_parameters(33)
    year = params.get("year", 1999)
    month = params.get("month", 3)
    gmt_offset = params.get("gmt_offset", -5)
    category = params.get("category", "Books")

    date_dim, customer_address, item = _tables(ctx, "date_dim", "customer_address", "item")

    mfg_ids = item.filter(col("i_category") == category).select("i_manufact_id").unique()
    date_filter = date_dim.filter((col("d_year") == year) & (col("d_moy") == month))
    addr_filter = customer_address.filter(col("ca_gmt_offset") == gmt_offset)

    def channel(table: str, item_key: str, date_key: str, addr_key: str, value_col: str) -> Any:
        return (
            ctx.get_table(table)
            .join(item, left_on=item_key, right_on="i_item_sk", how="inner")
            .join(date_filter, left_on=date_key, right_on="d_date_sk", how="inner")
            .join(addr_filter, left_on=addr_key, right_on="ca_address_sk", how="inner")
            .join(mfg_ids, on="i_manufact_id", how="semi")
            .group_by("i_manufact_id")
            .agg(_sum_or_null_expression(ctx, col(value_col)).alias("total_sales"))
        )

    combined = ctx.concat(
        [
            channel("store_sales", "ss_item_sk", "ss_sold_date_sk", "ss_addr_sk", "ss_ext_sales_price"),
            channel("catalog_sales", "cs_item_sk", "cs_sold_date_sk", "cs_bill_addr_sk", "cs_ext_sales_price"),
            channel("web_sales", "ws_item_sk", "ws_sold_date_sk", "ws_bill_addr_sk", "ws_ext_sales_price"),
        ]
    )
    return _sort_null_largest_expression(
        ctx,
        combined.group_by("i_manufact_id").agg(_sum_or_null_expression(ctx, col("total_sales")).alias("total_sales")),
        ["total_sales"],
    ).head(100)


def q33_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(33)
    year = params.get("year", 1999)
    month = params.get("month", 3)
    gmt_offset = params.get("gmt_offset", -5)
    category = params.get("category", "Books")

    date_dim, customer_address, item = _tables(ctx, "date_dim", "customer_address", "item")

    mfg_ids = item[item["i_category"] == category]["i_manufact_id"].unique()
    date_filter = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] == month)]
    addr_filter = customer_address[customer_address["ca_gmt_offset"] == gmt_offset]

    def channel(table: str, item_key: str, date_key: str, addr_key: str, value_col: str) -> Any:
        return _grouped_pandas_aggregates(
            ctx.get_table(table)
            .merge(item[item["i_manufact_id"].isin(mfg_ids)], left_on=item_key, right_on="i_item_sk", how="inner")
            .merge(date_filter, left_on=date_key, right_on="d_date_sk", how="inner")
            .merge(addr_filter, left_on=addr_key, right_on="ca_address_sk", how="inner"),
            "i_manufact_id",
            {"total_sales": (value_col, "sum")},
        )

    combined = ctx.concat(
        [
            channel("store_sales", "ss_item_sk", "ss_sold_date_sk", "ss_addr_sk", "ss_ext_sales_price"),
            channel("catalog_sales", "cs_item_sk", "cs_sold_date_sk", "cs_bill_addr_sk", "cs_ext_sales_price"),
            channel("web_sales", "ws_item_sk", "ws_sold_date_sk", "ws_bill_addr_sk", "ws_ext_sales_price"),
        ]
    )
    result = (
        _grouped_pandas_aggregates(combined, "i_manufact_id", {"total_sales": ("total_sales", "sum")})
        .sort_values("total_sales")
        .head(100)
    )
    return _none_for_null(result, list(result.columns))


def q71_expression_impl(ctx: DataFrameContext) -> Any:
    col = ctx.col

    params = get_parameters(71)
    year = params.get("year", 2000)
    month = params.get("month", 12)

    date_dim, item, time_dim = _tables(ctx, "date_dim", "item", "time_dim")

    date_filter = date_dim.filter((col("d_year") == year) & (col("d_moy") == month))

    def channel(table: str, date_key: str, price_col: str, item_key: str, time_key: str) -> Any:
        return (
            ctx.get_table(table)
            .join(date_filter, left_on=date_key, right_on="d_date_sk", how="inner")
            .select(
                [
                    col(price_col).alias("ext_price"),
                    col(item_key).alias("sold_item_sk"),
                    col(time_key).alias("time_sk"),
                ]
            )
        )

    combined = ctx.concat(
        [
            channel("web_sales", "ws_sold_date_sk", "ws_ext_sales_price", "ws_item_sk", "ws_sold_time_sk"),
            channel("catalog_sales", "cs_sold_date_sk", "cs_ext_sales_price", "cs_item_sk", "cs_sold_time_sk"),
            channel("store_sales", "ss_sold_date_sk", "ss_ext_sales_price", "ss_item_sk", "ss_sold_time_sk"),
        ]
    )
    grouped = (
        combined.join(item.filter(col("i_manager_id") == 1), left_on="sold_item_sk", right_on="i_item_sk", how="inner")
        .join(
            time_dim.filter(col("t_meal_time").is_in(["breakfast", "dinner"])),
            left_on="time_sk",
            right_on="t_time_sk",
            how="inner",
        )
        .group_by(["i_brand_id", "i_brand", "t_hour", "t_minute"])
        .agg(
            ctx.when(col("ext_price").count() > ctx.lit(0))
            .then(col("ext_price").sum())
            .otherwise(ctx.lit(None))
            .alias("ext_price")
        )
    )
    return _sort_null_largest_expression(ctx, grouped, ["ext_price", "i_brand_id"], [True, False])


def q71_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(71)
    year = params.get("year", 2000)
    month = params.get("month", 12)

    date_dim, item, time_dim = _tables(ctx, "date_dim", "item", "time_dim")

    date_filter = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] == month)]

    def channel(table: str, date_key: str, price_col: str, item_key: str, time_key: str) -> Any:
        return (
            ctx.get_table(table)
            .merge(date_filter, left_on=date_key, right_on="d_date_sk", how="inner")[[price_col, item_key, time_key]]
            .rename(columns={price_col: "ext_price", item_key: "sold_item_sk", time_key: "time_sk"})
        )

    combined = ctx.concat(
        [
            channel("web_sales", "ws_sold_date_sk", "ws_ext_sales_price", "ws_item_sk", "ws_sold_time_sk"),
            channel("catalog_sales", "cs_sold_date_sk", "cs_ext_sales_price", "cs_item_sk", "cs_sold_time_sk"),
            channel("store_sales", "ss_sold_date_sk", "ss_ext_sales_price", "ss_item_sk", "ss_sold_time_sk"),
        ]
    )
    item_filter = item[item["i_manager_id"] == 1]
    time_filter = time_dim[time_dim["t_meal_time"].isin(["breakfast", "dinner"])]

    grouped = (
        combined.merge(item_filter, left_on="sold_item_sk", right_on="i_item_sk", how="inner")
        .merge(time_filter, left_on="time_sk", right_on="t_time_sk", how="inner")
        .groupby(["i_brand_id", "i_brand", "t_hour", "t_minute"], as_index=False)
        .agg(ext_price=("ext_price", "sum"), priced=("ext_price", "count"))
    )
    grouped["ext_price"] = grouped["ext_price"].where(grouped["priced"] > 0)
    grouped = grouped.drop(columns=["priced"])
    result = _sort_null_largest_pandas(grouped, ["ext_price", "i_brand_id"], [True, False])
    return _none_for_null(result, ["ext_price"])


_Q74_AGGREGATES = {"sum": "sum", "min": "min", "max": "max", "avg": "mean", "stddev_samp": "std"}
_Q74_OUTPUT = ("c_customer_id", "c_first_name", "c_last_name")


def _q74_parameters(params: Any) -> tuple[int, str, list[str]]:
    year = params.get("year", 2001)
    aggregate = params.get("aggone", "max")
    if aggregate not in _Q74_AGGREGATES:
        raise ValueError(f"Q74 aggone must be one of {sorted(_Q74_AGGREGATES)}, got {aggregate!r}")
    positions = params.get("order_by", [2, 1, 3])
    if (
        not isinstance(positions, (list, tuple))
        or len(positions) != 3
        or any(str(position) not in {"1", "2", "3"} for position in positions)
    ):
        raise ValueError(f"Q74 order_by must contain three positions from 1, 2 and 3, got {positions!r}")
    order_by = [int(position) for position in positions]
    return year, _Q74_AGGREGATES[aggregate], [_Q74_OUTPUT[position - 1] for position in order_by]


def q74_expression_impl(ctx: DataFrameContext) -> Any:
    col = ctx.col
    lit = ctx.lit

    params = get_parameters(74)
    year, aggregate, order_by = _q74_parameters(params)

    customer, store_sales, web_sales, date_dim = _tables(ctx, "customer", "store_sales", "web_sales", "date_dim")

    def year_total(sales: Any, customer_key: str, date_key: str, amount: str, the_year: int, alias: str) -> Any:
        totals = (
            sales.join(customer, left_on=customer_key, right_on="c_customer_sk", how="inner")
            .join(date_dim.filter(col("d_year") == lit(the_year)), left_on=date_key, right_on="d_date_sk", how="inner")
            .group_by(list(_Q74_OUTPUT))
            .agg(
                getattr(col(amount), aggregate)().alias(alias),
                col(amount).count().alias("_values"),
            )
        )
        total = ctx.when(col("_values") > lit(0)).then(col(alias)).otherwise(lit(None))
        return totals.with_columns(total.alias(alias)).drop("_values")

    ss_y1 = year_total(store_sales, "ss_customer_sk", "ss_sold_date_sk", "ss_net_paid", year, "ss_y1_total")
    ss_y2 = year_total(store_sales, "ss_customer_sk", "ss_sold_date_sk", "ss_net_paid", year + 1, "ss_y2_total")
    ws_y1 = year_total(web_sales, "ws_bill_customer_sk", "ws_sold_date_sk", "ws_net_paid", year, "ws_y1_total")
    ws_y2 = year_total(web_sales, "ws_bill_customer_sk", "ws_sold_date_sk", "ws_net_paid", year + 1, "ws_y2_total")

    result = (
        ss_y2.join(ss_y1.select("c_customer_id", "ss_y1_total"), on="c_customer_id", how="inner")
        .join(ws_y1.select("c_customer_id", "ws_y1_total"), on="c_customer_id", how="inner")
        .join(ws_y2.select("c_customer_id", "ws_y2_total"), on="c_customer_id", how="inner")
    )

    result = result.filter(
        (col("ss_y1_total") > 0)
        & (col("ws_y1_total") > 0)
        & (
            ctx.when(col("ws_y1_total") > 0).then(col("ws_y2_total") / col("ws_y1_total")).otherwise(None)
            > ctx.when(col("ss_y1_total") > 0).then(col("ss_y2_total") / col("ss_y1_total")).otherwise(None)
        )
    )

    return result.select(list(_Q74_OUTPUT)).sort(order_by, nulls_last=True).head(100)


def q74_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(74)
    year, aggregate, order_by = _q74_parameters(params)

    customer, store_sales, web_sales, date_dim = _tables(ctx, "customer", "store_sales", "web_sales", "date_dim")

    def year_total(sales: Any, customer_key: str, date_key: str, amount: str, the_year: int, alias: str) -> Any:
        dates = date_dim[date_dim["d_year"] == the_year][["d_date_sk"]]
        joined = sales.merge(customer, left_on=customer_key, right_on="c_customer_sk", how="inner").merge(
            dates, left_on=date_key, right_on="d_date_sk", how="inner"
        )
        method = (lambda values: values.sum(min_count=1)) if aggregate == "sum" else aggregate
        totals = joined.groupby(list(_Q74_OUTPUT), as_index=False, dropna=False).agg(**{alias: (amount, method)})
        totals[alias] = totals[alias].astype("float64")
        return totals

    ss_y1 = year_total(store_sales, "ss_customer_sk", "ss_sold_date_sk", "ss_net_paid", year, "ss_y1_total")
    ss_y2 = year_total(store_sales, "ss_customer_sk", "ss_sold_date_sk", "ss_net_paid", year + 1, "ss_y2_total")
    ws_y1 = year_total(web_sales, "ws_bill_customer_sk", "ws_sold_date_sk", "ws_net_paid", year, "ws_y1_total")
    ws_y2 = year_total(web_sales, "ws_bill_customer_sk", "ws_sold_date_sk", "ws_net_paid", year + 1, "ws_y2_total")

    result = (
        ss_y2.merge(ss_y1[["c_customer_id", "ss_y1_total"]], on="c_customer_id", how="inner")
        .merge(ws_y1[["c_customer_id", "ws_y1_total"]], on="c_customer_id", how="inner")
        .merge(ws_y2[["c_customer_id", "ws_y2_total"]], on="c_customer_id", how="inner")
    )

    web_growth = (result["ws_y2_total"] / result["ws_y1_total"]).where(result["ws_y1_total"] > 0)
    store_growth = (result["ss_y2_total"] / result["ss_y1_total"]).where(result["ss_y1_total"] > 0)
    result = result[(result["ss_y1_total"] > 0) & (result["ws_y1_total"] > 0) & (web_growth > store_growth)]

    result = result[list(_Q74_OUTPUT)].sort_values(order_by, na_position="last").head(100)
    return _none_for_null(result, list(result.columns))


def q76_expression_impl(ctx: DataFrameContext) -> Any:
    col = ctx.col
    lit = ctx.lit

    params = get_parameters(76)
    null_col_ss = params.get("null_col_ss", "ss_customer_sk")
    null_col_ws = params.get("null_col_ws", "ws_bill_customer_sk")
    null_col_cs = params.get("null_col_cs", "cs_bill_customer_sk")

    date_dim, item = _tables(ctx, "date_dim", "item")

    def channel(name: str, table: str, null_col: str, date_key: str, item_key: str, value_col: str) -> Any:
        return (
            ctx.get_table(table)
            .filter(col(null_col).is_null())
            .join(date_dim, left_on=date_key, right_on="d_date_sk", how="inner")
            .join(item, left_on=item_key, right_on="i_item_sk", how="inner")
            .select(
                [
                    lit(name).alias("channel"),
                    lit(null_col).alias("col_name"),
                    col("d_year"),
                    col("d_qoy"),
                    col("i_category"),
                    col(value_col).alias("ext_sales_price"),
                ]
            )
        )

    combined = ctx.concat(
        [
            channel("store", "store_sales", null_col_ss, "ss_sold_date_sk", "ss_item_sk", "ss_ext_sales_price"),
            channel("web", "web_sales", null_col_ws, "ws_sold_date_sk", "ws_item_sk", "ws_ext_sales_price"),
            channel("catalog", "catalog_sales", null_col_cs, "cs_sold_date_sk", "cs_item_sk", "cs_ext_sales_price"),
        ]
    )

    grouped = combined.group_by(["channel", "col_name", "d_year", "d_qoy", "i_category"]).agg(
        [
            ctx.count().alias("sales_cnt"),
            ctx.when(col("ext_sales_price").count() > lit(0))
            .then(col("ext_sales_price").sum())
            .otherwise(lit(None))
            .alias("sales_amt"),
        ]
    )
    return _sort_null_largest_expression(
        ctx, grouped, ["channel", "col_name", "d_year", "d_qoy", "i_category"], [False] * 5
    ).head(100)


def q76_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(76)
    null_col_ss = params.get("null_col_ss", "ss_customer_sk")
    null_col_ws = params.get("null_col_ws", "ws_bill_customer_sk")
    null_col_cs = params.get("null_col_cs", "cs_bill_customer_sk")

    date_dim, item = _tables(ctx, "date_dim", "item")

    def channel(name: str, table: str, null_col: str, date_key: str, item_key: str, value_col: str) -> Any:
        sales = ctx.get_table(table)
        result = (
            sales[sales[null_col].isna()]
            .merge(date_dim, left_on=date_key, right_on="d_date_sk", how="inner")
            .merge(item, left_on=item_key, right_on="i_item_sk", how="inner")
        )
        result["channel"] = name
        result["col_name"] = null_col
        return result[["channel", "col_name", "d_year", "d_qoy", "i_category", value_col]].rename(
            columns={value_col: "ext_sales_price"}
        )

    combined = ctx.concat(
        [
            channel("store", "store_sales", null_col_ss, "ss_sold_date_sk", "ss_item_sk", "ss_ext_sales_price"),
            channel("web", "web_sales", null_col_ws, "ws_sold_date_sk", "ws_item_sk", "ws_ext_sales_price"),
            channel("catalog", "catalog_sales", null_col_cs, "cs_sold_date_sk", "cs_item_sk", "cs_ext_sales_price"),
        ]
    )

    result = (
        combined.groupby(["channel", "col_name", "d_year", "d_qoy", "i_category"], as_index=False, dropna=False)
        .agg(
            sales_cnt=("ext_sales_price", "size"),
            sales_amt=("ext_sales_price", "sum"),
            priced=("ext_sales_price", "count"),
        )
        .sort_values(["channel", "col_name", "d_year", "d_qoy", "i_category"], na_position="last")
        .head(100)
    )
    result["sales_amt"] = result["sales_amt"].where(result["priced"] > 0)
    return _none_for_null(result.drop(columns=["priced"]), ["i_category", "sales_amt"])


def q97_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(97)
    dms = params.get("dms", 1212)

    store_sales, catalog_sales, date_dim = _tables(ctx, "store_sales", "catalog_sales", "date_dim")
    col = ctx.col
    lit = ctx.lit

    date_filtered = date_dim.filter((col("d_month_seq") >= lit(dms)) & (col("d_month_seq") <= lit(dms + 11)))

    ssci = (
        store_sales.join(date_filtered, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .group_by(["ss_customer_sk", "ss_item_sk"])
        .agg(lit(1).alias("_count"))
        .select(
            [
                col("ss_customer_sk").alias("ss_customer_sk"),
                col("ss_item_sk").alias("ss_item_sk"),
            ]
        )
    )

    csci = (
        catalog_sales.join(date_filtered, left_on="cs_sold_date_sk", right_on="d_date_sk")
        .group_by(["cs_bill_customer_sk", "cs_item_sk"])
        .agg(lit(1).alias("_count"))
        .select(
            [
                col("cs_bill_customer_sk").alias("cs_customer_sk"),
                col("cs_item_sk").alias("cs_item_sk"),
            ]
        )
    )

    joined = ssci.join(
        csci,
        left_on=["ss_customer_sk", "ss_item_sk"],
        right_on=["cs_customer_sk", "cs_item_sk"],
        how="full",
    )

    return joined.select(
        [
            ctx.when(col("ss_customer_sk").is_not_null() & col("cs_customer_sk").is_null())
            .then(lit(1))
            .otherwise(lit(0))
            .sum()
            .alias("store_only"),
            ctx.when(col("ss_customer_sk").is_null() & col("cs_customer_sk").is_not_null())
            .then(lit(1))
            .otherwise(lit(0))
            .sum()
            .alias("catalog_only"),
            ctx.when(col("ss_customer_sk").is_not_null() & col("cs_customer_sk").is_not_null())
            .then(lit(1))
            .otherwise(lit(0))
            .sum()
            .alias("store_and_catalog"),
        ]
    )


def q97_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    params = get_parameters(97)
    dms = params.get("dms", 1212)

    store_sales, catalog_sales, date_dim = _tables(ctx, "store_sales", "catalog_sales", "date_dim")

    date_filtered = date_dim[(date_dim["d_month_seq"] >= dms) & (date_dim["d_month_seq"] <= dms + 11)]

    ss_joined = store_sales.merge(date_filtered[["d_date_sk"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    ssci = ss_joined.groupby(["ss_customer_sk", "ss_item_sk"], as_index=False).size()
    ssci = ssci[["ss_customer_sk", "ss_item_sk"]].drop_duplicates()

    cs_joined = catalog_sales.merge(date_filtered[["d_date_sk"]], left_on="cs_sold_date_sk", right_on="d_date_sk")
    csci = cs_joined.groupby(["cs_bill_customer_sk", "cs_item_sk"], as_index=False).size()
    csci = csci[["cs_bill_customer_sk", "cs_item_sk"]].drop_duplicates()
    csci = csci.rename(columns={"cs_bill_customer_sk": "cs_customer_sk", "cs_item_sk": "cs_item_sk_r"})

    joined = ssci.merge(
        csci,
        left_on=["ss_customer_sk", "ss_item_sk"],
        right_on=["cs_customer_sk", "cs_item_sk_r"],
        how="outer",
    )

    store_only = ((joined["ss_customer_sk"].notnull()) & (joined["cs_customer_sk"].isna())).sum()
    catalog_only = ((joined["ss_customer_sk"].isna()) & (joined["cs_customer_sk"].notnull())).sum()
    store_and_catalog = ((joined["ss_customer_sk"].notnull()) & (joined["cs_customer_sk"].notnull())).sum()

    return pd.DataFrame(
        {
            "store_only": [store_only],
            "catalog_only": [catalog_only],
            "store_and_catalog": [store_and_catalog],
        }
    )


def q49_expression_impl(ctx: DataFrameContext) -> Any:
    return _three_channel_return_ratio_expression(ctx, 49)


def q49_pandas_impl(ctx: DataFrameContext) -> Any:
    return _three_channel_return_ratio_pandas(ctx, 49)


def q75_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(75)
    year = params.get("year", 2001)
    category = params.get("category", "Books")

    item, date_dim = _tables(ctx, "item", "date_dim")
    col = ctx.col
    lit = ctx.lit

    item_filtered = item.filter(col("i_category") == lit(category))

    def channel(
        sales_table: str,
        returns_table: str,
        item_key: str,
        date_key: str,
        order_key: str,
        returns_order_key: str,
        returns_item_key: str,
        quantity_col: str,
        return_qty_col: str,
        amount_col: str,
        return_amount_col: str,
    ) -> Any:
        return (
            ctx.get_table(sales_table)
            .join(item_filtered, left_on=item_key, right_on="i_item_sk")
            .join(date_dim, left_on=date_key, right_on="d_date_sk")
            .join(
                ctx.get_table(returns_table),
                left_on=[order_key, item_key],
                right_on=[returns_order_key, returns_item_key],
                how="left",
            )
            .select(
                [
                    col("d_year"),
                    col("i_brand_id"),
                    col("i_class_id"),
                    col("i_category_id"),
                    col("i_manufact_id"),
                    (col(quantity_col) - col(return_qty_col).fill_null(0)).alias("sales_cnt"),
                    (
                        (col(amount_col) * lit(100)).round(0).cast_int64()
                        - (col(return_amount_col).fill_null(0.0) * lit(100)).round(0).cast_int64()
                    ).alias("sales_amt_cents"),
                ]
            )
        )

    channel_specs = (
        (
            "catalog_sales",
            "catalog_returns",
            "cs_item_sk",
            "cs_sold_date_sk",
            "cs_order_number",
            "cr_order_number",
            "cr_item_sk",
            "cs_quantity",
            "cr_return_quantity",
            "cs_ext_sales_price",
            "cr_return_amount",
        ),
        (
            "store_sales",
            "store_returns",
            "ss_item_sk",
            "ss_sold_date_sk",
            "ss_ticket_number",
            "sr_ticket_number",
            "sr_item_sk",
            "ss_quantity",
            "sr_return_quantity",
            "ss_ext_sales_price",
            "sr_return_amt",
        ),
        (
            "web_sales",
            "web_returns",
            "ws_item_sk",
            "ws_sold_date_sk",
            "ws_order_number",
            "wr_order_number",
            "wr_item_sk",
            "ws_quantity",
            "wr_return_quantity",
            "ws_ext_sales_price",
            "wr_return_amt",
        ),
    )
    combined = ctx.concat([channel(*spec) for spec in channel_specs]).unique()

    all_sales = combined.group_by(["d_year", "i_brand_id", "i_class_id", "i_category_id", "i_manufact_id"]).agg(
        [
            _sum_or_null_expression(ctx, col("sales_cnt")).alias("sales_cnt"),
            _sum_or_null_expression(ctx, col("sales_amt_cents")).alias("sales_amt_cents"),
        ]
    )

    curr_yr = all_sales.filter(col("d_year") == lit(year))
    prev_yr = all_sales.filter(col("d_year") == lit(year - 1))

    return _sort_null_largest_expression(
        ctx,
        curr_yr.join(
            prev_yr,
            on=["i_brand_id", "i_class_id", "i_category_id", "i_manufact_id"],
            suffix="_prev",
        )
        .filter((col("sales_cnt").cast_float64() / col("sales_cnt_prev").cast_float64()) < lit(0.9))
        .select(
            [
                col("d_year_prev").alias("prev_year"),
                col("d_year").alias("year"),
                col("i_brand_id"),
                col("i_class_id"),
                col("i_category_id"),
                col("i_manufact_id"),
                col("sales_cnt_prev").alias("prev_yr_cnt"),
                col("sales_cnt").alias("curr_yr_cnt"),
                (col("sales_cnt") - col("sales_cnt_prev")).alias("sales_cnt_diff"),
                ((col("sales_amt_cents") - col("sales_amt_cents_prev")).cast_float64() / lit(100)).alias(
                    "sales_amt_diff"
                ),
            ]
        ),
        ["sales_cnt_diff", "sales_amt_diff"],
    ).limit(100)


def q75_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(75)
    year = params.get("year", 2001)
    category = params.get("category", "Books")

    item, date_dim = _tables(ctx, "item", "date_dim")

    item_filtered = item[item["i_category"] == category]

    def channel(
        sales_table: str,
        returns_table: str,
        item_key: str,
        date_key: str,
        order_key: str,
        returns_order_key: str,
        returns_item_key: str,
        quantity_col: str,
        return_qty_col: str,
        amount_col: str,
        return_amount_col: str,
    ) -> Any:
        joined = ctx.get_table(sales_table).merge(item_filtered, left_on=item_key, right_on="i_item_sk")
        joined = joined.merge(date_dim, left_on=date_key, right_on="d_date_sk")
        joined = joined.merge(
            ctx.get_table(returns_table),
            left_on=[order_key, item_key],
            right_on=[returns_order_key, returns_item_key],
            how="left",
        )
        joined["sales_cnt"] = joined[quantity_col] - joined[return_qty_col].fillna(0)
        joined["sales_amt_cents"] = (joined[amount_col] * 100).round().astype("Int64") - (
            joined[return_amount_col].fillna(0.0) * 100
        ).round().astype("Int64")
        return joined[
            ["d_year", "i_brand_id", "i_class_id", "i_category_id", "i_manufact_id", "sales_cnt", "sales_amt_cents"]
        ]

    channel_specs = (
        (
            "catalog_sales",
            "catalog_returns",
            "cs_item_sk",
            "cs_sold_date_sk",
            "cs_order_number",
            "cr_order_number",
            "cr_item_sk",
            "cs_quantity",
            "cr_return_quantity",
            "cs_ext_sales_price",
            "cr_return_amount",
        ),
        (
            "store_sales",
            "store_returns",
            "ss_item_sk",
            "ss_sold_date_sk",
            "ss_ticket_number",
            "sr_ticket_number",
            "sr_item_sk",
            "ss_quantity",
            "sr_return_quantity",
            "ss_ext_sales_price",
            "sr_return_amt",
        ),
        (
            "web_sales",
            "web_returns",
            "ws_item_sk",
            "ws_sold_date_sk",
            "ws_order_number",
            "wr_order_number",
            "wr_item_sk",
            "ws_quantity",
            "wr_return_quantity",
            "ws_ext_sales_price",
            "wr_return_amt",
        ),
    )
    combined = ctx.concat([channel(*spec) for spec in channel_specs]).drop_duplicates()

    all_sales = _grouped_pandas_aggregates(
        combined,
        ["d_year", "i_brand_id", "i_class_id", "i_category_id", "i_manufact_id"],
        {"sales_cnt": ("sales_cnt", "sum"), "sales_amt_cents": ("sales_amt_cents", "sum")},
    )

    curr_yr = all_sales[all_sales["d_year"] == year]
    prev_yr = all_sales[all_sales["d_year"] == year - 1]

    joined = curr_yr.merge(
        prev_yr,
        on=["i_brand_id", "i_class_id", "i_category_id", "i_manufact_id"],
        suffixes=("", "_prev"),
    )

    joined = joined[joined["sales_cnt"] / joined["sales_cnt_prev"] < 0.9]

    result = joined.assign(
        prev_year=joined["d_year_prev"],
        year=joined["d_year"],
        prev_yr_cnt=joined["sales_cnt_prev"],
        curr_yr_cnt=joined["sales_cnt"],
        sales_cnt_diff=joined["sales_cnt"] - joined["sales_cnt_prev"],
        sales_amt_diff=(joined["sales_amt_cents"] - joined["sales_amt_cents_prev"]) / 100,
    )[
        [
            "prev_year",
            "year",
            "i_brand_id",
            "i_class_id",
            "i_category_id",
            "i_manufact_id",
            "prev_yr_cnt",
            "curr_yr_cnt",
            "sales_cnt_diff",
            "sales_amt_diff",
        ]
    ]

    result = result.sort_values(["sales_cnt_diff", "sales_amt_diff"]).head(100)
    return _none_for_null(result, list(result.columns))


_Q78_KEY_COLUMNS = ("ss_sold_year", "ss_item_sk", "ss_customer_sk")


def _q78_select_columns(params: Any) -> list[str]:
    columns = [name.strip() for name in str(params.get("select_columns", ", ".join(_Q78_KEY_COLUMNS))).split(",")]
    unknown = [name for name in columns if name not in _Q78_KEY_COLUMNS]
    if not columns or unknown:
        raise ValueError(f"Q78 select_columns must be drawn from {list(_Q78_KEY_COLUMNS)}, got {columns!r}")
    return columns


_Q78_ORDER_TAIL = (
    ("store_qty", True),
    ("store_wholesale_cost", True),
    ("store_sales_price", True),
    ("other_chan_qty", False),
    ("other_chan_wholesale_cost", False),
    ("other_chan_sales_price", False),
    ("ratio", False),
)


def _q78_order(select_columns: list[str]) -> tuple[list[str], list[bool]]:
    order_by = [*select_columns, *(name for name, _ in _Q78_ORDER_TAIL)]
    descending = [False] * len(select_columns) + [flag for _, flag in _Q78_ORDER_TAIL]
    return order_by, descending


def q78_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(78)
    year = params.get("year", 2000)
    select_columns = _q78_select_columns(params)

    date_dim = ctx.get_table("date_dim")
    col = ctx.col
    lit = ctx.lit

    def channel(
        sales_table: str,
        returns_table: str,
        left_on: list[str],
        right_on: list[str],
        date_key: str,
        return_null_col: str,
        group_cols: list[str],
        quantity_col: str,
        wholesale_col: str,
        sales_price_col: str,
        aliases: dict[str, str],
    ) -> Any:
        return (
            ctx.get_table(sales_table)
            .join(
                ctx.get_table(returns_table).with_columns(col(return_null_col).alias("_returned")),
                left_on=left_on,
                right_on=right_on,
                how="left",
            )
            .join(date_dim, left_on=date_key, right_on="d_date_sk")
            .filter(col("_returned").is_null())
            .group_by(group_cols)
            .agg(
                [
                    ctx.when(col(quantity_col).count() > lit(0))
                    .then(col(quantity_col).sum())
                    .otherwise(lit(None))
                    .alias(aliases["qty"]),
                    ctx.when(col(wholesale_col).count() > lit(0))
                    .then(col(wholesale_col).sum())
                    .otherwise(lit(None))
                    .alias(aliases["wc"]),
                    ctx.when(col(sales_price_col).count() > lit(0))
                    .then(col(sales_price_col).sum())
                    .otherwise(lit(None))
                    .alias(aliases["sp"]),
                ]
            )
            .rename({key: value for key, value in aliases.items() if key not in {"qty", "wc", "sp"}})
        )

    ss = channel(
        "store_sales",
        "store_returns",
        ["ss_ticket_number", "ss_item_sk"],
        ["sr_ticket_number", "sr_item_sk"],
        "ss_sold_date_sk",
        "sr_ticket_number",
        ["d_year", "ss_item_sk", "ss_customer_sk"],
        "ss_quantity",
        "ss_wholesale_cost",
        "ss_sales_price",
        {"d_year": "ss_sold_year", "qty": "ss_qty", "wc": "ss_wc", "sp": "ss_sp"},
    )
    cs = channel(
        "catalog_sales",
        "catalog_returns",
        ["cs_order_number", "cs_item_sk"],
        ["cr_order_number", "cr_item_sk"],
        "cs_sold_date_sk",
        "cr_order_number",
        ["d_year", "cs_item_sk", "cs_bill_customer_sk"],
        "cs_quantity",
        "cs_wholesale_cost",
        "cs_sales_price",
        {
            "d_year": "cs_sold_year",
            "cs_bill_customer_sk": "cs_customer_sk",
            "qty": "cs_qty",
            "wc": "cs_wc",
            "sp": "cs_sp",
        },
    )
    ws = channel(
        "web_sales",
        "web_returns",
        ["ws_order_number", "ws_item_sk"],
        ["wr_order_number", "wr_item_sk"],
        "ws_sold_date_sk",
        "wr_order_number",
        ["d_year", "ws_item_sk", "ws_bill_customer_sk"],
        "ws_quantity",
        "ws_wholesale_cost",
        "ws_sales_price",
        {
            "d_year": "ws_sold_year",
            "ws_bill_customer_sk": "ws_customer_sk",
            "qty": "ws_qty",
            "wc": "ws_wc",
            "sp": "ws_sp",
        },
    )

    selected = (
        ss.join(
            ws,
            left_on=["ss_sold_year", "ss_item_sk", "ss_customer_sk"],
            right_on=["ws_sold_year", "ws_item_sk", "ws_customer_sk"],
            how="left",
        )
        .join(
            cs,
            left_on=["ss_sold_year", "ss_item_sk", "ss_customer_sk"],
            right_on=["cs_sold_year", "cs_item_sk", "cs_customer_sk"],
            how="left",
        )
        .filter(
            (col("ss_sold_year") == lit(year))
            & ((col("ws_qty").fill_null(0) > lit(0)) | (col("cs_qty").fill_null(0) > lit(0)))
        )
        .with_columns(
            [
                _round_half_up_expression(
                    col("ss_qty").cast_float64()
                    / (col("ws_qty").fill_null(0) + col("cs_qty").fill_null(0)).cast_float64(),
                    2,
                ).alias("ratio"),
                (col("ws_qty").fill_null(0) + col("cs_qty").fill_null(0)).alias("other_chan_qty"),
                (col("ws_wc").fill_null(0) + col("cs_wc").fill_null(0)).alias("other_chan_wholesale_cost"),
                (col("ws_sp").fill_null(0) + col("cs_sp").fill_null(0)).alias("other_chan_sales_price"),
            ]
        )
        .select(
            [
                *select_columns,
                "ratio",
                col("ss_qty").alias("store_qty"),
                col("ss_wc").alias("store_wholesale_cost"),
                col("ss_sp").alias("store_sales_price"),
                "other_chan_qty",
                "other_chan_wholesale_cost",
                "other_chan_sales_price",
            ]
        )
    )
    order_by, descending = _q78_order(select_columns)
    return _sort_null_largest_expression(ctx, selected, order_by, descending).limit(100)


def q78_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(78)
    year = params.get("year", 2000)
    select_columns = _q78_select_columns(params)

    date_dim = ctx.get_table("date_dim")

    def channel(
        sales_table: str,
        returns_table: str,
        left_on: list[str],
        right_on: list[str],
        date_key: str,
        return_null_col: str,
        group_cols: list[str],
        value_cols: dict[str, str],
        aliases: dict[str, str],
    ) -> Any:
        joined = ctx.get_table(sales_table).merge(
            ctx.get_table(returns_table), left_on=left_on, right_on=right_on, how="left"
        )
        joined = joined.merge(date_dim, left_on=date_key, right_on="d_date_sk")
        agg_spec = {}
        for alias, source in value_cols.items():
            agg_spec[alias] = (source, "sum")
            agg_spec[f"{alias}_n"] = (source, "count")
        grouped = (
            joined[joined[return_null_col].isna()].groupby(group_cols, as_index=False, dropna=False).agg(**agg_spec)
        )
        for alias in value_cols:
            grouped[alias] = grouped[alias].where(grouped[f"{alias}_n"] > 0)
        return grouped.drop(columns=[f"{alias}_n" for alias in value_cols]).rename(columns=aliases)

    ss_agg = channel(
        "store_sales",
        "store_returns",
        ["ss_ticket_number", "ss_item_sk"],
        ["sr_ticket_number", "sr_item_sk"],
        "ss_sold_date_sk",
        "sr_ticket_number",
        ["d_year", "ss_item_sk", "ss_customer_sk"],
        {
            "ss_qty": "ss_quantity",
            "ss_wc": "ss_wholesale_cost",
            "ss_sp": "ss_sales_price",
        },
        {"d_year": "ss_sold_year"},
    )
    cs_agg = channel(
        "catalog_sales",
        "catalog_returns",
        ["cs_order_number", "cs_item_sk"],
        ["cr_order_number", "cr_item_sk"],
        "cs_sold_date_sk",
        "cr_order_number",
        ["d_year", "cs_item_sk", "cs_bill_customer_sk"],
        {
            "cs_qty": "cs_quantity",
            "cs_wc": "cs_wholesale_cost",
            "cs_sp": "cs_sales_price",
        },
        {"d_year": "cs_sold_year", "cs_bill_customer_sk": "cs_customer_sk"},
    )
    ws_agg = channel(
        "web_sales",
        "web_returns",
        ["ws_order_number", "ws_item_sk"],
        ["wr_order_number", "wr_item_sk"],
        "ws_sold_date_sk",
        "wr_order_number",
        ["d_year", "ws_item_sk", "ws_bill_customer_sk"],
        {
            "ws_qty": "ws_quantity",
            "ws_wc": "ws_wholesale_cost",
            "ws_sp": "ws_sales_price",
        },
        {"d_year": "ws_sold_year", "ws_bill_customer_sk": "ws_customer_sk"},
    )

    ws_join = ws_agg.dropna(subset=["ws_sold_year", "ws_item_sk", "ws_customer_sk"])
    cs_join = cs_agg.dropna(subset=["cs_sold_year", "cs_item_sk", "cs_customer_sk"])
    result = ss_agg.merge(
        ws_join,
        left_on=["ss_sold_year", "ss_item_sk", "ss_customer_sk"],
        right_on=["ws_sold_year", "ws_item_sk", "ws_customer_sk"],
        how="left",
    )
    result = result.merge(
        cs_join,
        left_on=["ss_sold_year", "ss_item_sk", "ss_customer_sk"],
        right_on=["cs_sold_year", "cs_item_sk", "cs_customer_sk"],
        how="left",
    )

    result = result[
        (result["ss_sold_year"] == year) & ((result["ws_qty"].fillna(0) > 0) | (result["cs_qty"].fillna(0) > 0))
    ]

    result["other_chan_qty"] = result["ws_qty"].fillna(0) + result["cs_qty"].fillna(0)
    result["other_chan_wholesale_cost"] = result["ws_wc"].fillna(0) + result["cs_wc"].fillna(0)
    result["other_chan_sales_price"] = result["ws_sp"].fillna(0) + result["cs_sp"].fillna(0)
    result["ratio"] = _round_half_up_pandas(result["ss_qty"] / result["other_chan_qty"], 2)

    result = result.rename(
        columns={
            "ss_qty": "store_qty",
            "ss_wc": "store_wholesale_cost",
            "ss_sp": "store_sales_price",
        }
    )

    result = result[
        [
            *select_columns,
            "ratio",
            "store_qty",
            "store_wholesale_cost",
            "store_sales_price",
            "other_chan_qty",
            "other_chan_wholesale_cost",
            "other_chan_sales_price",
        ]
    ]

    order_by, descending = _q78_order(select_columns)
    return (
        _sort_null_largest_pandas(result, order_by, descending)
        .head(100)
        .astype(object)
        .where(lambda frame: frame.notnull(), None)
    )


_CUSTOMER_YEAR_COLS = [
    "c_customer_id",
    "c_first_name",
    "c_last_name",
    "c_preferred_cust_flag",
    "c_birth_country",
    "c_login",
    "c_email_address",
]
_SELECT_ONE_COLUMNS = {
    "t_s_secyear.customer_preferred_cust_flag": "c_preferred_cust_flag",
    "t_s_secyear.customer_birth_country": "c_birth_country",
    "t_s_secyear.customer_login": "c_login",
    "t_s_secyear.customer_email_address": "c_email_address",
}
_SELECT_ONE_DEFAULT = "t_s_secyear.customer_preferred_cust_flag"
_CHANNEL_YEAR_SPECS = {
    "s": ("store_sales", "ss_customer_sk", "ss_sold_date_sk", "ss"),
    "c": ("catalog_sales", "cs_bill_customer_sk", "cs_sold_date_sk", "cs"),
    "w": ("web_sales", "ws_bill_customer_sk", "ws_sold_date_sk", "ws"),
}


def _select_one_column(select_one: str) -> str:
    try:
        return _SELECT_ONE_COLUMNS[select_one]
    except KeyError:
        raise ValueError(
            f"Unsupported SELECTONE value {select_one!r}; expected one of {sorted(_SELECT_ONE_COLUMNS)}"
        ) from None


def _channel_year_total_expression(ctx: DataFrameContext, channel: str, formula: str) -> Any:
    table_name, customer_key, date_key, prefix = _CHANNEL_YEAR_SPECS[channel]
    col = ctx.col
    lit = ctx.lit
    if formula == "profit_mix":
        value = (
            col(f"{prefix}_ext_list_price")
            - col(f"{prefix}_ext_wholesale_cost")
            - col(f"{prefix}_ext_discount_amt")
            + col(f"{prefix}_ext_sales_price")
        ) / lit(2)
    else:
        value = col(f"{prefix}_ext_list_price") - col(f"{prefix}_ext_discount_amt")
    return (
        ctx.get_table(table_name)
        .join(ctx.get_table("customer"), left_on=customer_key, right_on="c_customer_sk")
        .join(ctx.get_table("date_dim"), left_on=date_key, right_on="d_date_sk")
        .group_by(_CUSTOMER_YEAR_COLS + ["d_year"])
        .agg(value.sum().alias("year_total"))
        .with_columns(lit(channel).alias("sale_type"))
    )


def _channel_year_total_pandas(ctx: DataFrameContext, channel: str, formula: str) -> Any:
    table_name, customer_key, date_key, prefix = _CHANNEL_YEAR_SPECS[channel]
    frame = ctx.get_table(table_name).merge(ctx.get_table("customer"), left_on=customer_key, right_on="c_customer_sk")
    frame = frame.merge(ctx.get_table("date_dim")[["d_date_sk", "d_year"]], left_on=date_key, right_on="d_date_sk")
    if formula == "profit_mix":
        frame["year_total"] = (
            frame[f"{prefix}_ext_list_price"]
            - frame[f"{prefix}_ext_wholesale_cost"]
            - frame[f"{prefix}_ext_discount_amt"]
            + frame[f"{prefix}_ext_sales_price"]
        ) / 2
    else:
        frame["year_total"] = frame[f"{prefix}_ext_list_price"] - frame[f"{prefix}_ext_discount_amt"]
    result = frame.groupby(_CUSTOMER_YEAR_COLS + ["d_year"], as_index=False, dropna=False).agg(
        year_total=("year_total", "sum")
    )
    result["sale_type"] = channel
    return result


def _year_growth_expression(
    ctx: DataFrameContext,
    year: int,
    select_one: str,
    channels: tuple[str, ...],
    formula: str,
    winner: str,
    competitors: tuple[str, ...],
) -> Any:
    col = ctx.col
    lit = ctx.lit
    select_column = _select_one_column(select_one)
    year_total = ctx.concat([_channel_year_total_expression(ctx, channel, formula) for channel in channels])
    result = None
    for channel in channels:
        first = (
            year_total.filter((col("sale_type") == lit(channel)) & (col("d_year") == lit(year)))
            .filter(col("year_total") > lit(0))
            .select([col("c_customer_id"), col("year_total").alias(f"{channel}_first_total")])
        )
        second_cols = [col("c_customer_id"), col("year_total").alias(f"{channel}_sec_total")]
        if result is None:
            second_cols[1:1] = [col("c_first_name"), col("c_last_name"), col(select_column)]
        second = year_total.filter((col("sale_type") == lit(channel)) & (col("d_year") == lit(year + 1))).select(
            second_cols
        )
        result = (
            second.join(first, on="c_customer_id")
            if result is None
            else result.join(first, on="c_customer_id").join(second, on="c_customer_id")
        )
    result = result.with_columns(
        [
            (col(f"{channel}_sec_total") / col(f"{channel}_first_total")).alias(f"{channel}_growth")
            for channel in channels
        ]
    )
    predicate = None
    for channel in competitors:
        clause = col(f"{winner}_growth") > col(f"{channel}_growth")
        predicate = clause if predicate is None else predicate & clause
    selected = result.filter(predicate).select(
        [col("c_customer_id"), col("c_first_name"), col("c_last_name"), col(select_column)]
    )
    return _sort_null_largest_expression(
        ctx, selected, ["c_customer_id", "c_first_name", "c_last_name", select_column], [False] * 4
    ).limit(100)


def _year_growth_pandas(
    ctx: DataFrameContext,
    year: int,
    select_one: str,
    channels: tuple[str, ...],
    formula: str,
    winner: str,
    competitors: tuple[str, ...],
) -> Any:
    select_column = _select_one_column(select_one)
    year_total = ctx.concat([_channel_year_total_pandas(ctx, channel, formula) for channel in channels])
    result = None
    for channel in channels:
        first = year_total[
            (year_total["sale_type"] == channel) & (year_total["d_year"] == year) & (year_total["year_total"] > 0)
        ][["c_customer_id", "year_total"]].rename(columns={"year_total": f"{channel}_first_total"})
        second_cols = ["c_customer_id", "year_total"]
        if result is None:
            second_cols[1:1] = ["c_first_name", "c_last_name", select_column]
        second = year_total[(year_total["sale_type"] == channel) & (year_total["d_year"] == year + 1)][
            second_cols
        ].rename(columns={"year_total": f"{channel}_sec_total"})
        result = (
            second.merge(first, on="c_customer_id")
            if result is None
            else result.merge(first, on="c_customer_id").merge(second, on="c_customer_id")
        )
    for channel in channels:
        result[f"{channel}_growth"] = result[f"{channel}_sec_total"] / result[f"{channel}_first_total"]
    for channel in competitors:
        result = result[result[f"{winner}_growth"] > result[f"{channel}_growth"]]
    columns = ["c_customer_id", "c_first_name", "c_last_name", select_column]
    top = _sort_null_largest_pandas(result[columns], columns, [False] * 4).head(100)
    return top.astype(object).where(top.notnull(), None)


_YEAR_GROWTH_SHAPES: dict[int, tuple[tuple[str, ...], str, str, tuple[str, ...]]] = {
    4: (("s", "c", "w"), "profit_mix", "c", ("s", "w")),
    11: (("s", "w"), "net_discount", "w", ("s",)),
}


def _year_growth_args(query_id: int) -> tuple[Any, ...]:
    params = get_parameters(query_id)
    return (params.get("year", 2001), params.get("select_one", _SELECT_ONE_DEFAULT), *_YEAR_GROWTH_SHAPES[query_id])


def q4_expression_impl(ctx: DataFrameContext) -> Any:
    return _year_growth_expression(ctx, *_year_growth_args(4))


def q4_pandas_impl(ctx: DataFrameContext) -> Any:
    return _year_growth_pandas(ctx, *_year_growth_args(4))


def q11_expression_impl(ctx: DataFrameContext) -> Any:
    return _year_growth_expression(ctx, *_year_growth_args(11))


def q11_pandas_impl(ctx: DataFrameContext) -> Any:
    return _year_growth_pandas(ctx, *_year_growth_args(11))


def q5_expression_impl(ctx: DataFrameContext) -> Any:
    col = ctx.col
    lit = ctx.lit
    date_filtered = _date_window_expression(ctx, 5, "1998-08-04", days=14)

    def channel(
        channel_name: str,
        id_prefix: str,
        dimension_table: str,
        dimension_key: str,
        dimension_id: str,
        sales_spec: tuple[str, str, str, str, str],
        returns_spec: tuple[str, str, str, str, str],
        returns_join: tuple[str, tuple[str, str], tuple[str, str]] | None = None,
    ) -> Any:
        sales_table, sales_dim_col, sales_date_col, sales_price_col, sales_profit_col = sales_spec
        returns_table, returns_dim_col, returns_date_col, returns_amt_col, returns_loss_col = returns_spec
        sales_part = ctx.get_table(sales_table).select(
            [
                col(sales_dim_col).alias("dim_sk"),
                col(sales_date_col).alias("date_sk"),
                col(sales_price_col).alias("sales_price"),
                col(sales_profit_col).alias("profit"),
                lit(0.0).alias("return_amt"),
                lit(0.0).alias("net_loss"),
            ]
        )
        returns = ctx.get_table(returns_table)
        if returns_join is not None:
            join_table, left_on, right_on = returns_join
            returns = returns.join(
                ctx.get_table(join_table), left_on=list(left_on), right_on=list(right_on), how="left"
            )
        returns_part = returns.select(
            [
                col(returns_dim_col).alias("dim_sk"),
                col(returns_date_col).alias("date_sk"),
                lit(0.0).alias("sales_price"),
                lit(0.0).alias("profit"),
                col(returns_amt_col).alias("return_amt"),
                col(returns_loss_col).alias("net_loss"),
            ]
        )
        return (
            ctx.concat([sales_part, returns_part])
            .join(date_filtered, left_on="date_sk", right_on="d_date_sk")
            .join(ctx.get_table(dimension_table), left_on="dim_sk", right_on=dimension_key)
            .group_by(dimension_id)
            .agg(
                [
                    col("sales_price").sum().alias("sales"),
                    col("profit").sum().alias("profit_sum"),
                    col("return_amt").sum().alias("returns"),
                    col("net_loss").sum().alias("profit_loss"),
                    col("sales_price").count().alias("sales_n"),
                    col("profit").count().alias("profit_sum_n"),
                    col("return_amt").count().alias("returns_n"),
                    col("net_loss").count().alias("profit_loss_n"),
                ]
            )
            .with_columns(
                [
                    ctx.when(col(f"{name}_n") > lit(0)).then(col(name)).otherwise(lit(None)).alias(name)
                    for name in ("sales", "profit_sum", "returns", "profit_loss")
                ]
            )
            .with_columns(
                [
                    lit(channel_name).alias("channel"),
                    (lit(id_prefix) + col(dimension_id)).alias("id"),
                    (col("profit_sum") - col("profit_loss")).alias("profit"),
                ]
            )
            .select(["channel", "id", "sales", "returns", "profit"])
        )

    specs = (
        (
            "store channel",
            "store",
            "store",
            "s_store_sk",
            "s_store_id",
            ("store_sales", "ss_store_sk", "ss_sold_date_sk", "ss_ext_sales_price", "ss_net_profit"),
            ("store_returns", "sr_store_sk", "sr_returned_date_sk", "sr_return_amt", "sr_net_loss"),
        ),
        (
            "catalog channel",
            "catalog_page",
            "catalog_page",
            "cp_catalog_page_sk",
            "cp_catalog_page_id",
            ("catalog_sales", "cs_catalog_page_sk", "cs_sold_date_sk", "cs_ext_sales_price", "cs_net_profit"),
            ("catalog_returns", "cr_catalog_page_sk", "cr_returned_date_sk", "cr_return_amount", "cr_net_loss"),
        ),
        (
            "web channel",
            "web_site",
            "web_site",
            "web_site_sk",
            "web_site_id",
            ("web_sales", "ws_web_site_sk", "ws_sold_date_sk", "ws_ext_sales_price", "ws_net_profit"),
            ("web_returns", "ws_web_site_sk", "wr_returned_date_sk", "wr_return_amt", "wr_net_loss"),
            ("web_sales", ("wr_item_sk", "wr_order_number"), ("ws_item_sk", "ws_order_number")),
        ),
    )
    combined = ctx.concat([channel(*spec) for spec in specs])
    return _sales_returns_rollup_expression(ctx, combined)


def q5_pandas_impl(ctx: DataFrameContext) -> Any:
    date_filtered = _date_window_pandas(ctx, 5, "1998-08-04", days=14)

    def part(frame: Any, columns: dict[str, str], sales: bool) -> Any:
        result = frame[list(columns)].rename(columns=columns).copy()
        result["sales_price"] = result["sales_price"] if sales else 0.0
        result["profit"] = result["profit"] if sales else 0.0
        result["return_amt"] = 0.0 if sales else result["return_amt"]
        result["net_loss"] = 0.0 if sales else result["net_loss"]
        return result

    def channel(
        channel_name: str,
        id_prefix: str,
        dimension_table: str,
        dimension_key: str,
        dimension_id: str,
        sales_spec: tuple[str, dict[str, str]],
        returns_spec: tuple[str, dict[str, str]],
        returns_join: tuple[str, tuple[str, str], tuple[str, str]] | None = None,
    ) -> Any:
        sales_table, sales_cols = sales_spec
        returns_table, returns_cols = returns_spec
        returns = ctx.get_table(returns_table)
        if returns_join is not None:
            join_table, left_on, right_on = returns_join
            returns = returns.merge(
                ctx.get_table(join_table)[list(right_on) + ["ws_web_site_sk"]],
                left_on=list(left_on),
                right_on=list(right_on),
                how="left",
            )
        union = ctx.concat([part(ctx.get_table(sales_table), sales_cols, True), part(returns, returns_cols, False)])
        union = union.merge(date_filtered, left_on="date_sk", right_on="d_date_sk")
        union = union.merge(
            ctx.get_table(dimension_table)[[dimension_key, dimension_id]], left_on="dim_sk", right_on=dimension_key
        )
        result = union.groupby(dimension_id, as_index=False).agg(
            sales=("sales_price", "sum"),
            profit_sum=("profit", "sum"),
            returns=("return_amt", "sum"),
            profit_loss=("net_loss", "sum"),
            sales_n=("sales_price", "count"),
            profit_sum_n=("profit", "count"),
            returns_n=("return_amt", "count"),
            profit_loss_n=("net_loss", "count"),
        )
        for name in ("sales", "profit_sum", "returns", "profit_loss"):
            result[name] = result[name].where(result[f"{name}_n"] > 0)
        result["channel"] = channel_name
        result["id"] = id_prefix + result[dimension_id].astype(str)
        result["profit"] = result["profit_sum"] - result["profit_loss"]
        return result[["channel", "id", "sales", "returns", "profit"]]

    specs = (
        (
            "store channel",
            "store",
            "store",
            "s_store_sk",
            "s_store_id",
            (
                "store_sales",
                {
                    "ss_store_sk": "dim_sk",
                    "ss_sold_date_sk": "date_sk",
                    "ss_ext_sales_price": "sales_price",
                    "ss_net_profit": "profit",
                },
            ),
            (
                "store_returns",
                {
                    "sr_store_sk": "dim_sk",
                    "sr_returned_date_sk": "date_sk",
                    "sr_return_amt": "return_amt",
                    "sr_net_loss": "net_loss",
                },
            ),
        ),
        (
            "catalog channel",
            "catalog_page",
            "catalog_page",
            "cp_catalog_page_sk",
            "cp_catalog_page_id",
            (
                "catalog_sales",
                {
                    "cs_catalog_page_sk": "dim_sk",
                    "cs_sold_date_sk": "date_sk",
                    "cs_ext_sales_price": "sales_price",
                    "cs_net_profit": "profit",
                },
            ),
            (
                "catalog_returns",
                {
                    "cr_catalog_page_sk": "dim_sk",
                    "cr_returned_date_sk": "date_sk",
                    "cr_return_amount": "return_amt",
                    "cr_net_loss": "net_loss",
                },
            ),
        ),
        (
            "web channel",
            "web_site",
            "web_site",
            "web_site_sk",
            "web_site_id",
            (
                "web_sales",
                {
                    "ws_web_site_sk": "dim_sk",
                    "ws_sold_date_sk": "date_sk",
                    "ws_ext_sales_price": "sales_price",
                    "ws_net_profit": "profit",
                },
            ),
            (
                "web_returns",
                {
                    "ws_web_site_sk": "dim_sk",
                    "wr_returned_date_sk": "date_sk",
                    "wr_return_amt": "return_amt",
                    "wr_net_loss": "net_loss",
                },
            ),
            ("web_sales", ("wr_item_sk", "wr_order_number"), ("ws_item_sk", "ws_order_number")),
        ),
    )
    combined = ctx.concat([channel(*spec) for spec in specs])
    return _sales_returns_rollup_pandas(ctx, combined)


def _date_sales_customer_sets_expression(ctx: DataFrameContext, date_filtered: Any) -> tuple[Any, Any, Any]:
    col = ctx.col
    return (
        ctx.get_table("store_sales")
        .join(date_filtered, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .select(col("ss_customer_sk").alias("customer_sk"))
        .unique(),
        ctx.get_table("web_sales")
        .join(date_filtered, left_on="ws_sold_date_sk", right_on="d_date_sk")
        .select(col("ws_bill_customer_sk").alias("customer_sk"))
        .unique(),
        ctx.get_table("catalog_sales")
        .join(date_filtered, left_on="cs_sold_date_sk", right_on="d_date_sk")
        .select(col("cs_ship_customer_sk").alias("customer_sk"))
        .unique(),
    )


def _date_sales_customer_sets_pandas(ctx: DataFrameContext, date_filtered: Any) -> tuple[Any, Any, Any]:
    def customers(table_name: str, date_key: str, customer_key: str) -> Any:
        merged = ctx.get_table(table_name).merge(date_filtered, left_on=date_key, right_on="d_date_sk")
        return merged[[customer_key]].drop_duplicates().rename(columns={customer_key: "customer_sk"})

    return (
        customers("store_sales", "ss_sold_date_sk", "ss_customer_sk"),
        customers("web_sales", "ws_sold_date_sk", "ws_bill_customer_sk"),
        customers("catalog_sales", "cs_sold_date_sk", "cs_ship_customer_sk"),
    )


def _store_and_other_channel_customers_expression(ctx: DataFrameContext, date_filtered: Any) -> Any:
    store_customers, web_customers, catalog_customers = _date_sales_customer_sets_expression(ctx, date_filtered)
    web_or_catalog = ctx.concat([web_customers, catalog_customers]).unique()
    return store_customers.join(web_or_catalog, on="customer_sk")


def _store_and_other_channel_customers_pandas(ctx: DataFrameContext, date_filtered: Any) -> set[Any]:
    store_customers, web_customers, catalog_customers = _date_sales_customer_sets_pandas(ctx, date_filtered)
    web_or_catalog = ctx.to_set(
        ctx.concat([web_customers["customer_sk"], catalog_customers["customer_sk"]]).drop_duplicates()
    )
    return ctx.to_set(store_customers["customer_sk"]).intersection(web_or_catalog)


def _store_only_channel_customers_expression(ctx: DataFrameContext, date_filtered: Any) -> Any:
    store_customers, web_customers, catalog_customers = _date_sales_customer_sets_expression(ctx, date_filtered)
    return store_customers.join(web_customers, on="customer_sk", how="anti").join(
        catalog_customers, on="customer_sk", how="anti"
    )


def _store_only_channel_customers_pandas(ctx: DataFrameContext, date_filtered: Any) -> set[Any]:
    store_customers, web_customers, catalog_customers = _date_sales_customer_sets_pandas(ctx, date_filtered)
    return (
        ctx.to_set(store_customers["customer_sk"])
        - ctx.to_set(web_customers["customer_sk"])
        - ctx.to_set(catalog_customers["customer_sk"])
    )


def _customer_date_sets_expression(ctx: DataFrameContext, date_filtered: Any) -> tuple[Any, Any, Any]:
    customer = ctx.get_table("customer")
    columns = ["c_last_name", "c_first_name", "d_date"]

    def channel(table_name: str, date_key: str, customer_key: str) -> Any:
        return (
            ctx.get_table(table_name)
            .join(date_filtered, left_on=date_key, right_on="d_date_sk")
            .join(customer, left_on=customer_key, right_on="c_customer_sk")
            .select(columns)
            .unique()
        )

    return (
        channel("store_sales", "ss_sold_date_sk", "ss_customer_sk"),
        channel("catalog_sales", "cs_sold_date_sk", "cs_bill_customer_sk"),
        channel("web_sales", "ws_sold_date_sk", "ws_bill_customer_sk"),
    )


def _customer_date_sets_pandas(ctx: DataFrameContext, date_filtered: Any) -> tuple[Any, Any, Any]:
    customer = ctx.get_table("customer")[["c_customer_sk", "c_last_name", "c_first_name"]]
    columns = ["c_last_name", "c_first_name", "d_date"]

    def channel(table_name: str, date_key: str, customer_key: str) -> Any:
        merged = ctx.get_table(table_name).merge(date_filtered, left_on=date_key, right_on="d_date_sk")
        merged = merged.merge(customer, left_on=customer_key, right_on="c_customer_sk")
        return merged[columns].drop_duplicates()

    return (
        channel("store_sales", "ss_sold_date_sk", "ss_customer_sk"),
        channel("catalog_sales", "cs_sold_date_sk", "cs_bill_customer_sk"),
        channel("web_sales", "ws_sold_date_sk", "ws_bill_customer_sk"),
    )


def _three_channel_customer_count_expression(ctx: DataFrameContext, query_id: int, mode: str) -> Any:
    params = get_parameters(query_id)
    dms = params.get("dms", 1212)
    col = ctx.col
    lit = ctx.lit
    date_filtered = ctx.get_table("date_dim").filter(
        (col("d_month_seq") >= lit(dms)) & (col("d_month_seq") <= lit(dms + 11))
    )
    store_customers, catalog_customers, web_customers = _customer_date_sets_expression(ctx, date_filtered)

    def null_safe(frame: Any) -> Any:
        return frame.with_columns(
            col("c_last_name").is_null().alias("c_last_name_is_null"),
            col("c_first_name").is_null().alias("c_first_name_is_null"),
            col("c_last_name").fill_null(lit("")).alias("c_last_name"),
            col("c_first_name").fill_null(lit("")).alias("c_first_name"),
        )

    keys = ["c_last_name", "c_first_name", "c_last_name_is_null", "c_first_name_is_null", "d_date"]
    store_customers, catalog_customers, web_customers = (
        null_safe(frame) for frame in (store_customers, catalog_customers, web_customers)
    )
    if mode == "intersect":
        result = store_customers.join(catalog_customers, on=keys).join(web_customers, on=keys)
    else:
        result = store_customers.join(catalog_customers, on=keys, how="anti").join(web_customers, on=keys, how="anti")
    return result.select(ctx.count().alias("count"))


def _three_channel_customer_count_pandas(ctx: DataFrameContext, query_id: int, mode: str) -> Any:
    import pandas as pd

    params = get_parameters(query_id)
    dms = params.get("dms", 1212)
    date_filtered = ctx.get_table("date_dim")
    date_filtered = date_filtered[(date_filtered["d_month_seq"] >= dms) & (date_filtered["d_month_seq"] <= dms + 11)][
        ["d_date_sk", "d_date"]
    ]
    store_customers, catalog_customers, web_customers = _customer_date_sets_pandas(ctx, date_filtered)
    keys = ["c_last_name", "c_first_name", "d_date"]
    if mode == "intersect":
        result = store_customers.merge(catalog_customers, on=keys).merge(web_customers, on=keys)
    else:
        result = store_customers.merge(catalog_customers, on=keys, how="left", indicator=True)
        result = result[result["_merge"] == "left_only"][keys]
        result = result.merge(web_customers, on=keys, how="left", indicator=True)
        result = result[result["_merge"] == "left_only"]
    return pd.DataFrame({"count": [len(result)]})


def q10_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(10)
    year = params.get("year", 2002)
    month = params.get("month", 2)
    counties = params.get("counties")
    col = ctx.col
    lit = ctx.lit
    date_filtered = ctx.get_table("date_dim").filter(
        (col("d_year") == lit(year)) & (col("d_moy") >= lit(month)) & (col("d_moy") <= lit(month + 3))
    )
    customers = _store_and_other_channel_customers_expression(ctx, date_filtered)
    base = (
        ctx.get_table("customer")
        .join(ctx.get_table("customer_address"), left_on="c_current_addr_sk", right_on="ca_address_sk")
        .filter(col("ca_county").is_in(counties))
        .join(ctx.get_table("customer_demographics"), left_on="c_current_cdemo_sk", right_on="cd_demo_sk")
        .join(customers, left_on="c_customer_sk", right_on="customer_sk")
    )
    group_cols = [
        "cd_gender",
        "cd_marital_status",
        "cd_education_status",
        "cd_purchase_estimate",
        "cd_credit_rating",
        "cd_dep_count",
        "cd_dep_employed_count",
        "cd_dep_college_count",
    ]
    select_cols = [
        "cd_gender",
        "cd_marital_status",
        "cd_education_status",
        "cnt1",
        "cd_purchase_estimate",
        "cnt2",
        "cd_credit_rating",
        "cnt3",
        "cd_dep_count",
        "cnt4",
        "cd_dep_employed_count",
        "cnt5",
        "cd_dep_college_count",
        "cnt6",
    ]
    return _sort_null_largest_expression(
        ctx,
        base.group_by(group_cols)
        .agg(ctx.count().alias("cnt"))
        .with_columns([col("cnt").alias(f"cnt{i}") for i in range(1, 7)])
        .select(select_cols),
        group_cols,
    ).limit(100)


def q10_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(10)
    year = params.get("year", 2002)
    month = params.get("month", 2)
    counties = params.get("counties")
    date_filtered = ctx.get_table("date_dim")
    date_filtered = date_filtered[
        (date_filtered["d_year"] == year) & (date_filtered["d_moy"] >= month) & (date_filtered["d_moy"] <= month + 3)
    ][["d_date_sk"]]
    customers = _store_and_other_channel_customers_pandas(ctx, date_filtered)
    base = ctx.get_table("customer").merge(
        ctx.get_table("customer_address"), left_on="c_current_addr_sk", right_on="ca_address_sk"
    )
    base = base[base["ca_county"].isin(counties)]
    base = base.merge(ctx.get_table("customer_demographics"), left_on="c_current_cdemo_sk", right_on="cd_demo_sk")
    base = base[base["c_customer_sk"].isin(customers)]
    group_cols = [
        "cd_gender",
        "cd_marital_status",
        "cd_education_status",
        "cd_purchase_estimate",
        "cd_credit_rating",
        "cd_dep_count",
        "cd_dep_employed_count",
        "cd_dep_college_count",
    ]
    result = ctx.groupby_size(base, group_cols, name="cnt")
    for index in range(1, 7):
        result[f"cnt{index}"] = result["cnt"]
    select_cols = [
        "cd_gender",
        "cd_marital_status",
        "cd_education_status",
        "cnt1",
        "cd_purchase_estimate",
        "cnt2",
        "cd_credit_rating",
        "cnt3",
        "cd_dep_count",
        "cnt4",
        "cd_dep_employed_count",
        "cnt5",
        "cd_dep_college_count",
        "cnt6",
    ]
    return result[select_cols].sort_values(group_cols).head(100)


_Q35_EXPRESSION_AGGREGATES = {"sum": "sum", "min": "min", "max": "max", "avg": "mean", "stddev_samp": "std"}
_Q35_PANDAS_AGGREGATES = {"sum": "sum", "min": "min", "max": "max", "avg": "mean", "stddev_samp": "std"}


def _q35_aggregates(params: Any) -> tuple[str, str, str]:
    return params.get("aggone", "avg"), params.get("aggtwo", "max"), params.get("aggthree", "sum")


def q35_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(35)
    year = params.get("year", 2002)
    aggone, aggtwo, aggthree = (_Q35_EXPRESSION_AGGREGATES[name] for name in _q35_aggregates(params))
    col = ctx.col
    lit = ctx.lit
    date_filtered = ctx.get_table("date_dim").filter((col("d_year") == lit(year)) & (col("d_qoy") < lit(4)))
    customers = _store_and_other_channel_customers_expression(ctx, date_filtered)
    base = (
        ctx.get_table("customer")
        .join(ctx.get_table("customer_address"), left_on="c_current_addr_sk", right_on="ca_address_sk")
        .join(ctx.get_table("customer_demographics"), left_on="c_current_cdemo_sk", right_on="cd_demo_sk")
        .join(customers, left_on="c_customer_sk", right_on="customer_sk")
    )
    group_cols = [
        "ca_state",
        "cd_gender",
        "cd_marital_status",
        "cd_dep_count",
        "cd_dep_employed_count",
        "cd_dep_college_count",
    ]
    return (
        base.group_by(group_cols)
        .agg(
            [
                ctx.count().alias("cnt1"),
                getattr(col("cd_dep_count"), aggone)().alias("aggone1"),
                getattr(col("cd_dep_count"), aggtwo)().alias("aggtwo1"),
                getattr(col("cd_dep_count"), aggthree)().alias("aggthree1"),
                getattr(col("cd_dep_employed_count"), aggone)().alias("aggone2"),
                getattr(col("cd_dep_employed_count"), aggtwo)().alias("aggtwo2"),
                getattr(col("cd_dep_employed_count"), aggthree)().alias("aggthree2"),
                getattr(col("cd_dep_college_count"), aggone)().alias("aggone3"),
                getattr(col("cd_dep_college_count"), aggtwo)().alias("aggtwo3"),
                getattr(col("cd_dep_college_count"), aggthree)().alias("aggthree3"),
            ]
        )
        .with_columns([col("cnt1").alias("cnt2"), col("cnt1").alias("cnt3")])
        .select(
            [
                "ca_state",
                "cd_gender",
                "cd_marital_status",
                "cd_dep_count",
                "cnt1",
                "aggone1",
                "aggtwo1",
                "aggthree1",
                "cd_dep_employed_count",
                "cnt2",
                "aggone2",
                "aggtwo2",
                "aggthree2",
                "cd_dep_college_count",
                "cnt3",
                "aggone3",
                "aggtwo3",
                "aggthree3",
            ]
        )
        .sort(group_cols, nulls_last=True)
        .limit(100)
    )


def q35_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(35)
    year = params.get("year", 2002)
    aggone, aggtwo, aggthree = (_Q35_PANDAS_AGGREGATES[name] for name in _q35_aggregates(params))
    date_filtered = ctx.get_table("date_dim")
    date_filtered = date_filtered[(date_filtered["d_year"] == year) & (date_filtered["d_qoy"] < 4)][["d_date_sk"]]
    customers = _store_and_other_channel_customers_pandas(ctx, date_filtered)
    base = ctx.get_table("customer").merge(
        ctx.get_table("customer_address"), left_on="c_current_addr_sk", right_on="ca_address_sk"
    )
    base = base.merge(ctx.get_table("customer_demographics"), left_on="c_current_cdemo_sk", right_on="cd_demo_sk")
    base = base[base["c_customer_sk"].isin(customers)]
    group_cols = [
        "ca_state",
        "cd_gender",
        "cd_marital_status",
        "cd_dep_count",
        "cd_dep_employed_count",
        "cd_dep_college_count",
    ]
    agg_spec = {
        "cnt1": ("c_customer_sk", "count"),
        "aggone1": ("cd_dep_count", aggone),
        "aggtwo1": ("cd_dep_count", aggtwo),
        "aggthree1": ("cd_dep_count", aggthree),
        "aggone2": ("cd_dep_employed_count", aggone),
        "aggtwo2": ("cd_dep_employed_count", aggtwo),
        "aggthree2": ("cd_dep_employed_count", aggthree),
        "aggone3": ("cd_dep_college_count", aggone),
        "aggtwo3": ("cd_dep_college_count", aggtwo),
        "aggthree3": ("cd_dep_college_count", aggthree),
    }
    result = base.groupby(group_cols, as_index=False, dropna=False).agg(**agg_spec)
    result["cnt2"] = result["cnt1"]
    result["cnt3"] = result["cnt1"]
    result = _none_for_null(
        result, ["ca_state", *(f"agg{position}{number}" for position in ("one", "two", "three") for number in "123")]
    )
    return (
        result[
            [
                "ca_state",
                "cd_gender",
                "cd_marital_status",
                "cd_dep_count",
                "cnt1",
                "aggone1",
                "aggtwo1",
                "aggthree1",
                "cd_dep_employed_count",
                "cnt2",
                "aggone2",
                "aggtwo2",
                "aggthree2",
                "cd_dep_college_count",
                "cnt3",
                "aggone3",
                "aggtwo3",
                "aggthree3",
            ]
        ]
        .sort_values(group_cols, na_position="last")
        .head(100)
    )


def q38_expression_impl(ctx: DataFrameContext) -> Any:
    return _three_channel_customer_count_expression(ctx, 38, "intersect")


def q38_pandas_impl(ctx: DataFrameContext) -> Any:
    return _three_channel_customer_count_pandas(ctx, 38, "intersect")


def q87_expression_impl(ctx: DataFrameContext) -> Any:
    return _three_channel_customer_count_expression(ctx, 87, "except")


def q87_pandas_impl(ctx: DataFrameContext) -> Any:
    return _three_channel_customer_count_pandas(ctx, 87, "except")


def q69_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(69)
    year = params.get("year", 2001)
    month = params.get("month", 4)
    states = params.get("states", ["KY", "GA", "NM"])
    col = ctx.col
    lit = ctx.lit
    date_filtered = ctx.get_table("date_dim").filter(
        (col("d_year") == lit(year)) & (col("d_moy") >= lit(month)) & (col("d_moy") <= lit(month + 2))
    )
    customers = _store_only_channel_customers_expression(ctx, date_filtered)
    group_cols = ["cd_gender", "cd_marital_status", "cd_education_status", "cd_purchase_estimate", "cd_credit_rating"]
    return _sort_null_largest_expression(
        ctx,
        ctx.get_table("customer")
        .join(
            ctx.get_table("customer_address").filter(col("ca_state").is_in(states)),
            left_on="c_current_addr_sk",
            right_on="ca_address_sk",
        )
        .join(ctx.get_table("customer_demographics"), left_on="c_current_cdemo_sk", right_on="cd_demo_sk")
        .join(customers, left_on="c_customer_sk", right_on="customer_sk", how="semi")
        .group_by(group_cols)
        .agg(ctx.count().alias("cnt1"))
        .with_columns([col("cnt1").alias("cnt2"), col("cnt1").alias("cnt3")])
        .select(
            "cd_gender",
            "cd_marital_status",
            "cd_education_status",
            "cnt1",
            "cd_purchase_estimate",
            "cnt2",
            "cd_credit_rating",
            "cnt3",
        ),
        group_cols,
    ).head(100)


def q69_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(69)
    year = params.get("year", 2001)
    month = params.get("month", 4)
    states = params.get("states", ["KY", "GA", "NM"])
    date_filtered = ctx.get_table("date_dim")
    date_filtered = date_filtered[
        (date_filtered["d_year"] == year) & (date_filtered["d_moy"] >= month) & (date_filtered["d_moy"] <= month + 2)
    ][["d_date_sk"]]
    customers = _store_only_channel_customers_pandas(ctx, date_filtered)
    base = ctx.get_table("customer").merge(
        ctx.get_table("customer_address")[ctx.get_table("customer_address")["ca_state"].isin(states)],
        left_on="c_current_addr_sk",
        right_on="ca_address_sk",
    )
    base = base.merge(ctx.get_table("customer_demographics"), left_on="c_current_cdemo_sk", right_on="cd_demo_sk")
    base = base[base["c_customer_sk"].isin(customers)]
    group_cols = ["cd_gender", "cd_marital_status", "cd_education_status", "cd_purchase_estimate", "cd_credit_rating"]
    result = base.groupby(group_cols, as_index=False).size().rename(columns={"size": "cnt1"})
    result["cnt2"] = result["cnt1"]
    result["cnt3"] = result["cnt1"]
    cols = [
        "cd_gender",
        "cd_marital_status",
        "cd_education_status",
        "cnt1",
        "cd_purchase_estimate",
        "cnt2",
        "cd_credit_rating",
        "cnt3",
    ]
    return result[cols].sort_values(group_cols).head(100)


def q40_expression_impl(ctx: DataFrameContext) -> Any:
    from datetime import datetime, timedelta

    params = get_parameters(40)
    sales_date = datetime.strptime(params.get("sales_date", "1998-04-08"), "%Y-%m-%d").date()

    catalog_sales, catalog_returns, warehouse, item, date_dim = _tables(
        ctx, "catalog_sales", "catalog_returns", "warehouse", "item", "date_dim"
    )
    col = ctx.col
    lit = ctx.lit

    start_date = sales_date - timedelta(days=30)
    end_date = sales_date + timedelta(days=30)

    date_filtered = date_dim.filter((col("d_date") >= lit(start_date)) & (col("d_date") <= lit(end_date)))

    item_filtered = item.filter((col("i_current_price") >= lit(0.99)) & (col("i_current_price") <= lit(1.49)))

    cs_with_cr = catalog_sales.join(
        catalog_returns,
        left_on=["cs_order_number", "cs_item_sk"],
        right_on=["cr_order_number", "cr_item_sk"],
        how="left",
    )

    result = (
        cs_with_cr.join(date_filtered, left_on="cs_sold_date_sk", right_on="d_date_sk")
        .join(warehouse, left_on="cs_warehouse_sk", right_on="w_warehouse_sk")
        .join(item_filtered, left_on="cs_item_sk", right_on="i_item_sk")
    )

    result = result.with_columns(
        [
            ctx.when(col("d_date") < lit(sales_date))
            .then(col("cs_sales_price") - col("cr_refunded_cash").fill_null(0))
            .otherwise(lit(0))
            .alias("sales_before_val"),
            ctx.when(col("d_date") >= lit(sales_date))
            .then(col("cs_sales_price") - col("cr_refunded_cash").fill_null(0))
            .otherwise(lit(0))
            .alias("sales_after_val"),
        ]
    )

    return _sort_null_largest_expression(
        ctx,
        result.group_by(["w_state", "i_item_id"]).agg(
            [
                ctx.sum("sales_before_val").alias("sales_before"),
                ctx.sum("sales_after_val").alias("sales_after"),
            ]
        ),
        ["w_state", "i_item_id"],
    ).head(100)


def q40_pandas_impl(ctx: DataFrameContext) -> Any:
    from datetime import datetime, timedelta

    params = get_parameters(40)
    sales_date = datetime.strptime(params.get("sales_date", "1998-04-08"), "%Y-%m-%d").date()

    catalog_sales, catalog_returns, warehouse, item, date_dim = _tables(
        ctx, "catalog_sales", "catalog_returns", "warehouse", "item", "date_dim"
    )

    start_date = sales_date - timedelta(days=30)
    end_date = sales_date + timedelta(days=30)

    date_filtered = date_dim[(date_dim["d_date"] >= start_date) & (date_dim["d_date"] <= end_date)][
        ["d_date_sk", "d_date"]
    ]

    item_filtered = item[(item["i_current_price"] >= 0.99) & (item["i_current_price"] <= 1.49)][
        ["i_item_sk", "i_item_id"]
    ]

    cs_with_cr = catalog_sales.merge(
        catalog_returns[["cr_order_number", "cr_item_sk", "cr_refunded_cash"]],
        left_on=["cs_order_number", "cs_item_sk"],
        right_on=["cr_order_number", "cr_item_sk"],
        how="left",
    )

    result = cs_with_cr.merge(date_filtered, left_on="cs_sold_date_sk", right_on="d_date_sk")
    result = result.merge(
        warehouse[["w_warehouse_sk", "w_state"]], left_on="cs_warehouse_sk", right_on="w_warehouse_sk"
    )
    result = result.merge(item_filtered, left_on="cs_item_sk", right_on="i_item_sk")

    result["cr_refunded_cash"] = result["cr_refunded_cash"].fillna(0)

    net_sales = result["cs_sales_price"] - result["cr_refunded_cash"]
    before = result["d_date"] < sales_date
    result["sales_before_val"] = net_sales.where(before, 0)
    result["sales_after_val"] = net_sales.where(~before, 0)

    result = result.groupby(["w_state", "i_item_id"], as_index=False).agg(
        {
            "sales_before_val": "sum",
            "sales_after_val": "sum",
        }
    )
    result = result.rename(
        columns={
            "sales_before_val": "sales_before",
            "sales_after_val": "sales_after",
        }
    )

    return result.sort_values(["w_state", "i_item_id"]).head(100)


def q16_expression_impl(ctx: DataFrameContext) -> Any:
    from datetime import datetime, timedelta

    params = get_parameters(16)
    year = params.get("year", 1999)
    month = params.get("month", 2)
    state = params.get("state", "IL")
    counties = list(params.get("counties", ["Williamson County"] * 5))

    catalog_sales, catalog_returns, date_dim, customer_address, call_center = _tables(
        ctx, "catalog_sales", "catalog_returns", "date_dim", "customer_address", "call_center"
    )
    col = ctx.col
    lit = ctx.lit

    start_date = datetime(year, month, 1).date()
    end_date = start_date + timedelta(days=60)

    date_filtered = date_dim.filter((col("d_date") >= lit(start_date)) & (col("d_date") <= lit(end_date)))

    ca_filtered = customer_address.filter(col("ca_state") == lit(state))

    cc_filtered = call_center.filter(col("cc_county").is_in(counties))

    returned_orders = catalog_returns.select(["cr_order_number"]).unique()

    shipped = catalog_sales.filter(col("cs_warehouse_sk").is_not_null())
    platform = getattr(ctx, "platform", "polars")
    if platform == "datafusion":
        order_warehouses = shipped.select(["cs_order_number", "cs_warehouse_sk"]).unique()
        multi_warehouse_orders = (
            order_warehouses.join(
                order_warehouses.rename({"cs_warehouse_sk": "cs_warehouse_sk_2"}),
                on="cs_order_number",
            )
            .filter(col("cs_warehouse_sk") != col("cs_warehouse_sk_2"))
            .select(["cs_order_number"])
            .unique()
        )
    else:
        multi_warehouse_orders = (
            shipped.group_by("cs_order_number")
            .agg(col("cs_warehouse_sk").n_unique().alias("num_warehouses"))
            .filter(col("num_warehouses") > lit(1))
            .select("cs_order_number")
        )

    result = (
        shipped.join(date_filtered, left_on="cs_ship_date_sk", right_on="d_date_sk")
        .join(ca_filtered, left_on="cs_ship_addr_sk", right_on="ca_address_sk")
        .join(cc_filtered, left_on="cs_call_center_sk", right_on="cc_call_center_sk")
        .join(multi_warehouse_orders, on="cs_order_number", how="semi")
        .join(returned_orders, left_on="cs_order_number", right_on="cr_order_number", how="anti")
    )

    tallied = result.select(
        [
            col("cs_order_number").n_unique().alias("order count"),
            ctx.sum("cs_ext_ship_cost").alias("ship_cost"),
            ctx.sum("cs_net_profit").alias("net_profit"),
            col("cs_order_number").count().alias("n"),
        ]
    )
    return tallied.select(
        [
            col("order count"),
            ctx.when(col("n") > lit(0)).then(col("ship_cost")).otherwise(lit(None)).alias("total shipping cost"),
            ctx.when(col("n") > lit(0)).then(col("net_profit")).otherwise(lit(None)).alias("total net profit"),
        ]
    )


def q16_pandas_impl(ctx: DataFrameContext) -> Any:
    from datetime import datetime, timedelta

    import pandas as pd

    params = get_parameters(16)
    year = params.get("year", 1999)
    month = params.get("month", 2)
    state = params.get("state", "IL")
    counties = list(params.get("counties", ["Williamson County"] * 5))

    catalog_sales, catalog_returns, date_dim, customer_address, call_center = _tables(
        ctx, "catalog_sales", "catalog_returns", "date_dim", "customer_address", "call_center"
    )

    start_date = datetime(year, month, 1).date()
    end_date = start_date + timedelta(days=60)

    date_filtered = date_dim[(date_dim["d_date"] >= start_date) & (date_dim["d_date"] <= end_date)][["d_date_sk"]]

    ca_filtered = customer_address[customer_address["ca_state"] == state]
    cc_filtered = call_center[call_center["cc_county"].isin(counties)]

    returned_orders = catalog_returns[["cr_order_number"]].drop_duplicates()

    shipped = catalog_sales[catalog_sales["cs_warehouse_sk"].notnull()]
    order_warehouses = shipped[["cs_order_number", "cs_warehouse_sk"]].drop_duplicates()
    ow_count = ctx.groupby_size(order_warehouses, "cs_order_number", name="wh_count")
    multi_warehouse_orders = ow_count[ow_count["wh_count"] > 1][["cs_order_number"]]

    result = shipped.merge(date_filtered, left_on="cs_ship_date_sk", right_on="d_date_sk")
    result = result.merge(ca_filtered[["ca_address_sk"]], left_on="cs_ship_addr_sk", right_on="ca_address_sk")
    result = result.merge(cc_filtered[["cc_call_center_sk"]], left_on="cs_call_center_sk", right_on="cc_call_center_sk")

    result = result.merge(multi_warehouse_orders, on="cs_order_number")

    result = result.merge(
        returned_orders, left_on="cs_order_number", right_on="cr_order_number", how="left", indicator=True
    )
    result = result[result["_merge"] == "left_only"]

    order_count = result["cs_order_number"].nunique()
    if len(result) == 0:
        return pd.DataFrame({"order count": [order_count], "total shipping cost": [None], "total net profit": [None]})
    total_shipping = result["cs_ext_ship_cost"].sum()
    total_profit = result["cs_net_profit"].sum()

    return pd.DataFrame(
        {
            "order count": [order_count],
            "total shipping cost": [total_shipping],
            "total net profit": [total_profit],
        }
    )


def q17_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(17)
    year = params.get("year", 1998)
    quarter = params.get("quarter", 1)

    store_sales, store_returns, catalog_sales, date_dim, store, item = _tables(
        ctx, "store_sales", "store_returns", "catalog_sales", "date_dim", "store", "item"
    )
    col = ctx.col
    lit = ctx.lit

    quarter_name = f"{year}Q{quarter}"
    d1 = date_dim.filter(col("d_quarter_name") == lit(quarter_name))

    quarter_names_ret = [f"{year}Q{q}" for q in range(quarter, min(quarter + 3, 5))]
    d2 = date_dim.filter(col("d_quarter_name").is_in(quarter_names_ret))

    d3 = date_dim.filter(col("d_quarter_name").is_in(quarter_names_ret))

    ss_joined = (
        store_sales.join(d1.select("d_date_sk"), left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="ss_item_sk", right_on="i_item_sk")
        .join(store, left_on="ss_store_sk", right_on="s_store_sk")
    )

    sr_joined = store_returns.join(d2.select("d_date_sk"), left_on="sr_returned_date_sk", right_on="d_date_sk")

    ss_sr = ss_joined.join(
        sr_joined,
        left_on=["ss_customer_sk", "ss_item_sk", "ss_ticket_number"],
        right_on=["sr_customer_sk", "sr_item_sk", "sr_ticket_number"],
    )

    cs_joined = catalog_sales.join(d3.select("d_date_sk"), left_on="cs_sold_date_sk", right_on="d_date_sk")

    result = ss_sr.join(
        cs_joined,
        left_on=["ss_customer_sk", "ss_item_sk"],
        right_on=["cs_bill_customer_sk", "cs_item_sk"],
    )

    return _sort_null_largest_expression(
        ctx,
        result.group_by(["i_item_id", "i_item_desc", "s_state"])
        .agg(
            [
                ctx.count("ss_quantity").alias("store_sales_quantitycount"),
                col("ss_quantity").mean().alias("store_sales_quantityave"),
                col("ss_quantity").std().alias("store_sales_quantitystdev"),
                ctx.count("sr_return_quantity").alias("store_returns_quantitycount"),
                col("sr_return_quantity").mean().alias("store_returns_quantityave"),
                col("sr_return_quantity").std().alias("store_returns_quantitystdev"),
                ctx.count("cs_quantity").alias("catalog_sales_quantitycount"),
                col("cs_quantity").mean().alias("catalog_sales_quantityave"),
                col("cs_quantity").std().alias("catalog_sales_quantitystdev"),
            ]
        )
        .with_columns(
            [
                (col("store_sales_quantitystdev") / col("store_sales_quantityave")).alias("store_sales_quantitycov"),
                (col("store_returns_quantitystdev") / col("store_returns_quantityave")).alias(
                    "store_returns_quantitycov"
                ),
                (col("catalog_sales_quantitystdev") / col("catalog_sales_quantityave")).alias(
                    "catalog_sales_quantitycov"
                ),
            ]
        )
        .select(
            [
                "i_item_id",
                "i_item_desc",
                "s_state",
                "store_sales_quantitycount",
                "store_sales_quantityave",
                "store_sales_quantitystdev",
                "store_sales_quantitycov",
                "store_returns_quantitycount",
                "store_returns_quantityave",
                "store_returns_quantitystdev",
                "store_returns_quantitycov",
                "catalog_sales_quantitycount",
                "catalog_sales_quantityave",
                "catalog_sales_quantitystdev",
                "catalog_sales_quantitycov",
            ]
        ),
        ["i_item_id", "i_item_desc", "s_state"],
    ).head(100)


def q17_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(17)
    year = params.get("year", 1998)
    quarter = params.get("quarter", 1)

    store_sales, store_returns, catalog_sales, date_dim, store, item = _tables(
        ctx, "store_sales", "store_returns", "catalog_sales", "date_dim", "store", "item"
    )

    quarter_name = f"{year}Q{quarter}"
    d1 = date_dim[date_dim["d_quarter_name"] == quarter_name][["d_date_sk"]]

    quarter_names_ret = [f"{year}Q{q}" for q in range(quarter, min(quarter + 3, 5))]
    d2 = date_dim[date_dim["d_quarter_name"].isin(quarter_names_ret)][["d_date_sk"]]

    d3 = date_dim[date_dim["d_quarter_name"].isin(quarter_names_ret)][["d_date_sk"]]

    ss_joined = store_sales.merge(d1, left_on="ss_sold_date_sk", right_on="d_date_sk")
    ss_joined = ss_joined.merge(
        item[["i_item_sk", "i_item_id", "i_item_desc"]], left_on="ss_item_sk", right_on="i_item_sk"
    )
    ss_joined = ss_joined.merge(store[["s_store_sk", "s_state"]], left_on="ss_store_sk", right_on="s_store_sk")

    sr_joined = store_returns.merge(d2, left_on="sr_returned_date_sk", right_on="d_date_sk")

    ss_sr = ss_joined.merge(
        sr_joined,
        left_on=["ss_customer_sk", "ss_item_sk", "ss_ticket_number"],
        right_on=["sr_customer_sk", "sr_item_sk", "sr_ticket_number"],
    )

    cs_joined = catalog_sales.merge(d3, left_on="cs_sold_date_sk", right_on="d_date_sk")

    result = ss_sr.merge(
        cs_joined,
        left_on=["sr_customer_sk", "sr_item_sk"],
        right_on=["cs_bill_customer_sk", "cs_item_sk"],
    )

    result = result.groupby(["i_item_id", "i_item_desc", "s_state"], as_index=False).agg(
        {
            "ss_quantity": ["count", "mean", "std"],
            "sr_return_quantity": ["count", "mean", "std"],
            "cs_quantity": ["count", "mean", "std"],
        }
    )

    result.columns = [
        "i_item_id",
        "i_item_desc",
        "s_state",
        "store_sales_quantitycount",
        "store_sales_quantityave",
        "store_sales_quantitystdev",
        "store_returns_quantitycount",
        "store_returns_quantityave",
        "store_returns_quantitystdev",
        "catalog_sales_quantitycount",
        "catalog_sales_quantityave",
        "catalog_sales_quantitystdev",
    ]

    result["store_sales_quantitycov"] = result["store_sales_quantitystdev"] / result["store_sales_quantityave"]
    result["store_returns_quantitycov"] = result["store_returns_quantitystdev"] / result["store_returns_quantityave"]
    result["catalog_sales_quantitycov"] = result["catalog_sales_quantitystdev"] / result["catalog_sales_quantityave"]

    out_cols = [
        "i_item_id",
        "i_item_desc",
        "s_state",
        "store_sales_quantitycount",
        "store_sales_quantityave",
        "store_sales_quantitystdev",
        "store_sales_quantitycov",
        "store_returns_quantitycount",
        "store_returns_quantityave",
        "store_returns_quantitystdev",
        "store_returns_quantitycov",
        "catalog_sales_quantitycount",
        "catalog_sales_quantityave",
        "catalog_sales_quantitystdev",
        "catalog_sales_quantitycov",
    ]

    result = result[out_cols].sort_values(["i_item_id", "i_item_desc", "s_state"]).head(100)
    return _none_for_null(result, [column for column in result.columns if result[column].dtype in (object, float)])


def q18_expression_impl(ctx: DataFrameContext) -> Any:
    from .rollup_helper import expand_rollup_expression

    params = get_parameters(18)
    year = params.get("year", 2001)
    states = params.get("states", ["ND", "WI", "AL", "NC", "OK", "MS", "TN"])
    cd_gender = params.get("cd_gender", "M")
    cd_education_status = params.get("cd_education_status", "College")
    birth_months = params.get("birth_months", [9, 5, 12, 4, 1, 10])

    catalog_sales, customer_demographics, customer, customer_address, date_dim, item = _tables(
        ctx, "catalog_sales", "customer_demographics", "customer", "customer_address", "date_dim", "item"
    )
    col = ctx.col
    lit = ctx.lit

    date_filtered = date_dim.filter(col("d_year") == lit(year))

    cd1 = customer_demographics.filter(
        (col("cd_gender") == lit(cd_gender)) & (col("cd_education_status") == lit(cd_education_status))
    )

    ca_filtered = customer_address.filter(col("ca_state").is_in(states))

    customer_filtered = customer.filter(col("c_birth_month").is_in(birth_months))

    cs_joined = (
        catalog_sales.join(date_filtered, left_on="cs_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="cs_item_sk", right_on="i_item_sk")
        .join(cd1, left_on="cs_bill_cdemo_sk", right_on="cd_demo_sk")
        .join(customer_filtered, left_on="cs_bill_customer_sk", right_on="c_customer_sk")
        .join(ca_filtered, left_on="c_current_addr_sk", right_on="ca_address_sk")
        .join(
            customer_demographics.rename({"cd_demo_sk": "cd2_demo_sk", "cd_dep_count": "cd2_dep_count"}),
            left_on="c_current_cdemo_sk",
            right_on="cd2_demo_sk",
        )
    )

    agg_exprs = [
        col("cs_quantity").cast_float64().mean().alias("agg1"),
        col("cs_list_price").cast_float64().mean().alias("agg2"),
        col("cs_coupon_amt").cast_float64().mean().alias("agg3"),
        col("cs_sales_price").cast_float64().mean().alias("agg4"),
        col("cs_net_profit").cast_float64().mean().alias("agg5"),
        col("c_birth_year").cast_float64().mean().alias("agg6"),
        col("cd_dep_count").cast_float64().mean().alias("agg7"),
    ]

    group_cols = ["i_item_id", "ca_country", "ca_state", "ca_county"]
    result = expand_rollup_expression(cs_joined, group_cols, agg_exprs, ctx)

    sort_cols = ["ca_country", "ca_state", "ca_county", "i_item_id"]
    out_cols = [*group_cols, "agg1", "agg2", "agg3", "agg4", "agg5", "agg6", "agg7"]
    return result.select(out_cols).sort(sort_cols, nulls_last=True).head(100)


def q18_pandas_impl(ctx: DataFrameContext) -> Any:
    from .rollup_helper import expand_rollup_pandas

    params = get_parameters(18)
    year = params.get("year", 2001)
    states = params.get("states", ["ND", "WI", "AL", "NC", "OK", "MS", "TN"])
    cd_gender = params.get("cd_gender", "M")
    cd_education_status = params.get("cd_education_status", "College")
    birth_months = params.get("birth_months", [9, 5, 12, 4, 1, 10])

    catalog_sales, customer_demographics, customer, customer_address, date_dim, item = _tables(
        ctx, "catalog_sales", "customer_demographics", "customer", "customer_address", "date_dim", "item"
    )

    date_filtered = date_dim[date_dim["d_year"] == year][["d_date_sk"]]

    cd1 = customer_demographics[
        (customer_demographics["cd_gender"] == cd_gender)
        & (customer_demographics["cd_education_status"] == cd_education_status)
    ][["cd_demo_sk", "cd_dep_count"]]

    ca_filtered = customer_address[customer_address["ca_state"].isin(states)]

    customer_filtered = customer[customer["c_birth_month"].isin(birth_months)]

    cs_joined = catalog_sales.merge(date_filtered, left_on="cs_sold_date_sk", right_on="d_date_sk")
    cs_joined = cs_joined.merge(item[["i_item_sk", "i_item_id"]], left_on="cs_item_sk", right_on="i_item_sk")
    cs_joined = cs_joined.merge(cd1, left_on="cs_bill_cdemo_sk", right_on="cd_demo_sk")
    cs_joined = cs_joined.merge(
        customer_filtered[["c_customer_sk", "c_current_cdemo_sk", "c_current_addr_sk", "c_birth_year"]],
        left_on="cs_bill_customer_sk",
        right_on="c_customer_sk",
    )
    cs_joined = cs_joined.merge(
        ca_filtered[["ca_address_sk", "ca_country", "ca_state", "ca_county"]],
        left_on="c_current_addr_sk",
        right_on="ca_address_sk",
    )
    cs_joined = cs_joined.merge(
        customer_demographics[["cd_demo_sk", "cd_dep_count"]].rename(
            columns={"cd_demo_sk": "cd2_demo_sk", "cd_dep_count": "cd2_dep_count"}
        ),
        left_on="c_current_cdemo_sk",
        right_on="cd2_demo_sk",
    )

    agg_dict = {
        "agg1": ("cs_quantity", "mean"),
        "agg2": ("cs_list_price", "mean"),
        "agg3": ("cs_coupon_amt", "mean"),
        "agg4": ("cs_sales_price", "mean"),
        "agg5": ("cs_net_profit", "mean"),
        "agg6": ("c_birth_year", "mean"),
        "agg7": ("cd_dep_count", "mean"),
    }

    result = expand_rollup_pandas(cs_joined, ["i_item_id", "ca_country", "ca_state", "ca_county"], agg_dict, ctx)
    result = _none_for_null(result, list(agg_dict))

    out_cols = [
        "i_item_id",
        "ca_country",
        "ca_state",
        "ca_county",
        "agg1",
        "agg2",
        "agg3",
        "agg4",
        "agg5",
        "agg6",
        "agg7",
    ]
    return (
        result[out_cols]
        .sort_values(
            ["ca_country", "ca_state", "ca_county", "i_item_id"],
            na_position="last",
        )
        .head(100)
    )


_Q29_AGGREGATES = {"sum": "sum", "min": "min", "max": "max", "avg": "mean", "stddev_samp": "std"}


def _q29_aggregate(name: str) -> str:
    try:
        return _Q29_AGGREGATES[name]
    except KeyError:
        raise ValueError(f"Q29 AGG must be one of {sorted(_Q29_AGGREGATES)}, got {name!r}") from None


def q29_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(29)
    year = params.get("year", 1999)
    month = params.get("month", 4)
    agg = _q29_aggregate(params.get("agg", "sum"))

    store_sales, store_returns, catalog_sales, date_dim, store, item = _tables(
        ctx, "store_sales", "store_returns", "catalog_sales", "date_dim", "store", "item"
    )
    col = ctx.col
    lit = ctx.lit

    d1 = date_dim.filter((col("d_moy") == lit(month)) & (col("d_year") == lit(year)))

    d2 = date_dim.filter((col("d_moy") >= lit(month)) & (col("d_moy") <= lit(month + 3)) & (col("d_year") == lit(year)))

    d3 = date_dim.filter(col("d_year").is_in([year, year + 1, year + 2]))

    ss_joined = (
        store_sales.join(d1.select("d_date_sk"), left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="ss_item_sk", right_on="i_item_sk")
        .join(store, left_on="ss_store_sk", right_on="s_store_sk")
    )

    sr_joined = store_returns.join(d2.select("d_date_sk"), left_on="sr_returned_date_sk", right_on="d_date_sk")

    ss_sr = ss_joined.join(
        sr_joined,
        left_on=["ss_customer_sk", "ss_item_sk", "ss_ticket_number"],
        right_on=["sr_customer_sk", "sr_item_sk", "sr_ticket_number"],
    )

    cs_joined = catalog_sales.join(d3.select("d_date_sk"), left_on="cs_sold_date_sk", right_on="d_date_sk")

    result = ss_sr.join(
        cs_joined,
        left_on=["ss_customer_sk", "ss_item_sk"],
        right_on=["cs_bill_customer_sk", "cs_item_sk"],
    )

    return _sort_null_largest_expression(
        ctx,
        result.group_by(["i_item_id", "i_item_desc", "s_store_id", "s_store_name"]).agg(
            [
                (
                    ctx.when(col("ss_quantity").count() > lit(0)).then(col("ss_quantity").sum()).otherwise(lit(None))
                    if agg == "sum"
                    else getattr(col("ss_quantity"), agg)()
                ).alias("store_sales_quantity"),
                (
                    ctx.when(col("sr_return_quantity").count() > lit(0))
                    .then(col("sr_return_quantity").sum())
                    .otherwise(lit(None))
                    if agg == "sum"
                    else getattr(col("sr_return_quantity"), agg)()
                ).alias("store_returns_quantity"),
                (
                    ctx.when(col("cs_quantity").count() > lit(0)).then(col("cs_quantity").sum()).otherwise(lit(None))
                    if agg == "sum"
                    else getattr(col("cs_quantity"), agg)()
                ).alias("catalog_sales_quantity"),
            ]
        ),
        ["i_item_id", "i_item_desc", "s_store_id", "s_store_name"],
    ).head(100)


def q29_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(29)
    year = params.get("year", 1999)
    month = params.get("month", 4)
    agg = _q29_aggregate(params.get("agg", "sum"))

    store_sales, store_returns, catalog_sales, date_dim, store, item = _tables(
        ctx, "store_sales", "store_returns", "catalog_sales", "date_dim", "store", "item"
    )

    d1 = date_dim[(date_dim["d_moy"] == month) & (date_dim["d_year"] == year)][["d_date_sk"]]

    d2 = date_dim[(date_dim["d_moy"] >= month) & (date_dim["d_moy"] <= month + 3) & (date_dim["d_year"] == year)][
        ["d_date_sk"]
    ]

    d3 = date_dim[date_dim["d_year"].isin([year, year + 1, year + 2])][["d_date_sk"]]

    ss_joined = store_sales.merge(d1, left_on="ss_sold_date_sk", right_on="d_date_sk")
    ss_joined = ss_joined.merge(
        item[["i_item_sk", "i_item_id", "i_item_desc"]], left_on="ss_item_sk", right_on="i_item_sk"
    )
    ss_joined = ss_joined.merge(
        store[["s_store_sk", "s_store_id", "s_store_name"]], left_on="ss_store_sk", right_on="s_store_sk"
    )

    sr_joined = store_returns.merge(d2, left_on="sr_returned_date_sk", right_on="d_date_sk")

    ss_sr = ss_joined.merge(
        sr_joined,
        left_on=["ss_customer_sk", "ss_item_sk", "ss_ticket_number"],
        right_on=["sr_customer_sk", "sr_item_sk", "sr_ticket_number"],
    )

    cs_joined = catalog_sales.merge(d3, left_on="cs_sold_date_sk", right_on="d_date_sk")

    result = ss_sr.merge(
        cs_joined,
        left_on=["sr_customer_sk", "sr_item_sk"],
        right_on=["cs_bill_customer_sk", "cs_item_sk"],
    )

    result = _grouped_pandas_aggregates(
        result,
        ["i_item_id", "i_item_desc", "s_store_id", "s_store_name"],
        {
            "store_sales_quantity": ("ss_quantity", agg),
            "store_returns_quantity": ("sr_return_quantity", agg),
            "catalog_sales_quantity": ("cs_quantity", agg),
        },
        dropna=False,
    )
    result = _none_for_null(result, list(result.columns))

    return result.sort_values(["i_item_id", "i_item_desc", "s_store_id", "s_store_name"]).head(100)


def q27_expression_impl(ctx: DataFrameContext) -> Any:
    from .rollup_helper import expand_rollup_expression

    params = get_parameters(27)
    year = params.get("year", 1998)
    gender = params.get("gender", "F")
    marital_status = params.get("marital_status", "W")
    education = params.get("education", "Primary")
    states = params.get("states", ["TN"])

    store_sales, customer_demographics, date_dim, store, item = _tables(
        ctx, "store_sales", "customer_demographics", "date_dim", "store", "item"
    )
    col = ctx.col
    lit = ctx.lit

    date_filtered = date_dim.filter(col("d_year") == lit(year))

    cd_filtered = customer_demographics.filter(
        (col("cd_gender") == lit(gender))
        & (col("cd_marital_status") == lit(marital_status))
        & (col("cd_education_status") == lit(education))
    )

    store_filtered = store.filter(col("s_state").is_in(states))

    ss_joined = (
        store_sales.join(date_filtered, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="ss_item_sk", right_on="i_item_sk")
        .join(store_filtered, left_on="ss_store_sk", right_on="s_store_sk")
        .join(cd_filtered, left_on="ss_cdemo_sk", right_on="cd_demo_sk")
    )

    agg_exprs = [
        col("ss_quantity").mean().alias("agg1"),
        col("ss_list_price").mean().alias("agg2"),
        col("ss_coupon_amt").mean().alias("agg3"),
        col("ss_sales_price").mean().alias("agg4"),
    ]

    group_cols = ["i_item_id", "s_state"]
    result = expand_rollup_expression(ss_joined, group_cols, agg_exprs, ctx)

    return (
        result.with_columns(ctx.when(col("grouping_id") == lit(0)).then(lit(0)).otherwise(lit(1)).alias("g_state"))
        .select(["i_item_id", "s_state", "g_state", "agg1", "agg2", "agg3", "agg4"])
        .sort(["i_item_id", "s_state"], nulls_last=True)
        .head(100)
    )


def q27_pandas_impl(ctx: DataFrameContext) -> Any:
    from .rollup_helper import expand_rollup_pandas

    params = get_parameters(27)
    year = params.get("year", 1998)
    gender = params.get("gender", "F")
    marital_status = params.get("marital_status", "W")
    education = params.get("education", "Primary")
    states = params.get("states", ["TN"])

    store_sales, customer_demographics, date_dim, store, item = _tables(
        ctx, "store_sales", "customer_demographics", "date_dim", "store", "item"
    )

    date_filtered = date_dim[date_dim["d_year"] == year][["d_date_sk"]]

    cd_filtered = customer_demographics[
        (customer_demographics["cd_gender"] == gender)
        & (customer_demographics["cd_marital_status"] == marital_status)
        & (customer_demographics["cd_education_status"] == education)
    ][["cd_demo_sk"]]

    store_filtered = store[store["s_state"].isin(states)][["s_store_sk", "s_state"]]

    ss_joined = store_sales.merge(date_filtered, left_on="ss_sold_date_sk", right_on="d_date_sk")
    ss_joined = ss_joined.merge(item[["i_item_sk", "i_item_id"]], left_on="ss_item_sk", right_on="i_item_sk")
    ss_joined = ss_joined.merge(store_filtered, left_on="ss_store_sk", right_on="s_store_sk")
    ss_joined = ss_joined.merge(cd_filtered, left_on="ss_cdemo_sk", right_on="cd_demo_sk")

    agg_dict = {
        "agg1": ("ss_quantity", "mean"),
        "agg2": ("ss_list_price", "mean"),
        "agg3": ("ss_coupon_amt", "mean"),
        "agg4": ("ss_sales_price", "mean"),
    }

    result = expand_rollup_pandas(ss_joined, ["i_item_id", "s_state"], agg_dict, ctx)
    result = _none_for_null(result, list(agg_dict))

    result["g_state"] = result["grouping_id"] & 1
    out_cols = ["i_item_id", "s_state", "g_state", "agg1", "agg2", "agg3", "agg4"]
    return result[out_cols].sort_values(["i_item_id", "s_state"], na_position="last").head(100)


def q93_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(93)
    reason = params.get("reason", "reason 28")

    store_sales, store_returns = _tables(ctx, "store_sales", "store_returns")
    reason_table = ctx.get_table("reason")
    col = ctx.col
    lit = ctx.lit

    reason_filtered = reason_table.filter(col("r_reason_desc") == lit(reason))

    ss_with_sr = store_sales.join(
        store_returns,
        left_on=["ss_item_sk", "ss_ticket_number"],
        right_on=["sr_item_sk", "sr_ticket_number"],
        how="left",
    )

    ss_with_reason = ss_with_sr.join(reason_filtered, left_on="sr_reason_sk", right_on="r_reason_sk")

    result = ss_with_reason.with_columns(
        ctx.when(col("sr_return_quantity").is_not_null())
        .then((col("ss_quantity") - col("sr_return_quantity")) * col("ss_sales_price"))
        .otherwise(col("ss_quantity") * col("ss_sales_price"))
        .alias("act_sales")
    )

    return _sort_null_largest_expression(
        ctx,
        result.group_by("ss_customer_sk").agg(
            ctx.when(col("act_sales").count() > lit(0))
            .then(col("act_sales").sum())
            .otherwise(lit(None))
            .alias("sumsales")
        ),
        ["sumsales", "ss_customer_sk"],
    ).head(100)


def q93_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(93)
    reason = params.get("reason", "reason 28")

    store_sales, store_returns = _tables(ctx, "store_sales", "store_returns")
    reason_table = ctx.get_table("reason")

    reason_filtered = reason_table[reason_table["r_reason_desc"] == reason]

    ss_with_sr = store_sales.merge(
        store_returns[["sr_item_sk", "sr_ticket_number", "sr_return_quantity", "sr_reason_sk"]].dropna(
            subset=["sr_item_sk", "sr_ticket_number"]
        ),
        left_on=["ss_item_sk", "ss_ticket_number"],
        right_on=["sr_item_sk", "sr_ticket_number"],
        how="left",
    )

    ss_with_reason = ss_with_sr.merge(reason_filtered[["r_reason_sk"]], left_on="sr_reason_sk", right_on="r_reason_sk")

    ss_with_reason["act_sales"] = ss_with_reason.apply(
        lambda row: (
            (row["ss_quantity"] - row["sr_return_quantity"]) * row["ss_sales_price"]
            if row["sr_return_quantity"] is not None
            and not (
                isinstance(row["sr_return_quantity"], float) and row["sr_return_quantity"] != row["sr_return_quantity"]
            )
            else row["ss_quantity"] * row["ss_sales_price"]
        ),
        axis=1,
    )

    result = _grouped_pandas_aggregates(
        ss_with_reason, "ss_customer_sk", {"sumsales": ("act_sales", "sum")}, dropna=False
    )
    result = _none_for_null(result, list(result.columns))

    return result.sort_values(["sumsales", "ss_customer_sk"]).head(100)


def q80_expression_impl(ctx: DataFrameContext) -> Any:
    col = ctx.col
    lit = ctx.lit
    date_filtered = _date_window_expression(ctx, 80, "1998-08-04")
    item_filtered = ctx.get_table("item").filter(col("i_current_price") > lit(50))
    promo_filtered = ctx.get_table("promotion").filter(col("p_channel_tv") == lit("N"))
    combined = ctx.concat(
        [
            _q80_channel_expression(ctx, date_filtered, item_filtered, promo_filtered, spec)
            for spec in _Q80_CHANNEL_SPECS
        ]
    )
    return _sales_returns_rollup_expression(ctx, combined)


def q80_pandas_impl(ctx: DataFrameContext) -> Any:
    date_filtered = _date_window_pandas(ctx, 80, "1998-08-04")
    item, promotion = _tables(ctx, "item", "promotion")
    item_filtered = item[item["i_current_price"] > 50][["i_item_sk"]]
    promo_filtered = promotion[promotion["p_channel_tv"] == "N"][["p_promo_sk"]]
    combined = ctx.concat(
        [_q80_channel_pandas(ctx, date_filtered, item_filtered, promo_filtered, spec) for spec in _Q80_CHANNEL_SPECS]
    )
    return _sales_returns_rollup_pandas(ctx, combined)


def q77_expression_impl(ctx: DataFrameContext) -> Any:
    date_filtered = _date_window_expression(ctx, 77, "1998-08-04")
    combined = ctx.concat(
        [
            _q77_expression_channel(ctx, date_filtered, channel, sales, returns, how)
            for channel, sales, returns, how in _Q77_EXPRESSION_CHANNEL_SPECS
        ]
    )
    return _sales_returns_rollup_expression(ctx, combined)


def q77_pandas_impl(ctx: DataFrameContext) -> Any:
    date_filtered = _date_window_pandas(ctx, 77, "1998-08-04")
    combined = ctx.concat(
        [
            _q77_pandas_channel(ctx, date_filtered, channel, sales, returns, how)
            for channel, sales, returns, how in _Q77_PANDAS_CHANNEL_SPECS
        ]
    )
    return _sales_returns_rollup_pandas(ctx, combined)


def q58_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(58)
    sales_date = datetime.strptime(params.get("sales_date", "2000-01-03"), "%Y-%m-%d").date()
    item, date_dim = _tables(ctx, "item", "date_dim")
    col = ctx.col
    lit = ctx.lit

    week_seq_df = date_dim.filter(col("d_date") == lit(sales_date)).select("d_week_seq")
    dates_in_week = date_dim.join(week_seq_df, on="d_week_seq").select("d_date_sk")

    def channel(table: str, item_key: str, date_key: str, value_col: str, alias: str) -> Any:
        return (
            ctx.get_table(table)
            .join(item, left_on=item_key, right_on="i_item_sk")
            .join(dates_in_week, left_on=date_key, right_on="d_date_sk")
            .group_by("i_item_id")
            .agg(col(value_col).sum().alias(alias))
        )

    result = (
        channel("store_sales", "ss_item_sk", "ss_sold_date_sk", "ss_ext_sales_price", "ss_item_rev")
        .join(
            channel("catalog_sales", "cs_item_sk", "cs_sold_date_sk", "cs_ext_sales_price", "cs_item_rev"),
            on="i_item_id",
        )
        .join(
            channel("web_sales", "ws_item_sk", "ws_sold_date_sk", "ws_ext_sales_price", "ws_item_rev"), on="i_item_id"
        )
    )
    return (
        _q58_balance_expression(ctx, result)
        .with_columns(((col("ss_item_rev") + col("cs_item_rev") + col("ws_item_rev")) / lit(3)).alias("average"))
        .with_columns(
            [
                (col("ss_item_rev") / col("average") * lit(100)).alias("ss_dev"),
                (col("cs_item_rev") / col("average") * lit(100)).alias("cs_dev"),
                (col("ws_item_rev") / col("average") * lit(100)).alias("ws_dev"),
            ]
        )
        .select(
            [
                col("i_item_id").alias("item_id"),
                "ss_item_rev",
                "ss_dev",
                "cs_item_rev",
                "cs_dev",
                "ws_item_rev",
                "ws_dev",
                "average",
            ]
        )
        .sort(["item_id", "ss_item_rev"])
        .limit(100)
    )


def q58_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(58)
    sales_date = datetime.strptime(params.get("sales_date", "2000-01-03"), "%Y-%m-%d").date()

    item, date_dim = _tables(ctx, "item", "date_dim")

    week_seq = date_dim[date_dim["d_date"] == sales_date]["d_week_seq"].iloc[0]
    dates_in_week = date_dim[date_dim["d_week_seq"] == week_seq][["d_date_sk"]]

    def channel(table: str, item_key: str, date_key: str, value_col: str, alias: str) -> Any:
        joined = ctx.get_table(table).merge(item, left_on=item_key, right_on="i_item_sk")
        joined = joined.merge(dates_in_week, left_on=date_key, right_on="d_date_sk")
        return joined.groupby("i_item_id", as_index=False).agg(**{alias: (value_col, "sum")})

    result = channel("store_sales", "ss_item_sk", "ss_sold_date_sk", "ss_ext_sales_price", "ss_item_rev")
    result = result.merge(
        channel("catalog_sales", "cs_item_sk", "cs_sold_date_sk", "cs_ext_sales_price", "cs_item_rev"), on="i_item_id"
    )
    result = result.merge(
        channel("web_sales", "ws_item_sk", "ws_sold_date_sk", "ws_ext_sales_price", "ws_item_rev"), on="i_item_id"
    )
    result = _q58_balance_pandas(result)
    result["average"] = (result["ss_item_rev"] + result["cs_item_rev"] + result["ws_item_rev"]) / 3
    result["ss_dev"] = result["ss_item_rev"] / result["average"] * 100
    result["cs_dev"] = result["cs_item_rev"] / result["average"] * 100
    result["ws_dev"] = result["ws_item_rev"] / result["average"] * 100

    result = result.rename(columns={"i_item_id": "item_id"})
    result = result[["item_id", "ss_item_rev", "ss_dev", "cs_item_rev", "cs_dev", "ws_item_rev", "ws_dev", "average"]]

    return result.sort_values(["item_id", "ss_item_rev"]).head(100)


def q54_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(54)
    year = params.get("year", 1998)
    month = params.get("month", 12)
    category = params.get("category", "Women")
    item_class = params.get("class", "maternity")

    catalog_sales, web_sales, store_sales, customer, customer_address, store, item, date_dim = _tables(
        ctx, "catalog_sales", "web_sales", "store_sales", "customer", "customer_address", "store", "item", "date_dim"
    )
    col = ctx.col
    lit = ctx.lit

    item_filtered = item.filter((col("i_category") == lit(category)) & (col("i_class") == lit(item_class)))

    date_filtered = date_dim.filter((col("d_year") == lit(year)) & (col("d_moy") == lit(month)))

    cs_sales = catalog_sales.select(
        [
            col("cs_sold_date_sk").alias("sold_date_sk"),
            col("cs_bill_customer_sk").alias("customer_sk"),
            col("cs_item_sk").alias("item_sk"),
        ]
    )

    ws_sales = web_sales.select(
        [
            col("ws_sold_date_sk").alias("sold_date_sk"),
            col("ws_bill_customer_sk").alias("customer_sk"),
            col("ws_item_sk").alias("item_sk"),
        ]
    )

    cs_or_ws = ctx.concat([cs_sales, ws_sales])

    my_customers = (
        cs_or_ws.join(item_filtered, left_on="item_sk", right_on="i_item_sk")
        .join(date_filtered, left_on="sold_date_sk", right_on="d_date_sk")
        .join(customer, left_on="customer_sk", right_on="c_customer_sk")
        .select([col("customer_sk").alias("c_customer_sk"), "c_current_addr_sk"])
        .unique()
    )

    month_seq_df = (
        date_dim.filter((col("d_year") == lit(year)) & (col("d_moy") == lit(month))).select("d_month_seq").unique()
    )

    following_dates = (
        date_dim.join(month_seq_df, how="cross")
        .filter(
            (col("d_month_seq") >= col("d_month_seq_right") + lit(1))
            & (col("d_month_seq") <= col("d_month_seq_right") + lit(3))
        )
        .select("d_date_sk")
    )

    my_revenue = (
        my_customers.join(customer_address, left_on="c_current_addr_sk", right_on="ca_address_sk")
        .join(store, left_on=["ca_county", "ca_state"], right_on=["s_county", "s_state"])
        .join(store_sales, left_on="c_customer_sk", right_on="ss_customer_sk")
        .join(following_dates, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .group_by("c_customer_sk")
        .agg(_sum_or_null_expression(ctx, col("ss_ext_sales_price")).alias("revenue"))
    )

    segments = my_revenue.with_columns((col("revenue") / lit(50)).round(0).cast_int64().alias("segment"))

    return _sort_null_largest_expression(
        ctx,
        segments.group_by("segment")
        .agg(ctx.len().alias("num_customers"))
        .with_columns((col("segment") * lit(50)).alias("segment_base"))
        .select(["segment", "num_customers", "segment_base"]),
        ["segment", "num_customers"],
    ).limit(100)


def q54_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(54)
    year = params.get("year", 1998)
    month = params.get("month", 12)
    category = params.get("category", "Women")
    item_class = params.get("class", "maternity")

    catalog_sales, web_sales, store_sales, customer, customer_address, store, item, date_dim = _tables(
        ctx, "catalog_sales", "web_sales", "store_sales", "customer", "customer_address", "store", "item", "date_dim"
    )

    item_filtered = item[(item["i_category"] == category) & (item["i_class"] == item_class)]

    date_filtered = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] == month)]

    cs_sales = catalog_sales[["cs_sold_date_sk", "cs_bill_customer_sk", "cs_item_sk"]].rename(
        columns={"cs_sold_date_sk": "sold_date_sk", "cs_bill_customer_sk": "customer_sk", "cs_item_sk": "item_sk"}
    )
    ws_sales = web_sales[["ws_sold_date_sk", "ws_bill_customer_sk", "ws_item_sk"]].rename(
        columns={"ws_sold_date_sk": "sold_date_sk", "ws_bill_customer_sk": "customer_sk", "ws_item_sk": "item_sk"}
    )
    cs_or_ws = ctx.concat([cs_sales, ws_sales])

    merged = cs_or_ws.merge(item_filtered[["i_item_sk"]], left_on="item_sk", right_on="i_item_sk")
    merged = merged.merge(date_filtered[["d_date_sk"]], left_on="sold_date_sk", right_on="d_date_sk")
    merged = merged.merge(
        customer[["c_customer_sk", "c_current_addr_sk"]], left_on="customer_sk", right_on="c_customer_sk"
    )
    my_customers = merged[["c_customer_sk", "c_current_addr_sk"]].drop_duplicates()

    month_seq = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] == month)]["d_month_seq"].iloc[0]

    following_dates = date_dim[(date_dim["d_month_seq"] >= month_seq + 1) & (date_dim["d_month_seq"] <= month_seq + 3)][
        ["d_date_sk"]
    ]

    my_revenue = my_customers.merge(
        customer_address[["ca_address_sk", "ca_county", "ca_state"]],
        left_on="c_current_addr_sk",
        right_on="ca_address_sk",
    )
    my_revenue = my_revenue.merge(
        store[["s_county", "s_state"]], left_on=["ca_county", "ca_state"], right_on=["s_county", "s_state"]
    )
    my_revenue = my_revenue.merge(
        store_sales[["ss_customer_sk", "ss_sold_date_sk", "ss_ext_sales_price"]],
        left_on="c_customer_sk",
        right_on="ss_customer_sk",
    )
    my_revenue = my_revenue.merge(following_dates, left_on="ss_sold_date_sk", right_on="d_date_sk")

    revenue_agg = _grouped_pandas_aggregates(my_revenue, "c_customer_sk", {"revenue": ("ss_ext_sales_price", "sum")})

    revenue_agg["segment"] = (revenue_agg["revenue"] / 50).round().astype("Int64")

    result = _grouped_pandas_aggregates(
        revenue_agg, "segment", {"num_customers": ("c_customer_sk", "count")}, dropna=False
    )
    result["segment_base"] = result["segment"] * 50

    result = result[["segment", "num_customers", "segment_base"]]
    result = result.sort_values(["segment", "num_customers"]).head(100)
    return _none_for_null(result, list(result.columns))


def q44_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(44)
    store_sk = params.get("store_sk", 4)
    null_col = params.get("null_col", "ss_addr_sk")

    col = ctx.col
    lit = ctx.lit

    store_sales, item = _tables(ctx, "store_sales", "item")

    ss_store = store_sales.filter(col("ss_store_sk") == lit(store_sk))

    threshold = 0.9

    item_avg = ss_store.group_by("ss_item_sk").agg(col("ss_net_profit").mean().alias("rank_col"))

    ss_null = store_sales.filter((col("ss_store_sk") == lit(store_sk)) & col(null_col).is_null())
    store_baseline = ss_null.select(col("ss_net_profit").mean().alias("baseline"))

    item_with_baseline = item_avg.join(store_baseline, how="cross")
    qualified = item_with_baseline.filter(col("rank_col") > (lit(threshold) * col("baseline")))

    ascending = (
        qualified.with_columns(col("rank_col").rank(method="min", descending=False).alias("rnk"))
        .filter(col("rnk") <= 10)
        .select(["ss_item_sk", "rnk"])
        .rename({"ss_item_sk": "item_sk_asc", "rnk": "rnk_asc"})
    )

    descending = (
        qualified.with_columns(col("rank_col").rank(method="min", descending=True).alias("rnk"))
        .filter(col("rnk") <= 10)
        .select(["ss_item_sk", "rnk"])
        .rename({"ss_item_sk": "item_sk_desc", "rnk": "rnk_desc"})
    )

    result = ascending.join(descending, left_on="rnk_asc", right_on="rnk_desc")

    result = (
        result.join(item, left_on="item_sk_asc", right_on="i_item_sk")
        .rename({"i_product_name": "best_performing"})
        .select(["rnk_asc", "best_performing", "item_sk_desc"])
    )

    return (
        result.join(item, left_on="item_sk_desc", right_on="i_item_sk")
        .rename({"i_product_name": "worst_performing"})
        .select(["rnk_asc", "best_performing", "worst_performing"])
        .rename({"rnk_asc": "rnk"})
        .sort("rnk")
    )


def q44_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(44)
    store_sk = params.get("store_sk", 4)
    null_col = params.get("null_col", "ss_addr_sk")

    store_sales, item = _tables(ctx, "store_sales", "item")

    ss_store = store_sales[store_sales["ss_store_sk"] == store_sk]

    ss_null = ss_store[ss_store[null_col].isna()]
    baseline = ss_null["ss_net_profit"].mean()
    threshold = 0.9 * baseline

    item_avg = ss_store.groupby("ss_item_sk", as_index=False).agg(rank_col=("ss_net_profit", "mean"))

    qualified = item_avg[item_avg["rank_col"] > threshold].copy()

    qualified["rnk_asc"] = qualified["rank_col"].rank(method="min", ascending=True)
    ascending = qualified[qualified["rnk_asc"] <= 10][["ss_item_sk", "rnk_asc"]].copy()
    ascending = ascending.rename(columns={"ss_item_sk": "item_sk_asc"})

    qualified["rnk_desc"] = qualified["rank_col"].rank(method="min", ascending=False)
    descending = qualified[qualified["rnk_desc"] <= 10][["ss_item_sk", "rnk_desc"]].copy()
    descending = descending.rename(columns={"ss_item_sk": "item_sk_desc"})

    result = ascending.merge(descending, left_on="rnk_asc", right_on="rnk_desc")

    result = result.merge(
        item[["i_item_sk", "i_product_name"]],
        left_on="item_sk_asc",
        right_on="i_item_sk",
    )
    result = result.rename(columns={"i_product_name": "best_performing"})

    result = result.merge(
        item[["i_item_sk", "i_product_name"]],
        left_on="item_sk_desc",
        right_on="i_item_sk",
        suffixes=("", "_2"),
    )
    result = result.rename(columns={"i_product_name": "worst_performing"})

    result = result[["rnk_asc", "best_performing", "worst_performing"]].copy()
    result = result.rename(columns={"rnk_asc": "rnk"})
    return _none_for_null(result.sort_values("rnk"), ["best_performing", "worst_performing"])


_Q59_DAYS = (
    ("sun", "Sunday"),
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
    ("sat", "Saturday"),
)
_Q59_RESULT_COLS = ["s_store_name1", "s_store_id1", "d_week_seq1"] + [f"{prefix}_ratio" for prefix, _ in _Q59_DAYS]


def _q59_day_sales_aggs(ctx: DataFrameContext) -> list[Any]:
    col, lit = ctx.col, ctx.lit
    aggregations = []
    for prefix, day in _Q59_DAYS:
        day_sales = ctx.when(col("d_day_name") == lit(day)).then(col("ss_sales_price")).otherwise(lit(None))
        aggregations.append(
            ctx.when(day_sales.count() > lit(0)).then(day_sales.sum()).otherwise(lit(None)).alias(f"{prefix}_sales")
        )
    return aggregations


def _q59_rename_map(suffix: str, *, include_name: bool) -> dict[str, str]:
    mapping = {"s_store_id": f"s_store_id{suffix}", "d_week_seq": f"d_week_seq{suffix}"}
    mapping.update({f"{prefix}_sales": f"{prefix}_sales{suffix}" for prefix, _ in _Q59_DAYS})
    if include_name:
        mapping["s_store_name"] = f"s_store_name{suffix}"
    return mapping


def _q59_ratio_exprs(ctx: DataFrameContext) -> list[Any]:
    col = ctx.col
    return [(col(f"{prefix}_sales1") / col(f"{prefix}_sales2")).alias(f"{prefix}_ratio") for prefix, _ in _Q59_DAYS]


def _q59_select_exprs(ctx: DataFrameContext, suffix: str, *, include_name: bool) -> list[Any]:
    col = ctx.col
    names = ["s_store_id", "d_week_seq"] + [f"{prefix}_sales" for prefix, _ in _Q59_DAYS]
    if include_name:
        names.insert(0, "s_store_name")
    return [col(name).alias(_q59_rename_map(suffix, include_name=include_name)[name]) for name in names]


def _q59_period_expression(
    ctx: DataFrameContext, wss: Any, store: Any, date_dim: Any, dms: int, offset: int, suffix: str
) -> Any:
    col, lit = ctx.col, ctx.lit
    date_filtered = date_dim.filter(
        (col("d_month_seq") >= lit(dms + offset)) & (col("d_month_seq") <= lit(dms + offset + 11))
    )
    return (
        wss.join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .join(date_filtered, on="d_week_seq")
        .select(_q59_select_exprs(ctx, suffix, include_name=suffix == "1"))
    )


def q59_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(59)
    dms = params.get("d_month_seq", 1212)

    col = ctx.col
    lit = ctx.lit

    store_sales, date_dim, store = _tables(ctx, "store_sales", "date_dim", "store")

    ss_with_date = store_sales.join(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")

    wss = ss_with_date.group_by(["d_week_seq", "ss_store_sk"]).agg(_q59_day_sales_aggs(ctx))
    y = _q59_period_expression(ctx, wss, store, date_dim, dms, 0, "1")
    x = _q59_period_expression(ctx, wss, store, date_dim, dms, 12, "2")

    return _sort_null_largest_expression(
        ctx,
        y.join(
            x,
            left_on=["s_store_id1", (col("d_week_seq1") + lit(52))],
            right_on=["s_store_id2", "d_week_seq2"],
        )
        .with_columns(_q59_ratio_exprs(ctx))
        .select(_Q59_RESULT_COLS),
        ["s_store_name1", "s_store_id1", "d_week_seq1"],
    ).head(100)


def q59_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(59)
    dms = params.get("d_month_seq", 1212)

    store_sales, date_dim, store = _tables(ctx, "store_sales", "date_dim", "store")

    ss_with_date = store_sales.merge(date_dim, left_on="ss_sold_date_sk", right_on="d_date_sk")

    for prefix, day in _Q59_DAYS:
        ss_with_date[f"{prefix}_sales"] = ss_with_date["ss_sales_price"].where(ss_with_date["d_day_name"] == day)

    wss = ss_with_date.groupby(["d_week_seq", "ss_store_sk"], as_index=False).agg(
        {f"{prefix}_sales": (lambda values: values.sum(min_count=1)) for prefix, _ in _Q59_DAYS}
    )

    wss = wss.merge(store, left_on="ss_store_sk", right_on="s_store_sk")

    dates_1 = date_dim[(date_dim["d_month_seq"] >= dms) & (date_dim["d_month_seq"] <= dms + 11)][["d_week_seq"]]
    y = wss.merge(dates_1, on="d_week_seq", how="inner").copy()
    y = y.rename(columns=_q59_rename_map("1", include_name=True))

    dates_2 = date_dim[(date_dim["d_month_seq"] >= dms + 12) & (date_dim["d_month_seq"] <= dms + 23)][["d_week_seq"]]
    x = wss.merge(dates_2, on="d_week_seq", how="inner").copy()
    x = x.rename(columns=_q59_rename_map("2", include_name=False))

    y["join_key"] = y["d_week_seq1"] + 52

    result = y.merge(
        x[["s_store_id2", "d_week_seq2"] + [f"{prefix}_sales2" for prefix, _ in _Q59_DAYS]],
        left_on=["s_store_id1", "join_key"],
        right_on=["s_store_id2", "d_week_seq2"],
    )

    for prefix, _ in _Q59_DAYS:
        result[f"{prefix}_ratio"] = result[f"{prefix}_sales1"] / result[f"{prefix}_sales2"]

    result = result[_Q59_RESULT_COLS]
    return (
        result.sort_values(["s_store_name1", "s_store_id1", "d_week_seq1"])
        .head(100)
        .astype(object)
        .where(lambda frame: frame.notnull(), None)
    )


def q61_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(61)
    year = params.get("year", 1998)
    month = params.get("month", 11)
    gmt_offset = params.get("gmt_offset", -5.0)
    category = params.get("category", "Jewelry")

    col = ctx.col
    lit = ctx.lit

    store_sales, store, promotion, date_dim, customer, customer_address, item = _tables(
        ctx, "store_sales", "store", "promotion", "date_dim", "customer", "customer_address", "item"
    )

    date_filtered = date_dim.filter((col("d_year") == lit(year)) & (col("d_moy") == lit(month)))
    store_filtered = store.filter(col("s_gmt_offset") == lit(gmt_offset))
    ca_filtered = customer_address.filter(col("ca_gmt_offset") == lit(gmt_offset))
    item_filtered = item.filter(col("i_category") == lit(category))
    promo_filtered = promotion.filter(
        (col("p_channel_dmail") == lit("Y")) | (col("p_channel_email") == lit("Y")) | (col("p_channel_tv") == lit("Y"))
    )

    base = (
        store_sales.join(date_filtered, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(store_filtered, left_on="ss_store_sk", right_on="s_store_sk")
        .join(customer, left_on="ss_customer_sk", right_on="c_customer_sk")
        .join(ca_filtered, left_on="c_current_addr_sk", right_on="ca_address_sk")
        .join(item_filtered, left_on="ss_item_sk", right_on="i_item_sk")
    )

    total = base.select(
        ctx.when(col("ss_ext_sales_price").count() > lit(0))
        .then(col("ss_ext_sales_price").sum())
        .otherwise(lit(None))
        .alias("total")
    )

    promo_base = base.join(promo_filtered, left_on="ss_promo_sk", right_on="p_promo_sk")
    promo = promo_base.select(
        ctx.when(col("ss_ext_sales_price").count() > lit(0))
        .then(col("ss_ext_sales_price").sum())
        .otherwise(lit(None))
        .alias("promotions")
    )

    return promo.join(total, how="cross").with_columns(
        ((col("promotions") / col("total")) * lit(100)).alias("promo_pct")
    )


def q61_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(61)
    year = params.get("year", 1998)
    month = params.get("month", 11)
    gmt_offset = params.get("gmt_offset", -5.0)
    category = params.get("category", "Jewelry")

    store_sales, store, promotion, date_dim, customer, customer_address, item = _tables(
        ctx, "store_sales", "store", "promotion", "date_dim", "customer", "customer_address", "item"
    )

    date_filtered = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] == month)]
    store_filtered = store[store["s_gmt_offset"] == gmt_offset]
    ca_filtered = customer_address[customer_address["ca_gmt_offset"] == gmt_offset]
    item_filtered = item[item["i_category"] == category]
    promo_filtered = promotion[
        (promotion["p_channel_dmail"] == "Y")
        | (promotion["p_channel_email"] == "Y")
        | (promotion["p_channel_tv"] == "Y")
    ]

    base = store_sales.merge(date_filtered[["d_date_sk"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    base = base.merge(store_filtered[["s_store_sk"]], left_on="ss_store_sk", right_on="s_store_sk")
    base = base.merge(
        customer[["c_customer_sk", "c_current_addr_sk"]], left_on="ss_customer_sk", right_on="c_customer_sk"
    )
    base = base.merge(ca_filtered[["ca_address_sk"]], left_on="c_current_addr_sk", right_on="ca_address_sk")
    base = base.merge(item_filtered[["i_item_sk"]], left_on="ss_item_sk", right_on="i_item_sk")

    total = base["ss_ext_sales_price"].sum(min_count=1)

    promo_base = base.merge(promo_filtered[["p_promo_sk"]], left_on="ss_promo_sk", right_on="p_promo_sk")
    promotions = promo_base["ss_ext_sales_price"].sum(min_count=1)

    import pandas as pd

    return (
        pd.DataFrame(
            {
                "promotions": [promotions],
                "total": [total],
                "promo_pct": [
                    promotions / total * 100 if promotions == promotions and total == total and total != 0 else None
                ],
            }
        )
        .astype(object)
        .where(lambda frame: frame.notnull(), None)
    )


_Q85_PRICE_BANDS = ((100.00, 150.00), (50.00, 100.00), (150.00, 200.00))
_Q85_PROFIT_BANDS = ((100, 200), (150, 300), (50, 250))


def _q85_groups(params: Any) -> tuple[list[str], list[str], list[list[str]]]:
    marital = list(params.get("marital_statuses", ["M", "D", "U"]))
    education = list(params.get("education_statuses", ["4 yr Degree", "Primary", "Advanced Degree"]))
    states = list(params.get("states", ["KY", "GA", "NM", "MT", "OR", "IN", "WI", "MO", "WV"]))
    if len(marital) != 3 or len(education) != 3 or len(states) != 9:
        raise ValueError(
            f"Q85 needs 3 marital statuses, 3 education statuses and 9 states, got {marital!r}, {education!r}, {states!r}"
        )
    return marital, education, [states[0:3], states[3:6], states[6:9]]


def q85_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(85)
    year = params.get("year", 1998)
    marital, education, states = _q85_groups(params)

    col = ctx.col
    lit = ctx.lit

    web_sales, web_returns, web_page, customer_demographics, customer_address, date_dim, reason = _tables(
        ctx, "web_sales", "web_returns", "web_page", "customer_demographics", "customer_address", "date_dim", "reason"
    )

    date_filtered = date_dim.filter(col("d_year") == lit(year))

    ws_wr = web_sales.join(
        web_returns,
        left_on=["ws_item_sk", "ws_order_number"],
        right_on=["wr_item_sk", "wr_order_number"],
    )

    ws_wr = ws_wr.join(web_page, left_on="ws_web_page_sk", right_on="wp_web_page_sk")

    ws_wr = ws_wr.join(date_filtered, left_on="ws_sold_date_sk", right_on="d_date_sk")

    cd_columns = customer_demographics.columns
    cd1 = customer_demographics.rename({c: f"cd1_{c}" for c in cd_columns})
    ws_wr = ws_wr.join(cd1, left_on="wr_refunded_cdemo_sk", right_on="cd1_cd_demo_sk")

    cd2 = customer_demographics.rename({c: f"cd2_{c}" for c in cd_columns})
    ws_wr = ws_wr.join(cd2, left_on="wr_returning_cdemo_sk", right_on="cd2_cd_demo_sk")

    ws_wr = ws_wr.join(customer_address, left_on="wr_refunded_addr_sk", right_on="ca_address_sk")

    ws_wr = ws_wr.join(reason, left_on="wr_reason_sk", right_on="r_reason_sk")

    demo_filter = None
    for status, degree, (low, high) in zip(marital, education, _Q85_PRICE_BANDS):
        group = (
            (col("cd1_cd_marital_status") == lit(status))
            & (col("cd1_cd_marital_status") == col("cd2_cd_marital_status"))
            & (col("cd1_cd_education_status") == lit(degree))
            & (col("cd1_cd_education_status") == col("cd2_cd_education_status"))
            & col("ws_sales_price").is_between(low, high)
        )
        demo_filter = group if demo_filter is None else demo_filter | group
    state_filter = None
    for group_states, (low, high) in zip(states, _Q85_PROFIT_BANDS):
        group = col("ca_state").is_in(group_states) & col("ws_net_profit").is_between(low, high)
        state_filter = group if state_filter is None else state_filter | group
    country_filter = (col("ca_country") == lit("United States")) & state_filter

    filtered = ws_wr.filter(demo_filter & country_filter)

    return _sort_null_largest_expression(
        ctx,
        filtered.group_by("r_reason_desc")
        .agg(
            [
                ctx.mean("ws_quantity").alias("avg_quantity"),
                ctx.mean("wr_refunded_cash").alias("avg_refunded_cash"),
                ctx.mean("wr_fee").alias("avg_fee"),
            ]
        )
        .with_columns(col("r_reason_desc").str.slice(0, 20).alias("reason_desc_short"))
        .select(["reason_desc_short", "avg_quantity", "avg_refunded_cash", "avg_fee"]),
        ["reason_desc_short", "avg_quantity", "avg_refunded_cash", "avg_fee"],
    ).head(100)


def q85_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(85)
    year = params.get("year", 1998)
    marital, education, states = _q85_groups(params)

    web_sales, web_returns, web_page, customer_demographics, customer_address, date_dim, reason = _tables(
        ctx, "web_sales", "web_returns", "web_page", "customer_demographics", "customer_address", "date_dim", "reason"
    )

    date_filtered = date_dim[date_dim["d_year"] == year]

    ws_wr = web_sales.merge(
        web_returns,
        left_on=["ws_item_sk", "ws_order_number"],
        right_on=["wr_item_sk", "wr_order_number"],
    )

    ws_wr = ws_wr.merge(web_page, left_on="ws_web_page_sk", right_on="wp_web_page_sk")

    ws_wr = ws_wr.merge(date_filtered[["d_date_sk"]], left_on="ws_sold_date_sk", right_on="d_date_sk")

    cd1 = customer_demographics.copy()
    cd1.columns = [f"cd1_{c}" for c in cd1.columns]
    ws_wr = ws_wr.merge(cd1, left_on="wr_refunded_cdemo_sk", right_on="cd1_cd_demo_sk")

    cd2 = customer_demographics.copy()
    cd2.columns = [f"cd2_{c}" for c in cd2.columns]
    ws_wr = ws_wr.merge(cd2, left_on="wr_returning_cdemo_sk", right_on="cd2_cd_demo_sk")

    ws_wr = ws_wr.merge(customer_address, left_on="wr_refunded_addr_sk", right_on="ca_address_sk")

    ws_wr = ws_wr.merge(reason, left_on="wr_reason_sk", right_on="r_reason_sk")

    demo_filter = None
    for status, degree, (low, high) in zip(marital, education, _Q85_PRICE_BANDS):
        group = (
            (ws_wr["cd1_cd_marital_status"] == status)
            & (ws_wr["cd1_cd_marital_status"] == ws_wr["cd2_cd_marital_status"])
            & (ws_wr["cd1_cd_education_status"] == degree)
            & (ws_wr["cd1_cd_education_status"] == ws_wr["cd2_cd_education_status"])
            & ws_wr["ws_sales_price"].between(low, high)
        )
        demo_filter = group if demo_filter is None else demo_filter | group
    state_filter = None
    for group_states, (low, high) in zip(states, _Q85_PROFIT_BANDS):
        group = ws_wr["ca_state"].isin(group_states) & ws_wr["ws_net_profit"].between(low, high)
        state_filter = group if state_filter is None else state_filter | group
    country_filter = (ws_wr["ca_country"] == "United States") & state_filter
    filtered = ws_wr[demo_filter & country_filter]

    result = filtered.groupby("r_reason_desc", as_index=False).agg(
        {
            "ws_quantity": "mean",
            "wr_refunded_cash": "mean",
            "wr_fee": "mean",
        }
    )
    result = result.rename(
        columns={
            "ws_quantity": "avg_quantity",
            "wr_refunded_cash": "avg_refunded_cash",
            "wr_fee": "avg_fee",
        }
    )
    result["reason_desc_short"] = result["r_reason_desc"].str[:20]
    return (
        result[["reason_desc_short", "avg_quantity", "avg_refunded_cash", "avg_fee"]]
        .sort_values(["reason_desc_short", "avg_quantity", "avg_refunded_cash", "avg_fee"])
        .head(100)
    )


_Q66_SALES_COLUMNS = {
    "web": ("ws_sales_price", "ws_ext_sales_price", "ws_ext_list_price"),
    "catalog": ("cs_sales_price", "cs_ext_sales_price", "cs_ext_list_price"),
}
_Q66_NET_COLUMNS = {
    "web": ("ws_net_paid", "ws_net_paid_inc_tax", "ws_net_paid_inc_ship", "ws_net_paid_inc_ship_tax", "ws_net_profit"),
    "catalog": (
        "cs_net_paid",
        "cs_net_paid_inc_tax",
        "cs_net_paid_inc_ship",
        "cs_net_paid_inc_ship_tax",
        "cs_net_profit",
    ),
}


def _q66_value_columns(params: Any) -> dict[str, tuple[str, str]]:
    chosen = {
        "web": (
            params.get("web_sales_col", "ws_sales_price"),
            params.get("web_net_col", "ws_net_paid_inc_tax"),
        ),
        "catalog": (
            params.get("catalog_sales_col", "cs_sales_price"),
            params.get("catalog_net_col", "cs_net_paid_inc_ship_tax"),
        ),
    }
    for channel, (sales_col, net_col) in chosen.items():
        if sales_col not in _Q66_SALES_COLUMNS[channel]:
            raise ValueError(f"Q66 {channel} sales column {sales_col!r} is not one of {_Q66_SALES_COLUMNS[channel]}")
        if net_col not in _Q66_NET_COLUMNS[channel]:
            raise ValueError(f"Q66 {channel} net column {net_col!r} is not one of {_Q66_NET_COLUMNS[channel]}")
    return chosen


def q66_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(66)
    year = params.get("year", 2002)
    ship_carriers = params.get("ship_carriers", ["DIAMOND", "AIRBORNE"])
    time_start = params.get("time_start", 49530)
    value_columns = _q66_value_columns(params)

    col = ctx.col
    lit = ctx.lit

    web_sales, catalog_sales, warehouse, date_dim, time_dim, ship_mode = _tables(
        ctx, "web_sales", "catalog_sales", "warehouse", "date_dim", "time_dim", "ship_mode"
    )

    date_filtered = date_dim.filter(col("d_year") == lit(year))
    time_filtered = time_dim.filter((col("t_time") >= lit(time_start)) & (col("t_time") <= lit(time_start + 28800)))
    sm_filtered = ship_mode.filter(col("sm_carrier").is_in(ship_carriers))
    carriers_str = ",".join(ship_carriers)

    def build_monthly_aggs(sales_col: str, net_col: str, qty_col: str):
        aggs = []
        for month in range(1, 13):
            month_names = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
            mname = month_names[month - 1]
            aggs.append(
                ctx.when(col("d_moy") == lit(month))
                .then(col(sales_col) * col(qty_col))
                .otherwise(lit(0))
                .sum()
                .alias(f"{mname}_sales")
            )
            aggs.append(
                ctx.when(col("d_moy") == lit(month))
                .then(col(net_col) * col(qty_col))
                .otherwise(lit(0))
                .sum()
                .alias(f"{mname}_net")
            )
        return aggs

    ws = (
        web_sales.join(warehouse, left_on="ws_warehouse_sk", right_on="w_warehouse_sk")
        .join(date_filtered, left_on="ws_sold_date_sk", right_on="d_date_sk")
        .join(time_filtered, left_on="ws_sold_time_sk", right_on="t_time_sk")
        .join(sm_filtered, left_on="ws_ship_mode_sk", right_on="sm_ship_mode_sk")
    )

    ws_agg = (
        ws.group_by(["w_warehouse_name", "w_warehouse_sq_ft", "w_city", "w_county", "w_state", "w_country", "d_year"])
        .agg(build_monthly_aggs(*value_columns["web"], "ws_quantity"))
        .with_columns(
            [
                lit(carriers_str).alias("ship_carriers"),
                col("d_year").alias("year"),
            ]
        )
    )

    cs = (
        catalog_sales.join(warehouse, left_on="cs_warehouse_sk", right_on="w_warehouse_sk")
        .join(date_filtered, left_on="cs_sold_date_sk", right_on="d_date_sk")
        .join(time_filtered, left_on="cs_sold_time_sk", right_on="t_time_sk")
        .join(sm_filtered, left_on="cs_ship_mode_sk", right_on="sm_ship_mode_sk")
    )

    cs_agg = (
        cs.group_by(["w_warehouse_name", "w_warehouse_sq_ft", "w_city", "w_county", "w_state", "w_country", "d_year"])
        .agg(build_monthly_aggs(*value_columns["catalog"], "cs_quantity"))
        .with_columns(
            [
                lit(carriers_str).alias("ship_carriers"),
                col("d_year").alias("year"),
            ]
        )
    )

    combined = ctx.concat([ws_agg, cs_agg])

    group_cols = [
        "w_warehouse_name",
        "w_warehouse_sq_ft",
        "w_city",
        "w_county",
        "w_state",
        "w_country",
        "ship_carriers",
        "year",
    ]

    month_names = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    agg_exprs = []
    for mname in month_names:
        agg_exprs.append(ctx.sum(f"{mname}_sales").alias(f"{mname}_sales"))
    for mname in month_names:
        agg_exprs.append(ctx.sum(f"{mname}_net").alias(f"{mname}_net"))

    grouped = combined.group_by(group_cols).agg(agg_exprs)
    per_foot = [
        (col(f"{mname}_sales") / col("w_warehouse_sq_ft")).alias(f"{mname}_sales_per_sq_foot") for mname in month_names
    ]
    ordered = (
        group_cols
        + [f"{m}_sales" for m in month_names]
        + [f"{m}_sales_per_sq_foot" for m in month_names]
        + [f"{m}_net" for m in month_names]
    )
    return grouped.with_columns(per_foot).select(ordered).sort("w_warehouse_name", nulls_last=True).head(100)


def q66_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(66)
    year = params.get("year", 2002)
    ship_carriers = params.get("ship_carriers", ["DIAMOND", "AIRBORNE"])
    time_start = params.get("time_start", 49530)
    value_columns = _q66_value_columns(params)

    web_sales, catalog_sales, warehouse, date_dim, time_dim, ship_mode = _tables(
        ctx, "web_sales", "catalog_sales", "warehouse", "date_dim", "time_dim", "ship_mode"
    )

    date_filtered = date_dim[date_dim["d_year"] == year]
    time_filtered = time_dim[(time_dim["t_time"] >= time_start) & (time_dim["t_time"] <= time_start + 28800)]
    sm_filtered = ship_mode[ship_mode["sm_carrier"].isin(ship_carriers)]
    carriers_str = ",".join(ship_carriers)

    month_names = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

    def process_channel(sales_df, wh_col, date_col, time_col, sm_col, sales_col, net_col, qty_col):
        df = sales_df.merge(warehouse, left_on=wh_col, right_on="w_warehouse_sk")
        df = df.merge(date_filtered[["d_date_sk", "d_year", "d_moy"]], left_on=date_col, right_on="d_date_sk")
        df = df.merge(time_filtered[["t_time_sk"]], left_on=time_col, right_on="t_time_sk")
        df = df.merge(sm_filtered[["sm_ship_mode_sk"]], left_on=sm_col, right_on="sm_ship_mode_sk")

        for month in range(1, 13):
            mname = month_names[month - 1]
            df[f"{mname}_sales"] = df.apply(
                lambda r, m=month: r[sales_col] * r[qty_col] if r["d_moy"] == m else 0, axis=1
            )
            df[f"{mname}_net"] = df.apply(lambda r, m=month: r[net_col] * r[qty_col] if r["d_moy"] == m else 0, axis=1)

        group_cols = ["w_warehouse_name", "w_warehouse_sq_ft", "w_city", "w_county", "w_state", "w_country", "d_year"]
        agg_dict = {f"{m}_sales": "sum" for m in month_names}
        agg_dict.update({f"{m}_net": "sum" for m in month_names})

        result = df.groupby(group_cols, as_index=False, dropna=False).agg(agg_dict)
        result["ship_carriers"] = carriers_str
        result["year"] = result["d_year"]
        return result

    ws_agg = process_channel(
        web_sales,
        "ws_warehouse_sk",
        "ws_sold_date_sk",
        "ws_sold_time_sk",
        "ws_ship_mode_sk",
        *value_columns["web"],
        "ws_quantity",
    )

    cs_agg = process_channel(
        catalog_sales,
        "cs_warehouse_sk",
        "cs_sold_date_sk",
        "cs_sold_time_sk",
        "cs_ship_mode_sk",
        *value_columns["catalog"],
        "cs_quantity",
    )

    combined = ctx.concat([ws_agg, cs_agg])

    group_cols = [
        "w_warehouse_name",
        "w_warehouse_sq_ft",
        "w_city",
        "w_county",
        "w_state",
        "w_country",
        "ship_carriers",
        "year",
    ]
    agg_dict = {f"{m}_sales": "sum" for m in month_names}
    agg_dict.update({f"{m}_net": "sum" for m in month_names})

    result = combined.groupby(group_cols, as_index=False, dropna=False).agg(agg_dict)
    for mname in month_names:
        result[f"{mname}_sales_per_sq_foot"] = result[f"{mname}_sales"] / result["w_warehouse_sq_ft"]
    out_cols = (
        group_cols
        + [f"{m}_sales" for m in month_names]
        + [f"{m}_sales_per_sq_foot" for m in month_names]
        + [f"{m}_net" for m in month_names]
    )
    result = result[out_cols].sort_values("w_warehouse_name", na_position="last").head(100)
    return _none_for_null(
        result, ["w_warehouse_name", "w_warehouse_sq_ft"] + [f"{m}_sales_per_sq_foot" for m in month_names]
    )


def q8_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(8)
    year = params.get("year", 1998)
    qoy = params.get("qoy")
    zip_codes = params.get("zip_codes")

    store_sales, date_dim, store, customer_address, customer = _tables(
        ctx, "store_sales", "date_dim", "store", "customer_address", "customer"
    )

    col = ctx.col
    lit = ctx.lit

    date_filtered = date_dim.filter((col("d_qoy") == lit(qoy)) & (col("d_year") == lit(year)))

    preferred_zips = (
        customer_address.join(
            customer.filter(col("c_preferred_cust_flag") == lit("Y")),
            left_on="ca_address_sk",
            right_on="c_current_addr_sk",
        )
        .with_columns(col("ca_zip").cast_string().str.slice(0, 5).alias("zip5"))
        .group_by("zip5")
        .agg(ctx.len().alias("cnt"))
        .filter(col("cnt") > lit(10))
        .select("zip5")
    )

    target_zips = customer_address.filter(col("ca_zip").cast_string().str.slice(0, 5).is_in(zip_codes)).select(
        col("ca_zip").cast_string().str.slice(0, 5).alias("zip5")
    )

    valid_zips = target_zips.join(preferred_zips, on="zip5", how="semi")

    result = (
        store_sales.join(date_filtered, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .with_columns(col("s_zip").cast_string().str.slice(0, 2).alias("store_zip2"))
    )

    valid_zips_2char = valid_zips.with_columns(col("zip5").str.slice(0, 2).alias("zip2")).select("zip2").unique()

    result = result.join(valid_zips_2char, left_on="store_zip2", right_on="zip2")

    return _sort_null_largest_expression(
        ctx, result.group_by("s_store_name").agg(ctx.sum("ss_net_profit").alias("sum_net_profit")), ["s_store_name"]
    ).head(100)


def q8_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(8)
    year = params.get("year", 1998)
    qoy = params.get("qoy")
    zip_codes = params.get("zip_codes")

    store_sales, date_dim, store, customer_address, customer = _tables(
        ctx, "store_sales", "date_dim", "store", "customer_address", "customer"
    )

    date_filtered = date_dim[(date_dim["d_qoy"] == qoy) & (date_dim["d_year"] == year)]

    preferred_cust = customer[customer["c_preferred_cust_flag"] == "Y"]
    ca_pref = customer_address.merge(
        preferred_cust[["c_customer_sk", "c_current_addr_sk"]],
        left_on="ca_address_sk",
        right_on="c_current_addr_sk",
    )
    ca_pref["zip5"] = ca_pref["ca_zip"].str[:5]
    zip_counts = ctx.groupby_size(ca_pref, "zip5", name="cnt")
    preferred_zips = set(zip_counts[zip_counts["cnt"] > 10]["zip5"])

    target_zips = {z[:5] for z in zip_codes}

    valid_zips = target_zips & preferred_zips
    valid_zips_2char = {z[:2] for z in valid_zips}

    result = store_sales.merge(date_filtered[["d_date_sk"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    result = result.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    result["store_zip2"] = result["s_zip"].str[:2]

    result = result[result["store_zip2"].isin(valid_zips_2char)]

    return (
        result.groupby("s_store_name", as_index=False)
        .agg(sum_net_profit=("ss_net_profit", "sum"))
        .sort_values("s_store_name")
        .head(100)
    )


def q9_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(9)
    quantity_ranges = params.get("quantity_ranges", [(1, 20), (21, 40), (41, 60), (61, 80), (81, 100)])
    thresholds = params.get("thresholds", [25437, 22746, 9387, 10098, 18213])
    agg_then = params.get("agg_then", "ss_ext_discount_amt")
    agg_else = params.get("agg_else", "ss_net_profit")

    store_sales, reason = _tables(ctx, "store_sales", "reason")

    col = ctx.col
    lit = ctx.lit

    buckets = []
    for i, (q_min, q_max) in enumerate(quantity_ranges):
        bucket = store_sales.filter((col("ss_quantity") >= lit(q_min)) & (col("ss_quantity") <= lit(q_max)))
        cnt_df = bucket.select(ctx.len().alias("cnt"))
        cnt = cnt_df.scalar(0, 0) if hasattr(cnt_df, "scalar") else len(bucket)

        threshold = thresholds[i] if i < len(thresholds) else 1000

        if cnt > threshold:
            avg_val = bucket.select(ctx.mean(agg_then).alias("avg_val"))
        else:
            avg_val = bucket.select(ctx.mean(agg_else).alias("avg_val"))

        if hasattr(avg_val, "scalar"):
            val = avg_val.scalar(0, 0)
        else:
            val = avg_val.to_numpy()[0, 0] if len(avg_val) > 0 else None

        buckets.append(val)

    return reason.filter(col("r_reason_sk") == lit(1)).select(
        lit(buckets[0]).alias("bucket1") if len(buckets) > 0 else lit(None).alias("bucket1"),
        lit(buckets[1]).alias("bucket2") if len(buckets) > 1 else lit(None).alias("bucket2"),
        lit(buckets[2]).alias("bucket3") if len(buckets) > 2 else lit(None).alias("bucket3"),
        lit(buckets[3]).alias("bucket4") if len(buckets) > 3 else lit(None).alias("bucket4"),
        lit(buckets[4]).alias("bucket5") if len(buckets) > 4 else lit(None).alias("bucket5"),
    )


def q9_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    params = get_parameters(9)
    quantity_ranges = params.get("quantity_ranges", [(1, 20), (21, 40), (41, 60), (61, 80), (81, 100)])
    thresholds = params.get("thresholds", [25437, 22746, 9387, 10098, 18213])
    agg_then = params.get("agg_then", "ss_ext_discount_amt")
    agg_else = params.get("agg_else", "ss_net_profit")

    store_sales = ctx.get_table("store_sales")

    buckets = []
    for i, (q_min, q_max) in enumerate(quantity_ranges):
        bucket = store_sales[(store_sales["ss_quantity"] >= q_min) & (store_sales["ss_quantity"] <= q_max)]
        cnt = len(bucket)

        threshold = thresholds[i] if i < len(thresholds) else 1000

        column = bucket[agg_then if cnt > threshold else agg_else]

        buckets.append(column.mean() if column.count() else None)

    return pd.DataFrame(
        {
            "bucket1": [buckets[0]] if len(buckets) > 0 else [None],
            "bucket2": [buckets[1]] if len(buckets) > 1 else [None],
            "bucket3": [buckets[2]] if len(buckets) > 2 else [None],
            "bucket4": [buckets[3]] if len(buckets) > 3 else [None],
            "bucket5": [buckets[4]] if len(buckets) > 4 else [None],
        }
    )


_Q28_QUANTITY_RANGES = ((0, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 30))


def _q28_buckets(params: Any) -> list[tuple[int, int, int, int, int]]:
    list_prices = params.get("list_prices", [11, 91, 66, 142, 135, 28])
    coupon_amts = params.get("coupon_amts", [460, 1430, 920, 3054, 14180, 2513])
    wholesale_costs = params.get("wholesale_costs", [14, 32, 4, 80, 38, 42])
    buckets = len(_Q28_QUANTITY_RANGES)
    if not len(list_prices) == len(coupon_amts) == len(wholesale_costs) == buckets:
        raise ValueError(f"Q28 needs {buckets} values for each of its three parameters")
    return [
        (q_min, q_max, int(lp), int(ca), int(wc))
        for (q_min, q_max), lp, ca, wc in zip(_Q28_QUANTITY_RANGES, list_prices, coupon_amts, wholesale_costs)
    ]


def q28_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(28)
    buckets = _q28_buckets(params)

    store_sales = ctx.get_table("store_sales")
    col = ctx.col
    lit = ctx.lit

    bucket_results = []
    for i, (q_min, q_max, lp, ca, wc) in enumerate(buckets):
        bucket = store_sales.filter(
            (col("ss_quantity") >= lit(q_min))
            & (col("ss_quantity") <= lit(q_max))
            & (
                ((col("ss_list_price") >= lit(lp)) & (col("ss_list_price") <= lit(lp + 10)))
                | ((col("ss_coupon_amt") >= lit(ca)) & (col("ss_coupon_amt") <= lit(ca + 1000)))
                | ((col("ss_wholesale_cost") >= lit(wc)) & (col("ss_wholesale_cost") <= lit(wc + 20)))
            )
        )

        stats = bucket.select(
            ctx.mean("ss_list_price").alias(f"B{i + 1}_LP"),
            col("ss_list_price").count().alias(f"B{i + 1}_CNT"),
            col("ss_list_price").filter(col("ss_list_price").is_not_null()).n_unique().alias(f"B{i + 1}_CNTD"),
        )

        bucket_results.append(stats)

    result = bucket_results[0]
    for br in bucket_results[1:]:
        result = result.join(br, how="cross")

    return result


def q28_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    buckets = _q28_buckets(get_parameters(28))

    store_sales = ctx.get_table("store_sales")

    result_dict = {}
    for i, (q_min, q_max, lp, ca, wc) in enumerate(buckets):
        bucket = store_sales[
            (store_sales["ss_quantity"] >= q_min)
            & (store_sales["ss_quantity"] <= q_max)
            & (
                ((store_sales["ss_list_price"] >= lp) & (store_sales["ss_list_price"] <= lp + 10))
                | ((store_sales["ss_coupon_amt"] >= ca) & (store_sales["ss_coupon_amt"] <= ca + 1000))
                | ((store_sales["ss_wholesale_cost"] >= wc) & (store_sales["ss_wholesale_cost"] <= wc + 20))
            )
        ]

        result_dict[f"B{i + 1}_LP"] = bucket["ss_list_price"].mean()
        result_dict[f"B{i + 1}_CNT"] = bucket["ss_list_price"].count()
        result_dict[f"B{i + 1}_CNTD"] = bucket["ss_list_price"].nunique()

    return pd.DataFrame([result_dict])


_Q88_STORE_NAME = "ese"


def _q88_dep_counts(params: Any) -> list[int]:
    dep_counts = [int(value) for value in params.get("dep_counts", [3, 0, 1])]
    if len(dep_counts) != 3:
        raise ValueError(f"Q88 needs three dependent counts, got {dep_counts!r}")
    return dep_counts


def q88_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(88)
    dep_counts = _q88_dep_counts(params)
    store_name = _Q88_STORE_NAME

    store_sales, household_demographics, time_dim, store = _tables(
        ctx, "store_sales", "household_demographics", "time_dim", "store"
    )

    col = ctx.col
    lit = ctx.lit

    store_filtered = store.filter(col("s_store_name") == lit(store_name))

    hd_filters = None
    for dc in dep_counts:
        cond = (col("hd_dep_count") == lit(dc)) & (col("hd_vehicle_count") <= lit(dc + 2))
        hd_filters = cond if hd_filters is None else hd_filters | cond

    hd_filtered = household_demographics.filter(hd_filters)

    base = (
        store_sales.join(hd_filtered, left_on="ss_hdemo_sk", right_on="hd_demo_sk")
        .join(store_filtered, left_on="ss_store_sk", right_on="s_store_sk")
        .join(time_dim, left_on="ss_sold_time_sk", right_on="t_time_sk")
    )

    time_periods = [
        ("h8_30_to_9", 8, 30, 60),
        ("h9_to_9_30", 9, 0, 30),
        ("h9_30_to_10", 9, 30, 60),
        ("h10_to_10_30", 10, 0, 30),
        ("h10_30_to_11", 10, 30, 60),
        ("h11_to_11_30", 11, 0, 30),
        ("h11_30_to_12", 11, 30, 60),
        ("h12_to_12_30", 12, 0, 30),
    ]

    period_results = []
    for name, hour, min_start, min_end in time_periods:
        if min_end == 60:
            period_count = base.filter((col("t_hour") == lit(hour)) & (col("t_minute") >= lit(min_start))).select(
                ctx.len().alias(name)
            )
        else:
            period_count = base.filter((col("t_hour") == lit(hour)) & (col("t_minute") < lit(min_end))).select(
                ctx.len().alias(name)
            )
        period_results.append(period_count)

    result = period_results[0]
    for pr in period_results[1:]:
        result = result.join(pr, how="cross")

    return result


def q88_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    params = get_parameters(88)
    dep_counts = _q88_dep_counts(params)
    store_name = _Q88_STORE_NAME

    store_sales, household_demographics, time_dim, store = _tables(
        ctx, "store_sales", "household_demographics", "time_dim", "store"
    )

    store_filtered = store[store["s_store_name"] == store_name]

    hd_mask = None
    for dc in dep_counts:
        cond = (household_demographics["hd_dep_count"] == dc) & (household_demographics["hd_vehicle_count"] <= dc + 2)
        hd_mask = cond if hd_mask is None else hd_mask | cond
    hd_filtered = household_demographics[hd_mask]

    base = store_sales.merge(hd_filtered[["hd_demo_sk"]], left_on="ss_hdemo_sk", right_on="hd_demo_sk")
    base = base.merge(store_filtered[["s_store_sk"]], left_on="ss_store_sk", right_on="s_store_sk")
    base = base.merge(time_dim[["t_time_sk", "t_hour", "t_minute"]], left_on="ss_sold_time_sk", right_on="t_time_sk")

    time_periods = [
        ("h8_30_to_9", 8, 30, 60),
        ("h9_to_9_30", 9, 0, 30),
        ("h9_30_to_10", 9, 30, 60),
        ("h10_to_10_30", 10, 0, 30),
        ("h10_30_to_11", 10, 30, 60),
        ("h11_to_11_30", 11, 0, 30),
        ("h11_30_to_12", 11, 30, 60),
        ("h12_to_12_30", 12, 0, 30),
    ]

    result_dict = {}
    for name, hour, min_start, min_end in time_periods:
        if min_end == 60:
            period_data = base[(base["t_hour"] == hour) & (base["t_minute"] >= min_start)]
        else:
            period_data = base[(base["t_hour"] == hour) & (base["t_minute"] < min_end)]
        result_dict[name] = len(period_data)

    return pd.DataFrame([result_dict])


def _q14_cross_items_expression(
    ctx: DataFrameContext, year: int, store_sales: Any, catalog_sales: Any, web_sales: Any, item: Any, date_dim: Any
) -> tuple[Any, Any]:
    col = ctx.col
    lit = ctx.lit

    date_filtered = date_dim.filter((col("d_year") >= lit(year)) & (col("d_year") <= lit(year + 2)))

    ss_items = (
        store_sales.join(date_filtered, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="ss_item_sk", right_on="i_item_sk")
        .select("i_brand_id", "i_class_id", "i_category_id")
        .unique()
    )

    cs_items = (
        catalog_sales.join(date_filtered, left_on="cs_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="cs_item_sk", right_on="i_item_sk")
        .select("i_brand_id", "i_class_id", "i_category_id")
        .unique()
    )

    ws_items = (
        web_sales.join(date_filtered, left_on="ws_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="ws_item_sk", right_on="i_item_sk")
        .select("i_brand_id", "i_class_id", "i_category_id")
        .unique()
    )

    cross_items = ss_items.join(cs_items, on=["i_brand_id", "i_class_id", "i_category_id"], how="semi")
    cross_items = cross_items.join(ws_items, on=["i_brand_id", "i_class_id", "i_category_id"], how="semi")

    cross_item_sks = item.join(cross_items, on=["i_brand_id", "i_class_id", "i_category_id"]).select("i_item_sk")

    ss_for_avg = store_sales.join(date_filtered, left_on="ss_sold_date_sk", right_on="d_date_sk").select(
        (col("ss_quantity") * col("ss_list_price")).alias("sales")
    )
    cs_for_avg = catalog_sales.join(date_filtered, left_on="cs_sold_date_sk", right_on="d_date_sk").select(
        (col("cs_quantity") * col("cs_list_price")).alias("sales")
    )
    ws_for_avg = web_sales.join(date_filtered, left_on="ws_sold_date_sk", right_on="d_date_sk").select(
        (col("ws_quantity") * col("ws_list_price")).alias("sales")
    )
    all_sales = ctx.concat([ss_for_avg, cs_for_avg, ws_for_avg])
    avg_sales = all_sales.select(ctx.mean("sales").alias("average_sales"))

    avg_sales_val = avg_sales.scalar(0, 0) if hasattr(avg_sales, "scalar") else avg_sales.to_numpy()[0, 0]
    return cross_item_sks, avg_sales_val


def _q14_cross_items_pandas(
    ctx: DataFrameContext, year: int, store_sales: Any, catalog_sales: Any, web_sales: Any, item: Any, date_dim: Any
) -> tuple[Any, Any]:
    date_filtered = date_dim[(date_dim["d_year"] >= year) & (date_dim["d_year"] <= year + 2)]

    ss_items = store_sales.merge(date_filtered[["d_date_sk"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    ss_items = ss_items.merge(item, left_on="ss_item_sk", right_on="i_item_sk")
    ss_items = ss_items[["i_brand_id", "i_class_id", "i_category_id"]].drop_duplicates()

    cs_items = catalog_sales.merge(date_filtered[["d_date_sk"]], left_on="cs_sold_date_sk", right_on="d_date_sk")
    cs_items = cs_items.merge(item, left_on="cs_item_sk", right_on="i_item_sk")
    cs_items = cs_items[["i_brand_id", "i_class_id", "i_category_id"]].drop_duplicates()

    ws_items = web_sales.merge(date_filtered[["d_date_sk"]], left_on="ws_sold_date_sk", right_on="d_date_sk")
    ws_items = ws_items.merge(item, left_on="ws_item_sk", right_on="i_item_sk")
    ws_items = ws_items[["i_brand_id", "i_class_id", "i_category_id"]].drop_duplicates()

    cross_items = ss_items.merge(cs_items, on=["i_brand_id", "i_class_id", "i_category_id"])
    cross_items = cross_items.merge(ws_items, on=["i_brand_id", "i_class_id", "i_category_id"])

    cross_item_sks = item.merge(cross_items, on=["i_brand_id", "i_class_id", "i_category_id"])["i_item_sk"]

    ss_sales = store_sales.merge(date_filtered[["d_date_sk"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    ss_sales["sales"] = ss_sales["ss_quantity"] * ss_sales["ss_list_price"]

    cs_sales = catalog_sales.merge(date_filtered[["d_date_sk"]], left_on="cs_sold_date_sk", right_on="d_date_sk")
    cs_sales["sales"] = cs_sales["cs_quantity"] * cs_sales["cs_list_price"]

    ws_sales = web_sales.merge(date_filtered[["d_date_sk"]], left_on="ws_sold_date_sk", right_on="d_date_sk")
    ws_sales["sales"] = ws_sales["ws_quantity"] * ws_sales["ws_list_price"]

    all_sales = ctx.concat([ss_sales[["sales"]], cs_sales[["sales"]], ws_sales[["sales"]]])
    avg_sales_val = all_sales["sales"].mean()
    if hasattr(avg_sales_val, "compute"):
        avg_sales_val = avg_sales_val.compute()
    return cross_item_sks, avg_sales_val


def q14_expression_impl(ctx: DataFrameContext) -> Any:
    from .rollup_helper import expand_rollup_expression

    params = get_parameters(14)
    year = params.get("year", 1998)

    store_sales, catalog_sales, web_sales, item, date_dim = _tables(
        ctx, "store_sales", "catalog_sales", "web_sales", "item", "date_dim"
    )

    col = ctx.col
    lit = ctx.lit

    cross_item_sks, avg_sales_val = _q14_cross_items_expression(
        ctx, year, store_sales, catalog_sales, web_sales, item, date_dim
    )

    date_target = date_dim.filter((col("d_year") == lit(year + 2)) & (col("d_moy") == lit(11)))

    def channel_sales(sales_df, date_col, item_col, qty_col, price_col, channel_name):
        df = (
            sales_df.join(date_target, left_on=date_col, right_on="d_date_sk")
            .join(cross_item_sks, left_on=item_col, right_on="i_item_sk", how="semi")
            .join(item, left_on=item_col, right_on="i_item_sk")
            .with_columns((col(qty_col) * col(price_col)).alias("sales"))
        )
        grouped = df.group_by(["i_brand_id", "i_class_id", "i_category_id"]).agg(
            col("sales").sum().alias("sales"),
            ctx.len().alias("number_sales"),
        )
        grouped = grouped.filter(col("sales") > lit(avg_sales_val))
        grouped = grouped.with_columns(lit(channel_name).alias("channel"))
        return grouped

    ss_result = channel_sales(store_sales, "ss_sold_date_sk", "ss_item_sk", "ss_quantity", "ss_list_price", "store")
    cs_result = channel_sales(catalog_sales, "cs_sold_date_sk", "cs_item_sk", "cs_quantity", "cs_list_price", "catalog")
    ws_result = channel_sales(web_sales, "ws_sold_date_sk", "ws_item_sk", "ws_quantity", "ws_list_price", "web")

    combined = ctx.concat([ss_result, cs_result, ws_result])

    group_cols = ["channel", "i_brand_id", "i_class_id", "i_category_id"]
    agg_exprs = [
        ctx.sum("sales").alias("sum_sales"),
        ctx.sum("number_sales").alias("sum_number_sales"),
    ]

    result = expand_rollup_expression(combined, group_cols, agg_exprs, ctx)
    return result.select([*group_cols, "sum_sales", "sum_number_sales"]).sort(group_cols, nulls_last=True).head(100)


def q14_pandas_impl(ctx: DataFrameContext) -> Any:
    from .rollup_helper import expand_rollup_pandas

    params = get_parameters(14)
    year = params.get("year", 1998)

    store_sales, catalog_sales, web_sales, item, date_dim = _tables(
        ctx, "store_sales", "catalog_sales", "web_sales", "item", "date_dim"
    )

    cross_item_sks, avg_sales_val = _q14_cross_items_pandas(
        ctx, year, store_sales, catalog_sales, web_sales, item, date_dim
    )

    date_target = date_dim[(date_dim["d_year"] == year + 2) & (date_dim["d_moy"] == 11)]

    def channel_sales(sales_df, date_col, item_col, qty_col, price_col, channel_name):
        df = sales_df.merge(date_target[["d_date_sk"]], left_on=date_col, right_on="d_date_sk")
        df = df[df[item_col].isin(cross_item_sks)]
        df = df.merge(item, left_on=item_col, right_on="i_item_sk")
        df["sales"] = df[qty_col] * df[price_col]

        grouped = df.groupby(["i_brand_id", "i_class_id", "i_category_id"], as_index=False).agg(
            sales=("sales", "sum"),
            number_sales=(item_col, "count"),
        )
        grouped = grouped[grouped["sales"] > avg_sales_val]
        grouped["channel"] = channel_name
        return grouped

    ss_result = channel_sales(store_sales, "ss_sold_date_sk", "ss_item_sk", "ss_quantity", "ss_list_price", "store")
    cs_result = channel_sales(catalog_sales, "cs_sold_date_sk", "cs_item_sk", "cs_quantity", "cs_list_price", "catalog")
    ws_result = channel_sales(web_sales, "ws_sold_date_sk", "ws_item_sk", "ws_quantity", "ws_list_price", "web")

    combined = ctx.concat([ss_result, cs_result, ws_result])

    group_cols = ["channel", "i_brand_id", "i_class_id", "i_category_id"]
    agg_dict = {
        "sum_sales": ("sales", "sum"),
        "sum_number_sales": ("number_sales", "sum"),
    }

    result = expand_rollup_pandas(combined, group_cols, agg_dict, ctx)
    out_cols = [*group_cols, "sum_sales", "sum_number_sales"]
    return result[out_cols].sort_values(["channel", "i_brand_id", "i_class_id", "i_category_id"]).head(100)


_Q14B_SIDE_COLUMNS = ("channel", "brand", "class", "category", "sales", "number_sales")
_Q14B_COLUMNS = [f"{side}_{name}" for side in ("ty", "ly") for name in _Q14B_SIDE_COLUMNS]


def q14b_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(14)
    year = params.get("year", 1998)
    day = params.get("day", 16)

    store_sales, catalog_sales, web_sales, item, date_dim = _tables(
        ctx, "store_sales", "catalog_sales", "web_sales", "item", "date_dim"
    )

    col = ctx.col
    lit = ctx.lit

    cross_item_sks, avg_sales_val = _q14_cross_items_expression(
        ctx, year, store_sales, catalog_sales, web_sales, item, date_dim
    )
    keys = ["i_brand_id", "i_class_id", "i_category_id"]

    def store_week(week_year: int, side: str) -> Any:
        week = date_dim.filter(
            (col("d_year") == lit(week_year)) & (col("d_moy") == lit(12)) & (col("d_dom") == lit(day))
        ).select("d_week_seq")
        week_dates = date_dim.join(week, on="d_week_seq", how="semi").select("d_date_sk")
        grouped = (
            store_sales.join(week_dates, left_on="ss_sold_date_sk", right_on="d_date_sk")
            .join(cross_item_sks, left_on="ss_item_sk", right_on="i_item_sk", how="semi")
            .join(item, left_on="ss_item_sk", right_on="i_item_sk")
            .with_columns((col("ss_quantity") * col("ss_list_price")).alias("sales"))
            .group_by(keys)
            .agg(col("sales").sum().alias("sales"), ctx.len().alias("number_sales"))
            .filter(col("sales") > lit(avg_sales_val))
        )
        return grouped.select(
            lit("store").alias(f"{side}_channel"),
            col("i_brand_id").alias(f"{side}_brand"),
            col("i_class_id").alias(f"{side}_class"),
            col("i_category_id").alias(f"{side}_category"),
            col("sales").alias(f"{side}_sales"),
            col("number_sales").alias(f"{side}_number_sales"),
            *[col(key).alias(f"_{side}_{key}") for key in keys],
        )

    this_year = store_week(year + 1, "ty")
    last_year = store_week(year, "ly")
    joined = this_year.join(last_year, left_on=[f"_ty_{key}" for key in keys], right_on=[f"_ly_{key}" for key in keys])
    return (
        joined.select(_Q14B_COLUMNS)
        .sort(["ty_channel", "ty_brand", "ty_class", "ty_category"], nulls_last=True)
        .head(100)
    )


def q14b_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(14)
    year = params.get("year", 1998)
    day = params.get("day", 16)

    store_sales, catalog_sales, web_sales, item, date_dim = _tables(
        ctx, "store_sales", "catalog_sales", "web_sales", "item", "date_dim"
    )

    cross_item_sks, avg_sales_val = _q14_cross_items_pandas(
        ctx, year, store_sales, catalog_sales, web_sales, item, date_dim
    )
    keys = ["i_brand_id", "i_class_id", "i_category_id"]

    def store_week(week_year: int, side: str) -> Any:
        week = date_dim[(date_dim["d_year"] == week_year) & (date_dim["d_moy"] == 12) & (date_dim["d_dom"] == day)]
        week_dates = date_dim[date_dim["d_week_seq"].isin(week["d_week_seq"])][["d_date_sk"]]
        df = store_sales.merge(week_dates, left_on="ss_sold_date_sk", right_on="d_date_sk")
        df = df[df["ss_item_sk"].isin(cross_item_sks)]
        df = df.merge(item[["i_item_sk", *keys]], left_on="ss_item_sk", right_on="i_item_sk")
        df["sales"] = df["ss_quantity"] * df["ss_list_price"]
        grouped = df.groupby(keys, as_index=False).agg(sales=("sales", "sum"), number_sales=("ss_item_sk", "count"))
        grouped = grouped[grouped["sales"] > avg_sales_val]
        grouped["channel"] = "store"
        return grouped[["channel", *keys, "sales", "number_sales"]].rename(
            columns={
                "channel": f"{side}_channel",
                "i_brand_id": f"{side}_brand",
                "i_class_id": f"{side}_class",
                "i_category_id": f"{side}_category",
                "sales": f"{side}_sales",
                "number_sales": f"{side}_number_sales",
            }
        )

    this_year = store_week(year + 1, "ty")
    last_year = store_week(year, "ly")
    joined = this_year.merge(
        last_year, left_on=["ty_brand", "ty_class", "ty_category"], right_on=["ly_brand", "ly_class", "ly_category"]
    )
    return joined[_Q14B_COLUMNS].sort_values(["ty_channel", "ty_brand", "ty_class", "ty_category"]).head(100)


def _q23_frequent_items_and_best_customers_expression(
    ctx: DataFrameContext, year: int, top_percent: Any, store_sales: Any, customer: Any, item: Any, date_dim: Any
) -> tuple[Any, Any]:
    col = ctx.col
    lit = ctx.lit

    date_4yr = date_dim.filter((col("d_year") >= lit(year)) & (col("d_year") <= lit(year + 3)))

    frequent_items = (
        store_sales.join(date_4yr, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .join(item, left_on="ss_item_sk", right_on="i_item_sk")
        .with_columns(col("i_item_desc").str.slice(0, 30).alias("itemdesc"))
        .group_by(["itemdesc", "ss_item_sk", "d_date"])
        .agg(ctx.len().alias("cnt"))
        .filter(col("cnt") > lit(4))
        .select(col("ss_item_sk").alias("i_item_sk"))
        .unique()
    )

    max_store_sales = (
        store_sales.join(customer, left_on="ss_customer_sk", right_on="c_customer_sk")
        .join(date_4yr, left_on="ss_sold_date_sk", right_on="d_date_sk")
        .with_columns((col("ss_quantity") * col("ss_sales_price")).alias("csales_row"))
        .group_by("ss_customer_sk")
        .agg(
            ctx.when(col("csales_row").count() > lit(0))
            .then(col("csales_row").sum())
            .otherwise(lit(None))
            .alias("csales")
        )
        .select(ctx.max_("csales").alias("tpcds_cmax"))
    )

    if hasattr(max_store_sales, "scalar"):
        max_sales_val = max_store_sales.scalar(0, 0)
    else:
        max_sales_val = max_store_sales.to_numpy()[0, 0]

    threshold = (top_percent / 100.0) * max_sales_val if max_sales_val is not None else None

    best_customers = (
        store_sales.join(customer, left_on="ss_customer_sk", right_on="c_customer_sk")
        .with_columns((col("ss_quantity") * col("ss_sales_price")).alias("ssales_row"))
        .group_by("ss_customer_sk")
        .agg(
            ctx.when(col("ssales_row").count() > lit(0))
            .then(col("ssales_row").sum())
            .otherwise(lit(None))
            .alias("ssales")
        )
        .filter(col("ssales") > lit(threshold))
        .select(col("ss_customer_sk").alias("c_customer_sk"))
    )
    return frequent_items, best_customers


def _q23_frequent_items_and_best_customers_pandas(
    ctx: DataFrameContext, year: int, top_percent: Any, store_sales: Any, customer: Any, item: Any, date_dim: Any
) -> tuple[set[Any], set[Any]]:
    date_4yr = date_dim[(date_dim["d_year"] >= year) & (date_dim["d_year"] <= year + 3)]

    ss_items = store_sales.merge(date_4yr[["d_date_sk", "d_date"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    ss_items = ss_items.merge(item[["i_item_sk", "i_item_desc"]], left_on="ss_item_sk", right_on="i_item_sk")
    ss_items["itemdesc"] = ss_items["i_item_desc"].str[:30]

    freq_counts = ctx.groupby_agg(
        ss_items, ["itemdesc", "i_item_sk", "d_date"], {"cnt": ("i_item_sk", "count")}, as_index=False, dropna=False
    )
    frequent_item_sks = set(freq_counts[freq_counts["cnt"] > 4]["i_item_sk"])

    ss_cust = store_sales.merge(customer[["c_customer_sk"]], left_on="ss_customer_sk", right_on="c_customer_sk")
    ss_cust = ss_cust.merge(date_4yr[["d_date_sk"]], left_on="ss_sold_date_sk", right_on="d_date_sk")
    ss_cust["_sales"] = ss_cust["ss_quantity"] * ss_cust["ss_sales_price"]
    cust_sales = ss_cust.groupby("c_customer_sk", as_index=False).agg(
        csales=("_sales", "sum"), count=("_sales", "count")
    )
    cust_sales["csales"] = cust_sales["csales"].where(cust_sales["count"] > 0)
    max_sales_val = cust_sales["csales"].max()
    if hasattr(max_sales_val, "compute"):
        max_sales_val = max_sales_val.compute()

    threshold = (top_percent / 100.0) * max_sales_val

    all_cust_sales = store_sales.merge(customer[["c_customer_sk"]], left_on="ss_customer_sk", right_on="c_customer_sk")
    all_cust_sales["_sales"] = all_cust_sales["ss_quantity"] * all_cust_sales["ss_sales_price"]
    all_cust_sales_agg = all_cust_sales.groupby("c_customer_sk", as_index=False).agg(
        ssales=("_sales", "sum"), count=("_sales", "count")
    )
    all_cust_sales_agg["ssales"] = all_cust_sales_agg["ssales"].where(all_cust_sales_agg["count"] > 0)
    best_customer_sks = set(all_cust_sales_agg[all_cust_sales_agg["ssales"] > threshold]["c_customer_sk"])
    return frequent_item_sks, best_customer_sks


def q23_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(23)
    year = params.get("year", 1999)
    month = params.get("month", 1)
    top_percent = params.get("top_percent", 95)

    store_sales, catalog_sales, web_sales, customer, item, date_dim = _tables(
        ctx, "store_sales", "catalog_sales", "web_sales", "customer", "item", "date_dim"
    )

    col = ctx.col
    lit = ctx.lit

    frequent_items, best_customers = _q23_frequent_items_and_best_customers_expression(
        ctx, year, top_percent, store_sales, customer, item, date_dim
    )

    date_target = date_dim.filter((col("d_year") == lit(year)) & (col("d_moy") == lit(month)))

    cs_sales = (
        catalog_sales.join(date_target, left_on="cs_sold_date_sk", right_on="d_date_sk")
        .join(frequent_items, left_on="cs_item_sk", right_on="i_item_sk", how="semi")
        .join(best_customers, left_on="cs_bill_customer_sk", right_on="c_customer_sk", how="semi")
        .select((col("cs_quantity") * col("cs_list_price")).alias("sales"))
    )

    ws_sales = (
        web_sales.join(date_target, left_on="ws_sold_date_sk", right_on="d_date_sk")
        .join(frequent_items, left_on="ws_item_sk", right_on="i_item_sk", how="semi")
        .join(best_customers, left_on="ws_bill_customer_sk", right_on="c_customer_sk", how="semi")
        .select((col("ws_quantity") * col("ws_list_price")).alias("sales"))
    )

    all_sales = ctx.concat([cs_sales, ws_sales])
    tallied = all_sales.select(
        ctx.sum("sales").alias("sum_sales"),
        col("sales").count().alias("n"),
    )
    return tallied.select(ctx.when(col("n") > lit(0)).then(col("sum_sales")).otherwise(lit(None)).alias("sum_sales"))


def q23_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    params = get_parameters(23)
    year = params.get("year", 1999)
    month = params.get("month", 1)
    top_percent = params.get("top_percent", 95)

    store_sales, catalog_sales, web_sales, customer, item, date_dim = _tables(
        ctx, "store_sales", "catalog_sales", "web_sales", "customer", "item", "date_dim"
    )

    frequent_item_sks, best_customer_sks = _q23_frequent_items_and_best_customers_pandas(
        ctx, year, top_percent, store_sales, customer, item, date_dim
    )

    date_target = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] == month)]

    cs = catalog_sales.merge(date_target[["d_date_sk"]], left_on="cs_sold_date_sk", right_on="d_date_sk")
    cs = cs[cs["cs_item_sk"].isin(frequent_item_sks)]
    cs = cs[cs["cs_bill_customer_sk"].isin(best_customer_sks)]
    cs["sales"] = cs["cs_quantity"] * cs["cs_list_price"]

    ws = web_sales.merge(date_target[["d_date_sk"]], left_on="ws_sold_date_sk", right_on="d_date_sk")
    ws = ws[ws["ws_item_sk"].isin(frequent_item_sks)]
    ws = ws[ws["ws_bill_customer_sk"].isin(best_customer_sks)]
    ws["sales"] = ws["ws_quantity"] * ws["ws_list_price"]

    if cs["sales"].count() + ws["sales"].count() == 0:
        return pd.DataFrame({"sum_sales": [None]})
    total = cs["sales"].sum() + ws["sales"].sum()
    return pd.DataFrame({"sum_sales": [total]})


def q23b_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(23)
    year = params.get("year", 1999)
    month = params.get("month", 1)
    top_percent = params.get("top_percent", 95)

    store_sales, catalog_sales, web_sales, customer, item, date_dim = _tables(
        ctx, "store_sales", "catalog_sales", "web_sales", "customer", "item", "date_dim"
    )

    col = ctx.col
    lit = ctx.lit

    frequent_items, best_customers = _q23_frequent_items_and_best_customers_expression(
        ctx, year, top_percent, store_sales, customer, item, date_dim
    )
    date_target = date_dim.filter((col("d_year") == lit(year)) & (col("d_moy") == lit(month)))
    names = customer.select("c_customer_sk", "c_last_name", "c_first_name")

    def channel_sales(sales_df: Any, date_col: str, item_col: str, customer_col: str, qty: str, price: str) -> Any:
        grouped = (
            sales_df.join(date_target, left_on=date_col, right_on="d_date_sk")
            .join(frequent_items, left_on=item_col, right_on="i_item_sk", how="semi")
            .join(best_customers, left_on=customer_col, right_on="c_customer_sk", how="semi")
            .join(names, left_on=customer_col, right_on="c_customer_sk")
            .with_columns((col(qty) * col(price)).alias("_sales"))
            .group_by(["c_last_name", "c_first_name"])
            .agg(col("_sales").sum().alias("_sum"), col("_sales").count().alias("_n"))
        )
        return grouped.select(
            "c_last_name",
            "c_first_name",
            ctx.when(col("_n") > lit(0)).then(col("_sum")).otherwise(lit(None)).alias("sales"),
        )

    cs_sales = channel_sales(
        catalog_sales, "cs_sold_date_sk", "cs_item_sk", "cs_bill_customer_sk", "cs_quantity", "cs_list_price"
    )
    ws_sales = channel_sales(
        web_sales, "ws_sold_date_sk", "ws_item_sk", "ws_bill_customer_sk", "ws_quantity", "ws_list_price"
    )
    return ctx.concat([cs_sales, ws_sales]).sort(["c_last_name", "c_first_name", "sales"], nulls_last=True).head(100)


def q23b_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(23)
    year = params.get("year", 1999)
    month = params.get("month", 1)
    top_percent = params.get("top_percent", 95)

    store_sales, catalog_sales, web_sales, customer, item, date_dim = _tables(
        ctx, "store_sales", "catalog_sales", "web_sales", "customer", "item", "date_dim"
    )

    frequent_item_sks, best_customer_sks = _q23_frequent_items_and_best_customers_pandas(
        ctx, year, top_percent, store_sales, customer, item, date_dim
    )
    date_target = date_dim[(date_dim["d_year"] == year) & (date_dim["d_moy"] == month)]
    names = customer[["c_customer_sk", "c_last_name", "c_first_name"]]

    def channel_sales(sales_df: Any, date_col: str, item_col: str, customer_col: str, qty: str, price: str) -> Any:
        df = sales_df.merge(date_target[["d_date_sk"]], left_on=date_col, right_on="d_date_sk")
        df = df[df[item_col].isin(frequent_item_sks)]
        df = df[df[customer_col].isin(best_customer_sks)]
        df = df.merge(names, left_on=customer_col, right_on="c_customer_sk")
        df["_sales"] = df[qty] * df[price]
        grouped = ctx.groupby_agg(
            df,
            ["c_last_name", "c_first_name"],
            {"sales": ("_sales", "sum"), "_n": ("_sales", "count")},
            as_index=False,
            dropna=False,
        )
        grouped["sales"] = grouped["sales"].where(grouped["_n"] > 0)
        return grouped[["c_last_name", "c_first_name", "sales"]]

    cs_sales = channel_sales(
        catalog_sales, "cs_sold_date_sk", "cs_item_sk", "cs_bill_customer_sk", "cs_quantity", "cs_list_price"
    )
    ws_sales = channel_sales(
        web_sales, "ws_sold_date_sk", "ws_item_sk", "ws_bill_customer_sk", "ws_quantity", "ws_list_price"
    )
    combined = ctx.concat([cs_sales, ws_sales])
    return _none_for_null(
        combined.sort_values(["c_last_name", "c_first_name", "sales"], na_position="last").head(100),
        ["c_last_name", "c_first_name", "sales"],
    )


_Q24_AMOUNT_COLUMNS = ("ss_net_paid", "ss_net_paid_inc_tax", "ss_net_profit", "ss_sales_price", "ss_ext_sales_price")


def _q24_amount_column(params: Any) -> str:
    amount = str(params.get("amount_column", "ss_sales_price")).lower()
    if amount not in _Q24_AMOUNT_COLUMNS:
        raise ValueError(f"Q24 amount_column must be one of {_Q24_AMOUNT_COLUMNS}, got {amount!r}")
    return amount


def q24_expression_impl(ctx: DataFrameContext) -> Any:
    return _q24_expression(ctx, get_parameters(24).get("color", "orchid"))


def q24b_expression_impl(ctx: DataFrameContext) -> Any:
    return _q24_expression(ctx, get_parameters(24).get("second_color", "chiffon"))


def _q24_expression(ctx: DataFrameContext, color: str) -> Any:
    params = get_parameters(24)
    market_id = params.get("market_id", 7)
    amount = _q24_amount_column(params)

    store_sales, store_returns, store, item, customer, customer_address = _tables(
        ctx, "store_sales", "store_returns", "store", "item", "customer", "customer_address"
    )

    col = ctx.col
    lit = ctx.lit

    store_filtered = store.filter(col("s_market_id") == lit(market_id))

    ssales = (
        store_sales.join(
            store_returns,
            left_on=["ss_ticket_number", "ss_item_sk"],
            right_on=["sr_ticket_number", "sr_item_sk"],
        )
        .join(store_filtered, left_on="ss_store_sk", right_on="s_store_sk")
        .join(item, left_on="ss_item_sk", right_on="i_item_sk")
        .join(customer, left_on="ss_customer_sk", right_on="c_customer_sk")
        .join(customer_address, left_on="c_current_addr_sk", right_on="ca_address_sk")
    )

    ssales = ssales.filter(
        (col("c_birth_country") != col("ca_country").str.to_uppercase()) & (col("s_zip") == col("ca_zip"))
    )

    ssales_agg = ssales.group_by(
        [
            "c_last_name",
            "c_first_name",
            "s_store_name",
            "ca_state",
            "s_state",
            "i_color",
            "i_current_price",
            "i_manager_id",
            "i_units",
            "i_size",
        ]
    ).agg(ctx.when(col(amount).count() > lit(0)).then(col(amount).sum()).otherwise(lit(None)).alias("netpaid"))

    avg_netpaid = ssales_agg.select(ctx.mean("netpaid").alias("avg_netpaid"))
    avg_val = avg_netpaid.scalar(0, 0)

    threshold = 0.05 * avg_val if avg_val is not None else None

    return _sort_null_largest_expression(
        ctx,
        ssales_agg.filter(col("i_color") == lit(color))
        .group_by(["c_last_name", "c_first_name", "s_store_name"])
        .agg(ctx.when(col("netpaid").count() > lit(0)).then(col("netpaid").sum()).otherwise(lit(None)).alias("paid"))
        .filter(col("paid") > lit(threshold)),
        ["c_last_name", "c_first_name", "s_store_name"],
    )


def q24_pandas_impl(ctx: DataFrameContext) -> Any:
    return _q24_pandas(ctx, get_parameters(24).get("color", "orchid"))


def q24b_pandas_impl(ctx: DataFrameContext) -> Any:
    return _q24_pandas(ctx, get_parameters(24).get("second_color", "chiffon"))


def _q24_pandas(ctx: DataFrameContext, color: str) -> Any:
    params = get_parameters(24)
    market_id = params.get("market_id", 7)
    amount = _q24_amount_column(params)

    store_sales, store_returns, store, item, customer, customer_address = _tables(
        ctx, "store_sales", "store_returns", "store", "item", "customer", "customer_address"
    )

    store_filtered = store[store["s_market_id"] == market_id]

    ssales = store_sales.merge(
        store_returns,
        left_on=["ss_ticket_number", "ss_item_sk"],
        right_on=["sr_ticket_number", "sr_item_sk"],
    )
    ssales = ssales.merge(store_filtered, left_on="ss_store_sk", right_on="s_store_sk")
    ssales = ssales.merge(item, left_on="ss_item_sk", right_on="i_item_sk")
    ssales = ssales.merge(customer, left_on="ss_customer_sk", right_on="c_customer_sk")
    ssales = ssales.merge(customer_address, left_on="c_current_addr_sk", right_on="ca_address_sk")

    ssales["_ca_country_upper"] = ssales["ca_country"].str.upper()
    ssales = ssales[
        (~ssales["c_birth_country"].isna())
        & (~ssales["_ca_country_upper"].isna())
        & (~ssales["s_zip"].isna())
        & (~ssales["ca_zip"].isna())
    ]
    ssales = ssales.query("c_birth_country != _ca_country_upper and s_zip == ca_zip")

    group_cols = [
        "c_last_name",
        "c_first_name",
        "s_store_name",
        "ca_state",
        "s_state",
        "i_color",
        "i_current_price",
        "i_manager_id",
        "i_units",
        "i_size",
    ]
    ssales_agg = ctx.groupby_agg(
        ssales, group_cols, {"netpaid": (amount, "sum"), "_n": (amount, "count")}, as_index=False, dropna=False
    )
    ssales_agg["netpaid"] = ssales_agg["netpaid"].where(ssales_agg["_n"] > 0)

    avg_val = ssales_agg["netpaid"].mean()
    if hasattr(avg_val, "compute"):
        avg_val = avg_val.compute()
    threshold = 0.05 * avg_val

    color_data = ssales_agg.query("i_color == @_color", local_dict={"_color": color})
    result = ctx.groupby_agg(
        color_data,
        ["c_last_name", "c_first_name", "s_store_name"],
        {"paid": ("netpaid", "sum"), "_n": ("netpaid", "count")},
        as_index=False,
        dropna=False,
    )
    result["paid"] = result["paid"].where(result["_n"] > 0)
    result = result.drop(columns="_n")
    result = ctx.filter_gt(result, "paid", threshold)
    return _none_for_null(
        result.sort_values(["c_last_name", "c_first_name", "s_store_name"]),
        ["c_last_name", "c_first_name", "s_store_name", "paid"],
    )


def q21_expression_impl(ctx: DataFrameContext) -> Any:
    from datetime import timedelta

    params = get_parameters(21)
    price_min, price_max = 0.99, 1.49
    sales_date_default = params.get("sales_date", "1998-04-08")

    inventory, warehouse, item, date_dim = _tables(ctx, "inventory", "warehouse", "item", "date_dim")

    col = ctx.col
    lit = ctx.lit

    sales_date = (
        datetime.strptime(sales_date_default, "%Y-%m-%d").date()
        if isinstance(sales_date_default, str)
        else sales_date_default
    )
    date_start = sales_date - timedelta(days=30)
    date_end = sales_date + timedelta(days=30)

    item_filtered = item.filter((col("i_current_price") >= lit(price_min)) & (col("i_current_price") <= lit(price_max)))

    date_filtered = date_dim.filter((col("d_date") >= lit(date_start)) & (col("d_date") <= lit(date_end)))

    inv_data = (
        inventory.join(item_filtered, left_on="inv_item_sk", right_on="i_item_sk")
        .join(warehouse, left_on="inv_warehouse_sk", right_on="w_warehouse_sk")
        .join(date_filtered, left_on="inv_date_sk", right_on="d_date_sk")
    )

    inv_data = inv_data.with_columns(
        [
            ctx.when(col("d_date") < lit(sales_date))
            .then(col("inv_quantity_on_hand"))
            .otherwise(lit(0))
            .alias("inv_before"),
            ctx.when(col("d_date") >= lit(sales_date))
            .then(col("inv_quantity_on_hand"))
            .otherwise(lit(0))
            .alias("inv_after"),
        ]
    )

    grouped = inv_data.group_by(["w_warehouse_name", "i_item_id"]).agg(
        [
            ctx.sum("inv_before").alias("inv_before"),
            ctx.sum("inv_after").alias("inv_after"),
        ]
    )

    return (
        grouped.filter(
            (col("inv_before") > lit(0))
            & ((col("inv_after") / col("inv_before")) >= lit(2.0 / 3.0))
            & ((col("inv_after") / col("inv_before")) <= lit(3.0 / 2.0))
        )
        .sort(["w_warehouse_name", "i_item_id"], nulls_last=True)
        .head(100)
    )


def q21_pandas_impl(ctx: DataFrameContext) -> Any:
    from datetime import timedelta

    params = get_parameters(21)
    price_min, price_max = 0.99, 1.49
    sales_date_default = params.get("sales_date", "1998-04-08")

    inventory, warehouse, item, date_dim = _tables(ctx, "inventory", "warehouse", "item", "date_dim")

    sales_date = (
        datetime.strptime(sales_date_default, "%Y-%m-%d").date()
        if isinstance(sales_date_default, str)
        else sales_date_default
    )
    date_start = sales_date - timedelta(days=30)
    date_end = sales_date + timedelta(days=30)

    item_filtered = item[(item["i_current_price"] >= price_min) & (item["i_current_price"] <= price_max)]

    date_filtered = date_dim[(date_dim["d_date"] >= date_start) & (date_dim["d_date"] <= date_end)]

    inv_data = inventory.merge(item_filtered, left_on="inv_item_sk", right_on="i_item_sk")
    inv_data = inv_data.merge(warehouse, left_on="inv_warehouse_sk", right_on="w_warehouse_sk")
    inv_data = inv_data.merge(date_filtered, left_on="inv_date_sk", right_on="d_date_sk")

    inv_data["inv_before"] = inv_data.apply(
        lambda r: r["inv_quantity_on_hand"] if r["d_date"] < sales_date else 0,
        axis=1,
    )
    inv_data["inv_after"] = inv_data.apply(
        lambda r: r["inv_quantity_on_hand"] if r["d_date"] >= sales_date else 0,
        axis=1,
    )

    grouped = inv_data.groupby(["w_warehouse_name", "i_item_id"], as_index=False, dropna=False).agg(
        inv_before=("inv_before", "sum"),
        inv_after=("inv_after", "sum"),
    )

    result = grouped[grouped["inv_before"] > 0].copy()
    result["ratio"] = result["inv_after"] / result["inv_before"]
    result = result[(result["ratio"] >= 2.0 / 3.0) & (result["ratio"] <= 3.0 / 2.0)]
    result = result[["w_warehouse_name", "i_item_id", "inv_before", "inv_after"]]
    result = result.sort_values(["w_warehouse_name", "i_item_id"], na_position="last").head(100)
    return _none_for_null(result, ["w_warehouse_name"])


def q22_expression_impl(ctx: DataFrameContext) -> Any:
    from .rollup_helper import expand_rollup_expression

    params = get_parameters(22)
    dms = params.get("dms", 1212)

    inventory, item, date_dim = _tables(ctx, "inventory", "item", "date_dim")

    col = ctx.col

    date_filtered = date_dim.filter(col("d_month_seq").is_between(dms, dms + 11))

    inv_data = inventory.join(date_filtered, left_on="inv_date_sk", right_on="d_date_sk").join(
        item, left_on="inv_item_sk", right_on="i_item_sk"
    )

    group_cols = ["i_product_name", "i_brand", "i_class", "i_category"]
    agg_exprs = [ctx.mean("inv_quantity_on_hand").alias("qoh")]

    result = expand_rollup_expression(inv_data, group_cols, agg_exprs, ctx)
    return (
        result.select([*group_cols, "qoh"])
        .sort(["qoh", "i_product_name", "i_brand", "i_class", "i_category"], nulls_last=True)
        .head(100)
    )


def q22_pandas_impl(ctx: DataFrameContext) -> Any:
    from .rollup_helper import expand_rollup_pandas

    params = get_parameters(22)
    dms = params.get("dms", 1212)

    inventory, item, date_dim = _tables(ctx, "inventory", "item", "date_dim")

    date_filtered = date_dim[(date_dim["d_month_seq"] >= dms) & (date_dim["d_month_seq"] <= dms + 11)]

    inv_data = inventory.merge(date_filtered, left_on="inv_date_sk", right_on="d_date_sk")
    inv_data = inv_data.merge(item, left_on="inv_item_sk", right_on="i_item_sk")

    group_cols = ["i_product_name", "i_brand", "i_class", "i_category"]
    agg_dict = {"qoh": ("inv_quantity_on_hand", "mean")}

    result = expand_rollup_pandas(inv_data, group_cols, agg_dict, ctx)
    return (
        result[[*group_cols, "qoh"]]
        .sort_values(["qoh", "i_product_name", "i_brand", "i_class", "i_category"])
        .head(100)
    )


_Q39B_MIN_FIRST_MONTH_COV = 1.5


def q39_expression_impl(ctx: DataFrameContext) -> Any:
    return _q39_expression(ctx, first_month_min_cov=None)


def q39b_expression_impl(ctx: DataFrameContext) -> Any:
    return _q39_expression(ctx, first_month_min_cov=_Q39B_MIN_FIRST_MONTH_COV)


def _q39_expression(ctx: DataFrameContext, first_month_min_cov: float | None) -> Any:
    params = get_parameters(39)
    year = params.get("year", 2001)
    months = params.get("months", [1, 2])
    month1 = months[0] if len(months) > 0 else 1
    month2 = months[1] if len(months) > 1 else 2

    inventory, item, warehouse, date_dim = _tables(ctx, "inventory", "item", "warehouse", "date_dim")

    col = ctx.col
    lit = ctx.lit

    date_filtered = date_dim.filter(col("d_year") == lit(year))

    inv_data = (
        inventory.join(item, left_on="inv_item_sk", right_on="i_item_sk")
        .join(warehouse, left_on="inv_warehouse_sk", right_on="w_warehouse_sk")
        .join(date_filtered, left_on="inv_date_sk", right_on="d_date_sk")
    )

    grouped = inv_data.group_by(["w_warehouse_name", "inv_warehouse_sk", "inv_item_sk", "d_moy"]).agg(
        [
            ctx.std("inv_quantity_on_hand").alias("stdev"),
            ctx.mean("inv_quantity_on_hand").alias("mean"),
        ]
    )

    grouped = grouped.with_columns(
        ctx.when(col("mean") != lit(0)).then(col("stdev") / col("mean")).otherwise(lit(None)).alias("cov")
    )

    inv_cov = grouped.filter(
        ctx.when(col("mean") == lit(0)).then(lit(False)).otherwise(col("stdev") / col("mean") > lit(1))
    )

    inv1 = inv_cov.filter(col("d_moy") == lit(month1))
    if first_month_min_cov is not None:
        inv1 = inv1.filter(col("cov") > lit(first_month_min_cov))
    inv1 = inv1.select(
        [
            col("inv_warehouse_sk").alias("inv1_w_sk"),
            col("inv_item_sk").alias("inv1_i_sk"),
            col("d_moy").alias("inv1_moy"),
            col("mean").alias("inv1_mean"),
            col("cov").alias("inv1_cov"),
        ]
    )

    inv2 = inv_cov.filter(col("d_moy") == lit(month2)).select(
        [
            col("inv_warehouse_sk").alias("inv2_w_sk"),
            col("inv_item_sk").alias("inv2_i_sk"),
            col("d_moy").alias("inv2_moy"),
            col("mean").alias("inv2_mean"),
            col("cov").alias("inv2_cov"),
            col("inv_warehouse_sk").alias("_join_w_sk"),
            col("inv_item_sk").alias("_join_i_sk"),
        ]
    )

    return (
        inv1.join(
            inv2,
            left_on=["inv1_w_sk", "inv1_i_sk"],
            right_on=["_join_w_sk", "_join_i_sk"],
        )
        .select(
            [
                "inv1_w_sk",
                "inv1_i_sk",
                "inv1_moy",
                "inv1_mean",
                "inv1_cov",
                "inv2_w_sk",
                "inv2_i_sk",
                "inv2_moy",
                "inv2_mean",
                "inv2_cov",
            ]
        )
        .sort(["inv1_w_sk", "inv1_i_sk", "inv1_moy", "inv1_mean", "inv1_cov"])
    )


def q39_pandas_impl(ctx: DataFrameContext) -> Any:
    return _q39_pandas(ctx, first_month_min_cov=None)


def q39b_pandas_impl(ctx: DataFrameContext) -> Any:
    return _q39_pandas(ctx, first_month_min_cov=_Q39B_MIN_FIRST_MONTH_COV)


def _q39_pandas(ctx: DataFrameContext, first_month_min_cov: float | None) -> Any:
    params = get_parameters(39)
    year = params.get("year", 2001)
    months = params.get("months", [1, 2])
    month1 = months[0] if len(months) > 0 else 1
    month2 = months[1] if len(months) > 1 else 2

    inventory, item, warehouse, date_dim = _tables(ctx, "inventory", "item", "warehouse", "date_dim")

    date_filtered = date_dim[date_dim["d_year"] == year]

    inv_data = inventory.merge(item, left_on="inv_item_sk", right_on="i_item_sk")
    inv_data = inv_data.merge(warehouse, left_on="inv_warehouse_sk", right_on="w_warehouse_sk")
    inv_data = inv_data.merge(date_filtered, left_on="inv_date_sk", right_on="d_date_sk")

    grouped = inv_data.groupby(
        ["w_warehouse_name", "w_warehouse_sk", "i_item_sk", "d_moy"], as_index=False, dropna=False
    ).agg(
        stdev=("inv_quantity_on_hand", "std"),
        mean=("inv_quantity_on_hand", "mean"),
    )

    grouped["cov"] = grouped.apply(lambda r: r["stdev"] / r["mean"] if r["mean"] != 0 else None, axis=1)

    inv_cov = grouped[(grouped["mean"] != 0) & (grouped["stdev"] / grouped["mean"] > 1)].copy()

    inv1 = inv_cov[inv_cov["d_moy"] == month1]
    if first_month_min_cov is not None:
        inv1 = inv1[inv1["cov"] > first_month_min_cov]
    inv1 = inv1[["w_warehouse_sk", "i_item_sk", "d_moy", "mean", "cov"]].copy()
    inv1.columns = ["inv1_w_sk", "inv1_i_sk", "inv1_moy", "inv1_mean", "inv1_cov"]

    inv2 = inv_cov[inv_cov["d_moy"] == month2][["w_warehouse_sk", "i_item_sk", "d_moy", "mean", "cov"]].copy()
    inv2.columns = ["inv2_w_sk", "inv2_i_sk", "inv2_moy", "inv2_mean", "inv2_cov"]

    result = inv1.merge(inv2, left_on=["inv1_w_sk", "inv1_i_sk"], right_on=["inv2_w_sk", "inv2_i_sk"])
    return result.sort_values(["inv1_w_sk", "inv1_i_sk", "inv1_moy", "inv1_mean", "inv1_cov"])


def q64_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(64)
    year = params.get("year", 1999)
    colors = params.get("colors", ["slate", "blanched", "burnished", "chartreuse", "peru", "thistle"])
    price_min = params.get("price_min", 0)

    (
        store_sales,
        store_returns,
        catalog_sales,
        catalog_returns,
        date_dim,
        store,
        customer,
        customer_demographics,
        promotion,
        household_demographics,
        customer_address,
        income_band,
        item,
    ) = _tables(
        ctx,
        "store_sales",
        "store_returns",
        "catalog_sales",
        "catalog_returns",
        "date_dim",
        "store",
        "customer",
        "customer_demographics",
        "promotion",
        "household_demographics",
        "customer_address",
        "income_band",
        "item",
    )

    col = ctx.col
    lit = ctx.lit

    cs_with_returns = catalog_sales.join(
        catalog_returns,
        left_on=["cs_item_sk", "cs_order_number"],
        right_on=["cr_item_sk", "cr_order_number"],
    )
    cs_ui = (
        cs_with_returns.group_by("cs_item_sk")
        .agg(
            [
                _sum_or_null_expression(ctx, ctx.col("cs_ext_list_price")).alias("sale"),
                (
                    _sum_or_null_expression(ctx, ctx.col("cr_refunded_cash"))
                    + _sum_or_null_expression(ctx, ctx.col("cr_reversed_charge"))
                    + _sum_or_null_expression(ctx, ctx.col("cr_store_credit"))
                ).alias("refund"),
            ]
        )
        .filter(col("sale") > lit(2) * col("refund"))
        .select("cs_item_sk")
    )

    item_filtered = item.filter(
        col("i_color").is_in(colors)
        & (col("i_current_price") >= lit(price_min))
        & (col("i_current_price") <= lit(price_min + 10))
        & (col("i_current_price") >= lit(price_min + 1))
        & (col("i_current_price") <= lit(price_min + 15))
    )

    ss_sr = store_sales.join(
        store_returns,
        left_on=["ss_item_sk", "ss_ticket_number"],
        right_on=["sr_item_sk", "sr_ticket_number"],
    )

    ss_sr = ss_sr.join(cs_ui, left_on="ss_item_sk", right_on="cs_item_sk", how="semi")

    cross_sales = (
        ss_sr.join(item_filtered, left_on="ss_item_sk", right_on="i_item_sk")
        .join(store, left_on="ss_store_sk", right_on="s_store_sk")
        .join(customer, left_on="ss_customer_sk", right_on="c_customer_sk")
        .join(
            customer_demographics.select(["cd_demo_sk", "cd_marital_status"]).rename(
                {"cd_marital_status": "cd1_marital_status"}
            ),
            left_on="ss_cdemo_sk",
            right_on="cd_demo_sk",
        )
        .join(promotion, left_on="ss_promo_sk", right_on="p_promo_sk")
        .join(
            household_demographics.select(["hd_demo_sk", "hd_income_band_sk"]).rename(
                {"hd_income_band_sk": "hd1_ib_sk"}
            ),
            left_on="ss_hdemo_sk",
            right_on="hd_demo_sk",
        )
        .join(
            customer_address.select(
                ["ca_address_sk", "ca_street_number", "ca_street_name", "ca_city", "ca_zip"]
            ).rename(
                {
                    "ca_street_number": "b_street_number",
                    "ca_street_name": "b_street_name",
                    "ca_city": "b_city",
                    "ca_zip": "b_zip",
                }
            ),
            left_on="ss_addr_sk",
            right_on="ca_address_sk",
        )
        .join(
            date_dim.select(["d_date_sk", "d_year"]).rename({"d_year": "syear"}),
            left_on="ss_sold_date_sk",
            right_on="d_date_sk",
        )
        .join(
            date_dim.select(["d_date_sk", "d_year"]).rename({"d_date_sk": "d2_date_sk", "d_year": "fsyear"}),
            left_on="c_first_sales_date_sk",
            right_on="d2_date_sk",
        )
        .join(
            date_dim.select(["d_date_sk", "d_year"]).rename({"d_date_sk": "d3_date_sk", "d_year": "s2year"}),
            left_on="c_first_shipto_date_sk",
            right_on="d3_date_sk",
        )
        .join(
            customer_demographics.select(["cd_demo_sk", "cd_marital_status"]).rename(
                {"cd_marital_status": "cd2_marital_status", "cd_demo_sk": "cd2_demo_sk"}
            ),
            left_on="c_current_cdemo_sk",
            right_on="cd2_demo_sk",
        )
        .join(
            household_demographics.select(["hd_demo_sk", "hd_income_band_sk"]).rename(
                {"hd_income_band_sk": "hd2_ib_sk", "hd_demo_sk": "hd2_demo_sk"}
            ),
            left_on="c_current_hdemo_sk",
            right_on="hd2_demo_sk",
        )
        .join(
            customer_address.select(
                ["ca_address_sk", "ca_street_number", "ca_street_name", "ca_city", "ca_zip"]
            ).rename(
                {
                    "ca_address_sk": "c_addr_sk",
                    "ca_street_number": "c_street_number",
                    "ca_street_name": "c_street_name",
                    "ca_city": "c_city",
                    "ca_zip": "c_zip",
                }
            ),
            left_on="c_current_addr_sk",
            right_on="c_addr_sk",
        )
        .join(
            income_band.select(["ib_income_band_sk"]).rename({"ib_income_band_sk": "ib1_sk"}),
            left_on="hd1_ib_sk",
            right_on="ib1_sk",
        )
        .join(
            income_band.select(["ib_income_band_sk"]).rename({"ib_income_band_sk": "ib2_sk"}),
            left_on="hd2_ib_sk",
            right_on="ib2_sk",
        )
    )

    cross_sales = cross_sales.filter(col("cd1_marital_status") != col("cd2_marital_status"))

    grouped = cross_sales.group_by(
        [
            "i_product_name",
            "ss_item_sk",
            "s_store_name",
            "s_zip",
            "b_street_number",
            "b_street_name",
            "b_city",
            "b_zip",
            "c_street_number",
            "c_street_name",
            "c_city",
            "c_zip",
            "syear",
            "fsyear",
            "s2year",
        ]
    ).agg(
        [
            ctx.len().alias("cnt"),
            _sum_or_null_expression(ctx, ctx.col("ss_wholesale_cost")).alias("s1"),
            _sum_or_null_expression(ctx, ctx.col("ss_list_price")).alias("s2"),
            _sum_or_null_expression(ctx, ctx.col("ss_coupon_amt")).alias("s3"),
        ]
    )

    cs1 = grouped.filter(col("syear") == lit(year))
    cs2 = grouped.filter(col("syear") == lit(year + 1))

    return _sort_null_largest_expression(
        ctx,
        cs1.join(
            cs2,
            on=["ss_item_sk", "s_store_name", "s_zip"],
            suffix="_y2",
        )
        .filter(col("cnt_y2") <= col("cnt"))
        .select(
            [
                "i_product_name",
                "s_store_name",
                "s_zip",
                "b_street_number",
                "b_street_name",
                "b_city",
                "b_zip",
                "c_street_number",
                "c_street_name",
                "c_city",
                "c_zip",
                "syear",
                "cnt",
                "s1",
                "s2",
                "s3",
                col("s1_y2").alias("s12"),
                col("s2_y2").alias("s22"),
                col("s3_y2").alias("s32"),
                col("syear_y2").alias("syear2"),
                col("cnt_y2").alias("cnt2"),
            ]
        ),
        ["i_product_name", "s_store_name", "cnt2", "s1", "s12"],
    )


def q64_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(64)
    year = params.get("year", 1999)
    colors = params.get("colors", ["slate", "blanched", "burnished", "chartreuse", "peru", "thistle"])
    price_min = params.get("price_min", 0)

    (
        store_sales,
        store_returns,
        catalog_sales,
        catalog_returns,
        date_dim,
        store,
        customer,
        customer_demographics,
        promotion,
        household_demographics,
        customer_address,
        income_band,
        item,
    ) = _tables(
        ctx,
        "store_sales",
        "store_returns",
        "catalog_sales",
        "catalog_returns",
        "date_dim",
        "store",
        "customer",
        "customer_demographics",
        "promotion",
        "household_demographics",
        "customer_address",
        "income_band",
        "item",
    )

    cs_cr = catalog_sales.merge(
        catalog_returns,
        left_on=["cs_item_sk", "cs_order_number"],
        right_on=["cr_item_sk", "cr_order_number"],
    )
    cs_agg = _grouped_pandas_aggregates(
        cs_cr,
        "cs_item_sk",
        {
            "sale": ("cs_ext_list_price", "sum"),
            "refund_cash": ("cr_refunded_cash", "sum"),
            "refund_charge": ("cr_reversed_charge", "sum"),
            "refund_credit": ("cr_store_credit", "sum"),
        },
    )
    cs_agg["refund"] = cs_agg["refund_cash"] + cs_agg["refund_charge"] + cs_agg["refund_credit"]
    cs_ui_items = set(cs_agg[cs_agg["sale"] > 2 * cs_agg["refund"]]["cs_item_sk"])

    item_filtered = item[
        item["i_color"].isin(colors)
        & (item["i_current_price"] >= price_min)
        & (item["i_current_price"] <= price_min + 10)
        & (item["i_current_price"] >= price_min + 1)
        & (item["i_current_price"] <= price_min + 15)
    ]

    ss_sr = store_sales.merge(
        store_returns,
        left_on=["ss_item_sk", "ss_ticket_number"],
        right_on=["sr_item_sk", "sr_ticket_number"],
    )
    ss_sr = ss_sr[ss_sr["ss_item_sk"].isin(cs_ui_items)]

    cross_sales = ss_sr.merge(item_filtered, left_on="ss_item_sk", right_on="i_item_sk")
    cross_sales = cross_sales.merge(store, left_on="ss_store_sk", right_on="s_store_sk")
    cross_sales = cross_sales.merge(customer, left_on="ss_customer_sk", right_on="c_customer_sk")

    cd1 = customer_demographics[["cd_demo_sk", "cd_marital_status"]].copy()
    cd1.columns = ["cd1_demo_sk", "cd1_marital_status"]
    cross_sales = cross_sales.merge(cd1, left_on="ss_cdemo_sk", right_on="cd1_demo_sk")

    cross_sales = cross_sales.merge(promotion, left_on="ss_promo_sk", right_on="p_promo_sk")

    hd1 = household_demographics[["hd_demo_sk", "hd_income_band_sk"]].copy()
    hd1.columns = ["hd1_demo_sk", "hd1_ib_sk"]
    cross_sales = cross_sales.merge(hd1, left_on="ss_hdemo_sk", right_on="hd1_demo_sk")

    ca1 = customer_address[["ca_address_sk", "ca_street_number", "ca_street_name", "ca_city", "ca_zip"]].copy()
    ca1.columns = ["ca1_address_sk", "b_street_number", "b_street_name", "b_city", "b_zip"]
    cross_sales = cross_sales.merge(ca1, left_on="ss_addr_sk", right_on="ca1_address_sk")

    d1 = date_dim[["d_date_sk", "d_year"]].copy()
    d1.columns = ["d1_date_sk", "syear"]
    cross_sales = cross_sales.merge(d1, left_on="ss_sold_date_sk", right_on="d1_date_sk")

    d2 = date_dim[["d_date_sk", "d_year"]].copy()
    d2.columns = ["d2_date_sk", "fsyear"]
    cross_sales = cross_sales.merge(d2, left_on="c_first_sales_date_sk", right_on="d2_date_sk")

    d3 = date_dim[["d_date_sk", "d_year"]].copy()
    d3.columns = ["d3_date_sk", "s2year"]
    cross_sales = cross_sales.merge(d3, left_on="c_first_shipto_date_sk", right_on="d3_date_sk")

    cd2 = customer_demographics[["cd_demo_sk", "cd_marital_status"]].copy()
    cd2.columns = ["cd2_demo_sk", "cd2_marital_status"]
    cross_sales = cross_sales.merge(cd2, left_on="c_current_cdemo_sk", right_on="cd2_demo_sk")

    hd2 = household_demographics[["hd_demo_sk", "hd_income_band_sk"]].copy()
    hd2.columns = ["hd2_demo_sk", "hd2_ib_sk"]
    cross_sales = cross_sales.merge(hd2, left_on="c_current_hdemo_sk", right_on="hd2_demo_sk")

    ca2 = customer_address[["ca_address_sk", "ca_street_number", "ca_street_name", "ca_city", "ca_zip"]].copy()
    ca2.columns = ["ca2_address_sk", "c_street_number", "c_street_name", "c_city", "c_zip"]
    cross_sales = cross_sales.merge(ca2, left_on="c_current_addr_sk", right_on="ca2_address_sk")

    ib1 = income_band[["ib_income_band_sk"]].copy()
    ib1.columns = ["ib1_sk"]
    cross_sales = cross_sales.merge(ib1, left_on="hd1_ib_sk", right_on="ib1_sk")

    ib2 = income_band[["ib_income_band_sk"]].copy()
    ib2.columns = ["ib2_sk"]
    cross_sales = cross_sales.merge(ib2, left_on="hd2_ib_sk", right_on="ib2_sk")

    cross_sales = cross_sales[cross_sales["cd1_marital_status"] != cross_sales["cd2_marital_status"]]

    grouped = _grouped_pandas_aggregates(
        cross_sales,
        [
            "i_product_name",
            "i_item_sk",
            "s_store_name",
            "s_zip",
            "b_street_number",
            "b_street_name",
            "b_city",
            "b_zip",
            "c_street_number",
            "c_street_name",
            "c_city",
            "c_zip",
            "syear",
            "fsyear",
            "s2year",
        ],
        {
            "cnt": ("ss_item_sk", "count"),
            "s1": ("ss_wholesale_cost", "sum"),
            "s2": ("ss_list_price", "sum"),
            "s3": ("ss_coupon_amt", "sum"),
        },
        dropna=False,
    )

    join_keys = ["i_item_sk", "s_store_name", "s_zip"]
    cs1 = grouped[grouped["syear"] == year].dropna(subset=join_keys)
    cs2 = grouped[grouped["syear"] == year + 1].dropna(subset=join_keys)
    cs2 = cs2.rename(columns={"syear": "syear2", "cnt": "cnt2", "s1": "s12", "s2": "s22", "s3": "s32"})
    cs2 = cs2[[*join_keys, "syear2", "cnt2", "s12", "s22", "s32"]]

    result = cs1.merge(cs2, on=join_keys)
    result = result[result["cnt2"] <= result["cnt"]]
    result = result.sort_values(["i_product_name", "s_store_name", "cnt2", "s1", "s12"])[
        [
            "i_product_name",
            "s_store_name",
            "s_zip",
            "b_street_number",
            "b_street_name",
            "b_city",
            "b_zip",
            "c_street_number",
            "c_street_name",
            "c_city",
            "c_zip",
            "syear",
            "cnt",
            "s1",
            "s2",
            "s3",
            "s12",
            "s22",
            "s32",
            "syear2",
            "cnt2",
        ]
    ]
    return _none_for_null(result, list(result.columns))


def q84_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(84)
    city = params.get("city", "Edgewood")
    income_min = params.get("income_band", 38128)
    income_max = income_min + 50000

    customer, customer_address, customer_demographics, household_demographics, income_band, store_returns = _tables(
        ctx,
        "customer",
        "customer_address",
        "customer_demographics",
        "household_demographics",
        "income_band",
        "store_returns",
    )

    col = ctx.col
    lit = ctx.lit

    ca_filtered = customer_address.filter(col("ca_city") == lit(city))

    ib_filtered = income_band.filter(
        (col("ib_lower_bound") >= lit(income_min)) & (col("ib_upper_bound") <= lit(income_max))
    )

    result = (
        customer.join(ca_filtered, left_on="c_current_addr_sk", right_on="ca_address_sk")
        .join(customer_demographics, left_on="c_current_cdemo_sk", right_on="cd_demo_sk")
        .join(household_demographics, left_on="c_current_hdemo_sk", right_on="hd_demo_sk")
        .join(ib_filtered, left_on="hd_income_band_sk", right_on="ib_income_band_sk")
        .join(store_returns.select("sr_cdemo_sk"), left_on="c_current_cdemo_sk", right_on="sr_cdemo_sk")
    )

    return (
        result.with_columns(
            (ctx.coalesce(col("c_last_name"), lit("")) + lit(", ") + ctx.coalesce(col("c_first_name"), lit(""))).alias(
                "customername"
            )
        )
        .select(
            [
                col("c_customer_id").alias("customer_id"),
                "customername",
            ]
        )
        .sort("customer_id")
        .head(100)
    )


def q84_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters(84)
    city = params.get("city", "Edgewood")
    income_min = params.get("income_band", 38128)
    income_max = income_min + 50000

    customer, customer_address, customer_demographics, household_demographics, income_band, store_returns = _tables(
        ctx,
        "customer",
        "customer_address",
        "customer_demographics",
        "household_demographics",
        "income_band",
        "store_returns",
    )

    ca_filtered = customer_address[customer_address["ca_city"] == city]

    ib_filtered = income_band[
        (income_band["ib_lower_bound"] >= income_min) & (income_band["ib_upper_bound"] <= income_max)
    ]

    result = customer.merge(ca_filtered, left_on="c_current_addr_sk", right_on="ca_address_sk")
    result = result.merge(customer_demographics, left_on="c_current_cdemo_sk", right_on="cd_demo_sk")
    result = result.merge(household_demographics, left_on="c_current_hdemo_sk", right_on="hd_demo_sk")
    result = result.merge(ib_filtered, left_on="hd_income_band_sk", right_on="ib_income_band_sk")

    result = result.merge(store_returns[["sr_cdemo_sk"]], left_on="c_current_cdemo_sk", right_on="sr_cdemo_sk")

    result["customername"] = result["c_last_name"].fillna("") + ", " + result["c_first_name"].fillna("")
    result = result[["c_customer_id", "customername"]].copy()
    result.columns = ["customer_id", "customername"]
    return result.sort_values("customer_id").head(100)


_CATEGORY_CODES = {
    "A": QueryCategory.AGGREGATE,
    "F": QueryCategory.FILTER,
    "J": QueryCategory.JOIN,
    "M": QueryCategory.MULTI_JOIN,
    "N": QueryCategory.ANALYTICAL,
    "Q": QueryCategory.SUBQUERY,
    "S": QueryCategory.SORT,
    "T": QueryCategory.TPCDS,
    "W": QueryCategory.WINDOW,
}


def _impl_for(query_id: str, family: str) -> QueryImpl:
    name = f"q{query_id[1:].lower()}_{family}_impl"
    impl = globals().get(name)
    if impl is not None:
        return impl
    return _GENERATED_IMPLS[name]


def _load_queries() -> list[DataFrameQuery]:
    metadata = Path(__file__).with_name("query_metadata.csv").read_text(encoding="utf-8")
    return [
        DataFrameQuery(
            query_id=query_id,
            query_name=query_name,
            description=description,
            categories=[_CATEGORY_CODES[code] for code in category_codes.split(",")],
            expression_impl=_impl_for(query_id, "expression"),
            pandas_impl=_impl_for(query_id, "pandas"),
        )
        for query_id, query_name, description, category_codes in reader(metadata.splitlines(), delimiter="|")
    ]


configure_query_loader(_load_queries)
