# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from csv import reader
from typing import Any

from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.pandas_ops import is_lazy_frame
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory

from .registry import register_query


def q1_expression_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    col = ctx.col
    return trips.select(col("vendor_id").count().alias("count"))


def q1_pandas_impl(ctx: DataFrameContext) -> Any:

    import pandas as pd

    trips = ctx.get_table("trips")
    return pd.DataFrame({"count": [len(trips)]})


def q2_expression_impl(ctx: DataFrameContext) -> Any:
    trips = ctx.get_table("trips")
    col = ctx.col
    return trips.select(
        col("fare_amount").sum().alias("sum_fare_amount"),
        col("fare_amount").mean().alias("mean_fare_amount"),
    )


def q2_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    trips = ctx.get_table("trips")
    return pd.DataFrame(
        {
            "sum_fare_amount": [trips["fare_amount"].sum()],
            "mean_fare_amount": [trips["fare_amount"].mean()],
        }
    )


def q3_expression_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    col = ctx.col
    return (
        trips.group_by("passenger_count").agg(col("fare_amount").sum().alias("sum_fare_amount")).sort("passenger_count")
    )


def q3_pandas_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    return (
        trips.groupby(["passenger_count"], as_index=False)
        .agg(sum_fare_amount=("fare_amount", "sum"))
        .sort_values("passenger_count")
    )


def q4_expression_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    col = ctx.col
    return (
        trips.group_by("passenger_count")
        .agg(
            col("fare_amount").sum().alias("sum_fare_amount"),
            col("fare_amount").mean().alias("mean_fare_amount"),
        )
        .sort("passenger_count")
    )


def q4_pandas_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    return (
        trips.groupby(["passenger_count"], as_index=False)
        .agg(sum_fare_amount=("fare_amount", "sum"), mean_fare_amount=("fare_amount", "mean"))
        .sort_values("passenger_count")
    )


def q5_expression_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    col = ctx.col
    return (
        trips.group_by("passenger_count", "vendor_id")
        .agg(col("fare_amount").sum().alias("sum_fare_amount"))
        .sort(["passenger_count", "vendor_id"])
    )


def q5_pandas_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    return (
        trips.groupby(["passenger_count", "vendor_id"], as_index=False)
        .agg(sum_fare_amount=("fare_amount", "sum"))
        .sort_values(["passenger_count", "vendor_id"])
    )


def q6_expression_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    col = ctx.col
    return (
        trips.group_by("passenger_count", "vendor_id")
        .agg(
            col("fare_amount").sum().alias("sum_fare_amount"),
            col("fare_amount").mean().alias("mean_fare_amount"),
        )
        .sort(["passenger_count", "vendor_id"])
    )


def q6_pandas_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    return (
        trips.groupby(["passenger_count", "vendor_id"], as_index=False)
        .agg(sum_fare_amount=("fare_amount", "sum"), mean_fare_amount=("fare_amount", "mean"))
        .sort_values(["passenger_count", "vendor_id"])
    )


def q7_expression_impl(ctx: DataFrameContext) -> Any:
    trips = ctx.get_table("trips")
    col = ctx.col
    return (
        trips.with_columns(col("pickup_datetime").dt.hour().alias("hour"))
        .group_by("hour")
        .agg(col("fare_amount").sum().alias("sum_fare_amount"))
        .sort("hour")
    )


def q7_pandas_impl(ctx: DataFrameContext) -> Any:
    trips = ctx.get_table("trips")
    df = trips.copy()
    df["hour"] = df["pickup_datetime"].dt.hour
    return df.groupby(["hour"], as_index=False).agg(sum_fare_amount=("fare_amount", "sum")).sort_values("hour")


def q8_expression_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    col = ctx.col
    return (
        trips.with_columns(
            col("pickup_datetime").dt.year().alias("year"),
            col("pickup_datetime").dt.hour().alias("hour"),
        )
        .group_by("year", "hour")
        .agg(col("fare_amount").sum().alias("sum_fare_amount"))
        .sort(["year", "hour"])
    )


def q8_pandas_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    df = trips.copy()
    df["year"] = df["pickup_datetime"].dt.year
    df["hour"] = df["pickup_datetime"].dt.hour
    return (
        df.groupby(["year", "hour"], as_index=False)
        .agg(sum_fare_amount=("fare_amount", "sum"))
        .sort_values(["year", "hour"])
    )


