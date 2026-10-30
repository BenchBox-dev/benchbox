from __future__ import annotations

from csv import reader
from typing import Any

import numpy as np

from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.pandas_ops import select as pandas_select, where as pandas_where
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory

from .parameters import get_parameters
from .registry import register_query


def _items(value: str) -> list[str]:
    return [item for item in value.split(",") if item]


def _date_bounds(query_id: str, default_start: str, default_end: str) -> tuple[Any, Any]:
    params = get_parameters(query_id)
    return params.get("start_date", default_start), params.get("end_date", default_end)


def _expression_window(
    ctx: DataFrameContext, query_id: str, start: str, end: str, extra: Any = None
) -> tuple[Any, Any, Any]:
    start_date, end_date = _date_bounds(query_id, start, end)
    col, lit = ctx.col, ctx.lit
    condition = (col("pickup_datetime") >= lit(start_date)) & (col("pickup_datetime") < lit(end_date))
    if extra is not None:
        extras = extra(col, lit)
        for extra_condition in extras if isinstance(extras, tuple) else (extras,):
            condition = condition & extra_condition
    return ctx.get_table("trips").filter(condition), col, lit


def _pandas_window(
    ctx: DataFrameContext, query_id: str, start: str, end: str, *, copy: bool = False, extra: Any = None
) -> Any:
    start_date, end_date = _date_bounds(query_id, start, end)
    trips = ctx.get_table("trips")
    condition = (trips["pickup_datetime"] >= start_date) & (trips["pickup_datetime"] < end_date)
    if extra is not None:
        condition &= extra(trips)
    filtered = trips[condition]
    return filtered.copy() if copy else filtered


def _expression_top_zone(ctx: DataFrameContext, query_id: str, location_col: str) -> Any:
    trips, col, _ = _expression_window(ctx, query_id, "2019-01-01", "2019-01-31")
    return (
        trips.join(ctx.get_table("taxi_zones"), left_on=location_col, right_on="location_id", how="left")
        .group_by(location_col, "zone", "borough")
        .agg(col(location_col).count().alias("trip_count"))
        .sort("trip_count", descending=True)
        .limit(20)
    )


def _pandas_top_zone(ctx: DataFrameContext, query_id: str, location_col: str) -> Any:
    merged = _pandas_window(ctx, query_id, "2019-01-01", "2019-01-31").merge(
        ctx.get_table("taxi_zones"), left_on=location_col, right_on="location_id", how="left"
    )
    return (
        merged.groupby([location_col, "zone", "borough"], as_index=False, dropna=False)
        .agg(trip_count=(location_col, "count"))
        .sort_values("trip_count", ascending=False)
        .head(20)
    )


def _expression_trip_summary(ctx: DataFrameContext, query_id: str, end: str, group_col: str, *, tip: bool) -> Any:
    trips, col, _ = _expression_window(ctx, query_id, "2019-01-01", end)
    aggs = [
        col(group_col).count().alias("trip_count"),
        col("trip_distance").mean().alias("avg_distance"),
        col("total_amount").mean().alias("avg_fare"),
    ]
    if tip:
        aggs.append(col("tip_amount").mean().alias("avg_tip"))
    aggs.append(col("total_amount").sum().alias("total_revenue"))
    return trips.group_by(group_col).agg(*aggs).sort("trip_count", descending=True)


def _pandas_trip_summary(ctx: DataFrameContext, query_id: str, end: str, group_col: str, *, tip: bool) -> Any:
    aggs = {
        "trip_count": (group_col, "count"),
        "avg_distance": ("trip_distance", "mean"),
        "avg_fare": ("total_amount", "mean"),
    }
    if tip:
        aggs["avg_tip"] = ("tip_amount", "mean")
    aggs["total_revenue"] = ("total_amount", "sum")
    return (
        _pandas_window(ctx, query_id, "2019-01-01", end)
        .groupby([group_col], as_index=False, dropna=False)
        .agg(**aggs)
        .sort_values("trip_count", ascending=False)
    )


def _expr_extra(name: str) -> Any:
    return {
        "fare_positive": lambda col, lit: col("fare_amount") > lit(0),
        "fare_0_100": lambda col, lit: (col("fare_amount") >= lit(0), col("fare_amount") <= lit(100)),
        "card": lambda col, lit: col("payment_type") == lit(1),
        "distance_0_30": lambda col, lit: (col("trip_distance") >= lit(0), col("trip_distance") <= lit(30)),
        "passenger_1_6": lambda col, lit: (col("passenger_count") >= lit(1), col("passenger_count") <= lit(6)),
        "duration_positive": lambda col, _lit: col("dropoff_datetime") > col("pickup_datetime"),
        "airport_rate": lambda col, _lit: col("rate_code_id").is_in([2, 3]),
    }[name]


