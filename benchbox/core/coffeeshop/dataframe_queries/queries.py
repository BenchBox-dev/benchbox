# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from csv import reader
from datetime import date
from typing import Any


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _date_between(series: Any, start: str | date, end: str | date) -> Any:
    import pandas as pd

    low, high = _parse_date(start), _parse_date(end)
    dtype = series.dtype if isinstance(series, pd.Series) else None
    is_arrow_date = isinstance(dtype, pd.ArrowDtype) and str(dtype).startswith(("date32", "date64"))
    if isinstance(series, pd.Series) and pd.api.types.is_datetime64_any_dtype(series) and not is_arrow_date:
        low, high = pd.Timestamp(low), pd.Timestamp(high)
    return (series >= low) & (series <= high)


def _hour_pandas(series: Any) -> Any:
    import pandas as pd

    if isinstance(series, pd.Series) and (
        pd.api.types.is_datetime64_any_dtype(series) or isinstance(series.dtype, pd.PeriodDtype)
    ):
        return series.dt.hour.astype("int64")
    if isinstance(series, pd.Series) and pd.api.types.is_timedelta64_dtype(series):
        return (series.dt.total_seconds() // 3600).astype("int64")
    return series.astype("string").str[:2].astype("int64")


def _is_temporal_expression_column(frame: Any, name: str) -> bool:
    try:
        native = getattr(frame, "native", frame)
        if hasattr(native, "collect_schema"):
            dtype = native.collect_schema().get(name)
        elif hasattr(native, "schema") and not callable(native.schema):
            dtype = dict(native.schema).get(name)
        else:
            return False
        if dtype is None:
            return False
        type_name = type(dtype).__name__.lower()
        return any(token in type_name for token in ("time", "date", "datetime", "timestamp"))
    except Exception:
        return False


from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.pandas_ops import select as pandas_select, where as pandas_where
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory

from .parameters import get_parameters
from .registry import register_query


def sa1_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("SA1")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2023-01-31")

    ol = ctx.get_table("order_lines")
    dl = ctx.get_table("dim_locations")
    col = ctx.col
    lit = ctx.lit

    return (
        ol.filter(
            (col("order_date") >= lit(_parse_date(start_date))) & (col("order_date") <= lit(_parse_date(end_date)))
        )
        .join(dl, left_on="location_record_id", right_on="record_id")
        .group_by("order_date", "region")
        .agg(
            col("order_id").n_unique().alias("order_count"),
            col("total_price").sum().alias("gross_revenue"),
        )
        .with_columns(
            ctx.when(col("order_count") != lit(0))
            .then(col("gross_revenue") / col("order_count"))
            .otherwise(lit(None))
            .alias("avg_order_value")
        )
        .sort(["order_date", "region"])
    )


def sa1_pandas_impl(ctx: DataFrameContext) -> Any:
    import numpy as np

    params = get_parameters("SA1")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2023-01-31")

    ol = ctx.get_table("order_lines")
    dl = ctx.get_table("dim_locations")
    filtered = ol[_date_between(ol["order_date"], start_date, end_date)]
    merged = filtered.drop(columns=["location_id", "region"], errors="ignore").merge(
        dl, left_on="location_record_id", right_on="record_id"
    )
    grouped = merged.groupby(["order_date", "region"], as_index=False).agg(
        order_count=("order_id", "nunique"),
        gross_revenue=("total_price", "sum"),
    )
    grouped["avg_order_value"] = pandas_where(
        grouped["order_count"] != 0, grouped["gross_revenue"] / grouped["order_count"], np.nan
    )
    return grouped.sort_values(["order_date", "region"])


def sa2_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("SA2")
    year = params.get("year", 2023)
    limit = params.get("limit", 15)

    ol = ctx.get_table("order_lines")
    dp = ctx.get_table("dim_products")
    col = ctx.col
    lit = ctx.lit

    return (
        ol.with_columns(col("order_date").dt.year().alias("year"))
        .filter(col("year") == lit(year))
        .join(dp, left_on="product_record_id", right_on="record_id")
        .group_by("subcategory", "name")
        .agg(
            col("quantity").sum().alias("total_quantity"),
            col("total_price").sum().alias("total_revenue"),
        )
        .rename({"name": "product_name"})
        .sort("total_revenue", descending=True)
        .limit(limit)
    )


def sa2_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("SA2")
    year = params.get("year", 2023)
    limit = params.get("limit", 15)

    ol = ctx.get_table("order_lines")
    dp = ctx.get_table("dim_products")
    filtered = ol[ol["order_date"].dt.year == year]
    merged = filtered.drop(columns=["product_id"], errors="ignore").merge(
        dp, left_on="product_record_id", right_on="record_id"
    )
    return (
        merged.groupby(["subcategory", "name"], as_index=False)
        .agg(total_quantity=("quantity", "sum"), total_revenue=("total_price", "sum"))
        .rename(columns={"name": "product_name"})
        .sort_values("total_revenue", ascending=False)
        .head(limit)
    )


def sa3_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("SA3")
    year = params.get("year", 2023)

    ol = ctx.get_table("order_lines")
    col = ctx.col
    lit = ctx.lit

    return (
        ol.with_columns(
            col("order_date").dt.year().alias("year"),
            col("order_date").dt.month().alias("month"),
        )
        .filter(col("year") == lit(year))
        .group_by("year", "month")
        .agg(
            col("order_id").n_unique().alias("orders"),
            col("quantity").sum().alias("items_sold"),
            col("total_price").sum().alias("revenue"),
        )
        .with_columns(
            ctx.when(col("orders") != lit(0))
            .then(col("revenue") / col("orders"))
            .otherwise(lit(None))
            .alias("avg_order_value"),
            ctx.when(col("orders") != lit(0))
            .then(col("items_sold") / col("orders"))
            .otherwise(lit(None))
            .alias("avg_items_per_order"),
        )
        .sort(["year", "month"])
    )


def sa3_pandas_impl(ctx: DataFrameContext) -> Any:
    import numpy as np

    params = get_parameters("SA3")
    year = params.get("year", 2023)

    ol = ctx.get_table("order_lines")
    filtered = ol[ol["order_date"].dt.year == year].copy()
    filtered["year"] = filtered["order_date"].dt.year
    filtered["month"] = filtered["order_date"].dt.month
    grouped = filtered.groupby(["year", "month"], as_index=False).agg(
        orders=("order_id", "nunique"),
        items_sold=("quantity", "sum"),
        revenue=("total_price", "sum"),
    )
    grouped["avg_order_value"] = pandas_where(grouped["orders"] != 0, grouped["revenue"] / grouped["orders"], np.nan)
    grouped["avg_items_per_order"] = pandas_where(
        grouped["orders"] != 0, grouped["items_sold"] / grouped["orders"], np.nan
    )
    return grouped.sort_values(["year", "month"])


def sa4_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("SA4")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2024-12-31")

    ol = ctx.get_table("order_lines")
    dl = ctx.get_table("dim_locations")
    col = ctx.col
    lit = ctx.lit

    grouped = (
        ol.filter(
            (col("order_date") >= lit(_parse_date(start_date))) & (col("order_date") <= lit(_parse_date(end_date)))
        )
        .join(dl, left_on="location_record_id", right_on="record_id")
        .group_by("region")
        .agg(
            col("order_id").n_unique().alias("orders"),
            col("total_price").sum().alias("revenue"),
        )
    )
    total_val = grouped.select(col("revenue").sum().alias("total")).scalar()
    return grouped.with_columns(
        ctx.when(lit(total_val) != lit(0))
        .then(col("revenue") / lit(total_val))
        .otherwise(lit(0.0))
        .alias("revenue_share")
    ).sort("revenue", descending=True)


def sa4_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("SA4")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2024-12-31")

    ol = ctx.get_table("order_lines")
    dl = ctx.get_table("dim_locations")
    filtered = ol[_date_between(ol["order_date"], start_date, end_date)]
    merged = filtered.drop(columns=["location_id", "region"], errors="ignore").merge(
        dl, left_on="location_record_id", right_on="record_id"
    )
    grouped = merged.groupby(["region"], as_index=False).agg(
        orders=("order_id", "nunique"),
        revenue=("total_price", "sum"),
    )
    total = grouped["revenue"].sum()
    grouped["revenue_share"] = grouped["revenue"] / total if total != 0 else 0
    return grouped.sort_values("revenue", ascending=False)


def sa5_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("SA5")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2024-12-31")
    limit = params.get("limit", 20)

    ol = ctx.get_table("order_lines")
    dl = ctx.get_table("dim_locations")
    col = ctx.col
    lit = ctx.lit

    return (
        ol.filter(
            (col("order_date") >= lit(_parse_date(start_date))) & (col("order_date") <= lit(_parse_date(end_date)))
        )
        .join(dl, left_on="location_record_id", right_on="record_id")
        .group_by("location_id", "city", "state", "region")
        .agg(
            col("order_id").n_unique().alias("orders"),
            col("total_price").sum().alias("revenue"),
            col("total_price").mean().alias("avg_line_value"),
        )
        .sort("revenue", descending=True)
        .limit(limit)
    )


def sa5_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("SA5")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2024-12-31")
    limit = params.get("limit", 20)

    ol = ctx.get_table("order_lines")
    dl = ctx.get_table("dim_locations")
    filtered = ol[_date_between(ol["order_date"], start_date, end_date)]
    merged = filtered.drop(columns=["location_id", "region"], errors="ignore").merge(
        dl, left_on="location_record_id", right_on="record_id"
    )
    return (
        merged.groupby(["location_id", "city", "state", "region"], as_index=False)
        .agg(orders=("order_id", "nunique"), revenue=("total_price", "sum"), avg_line_value=("total_price", "mean"))
        .sort_values("revenue", ascending=False)
        .head(limit)
    )


def pr1_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("PR1")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2023-12-31")

    ol = ctx.get_table("order_lines")
    dp = ctx.get_table("dim_products")
    col = ctx.col
    lit = ctx.lit

    return (
        ol.filter(
            (col("order_date") >= lit(_parse_date(start_date))) & (col("order_date") <= lit(_parse_date(end_date)))
        )
        .join(dp, left_on="product_record_id", right_on="record_id")
        .filter((col("order_date") >= col("from_date")) & (col("order_date") <= col("to_date")))
        .group_by("subcategory")
        .agg(
            col("product_id").n_unique().alias("active_products"),
            col("quantity").sum().alias("quantity_sold"),
            col("total_price").sum().alias("revenue"),
        )
        .sort("revenue", descending=True)
    )


def pr1_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("PR1")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2023-12-31")

    ol = ctx.get_table("order_lines")
    dp = ctx.get_table("dim_products")
    filtered = ol[_date_between(ol["order_date"], start_date, end_date)]
    merged = filtered.drop(columns=["product_id"], errors="ignore").merge(
        dp, left_on="product_record_id", right_on="record_id"
    )
    merged = merged[(merged["order_date"] >= merged["from_date"]) & (merged["order_date"] <= merged["to_date"])]
    return (
        merged.groupby(["subcategory"], as_index=False)
        .agg(
            active_products=("product_id", "nunique"), quantity_sold=("quantity", "sum"), revenue=("total_price", "sum")
        )
        .sort_values("revenue", ascending=False)
    )


def pr2_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("PR2")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2024-12-31")

    ol = ctx.get_table("order_lines")
    col = ctx.col
    lit = ctx.lit

    return (
        ol.filter(
            (col("order_date") >= lit(_parse_date(start_date))) & (col("order_date") <= lit(_parse_date(end_date)))
        )
        .with_columns(
            ctx.when(col("unit_price") < lit(4))
            .then(lit("Under $4"))
            .otherwise(
                ctx.when(col("unit_price") < lit(6))
                .then(lit("$4-$5.99"))
                .otherwise(ctx.when(col("unit_price") < lit(8)).then(lit("$6-$7.99")).otherwise(lit("$8+")))
            )
            .alias("price_band")
        )
        .group_by("price_band")
        .agg(
            col("price_band").count().alias("line_count"),
            col("total_price").sum().alias("revenue"),
        )
        .sort("price_band")
    )


def pr2_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("PR2")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2024-12-31")

    ol = ctx.get_table("order_lines")
    filtered = ol[_date_between(ol["order_date"], start_date, end_date)].copy()
    conditions = [
        filtered["unit_price"] < 4,
        filtered["unit_price"] < 6,
        filtered["unit_price"] < 8,
    ]
    choices = ["Under $4", "$4-$5.99", "$6-$7.99"]
    filtered["price_band"] = pandas_select(conditions, choices, "$8+")
    return (
        filtered.groupby(["price_band"], as_index=False)
        .agg(line_count=("price_band", "count"), revenue=("total_price", "sum"))
        .sort_values("price_band")
    )


def tr1_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("TR1")
    start_year = params.get("start_year", 2023)
    end_year = params.get("end_year", 2024)

    ol = ctx.get_table("order_lines")
    col = ctx.col
    lit = ctx.lit

    return (
        ol.with_columns(
            col("order_date").dt.year().alias("year"),
            col("order_date").dt.month().alias("month"),
        )
        .filter((col("year") >= lit(start_year)) & (col("year") <= lit(end_year)))
        .with_columns((((col("month") - lit(1)) / lit(3)).floor().cast_int() + lit(1)).alias("quarter"))
        .group_by("year", "quarter")
        .agg(
            col("order_id").n_unique().alias("orders"),
            col("total_price").sum().alias("revenue"),
        )
        .sort(["year", "quarter"])
    )


def tr1_pandas_impl(ctx: DataFrameContext) -> Any:
    import numpy as np

    params = get_parameters("TR1")
    start_year = params.get("start_year", 2023)
    end_year = params.get("end_year", 2024)

    ol = ctx.get_table("order_lines")
    filtered = ol[(ol["order_date"].dt.year >= start_year) & (ol["order_date"].dt.year <= end_year)].copy()
    filtered["year"] = filtered["order_date"].dt.year
    filtered["quarter"] = np.floor((filtered["order_date"].dt.month - 1) / 3).astype(int) + 1
    return (
        filtered.groupby(["year", "quarter"], as_index=False)
        .agg(orders=("order_id", "nunique"), revenue=("total_price", "sum"))
        .sort_values(["year", "quarter"])
    )


def tm1_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("TM1")
    region = params.get("region", "South")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2024-12-31")

    ol = ctx.get_table("order_lines")
    dl = ctx.get_table("dim_locations")
    col = ctx.col
    lit = ctx.lit

    joined = (
        ol.filter(
            (col("order_date") >= lit(_parse_date(start_date))) & (col("order_date") <= lit(_parse_date(end_date)))
        )
        .join(dl, left_on="location_record_id", right_on="record_id")
        .filter(col("region") == lit(region))
    )
    if _is_temporal_expression_column(joined, "order_time"):
        hour_expr = col("order_time").dt.hour()
    else:
        hour_expr = col("order_time").str.slice(0, 2).cast_int()
    with_hour = joined.with_columns(hour_expr.alias("hour"))
    with_part = with_hour.with_columns(
        ctx.when((col("hour") >= lit(5)) & (col("hour") <= lit(10)))
        .then(lit("Morning"))
        .otherwise(
            ctx.when((col("hour") >= lit(11)) & (col("hour") <= lit(14)))
            .then(lit("Midday"))
            .otherwise(
                ctx.when((col("hour") >= lit(15)) & (col("hour") <= lit(17)))
                .then(lit("Afternoon"))
                .otherwise(lit("Evening"))
            )
        )
        .alias("day_part")
    )
    return (
        with_part.group_by("day_part")
        .agg(
            col("order_id").n_unique().alias("orders"),
            col("total_price").sum().alias("revenue"),
            col("total_price").mean().alias("avg_line_value"),
        )
        .sort("day_part")
    )


def tm1_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("TM1")
    region = params.get("region", "South")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2024-12-31")

    ol = ctx.get_table("order_lines")
    dl = ctx.get_table("dim_locations")
    filtered = ol[_date_between(ol["order_date"], start_date, end_date)]
    merged = filtered.drop(columns=["location_id", "region"], errors="ignore").merge(
        dl, left_on="location_record_id", right_on="record_id"
    )
    merged = merged[merged["region"] == region].copy()
    merged["hour"] = _hour_pandas(merged["order_time"])
    conditions = [
        (merged["hour"] >= 5) & (merged["hour"] <= 10),
        (merged["hour"] >= 11) & (merged["hour"] <= 14),
        (merged["hour"] >= 15) & (merged["hour"] <= 17),
    ]
    choices = ["Morning", "Midday", "Afternoon"]
    merged["day_part"] = pandas_select(conditions, choices, "Evening")
    return (
        merged.groupby(["day_part"], as_index=False)
        .agg(orders=("order_id", "nunique"), revenue=("total_price", "sum"), avg_line_value=("total_price", "mean"))
        .sort_values("day_part")
    )


def qc1_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("QC1")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2024-12-31")

    ol = ctx.get_table("order_lines")
    col = ctx.col
    lit = ctx.lit

    filtered = ol.filter(
        (col("order_date") >= lit(_parse_date(start_date))) & (col("order_date") <= lit(_parse_date(end_date)))
    )

    return filtered.select(
        ctx.when(col("order_id").n_unique() != lit(0))
        .then(col("order_id").count().cast_float() / col("order_id").n_unique().cast_float())
        .otherwise(lit(None))
        .alias("avg_lines_per_order"),
        ctx.when(col("order_id").n_unique() != lit(0))
        .then(col("quantity").sum().cast_float() / col("order_id").n_unique().cast_float())
        .otherwise(lit(None))
        .alias("avg_items_per_order"),
    )


def qc1_pandas_impl(ctx: DataFrameContext) -> Any:
    import numpy as np
    import pandas as pd

    params = get_parameters("QC1")
    start_date = params.get("start_date", "2023-01-01")
    end_date = params.get("end_date", "2024-12-31")

    ol = ctx.get_table("order_lines")
    filtered = ol[_date_between(ol["order_date"], start_date, end_date)]
    total_lines = len(filtered)
    unique_orders = filtered["order_id"].nunique()
    total_items = filtered["quantity"].sum()
    return pd.DataFrame(
        {
            "avg_lines_per_order": [total_lines / unique_orders if unique_orders else np.nan],
            "avg_items_per_order": [total_items / unique_orders if unique_orders else np.nan],
        }
    )


def qc2_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("QC2")
    start_year = params.get("start_year", 2023)
    end_year = params.get("end_year", 2024)

    ol = ctx.get_table("order_lines")
    dl = ctx.get_table("dim_locations")
    col = ctx.col
    lit = ctx.lit

    return (
        ol.with_columns(
            col("order_date").dt.year().alias("year"),
            col("order_date").dt.month().alias("month"),
        )
        .filter((col("year") >= lit(start_year)) & (col("year") <= lit(end_year)))
        .join(dl, left_on="location_record_id", right_on="record_id")
        .with_columns(
            ctx.when(col("month").is_in([12, 1, 2]))
            .then(lit("Winter"))
            .otherwise(
                ctx.when(col("month").is_in([3, 4, 5]))
                .then(lit("Spring"))
                .otherwise(ctx.when(col("month").is_in([6, 7, 8])).then(lit("Summer")).otherwise(lit("Fall")))
            )
            .alias("season")
        )
        .group_by("region", "season")
        .agg(
            col("total_price").sum().alias("revenue"),
            col("order_id").n_unique().alias("orders"),
        )
        .sort(["region", "season"])
    )


def qc2_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("QC2")
    start_year = params.get("start_year", 2023)
    end_year = params.get("end_year", 2024)

    ol = ctx.get_table("order_lines")
    dl = ctx.get_table("dim_locations")
    filtered = ol[(ol["order_date"].dt.year >= start_year) & (ol["order_date"].dt.year <= end_year)].copy()
    merged = filtered.drop(columns=["location_id", "region"], errors="ignore").merge(
        dl, left_on="location_record_id", right_on="record_id"
    )
    month = merged["order_date"].dt.month
    conditions = [
        month.isin([12, 1, 2]),
        month.isin([3, 4, 5]),
        month.isin([6, 7, 8]),
    ]
    choices = ["Winter", "Spring", "Summer"]
    merged["season"] = pandas_select(conditions, choices, "Fall")
    return (
        merged.groupby(["region", "season"], as_index=False)
        .agg(revenue=("total_price", "sum"), orders=("order_id", "nunique"))
        .sort_values(["region", "season"])
    )


_CATEGORY_CODES = {
    "AG": QueryCategory.AGGREGATE,
    "AN": QueryCategory.ANALYTICAL,
    "FI": QueryCategory.FILTER,
    "GB": QueryCategory.GROUP_BY,
    "JO": QueryCategory.JOIN,
    "SO": QueryCategory.SORT,
    "WI": QueryCategory.WINDOW,
}

_QUERY_METADATA = """\
SA1|Daily Revenue by Region|Daily revenue and order volume by region with NULLIF avg order value|FI,JO,GB,AG|sa1
SA2|Top Products by Revenue|Top products by revenue for a given year with JOIN to dim_products|FI,JO,GB,SO|sa2
SA3|Monthly Performance|Monthly orders, items sold, revenue with NULLIF derived metrics|FI,GB,AG,AN|sa3
SA4|Revenue Share by Region|Revenue share by region using SUM OVER() window function|FI,JO,GB,WI|sa4
SA5|Top Locations|Top-performing locations by revenue with multi-column GROUP BY|FI,JO,GB,SO|sa5
PR1|Product Mix|Product mix by subcategory with date validity check on dim_products|FI,JO,GB,AG|pr1
PR2|Price Band Distribution|Price-band distribution using multi-way CASE WHEN binning|FI,GB,AG,AN|pr2
TR1|Quarterly Trends|Quarterly revenue and order growth using FLOOR quarter calculation|FI,GB,AG,AN|tr1
TM1|Day-Part Cadence|Order cadence by day-part (Morning/Midday/Afternoon/Evening) for a region|FI,JO,GB,AN|tm1
QC1|Lines per Order|Average lines and items per order with NULLIF protection|FI,AG|qc1
QC2|Seasonal Revenue|Seasonal revenue comparison across regions using CASE WHEN month mapping|FI,JO,GB,AN|qc2
"""


def _impl_for(stem: str, family: str) -> Any:
    return globals()[f"{stem}_{family}_impl"]


def _register_all_queries() -> None:
    for query_id, query_name, description, category_codes, impl_stem in reader(
        _QUERY_METADATA.splitlines(), delimiter="|"
    ):
        register_query(
            DataFrameQuery(
                query_id=query_id,
                query_name=query_name,
                description=description,
                categories=[_CATEGORY_CODES[code] for code in category_codes.split(",")],
                expression_impl=_impl_for(impl_stem, "expression"),
                pandas_impl=_impl_for(impl_stem, "pandas"),
            )
        )


_register_all_queries()