def q9_expression_impl(ctx: DataFrameContext) -> Any:

    trips = ctx.get_table("trips")
    col = ctx.col
    return (
        trips.group_by("passenger_count")
        .agg(
            col("fare_amount").quantile(0.5, interpolation="linear").alias("median_fare_amount"),
            col("fare_amount").quantile(0.9, interpolation="linear").alias("p90_fare_amount"),
        )
        .sort("passenger_count")
    )


def _fare_quantiles(group: Any) -> Any:
    import pandas as pd

    fares = group["fare_amount"]
    return pd.Series(
        {
            "median_fare_amount": fares.quantile(0.5, interpolation="linear"),
            "p90_fare_amount": fares.quantile(0.9, interpolation="linear"),
        }
    )


def q9_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    trips = ctx.get_table("trips")
    if is_lazy_frame(trips):
        meta = pd.DataFrame(
            {
                "median_fare_amount": pd.Series([], dtype="float64"),
                "p90_fare_amount": pd.Series([], dtype="float64"),
            }
        )
        return (
            trips.groupby("passenger_count")[["fare_amount"]]
            .apply(_fare_quantiles, meta=meta)
            .reset_index()
            .sort_values("passenger_count")
        )
    grouped = trips.groupby("passenger_count")["fare_amount"]
    median = (
        grouped.quantile(0.5, interpolation="linear")
        .reset_index()
        .rename(columns={"fare_amount": "median_fare_amount"})
    )
    p90 = grouped.quantile(0.9, interpolation="linear").reset_index().rename(columns={"fare_amount": "p90_fare_amount"})
    return median.merge(p90, on="passenger_count").sort_values("passenger_count")


def q10_expression_impl(ctx: DataFrameContext) -> Any:
    trips = ctx.get_table("trips")
    col = ctx.col
    return (
        trips.filter(col("pickup_location_id").is_not_null())
        .group_by("pickup_location_id")
        .agg(col("pickup_location_id").count().alias("trip_count"))
        .sort(["trip_count", "pickup_location_id"], descending=[True, False])
        .limit(10)
    )


def q10_pandas_impl(ctx: DataFrameContext) -> Any:
    trips = ctx.get_table("trips")
    filtered = trips[trips["pickup_location_id"].notnull()]
    return (
        filtered.groupby(["pickup_location_id"], as_index=False)
        .agg(trip_count=("pickup_location_id", "count"))
        .sort_values(["trip_count", "pickup_location_id"], ascending=[False, True])
        .head(10)
    )


_CATEGORY_CODES = {
    "AG": QueryCategory.AGGREGATE,
    "AN": QueryCategory.ANALYTICAL,
    "FI": QueryCategory.FILTER,
    "GB": QueryCategory.GROUP_BY,
    "SC": QueryCategory.SCAN,
    "SO": QueryCategory.SORT,
}

_QUERY_METADATA = """\
Q1|Count|Basic COUNT(*) full table scan|SC,AG|q1
Q2|Sum and Mean|SUM and AVG of fare_amount (scalar aggregation)|AG|q2
Q3|Sum by Passenger Count|SUM(fare_amount) GROUP BY passenger_count|GB,AG|q3
Q4|Sum and Mean by Passenger Count|SUM and AVG of fare_amount GROUP BY passenger_count|GB,AG|q4
Q5|Sum by Passenger and Vendor|SUM(fare_amount) GROUP BY passenger_count, vendor_id|GB,AG|q5
Q6|Sum and Mean by Passenger and Vendor|SUM and AVG of fare_amount GROUP BY passenger_count, vendor_id|GB,AG|q6
Q7|Sum by Hour|SUM(fare_amount) GROUP BY EXTRACT(HOUR FROM pickup_datetime)|GB,AG,AN|q7
Q8|Sum by Year and Hour|SUM(fare_amount) GROUP BY EXTRACT(YEAR), EXTRACT(HOUR)|GB,AG,AN|q8
Q9|Percentiles by Passenger Count|PERCENTILE_CONT(0.5, 0.9) of fare_amount GROUP BY passenger_count|GB,AG,AN|q9
Q10|Top Pickup Locations|Top 10 pickup locations by trip count with WHERE IS NOT NULL|FI,GB,SO|q10
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