def _pandas_extra(name: str) -> Any:
    return {
        "fare_positive": lambda t: t["fare_amount"] > 0,
        "fare_0_100": lambda t: (t["fare_amount"] >= 0) & (t["fare_amount"] <= 100),
        "card": lambda t: t["payment_type"] == 1,
        "distance_0_30": lambda t: (t["trip_distance"] >= 0) & (t["trip_distance"] <= 30),
        "passenger_1_6": lambda t: (t["passenger_count"] >= 1) & (t["passenger_count"] <= 6),
        "duration_positive": lambda t: t["dropoff_datetime"] > t["pickup_datetime"],
        "airport_rate": lambda t: t["rate_code_id"].isin([2, 3]),
    }[name]


def _expr_derive(ctx: DataFrameContext, frame: Any, names: str) -> Any:
    col, lit = ctx.col, ctx.lit
    for name in _items(names):
        if name == "hour":
            frame = frame.with_columns(col("pickup_datetime").dt.hour().alias("hour"))
        elif name == "day":
            frame = frame.with_columns(col("pickup_datetime").dt.truncate("1d").alias("day"))
        elif name == "month":
            frame = frame.with_columns(col("pickup_datetime").dt.truncate("1mo").alias("month"))
        elif name == "day_of_week":
            frame = frame.with_columns(
                (
                    (col("pickup_datetime").dt.weekday() + lit(1))
                    - ((col("pickup_datetime").dt.weekday() + lit(1)) / lit(7)).floor() * lit(7)
                ).alias("day_of_week")
            )
        elif name == "tip_pct_nonzero":
            frame = frame.with_columns(
                ctx.when(col("fare_amount") != lit(0))
                .then(col("tip_amount") / col("fare_amount") * lit(100))
                .otherwise(lit(None))
                .alias("tip_pct")
            )
        elif name == "tip_pct_positive":
            frame = frame.with_columns(
                ctx.when(col("fare_amount") > lit(0))
                .then(col("tip_amount") / col("fare_amount") * lit(100))
                .otherwise(lit(None))
                .alias("tip_pct")
            )
        elif name == "fare_bucket":
            frame = frame.with_columns(((col("fare_amount") / lit(5)).floor() * lit(5)).alias("fare_bucket"))
        elif name == "distance_miles":
            frame = frame.with_columns(col("trip_distance").floor().alias("distance_miles"))
        elif name == "fare_per_mile":
            frame = frame.with_columns(
                ctx.when(col("trip_distance") != lit(0))
                .then(col("total_amount") / col("trip_distance"))
                .otherwise(lit(None))
                .alias("fare_per_mile")
            )
        elif name == "duration":
            frame = frame.with_columns(
                ((col("dropoff_datetime") - col("pickup_datetime")).dt.total_seconds()).alias("duration_sec")
            )
        elif name == "duration_bucket":
            frame = frame.filter((col("duration_sec") >= lit(60)) & (col("duration_sec") <= lit(7200))).with_columns(
                ((col("duration_sec") / lit(60) / lit(5)).floor() * lit(5)).alias("duration_bucket_min")
            )
        elif name == "day_type":
            dowi = col("pickup_datetime").dt.weekday() + lit(1)
            dow = dowi - (dowi / lit(7)).floor() * lit(7)
            frame = frame.with_columns(
                col("pickup_datetime").dt.hour().alias("hour"),
                ctx.when(dow.is_in([0, 6])).then(lit("weekend")).otherwise(lit("weekday")).alias("day_type"),
            )
        elif name == "period":
            frame = frame.with_columns(
                col("pickup_datetime").dt.hour().alias("hour"),
                ((col("dropoff_datetime") - col("pickup_datetime")).dt.total_seconds() / lit(60)).alias("duration_min"),
            )
            frame = frame.with_columns(
                ctx.when((col("hour") >= lit(7)) & (col("hour") <= lit(9)))
                .then(lit("morning_rush"))
                .otherwise(
                    ctx.when((col("hour") >= lit(17)) & (col("hour") <= lit(19)))
                    .then(lit("evening_rush"))
                    .otherwise(lit("off_peak"))
                )
                .alias("period")
            )
        elif name == "year_month":
            frame = frame.with_columns(
                col("pickup_datetime").dt.year().alias("year"), col("pickup_datetime").dt.month().alias("month")
            )
    return frame


def _pandas_derive(frame: Any, names: str) -> Any:
    for name in _items(names):
        if name == "hour":
            frame["hour"] = frame["pickup_datetime"].dt.hour
        elif name == "day":
            frame["day"] = frame["pickup_datetime"].dt.floor("D")
        elif name == "month":
            frame["month"] = frame["pickup_datetime"].dt.to_period("M").dt.to_timestamp()
        elif name == "day_of_week":
            frame["day_of_week"] = (frame["pickup_datetime"].dt.dayofweek + 1) % 7
        elif name == "tip_pct_nonzero":
            frame["tip_pct"] = pandas_where(
                frame["fare_amount"] != 0, frame["tip_amount"] / frame["fare_amount"] * 100, np.nan
            )
        elif name == "tip_pct_positive":
            frame["tip_pct"] = pandas_where(
                frame["fare_amount"] > 0, frame["tip_amount"] / frame["fare_amount"] * 100, np.nan
            )
        elif name == "fare_bucket":
            frame["fare_bucket"] = np.floor(frame["fare_amount"] / 5) * 5
        elif name == "distance_miles":
            frame["distance_miles"] = np.floor(frame["trip_distance"])
        elif name == "fare_per_mile":
            frame["fare_per_mile"] = pandas_where(
                frame["trip_distance"] != 0, frame["total_amount"] / frame["trip_distance"], np.nan
            )
        elif name == "duration":
            frame["duration_sec"] = (frame["dropoff_datetime"] - frame["pickup_datetime"]).dt.total_seconds()
        elif name == "duration_bucket":
            frame = frame[(frame["duration_sec"] >= 60) & (frame["duration_sec"] <= 7200)]
            frame["duration_bucket_min"] = np.floor(frame["duration_sec"] / 60 / 5) * 5
        elif name == "day_type":
            frame["hour"] = frame["pickup_datetime"].dt.hour
            frame["day_type"] = pandas_where(
                ((frame["pickup_datetime"].dt.dayofweek + 1) % 7).isin([0, 6]), "weekend", "weekday"
            )
        elif name == "period":
            frame["hour"] = frame["pickup_datetime"].dt.hour
            frame["duration_min"] = (frame["dropoff_datetime"] - frame["pickup_datetime"]).dt.total_seconds() / 60
            frame["period"] = pandas_select(
                [(frame["hour"] >= 7) & (frame["hour"] <= 9), (frame["hour"] >= 17) & (frame["hour"] <= 19)],
                ["morning_rush", "evening_rush"],
                "off_peak",
            )
        elif name == "year_month":
            frame["year"] = frame["pickup_datetime"].dt.year
            frame["month"] = frame["pickup_datetime"].dt.month
    return frame


def _parse_aggs(value: str) -> dict[str, tuple[str, str]]:
    return {out: (source, func) for out, source, func in (item.split(":") for item in value.split(";") if item)}


def _expr_aggs(col: Any, aggs: str) -> list[Any]:
    operations = []
    for out, source, func in (item.split(":") for item in aggs.split(";") if item):
        expr = getattr(col(source), func)() if func in {"count", "sum", "mean", "min", "max"} else None
        operations.append(expr.alias(out))
    return operations


def _sort_expr(frame: Any, sort: str, desc: str) -> Any:
    if not sort:
        return frame
    columns, flags = _items(sort), [item == "True" for item in _items(desc)]
    return frame.sort(
        columns if len(columns) > 1 else columns[0], descending=flags if len(flags) > 1 else bool(flags and flags[0])
    )


def _sort_pandas(frame: Any, sort: str, desc: str) -> Any:
    if not sort:
        return frame
    columns, flags = _items(sort), [item == "True" for item in _items(desc)]
    return frame.sort_values(
        columns if len(columns) > 1 else columns[0],
        ascending=[not f for f in flags] if len(flags) > 1 else not bool(flags and flags[0]),
    )


def _make_group_query(row: list[str]) -> tuple[Any, Any]:
    qid, start, end, extra, derives, group, aggs, sort, desc, limit = row

    def expression_impl(ctx: DataFrameContext) -> Any:
        frame, col, _ = _expression_window(ctx, qid, start, end, _expr_extra(extra) if extra else None)
        frame = _expr_derive(ctx, frame, derives).group_by(*_items(group)).agg(*_expr_aggs(col, aggs))
        frame = _sort_expr(frame, sort, desc)
        return frame.limit(int(limit)) if limit else frame

    def pandas_impl(ctx: DataFrameContext) -> Any:
        frame = _pandas_window(ctx, qid, start, end, copy=True, extra=_pandas_extra(extra) if extra else None)
        frame = (
            _pandas_derive(frame, derives).groupby(_items(group), as_index=False, dropna=False).agg(**_parse_aggs(aggs))
        )
        frame = _sort_pandas(frame, sort, desc)
        return frame.head(int(limit)) if limit else frame

    expression_impl.__name__ = f"q{qid[1:]}_expression_impl"
    pandas_impl.__name__ = f"q{qid[1:]}_pandas_impl"
    expression_impl.__qualname__ = expression_impl.__name__
    pandas_impl.__qualname__ = pandas_impl.__name__
    return expression_impl, pandas_impl


_GROUP_SPECS = """\
Q1|2019-01-01|2019-01-31||hour|hour|trip_count:hour:count|hour||
Q2|2019-01-01|2019-12-31||day|day|trip_count:day:count|day||
Q3|2019-01-01|2019-12-31||month|month|trip_count:month:count;total_revenue:total_amount:sum|month||
Q4|2019-01-01|2019-03-31||day_of_week|day_of_week|trip_count:day_of_week:count;avg_distance:trip_distance:mean;avg_fare:total_amount:mean|day_of_week||
Q9|2019-01-01|2019-01-31|fare_positive|tip_pct_nonzero|payment_type|trip_count:payment_type:count;total_revenue:total_amount:sum;total_tips:tip_amount:sum;avg_tip_percentage:tip_pct:mean|total_revenue|True|
Q10|2019-01-01|2019-01-31|fare_0_100|fare_bucket|fare_bucket|trip_count:fare_bucket:count;avg_distance:trip_distance:mean;avg_tip:tip_amount:mean|fare_bucket||
Q11|2019-01-01|2019-01-31|card|hour,tip_pct_positive|hour|trip_count:hour:count;avg_tip:tip_amount:mean;avg_tip_pct:tip_pct:mean|hour||
Q12|2019-01-01|2019-12-31||month|month|extra_revenue:extra:sum;mta_tax_revenue:mta_tax:sum;improvement_revenue:improvement_surcharge:sum;congestion_revenue:congestion_surcharge:sum;tolls_revenue:tolls_amount:sum|month||
Q13|2019-01-01|2019-01-31|distance_0_30|distance_miles|distance_miles|trip_count:distance_miles:count;avg_fare:total_amount:mean|distance_miles||
Q14|2019-01-01|2019-01-31|passenger_1_6|fare_per_mile|passenger_count|trip_count:passenger_count:count;avg_distance:trip_distance:mean;avg_fare:total_amount:mean;avg_fare_per_mile:fare_per_mile:mean|passenger_count||
Q15|2019-01-01|2019-01-31|duration_positive|duration,duration_bucket|duration_bucket_min|trip_count:duration_bucket_min:count;avg_distance:trip_distance:mean;avg_fare:total_amount:mean|duration_bucket_min||
Q19|2019-01-01|2019-01-07||hour|hour,pickup_location_id|trip_count:hour:count|hour,trip_count|False,True|
Q20|2019-01-01|2019-01-31||day_type|day_type,hour|trip_count:hour:count;avg_distance:trip_distance:mean;avg_fare:total_amount:mean|day_type,hour||
Q21|2019-01-01|2019-01-31|duration_positive|period|period|trip_count:period:count;avg_distance:trip_distance:mean;avg_fare:total_amount:mean;avg_duration_min:duration_min:mean|trip_count|True|
Q22|2019-01-01|2019-12-31||year_month|year,month|trip_count:year:count;total_revenue:total_amount:sum;avg_fare:total_amount:mean|year,month||
"""

for _row in reader(_GROUP_SPECS.splitlines(), delimiter="|"):
    globals()[f"q{_row[0][1:]}_expression_impl"], globals()[f"q{_row[0][1:]}_pandas_impl"] = _make_group_query(_row)


def q5_expression_impl(ctx: DataFrameContext) -> Any:
    return _expression_top_zone(ctx, "Q5", "pickup_location_id")


def q5_pandas_impl(ctx: DataFrameContext) -> Any:
    return _pandas_top_zone(ctx, "Q5", "pickup_location_id")


def q6_expression_impl(ctx: DataFrameContext) -> Any:
    return _expression_top_zone(ctx, "Q6", "dropoff_location_id")


def q6_pandas_impl(ctx: DataFrameContext) -> Any:
    return _pandas_top_zone(ctx, "Q6", "dropoff_location_id")


def q7_expression_impl(ctx: DataFrameContext) -> Any:
    trips, col, _ = _expression_window(ctx, "Q7", "2019-01-01", "2019-01-31")
    zones = ctx.get_table("taxi_zones")
    return (
        trips.join(
            zones.rename({"zone": "pickup_zone", "borough": "pickup_borough", "service_zone": "pickup_svc"}),
            left_on="pickup_location_id",
            right_on="location_id",
            how="left",
        )
        .join(
            zones.rename({"zone": "dropoff_zone", "borough": "dropoff_borough", "service_zone": "dropoff_svc"}),
            left_on="dropoff_location_id",
            right_on="location_id",
            how="left",
        )
        .group_by("pickup_location_id", "pickup_zone", "dropoff_location_id", "dropoff_zone")
        .agg(
            col("pickup_location_id").count().alias("trip_count"),
            col("trip_distance").mean().alias("avg_distance"),
            col("total_amount").mean().alias("avg_fare"),
        )
        .sort("trip_count", descending=True)
        .limit(50)
    )


def q7_pandas_impl(ctx: DataFrameContext) -> Any:
    zones = ctx.get_table("taxi_zones")
    merged = _pandas_window(ctx, "Q7", "2019-01-01", "2019-01-31").merge(
        zones.rename(columns={"zone": "pickup_zone"})[["location_id", "pickup_zone"]],
        left_on="pickup_location_id",
        right_on="location_id",
        how="left",
    )
    merged = merged.merge(
        zones.rename(columns={"zone": "dropoff_zone"})[["location_id", "dropoff_zone"]],
        left_on="dropoff_location_id",
        right_on="location_id",
        how="left",
        suffixes=("", "_dz"),
    )
    return (
        merged.groupby(
            ["pickup_location_id", "pickup_zone", "dropoff_location_id", "dropoff_zone"],
            as_index=False,
            dropna=False,
        )
        .agg(
            trip_count=("pickup_location_id", "count"),
            avg_distance=("trip_distance", "mean"),
            avg_fare=("total_amount", "mean"),
        )
        .sort_values("trip_count", ascending=False)
        .head(50)
    )


def q8_expression_impl(ctx: DataFrameContext) -> Any:
    trips, col, _ = _expression_window(ctx, "Q8", "2019-01-01", "2019-03-31")
    return (
        trips.join(ctx.get_table("taxi_zones"), left_on="pickup_location_id", right_on="location_id", how="left")
        .filter(col("borough").is_not_null())
        .group_by("borough")
        .agg(
            col("borough").count().alias("trip_count"),
            col("total_amount").sum().alias("total_revenue"),
            col("trip_distance").mean().alias("avg_distance"),
            col("tip_amount").mean().alias("avg_tip"),
        )
        .sort("trip_count", descending=True)
    )


def q8_pandas_impl(ctx: DataFrameContext) -> Any:
    merged = _pandas_window(ctx, "Q8", "2019-01-01", "2019-03-31").merge(
        ctx.get_table("taxi_zones"), left_on="pickup_location_id", right_on="location_id", how="left"
    )
    merged = merged[merged["borough"].notnull()]
    return (
        merged.groupby(["borough"], as_index=False, dropna=False)
        .agg(
            trip_count=("borough", "count"),
            total_revenue=("total_amount", "sum"),
            avg_distance=("trip_distance", "mean"),
            avg_tip=("tip_amount", "mean"),
        )
        .sort_values("trip_count", ascending=False)
    )


def q16_expression_impl(ctx: DataFrameContext) -> Any:
    return _expression_trip_summary(ctx, "Q16", "2019-03-31", "rate_code_id", tip=False)


def q16_pandas_impl(ctx: DataFrameContext) -> Any:
    return _pandas_trip_summary(ctx, "Q16", "2019-03-31", "rate_code_id", tip=False)


def q17_expression_impl(ctx: DataFrameContext) -> Any:
    trips, col, _ = _expression_window(ctx, "Q17", "2019-01-01", "2019-03-31", _expr_extra("airport_rate"))
    return (
        trips.join(ctx.get_table("taxi_zones"), left_on="pickup_location_id", right_on="location_id", how="left")
        .group_by("rate_code_id", "zone")
        .agg(
            col("rate_code_id").count().alias("trip_count"),
            col("trip_distance").mean().alias("avg_distance"),
            col("total_amount").mean().alias("avg_fare"),
            col("tip_amount").mean().alias("avg_tip"),
        )
        .sort("trip_count", descending=True)
        .limit(20)
    )


def q17_pandas_impl(ctx: DataFrameContext) -> Any:
    merged = _pandas_window(ctx, "Q17", "2019-01-01", "2019-03-31", extra=_pandas_extra("airport_rate")).merge(
        ctx.get_table("taxi_zones"), left_on="pickup_location_id", right_on="location_id", how="left"
    )
    return (
        merged.groupby(["rate_code_id", "zone"], as_index=False, dropna=False)
        .agg(
            trip_count=("rate_code_id", "count"),
            avg_distance=("trip_distance", "mean"),
            avg_fare=("total_amount", "mean"),
            avg_tip=("tip_amount", "mean"),
        )
        .sort_values("trip_count", ascending=False)
        .head(20)
    )


def q18_expression_impl(ctx: DataFrameContext) -> Any:
    return _expression_trip_summary(ctx, "Q18", "2019-01-31", "vendor_id", tip=True)


def q18_pandas_impl(ctx: DataFrameContext) -> Any:
    return _pandas_trip_summary(ctx, "Q18", "2019-01-31", "vendor_id", tip=True)


def q23_expression_impl(ctx: DataFrameContext) -> Any:
    trips, col, _ = _expression_window(ctx, "Q23", "2019-06-15", "2019-06-16")
    return trips.select(
        col("trip_id").count().alias("trip_count"),
        col("total_amount").sum().alias("total_revenue"),
        col("trip_distance").mean().alias("avg_distance"),
        col("total_amount").mean().alias("avg_fare"),
        col("tip_amount").mean().alias("avg_tip"),
        col("pickup_datetime").min().alias("first_trip"),
        col("pickup_datetime").max().alias("last_trip"),
    )


def q23_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    trips = _pandas_window(ctx, "Q23", "2019-06-15", "2019-06-16")
    return pd.DataFrame(
        {
            "trip_count": [len(trips)],
            "total_revenue": [trips["total_amount"].sum()],
            "avg_distance": [trips["trip_distance"].mean()],
            "avg_fare": [trips["total_amount"].mean()],
            "avg_tip": [trips["tip_amount"].mean()],
            "first_trip": [trips["pickup_datetime"].min()],
            "last_trip": [trips["pickup_datetime"].max()],
        }
    )


def q24_expression_impl(ctx: DataFrameContext) -> Any:
    zone_id = get_parameters("Q24").get("zone_id", 132)
    trips, col, _ = _expression_window(
        ctx, "Q24", "2019-01-01", "2019-01-31", lambda col, lit: col("pickup_location_id") == lit(zone_id)
    )
    return (
        trips.join(ctx.get_table("taxi_zones"), left_on="pickup_location_id", right_on="location_id")
        .group_by("zone", "borough")
        .agg(
            col("zone").count().alias("pickup_count"),
            col("trip_distance").mean().alias("avg_distance"),
            col("total_amount").mean().alias("avg_fare"),
            col("total_amount").sum().alias("total_revenue"),
        )
    )


def q24_pandas_impl(ctx: DataFrameContext) -> Any:
    zone_id = get_parameters("Q24").get("zone_id", 132)
    trips = _pandas_window(
        ctx, "Q24", "2019-01-01", "2019-01-31", extra=lambda trips: trips["pickup_location_id"] == zone_id
    )
    return (
        trips.merge(ctx.get_table("taxi_zones"), left_on="pickup_location_id", right_on="location_id")
        .groupby(["zone", "borough"], as_index=False, dropna=False)
        .agg(
            pickup_count=("zone", "count"),
            avg_distance=("trip_distance", "mean"),
            avg_fare=("total_amount", "mean"),
            total_revenue=("total_amount", "sum"),
        )
    )


def q25_expression_impl(ctx: DataFrameContext) -> Any:
    return ctx.get_table("trips").select(ctx.col("trip_id").count().alias("total_trips"))


def q25_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    return pd.DataFrame({"total_trips": [len(ctx.get_table("trips"))]})


_CATEGORY_CODES = {
    "AG": QueryCategory.AGGREGATE,
    "AN": QueryCategory.ANALYTICAL,
    "FI": QueryCategory.FILTER,
    "GB": QueryCategory.GROUP_BY,
    "JO": QueryCategory.JOIN,
    "MJ": QueryCategory.MULTI_JOIN,
    "SC": QueryCategory.SCAN,
    "SO": QueryCategory.SORT,
}

_QUERY_METADATA = """Q1|Trips per Hour|Hourly trip count distribution using EXTRACT(HOUR)|FI,GB,AG,AN
Q2|Trips per Day|Daily trip count using DATE_TRUNC('day')|FI,GB,AG,AN
Q3|Trips per Month|Monthly trip count and total revenue using DATE_TRUNC('month')|FI,GB,AG,AN
Q4|Trips by Day of Week|Day-of-week distribution with avg distance and fare|FI,GB,AG,AN
Q5|Top Pickup Zones|Top 20 pickup zones by trip count with LEFT JOIN to taxi_zones|FI,JO,GB,SO
Q6|Top Dropoff Zones|Top 20 dropoff zones by trip count with LEFT JOIN to taxi_zones|FI,JO,GB,SO
Q7|Top Routes|Top 50 routes (pickup->dropoff) with double LEFT JOIN to taxi_zones|FI,MJ,GB,SO
Q8|Borough Summary|Borough-level trip count, revenue, distance, and tip averages|FI,JO,GB,SO
Q9|Revenue by Payment Type|Revenue and tip analysis by payment type with NULLIF protection|FI,GB,AG,AN
Q10|Fare Distribution|Fare distribution in $5 buckets using FLOOR bucketing|FI,GB,AG,AN
Q11|Tip Analysis|Tip analysis by hour for credit card payments with conditional avg|FI,GB,AG,AN
Q12|Surcharge Revenue|Monthly surcharge revenue breakdown (extra, MTA tax, improvement, congestion, tolls)|FI,GB,AG,AN
Q13|Distance Distribution|Trip distance distribution in 1-mile buckets|FI,GB,AG
Q14|Passenger Count Analysis|Metrics by passenger count (1-6) with NULLIF fare-per-mile|FI,GB,AG,AN
Q15|Trip Duration Analysis|Duration distribution in 5-min buckets using timestamp arithmetic|FI,GB,AG,AN
Q16|Rate Code Summary|Trip metrics by rate code (Standard, JFK, Newark, etc.)|FI,GB,AG
Q17|Airport Trips|Airport trip analysis (JFK/Newark) with zone join|FI,JO,GB,SO
Q18|Vendor Comparison|Vendor-level trip metrics comparison|FI,GB,AG
Q19|Hourly Zone Heatmap|2D grouping by hour and pickup zone for heatmap visualization|FI,GB,SO,AN
Q20|Weekday/Weekend Comparison|Weekday vs weekend metrics by hour using CASE WHEN on day-of-week|FI,GB,SO,AN
Q21|Rush Hour Analysis|Morning rush, evening rush, and off-peak period comparison|FI,GB,AG,AN
Q22|Monthly Year-over-Year|Year and month grouping for YoY comparison over 2-year window|FI,GB,AG,AN
Q23|Single-Day Summary|Scalar aggregation for a single day (no GROUP BY)|FI,AG
Q24|Zone Detail|Detailed metrics for a specific zone with INNER JOIN|FI,JO,GB,AG
Q25|Full Scan Count|COUNT(*) baseline full table scan|SC,AG
"""


def _impl_for(query_id: str, family: str) -> Any:
    return globals()[f"q{query_id[1:]}_{family}_impl"]


for _query_id, _query_name, _description, _category_codes in reader(_QUERY_METADATA.splitlines(), delimiter="|"):
    register_query(
        DataFrameQuery(
            query_id=_query_id,
            query_name=_query_name,
            description=_description,
            categories=[_CATEGORY_CODES[code] for code in _category_codes.split(",")],
            expression_impl=_impl_for(_query_id, "expression"),
            pandas_impl=_impl_for(_query_id, "pandas"),
        )
    )
