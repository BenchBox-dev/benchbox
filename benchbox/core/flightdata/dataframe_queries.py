# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from csv import reader
from datetime import date
from typing import Any

from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory, QueryRegistry

try:
    from benchbox.core.flightdata.downloader import LAST_AVAILABLE_YEAR as _LAST_YEAR
except (ImportError, FileNotFoundError, KeyError, ValueError):
    _LAST_YEAR = 2024

FLIGHTDATA_DEFAULT_PARAMS: dict[str, Any] = {
    "start_date": date(_LAST_YEAR, 12, 1),
    "end_date": date(_LAST_YEAR + 1, 1, 1),
}

_parameter_overrides: dict[str, Any] | None = None


def set_parameter_overrides(overrides: dict[str, Any] | None) -> None:
    global _parameter_overrides
    _parameter_overrides = overrides


def get_flightdata_parameters() -> dict[str, Any]:
    params = dict(FLIGHTDATA_DEFAULT_PARAMS)
    if _parameter_overrides is not None:
        params.update(_parameter_overrides)
    return params


def _round_half_away(ctx: DataFrameContext, value: Any, digits: int) -> Any:
    lit = ctx.lit
    factor = 10.0**digits
    magnitude = ((value.abs() * lit(factor)) + lit(0.5)).floor() / lit(factor)
    return ctx.when(value < lit(0)).then(lit(0) - magnitude).otherwise(magnitude)


def _pandas_round_half_away(values: Any, digits: int) -> Any:
    import numpy as np

    factor = 10.0**digits
    return np.sign(values) * np.floor(np.abs(values) * factor + 0.5) / factor


def _date_condition(col: Any, lit: Any, extra: Any = None) -> Any:
    p = get_flightdata_parameters()
    date_condition = (col("flight_date") >= lit(p["start_date"])) & (col("flight_date") < lit(p["end_date"]))
    condition = date_condition
    if extra is not None:
        extras = extra(col, lit)
        if not isinstance(extras, tuple):
            extras = (extras,)
        condition = extras[0]
        extras = extras[1:]
        for extra_condition in extras:
            condition = condition & extra_condition
        condition = condition & date_condition
    return condition


def _date_window(ctx: DataFrameContext, extra: Any = None) -> tuple[Any, Any, Any]:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit
    condition = _date_condition(col, lit, extra)
    return flights.filter(condition), col, lit


def _pandas_window(ctx: DataFrameContext, *, source: Any = None, copy: bool = False, extra: Any = None) -> Any:
    p = get_flightdata_parameters()
    flights = ctx.get_table("flights") if source is None else source
    condition = (flights["flight_date"] >= p["start_date"]) & (flights["flight_date"] < p["end_date"])
    if extra is not None:
        condition &= extra(flights)
    filtered = flights[condition]
    return filtered.copy() if copy else filtered


def _airline_names(ctx: DataFrameContext) -> Any:
    col = ctx.col
    return ctx.get_table("airlines").select(col("code").alias("a_code"), col("name").alias("airline_name"))


def _pandas_airline_names(ctx: DataFrameContext) -> Any:
    return (
        ctx.get_table("flights")
        .merge(ctx.get_table("airlines"), left_on="reporting_airline", right_on="code", how="left")
        .rename(columns={"name": "airline_name"})
    )


def ontime_by_carrier_expression_impl(ctx: DataFrameContext) -> Any:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit

    return (
        flights.join(_airline_names(ctx), left_on="reporting_airline", right_on="a_code", how="left")
        .filter(_date_condition(col, lit, lambda col, lit: col("cancelled") == lit(0)))
        .with_columns(
            (col("arr_delay") <= lit(15)).cast(int).alias("_ontime"),
            ctx.when(col("arr_delay") > lit(0)).then(col("arr_delay")).otherwise(lit(None)).alias("_late_delay"),
        )
        .group_by("reporting_airline", "airline_name")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("_ontime").sum().alias("ontime_flights"),
            col("_late_delay").mean().alias("_avg_late"),
        )
        .with_columns(
            _round_half_away(ctx, lit(100.0) * col("ontime_flights") / col("total_flights"), 2).alias("ontime_pct"),
            _round_half_away(ctx, col("_avg_late"), 2).alias("avg_delay_when_late"),
        )
        .select(
            "reporting_airline",
            "airline_name",
            "total_flights",
            "ontime_flights",
            "ontime_pct",
            "avg_delay_when_late",
        )
        .sort(["ontime_pct", "reporting_airline"], descending=[True, False], nulls_last=True)
    )


def delay_by_airport_expression_impl(ctx: DataFrameContext) -> Any:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit
    airports = ctx.get_table("airports")

    ap = airports.select(
        col("code").alias("ap_code"),
        col("name").alias("airport_name"),
        col("city"),
        col("state"),
    )

    return (
        flights.join(ap, left_on="origin", right_on="ap_code", how="left")
        .filter(
            _date_condition(
                ctx.col, ctx.lit, lambda col, lit: (col("cancelled") == lit(0), col("dep_delay").is_not_null())
            )
        )
        .with_columns((col("dep_delay") > lit(15)).cast(int).alias("_delayed"))
        .group_by("origin", "airport_name", "city", "state")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("dep_delay").mean().alias("_avg_dep"),
            col("arr_delay").mean().alias("_avg_arr"),
            col("_delayed").sum().alias("delayed_flights"),
        )
        .with_columns(
            _round_half_away(ctx, col("_avg_dep"), 2).alias("avg_dep_delay"),
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
        )
        .select(
            "origin",
            "airport_name",
            "city",
            "state",
            "total_flights",
            "avg_dep_delay",
            "avg_arr_delay",
            "delayed_flights",
        )
        .filter(col("total_flights") >= lit(100))
        .sort(["avg_dep_delay", "origin"], descending=[True, False])
        .limit(50)
    )


def delay_by_hour_expression_impl(ctx: DataFrameContext) -> Any:
    flights, col, lit = _date_window(
        ctx,
        lambda col, lit: (
            col("cancelled") == lit(0),
            col("dep_delay").is_not_null(),
            col("crs_dep_time").is_not_null(),
        ),
    )

    return (
        flights.with_columns(
            (col("crs_dep_time") / lit(100)).floor().cast(int).alias("dep_hour"),
            (col("dep_delay") > lit(15)).cast(int).alias("_delayed"),
        )
        .group_by("dep_hour")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("dep_delay").mean().alias("_avg_dep"),
            col("arr_delay").mean().alias("_avg_arr"),
            col("_delayed").sum().alias("delayed_count"),
        )
        .with_columns(
            _round_half_away(ctx, col("_avg_dep"), 2).alias("avg_dep_delay"),
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
            _round_half_away(ctx, lit(100.0) * col("delayed_count") / col("total_flights"), 2).alias("delay_rate_pct"),
        )
        .select("dep_hour", "total_flights", "avg_dep_delay", "avg_arr_delay", "delayed_count", "delay_rate_pct")
        .sort("dep_hour")
    )


def best_routes_expression_impl(ctx: DataFrameContext) -> Any:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit
    airports = ctx.get_table("airports")

    ao = airports.select(col("code").alias("ao_code"), col("city").alias("origin_city"))
    ad = airports.select(col("code").alias("ad_code"), col("city").alias("dest_city"))

    return (
        flights.join(ao, left_on="origin", right_on="ao_code", how="left")
        .join(ad, left_on="dest", right_on="ad_code", how="left")
        .filter(
            _date_condition(
                ctx.col, ctx.lit, lambda col, lit: (col("cancelled") == lit(0), col("arr_delay").is_not_null())
            )
        )
        .with_columns((col("arr_delay") <= lit(15)).cast(int).alias("_ontime"))
        .group_by("origin", "dest", "origin_city", "dest_city")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("_ontime").sum().alias("_ontime_sum"),
            col("arr_delay").mean().alias("_avg_arr"),
            col("distance").mean().alias("_avg_dist"),
        )
        .filter(col("total_flights") >= lit(50))
        .with_columns(
            _round_half_away(ctx, lit(100.0) * col("_ontime_sum") / col("total_flights"), 2).alias("ontime_pct"),
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
            _round_half_away(ctx, col("_avg_dist"), 0).alias("avg_distance_miles"),
        )
        .select(
            "origin",
            "dest",
            "origin_city",
            "dest_city",
            "total_flights",
            "ontime_pct",
            "avg_arr_delay",
            "avg_distance_miles",
        )
        .sort(["ontime_pct", "origin", "dest"], descending=[True, False, False], nulls_last=True)
        .limit(25)
    )


def improvement_trend_expression_impl(ctx: DataFrameContext) -> Any:
    flights, col, lit = _date_window(ctx)

    return (
        flights.with_columns(
            (col("cancelled") == lit(1)).cast(int).alias("_cancelled"),
            ((col("cancelled") == lit(0)) & (col("arr_delay") <= lit(15))).cast(int).alias("_ontime"),
            (col("cancelled") == lit(0)).cast(int).alias("_operated"),
            ctx.when(col("cancelled") == lit(0)).then(col("arr_delay")).otherwise(lit(None)).alias("_arr_nc"),
        )
        .group_by("year")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("_cancelled").sum().alias("cancelled_flights"),
            col("_ontime").sum().alias("ontime_flights"),
            col("_operated").sum().alias("_non_cancelled"),
            col("_arr_nc").mean().alias("_avg_arr"),
        )
        .with_columns(
            ctx.when(col("_non_cancelled") > lit(0))
            .then(col("_non_cancelled"))
            .otherwise(lit(None))
            .alias("_non_cancelled_nz"),
        )
        .with_columns(
            _round_half_away(ctx, lit(100.0) * col("cancelled_flights") / col("total_flights"), 2).alias(
                "cancellation_rate_pct"
            ),
            _round_half_away(ctx, lit(100.0) * col("ontime_flights") / col("_non_cancelled_nz"), 2).alias("ontime_pct"),
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
        )
        .select(
            "year",
            "total_flights",
            "cancelled_flights",
            "cancellation_rate_pct",
            "ontime_flights",
            "ontime_pct",
            "avg_arr_delay",
        )
        .sort("year")
    )


def delay_causes_expression_impl(ctx: DataFrameContext) -> Any:
    flights, col, lit = _date_window(ctx, lambda col, lit: (col("cancelled") == lit(0), col("arr_delay") > lit(15)))

    prepared = flights.with_columns(
        (col("carrier_delay") > lit(0)).cast(int).fill_null(0).alias("_carrier_flag"),
        ctx.when(col("carrier_delay") > lit(0)).then(col("carrier_delay")).otherwise(lit(None)).alias("_carrier_pos"),
        (col("weather_delay") > lit(0)).cast(int).fill_null(0).alias("_weather_flag"),
        ctx.when(col("weather_delay") > lit(0)).then(col("weather_delay")).otherwise(lit(None)).alias("_weather_pos"),
        (col("nas_delay") > lit(0)).cast(int).fill_null(0).alias("_nas_flag"),
        ctx.when(col("nas_delay") > lit(0)).then(col("nas_delay")).otherwise(lit(None)).alias("_nas_pos"),
        (col("security_delay") > lit(0)).cast(int).fill_null(0).alias("_security_flag"),
        ctx.when(col("security_delay") > lit(0))
        .then(col("security_delay"))
        .otherwise(lit(None))
        .alias("_security_pos"),
        (col("late_aircraft_delay") > lit(0)).cast(int).fill_null(0).alias("_late_flag"),
        ctx.when(col("late_aircraft_delay") > lit(0))
        .then(col("late_aircraft_delay"))
        .otherwise(lit(None))
        .alias("_late_pos"),
    )
    totals = prepared.select(
        col("_carrier_flag").sum().alias("carrier_delay_count"),
        col("_carrier_pos").mean().alias("_avg_carrier"),
        col("_weather_flag").sum().alias("weather_delay_count"),
        col("_weather_pos").mean().alias("_avg_weather"),
        col("_nas_flag").sum().alias("nas_delay_count"),
        col("_nas_pos").mean().alias("_avg_nas"),
        col("_security_flag").sum().alias("security_delay_count"),
        col("_security_pos").mean().alias("_avg_security"),
        col("_late_flag").sum().alias("late_aircraft_count"),
        col("_late_pos").mean().alias("_avg_late"),
        col("flight_id").count().alias("total_delayed_flights"),
    )
    return totals.with_columns(
        _round_half_away(ctx, col("_avg_carrier"), 2).alias("avg_carrier_delay"),
        _round_half_away(ctx, col("_avg_weather"), 2).alias("avg_weather_delay"),
        _round_half_away(ctx, col("_avg_nas"), 2).alias("avg_nas_delay"),
        _round_half_away(ctx, col("_avg_security"), 2).alias("avg_security_delay"),
        _round_half_away(ctx, col("_avg_late"), 2).alias("avg_late_aircraft_delay"),
    ).select(
        "carrier_delay_count",
        "avg_carrier_delay",
        "weather_delay_count",
        "avg_weather_delay",
        "nas_delay_count",
        "avg_nas_delay",
        "security_delay_count",
        "avg_security_delay",
        "late_aircraft_count",
        "avg_late_aircraft_delay",
        "total_delayed_flights",
    )


def cascade_delays_expression_impl(ctx: DataFrameContext) -> Any:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit

    return (
        flights.join(_airline_names(ctx), left_on="reporting_airline", right_on="a_code", how="left")
        .filter(_date_condition(col, lit, lambda col, lit: col("cancelled") == lit(0)))
        .with_columns(
            (col("late_aircraft_delay") > lit(0)).cast(int).alias("_cascade"),
            ctx.when(col("late_aircraft_delay") > lit(0))
            .then(col("late_aircraft_delay"))
            .otherwise(lit(None))
            .alias("_cascade_pos"),
            ctx.when(col("late_aircraft_delay") > lit(0))
            .then(_round_half_away(ctx, col("late_aircraft_delay") * lit(10), 0).cast(int))
            .otherwise(lit(0))
            .alias("_late_tenths"),
        )
        .group_by("reporting_airline", "airline_name")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("_cascade").sum().alias("cascade_delayed"),
            col("_cascade_pos").mean().alias("_avg_cascade"),
            col("_late_tenths").sum().alias("_total_tenths"),
        )
        .with_columns(
            _round_half_away(ctx, lit(100.0) * col("cascade_delayed") / col("total_flights"), 2).alias(
                "cascade_rate_pct"
            ),
            _round_half_away(ctx, col("_avg_cascade"), 2).alias("avg_cascade_delay"),
            ((col("_total_tenths") + lit(5)) / lit(10)).floor().alias("total_cascade_minutes"),
        )
        .select(
            "reporting_airline",
            "airline_name",
            "total_flights",
            "cascade_delayed",
            "cascade_rate_pct",
            "avg_cascade_delay",
            "total_cascade_minutes",
        )
        .sort(["cascade_rate_pct", "reporting_airline"], descending=[True, False])
    )


def weather_impact_expression_impl(ctx: DataFrameContext) -> Any:
    flights, col, lit = _date_window(ctx, lambda col, lit: col("cancelled") == lit(0))

    return (
        flights.with_columns(
            (col("weather_delay") > lit(0)).cast(int).alias("_weather"),
            ctx.when(col("weather_delay") > lit(0))
            .then(col("weather_delay"))
            .otherwise(lit(None))
            .alias("_weather_pos"),
            _round_half_away(ctx, col("weather_delay").fill_null(lit(0)) * lit(10), 0)
            .cast(int)
            .alias("_weather_tenths"),
        )
        .group_by("month")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("_weather").sum().alias("weather_delayed"),
            col("_weather_pos").mean().alias("_avg_weather"),
            col("_weather_tenths").sum().alias("_tenths_sum"),
        )
        .with_columns(
            _round_half_away(ctx, lit(100.0) * col("weather_delayed") / col("total_flights"), 2).alias(
                "weather_delay_rate_pct"
            ),
            _round_half_away(ctx, col("_avg_weather"), 2).alias("avg_weather_delay_min"),
            ((col("_tenths_sum") + lit(5)) / lit(10)).floor().alias("total_weather_minutes"),
        )
        .select(
            "month",
            "total_flights",
            "weather_delayed",
            "weather_delay_rate_pct",
            "avg_weather_delay_min",
            "total_weather_minutes",
        )
        .sort("month")
    )


def recovery_time_expression_impl(ctx: DataFrameContext) -> Any:
    flights, col, lit = _date_window(
        ctx,
        lambda col, lit: (
            col("cancelled") == lit(0),
            col("dep_delay").is_not_null(),
            col("arr_delay").is_not_null(),
        ),
    )

    return (
        flights.with_columns(
            (col("dep_delay") - col("arr_delay")).alias("minutes_recovered"),
            (col("arr_delay") < col("dep_delay")).cast(int).alias("recovered_flag"),
            ctx.when(col("dep_delay") <= lit(0))
            .then(lit("No departure delay"))
            .when(col("dep_delay") <= lit(15))
            .then(lit("1-15 min departure delay"))
            .when(col("dep_delay") <= lit(30))
            .then(lit("16-30 min departure delay"))
            .when(col("dep_delay") <= lit(60))
            .then(lit("31-60 min departure delay"))
            .otherwise(lit("60+ min departure delay"))
            .alias("delay_bucket"),
        )
        .group_by("delay_bucket")
        .agg(
            col("flight_id").count().alias("flight_count"),
            col("dep_delay").mean().alias("_avg_dep"),
            col("arr_delay").mean().alias("_avg_arr"),
            col("minutes_recovered").mean().alias("_avg_recovered"),
            col("recovered_flag").sum().alias("_recovered_sum"),
        )
        .with_columns(
            _round_half_away(ctx, col("_avg_dep"), 2).alias("avg_dep_delay"),
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
            _round_half_away(ctx, col("_avg_recovered"), 2).alias("avg_minutes_recovered"),
            _round_half_away(ctx, lit(100.0) * col("_recovered_sum") / col("flight_count"), 2).alias("pct_recovered"),
        )
        .select(
            "delay_bucket",
            "flight_count",
            "avg_dep_delay",
            "avg_arr_delay",
            "avg_minutes_recovered",
            "pct_recovered",
        )
        .sort("avg_dep_delay")
    )


def busiest_routes_expression_impl(ctx: DataFrameContext) -> Any:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit
    airports = ctx.get_table("airports")

    ao = airports.select(
        col("code").alias("ao_code"),
        col("city").alias("origin_city"),
        col("state").alias("origin_state"),
    )
    ad = airports.select(
        col("code").alias("ad_code"),
        col("city").alias("dest_city"),
        col("state").alias("dest_state"),
    )

    return (
        flights.join(ao, left_on="origin", right_on="ao_code", how="left")
        .join(ad, left_on="dest", right_on="ad_code", how="left")
        .filter(_date_condition(col, lit, lambda col, lit: col("cancelled") == lit(0)))
        .with_columns((col("arr_delay") <= lit(15)).cast(int).alias("_ontime"))
        .group_by("origin", "dest", "origin_city", "origin_state", "dest_city", "dest_state")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("distance").mean().alias("_avg_dist"),
            col("actual_elapsed_time").mean().alias("_avg_dur"),
            col("_ontime").sum().alias("_ontime_sum"),
        )
        .with_columns(
            _round_half_away(ctx, col("_avg_dist"), 0).alias("avg_distance_miles"),
            _round_half_away(ctx, col("_avg_dur"), 0).alias("avg_duration_min"),
            _round_half_away(ctx, lit(100.0) * col("_ontime_sum") / col("total_flights"), 2).alias("ontime_pct"),
        )
        .select(
            "origin",
            "dest",
            "origin_city",
            "origin_state",
            "dest_city",
            "dest_state",
            "total_flights",
            "avg_distance_miles",
            "avg_duration_min",
            "ontime_pct",
        )
        .sort(["total_flights", "origin", "dest"], descending=[True, False, False])
        .limit(25)
    )


def route_reliability_expression_impl(ctx: DataFrameContext) -> Any:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit
    airports = ctx.get_table("airports")

    ao = airports.select(col("code").alias("ao_code"), col("city").alias("origin_city"))
    ad = airports.select(col("code").alias("ad_code"), col("city").alias("dest_city"))

    return (
        flights.join(ao, left_on="origin", right_on="ao_code", how="left")
        .join(ad, left_on="dest", right_on="ad_code", how="left")
        .filter(_date_condition(col, lit))
        .with_columns(
            (col("cancelled") == lit(1)).cast(int).alias("_cancelled"),
            ((col("cancelled") == lit(0)) & (col("arr_delay") <= lit(15))).cast(int).alias("_ontime"),
            (col("cancelled") == lit(0)).cast(int).alias("_operated"),
        )
        .group_by("origin", "dest", "origin_city", "dest_city")
        .agg(
            col("flight_id").count().alias("total_scheduled"),
            col("_cancelled").sum().alias("cancelled_count"),
            col("_ontime").sum().alias("ontime_count"),
            col("_operated").sum().alias("_non_cancelled"),
            col("distance").mean().alias("_avg_dist"),
        )
        .filter(col("total_scheduled") >= lit(100))
        .with_columns(
            ctx.when(col("_non_cancelled") > lit(0))
            .then(col("_non_cancelled"))
            .otherwise(lit(None))
            .alias("_non_cancelled_nz"),
        )
        .with_columns(
            _round_half_away(ctx, lit(100.0) * col("cancelled_count") / col("total_scheduled"), 2).alias(
                "cancellation_rate_pct"
            ),
            _round_half_away(ctx, lit(100.0) * col("ontime_count") / col("_non_cancelled_nz"), 2).alias("ontime_pct"),
            _round_half_away(ctx, col("_avg_dist"), 0).alias("distance_miles"),
        )
        .select(
            "origin",
            "dest",
            "origin_city",
            "dest_city",
            "total_scheduled",
            "cancelled_count",
            "cancellation_rate_pct",
            "ontime_count",
            "ontime_pct",
            "distance_miles",
        )
        .sort(
            ["ontime_pct", "cancellation_rate_pct", "origin", "dest"],
            descending=[True, False, False, False],
            nulls_last=True,
        )
        .limit(30)
    )


def distance_delay_expression_impl(ctx: DataFrameContext) -> Any:
    flights, col, lit = _date_window(ctx, lambda col, lit: (col("cancelled") == lit(0), col("arr_delay").is_not_null()))

    return (
        flights.with_columns(
            ctx.when(col("distance") < lit(250))
            .then(lit("Short-haul (<250 mi)"))
            .when(col("distance") < lit(500))
            .then(lit("Medium-haul (250-499 mi)"))
            .when(col("distance") < lit(1000))
            .then(lit("Long-haul (500-999 mi)"))
            .when(col("distance") < lit(2000))
            .then(lit("Cross-country (1000-1999 mi)"))
            .otherwise(lit("Ultra-long (2000+ mi)"))
            .alias("distance_bucket"),
        )
        .with_columns((col("arr_delay") <= lit(15)).cast(int).alias("_ontime"))
        .group_by("distance_bucket")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("distance").mean().alias("_avg_dist"),
            col("dep_delay").mean().alias("_avg_dep"),
            col("arr_delay").mean().alias("_avg_arr"),
            col("_ontime").sum().alias("_ontime_sum"),
        )
        .with_columns(
            _round_half_away(ctx, col("_avg_dist"), 0).alias("avg_distance_miles"),
            _round_half_away(ctx, col("_avg_dep"), 2).alias("avg_dep_delay"),
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
            _round_half_away(ctx, lit(100.0) * col("_ontime_sum") / col("total_flights"), 2).alias("ontime_pct"),
        )
        .select(
            "distance_bucket",
            "total_flights",
            "avg_distance_miles",
            "avg_dep_delay",
            "avg_arr_delay",
            "ontime_pct",
        )
        .sort("avg_distance_miles")
    )


def hub_connectivity_expression_impl(ctx: DataFrameContext) -> Any:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit
    airports = ctx.get_table("airports")

    ap = airports.select(
        col("code").alias("ap_code"),
        col("name").alias("airport_name"),
        col("city"),
        col("state"),
    )

    return (
        flights.join(ap, left_on="origin", right_on="ap_code", how="left")
        .filter(_date_condition(col, lit, lambda col, lit: col("cancelled") == lit(0)))
        .group_by("origin", "airport_name", "city", "state")
        .agg(
            col("dest").n_unique().alias("unique_destinations"),
            col("reporting_airline").n_unique().alias("serving_carriers"),
            col("flight_id").count().alias("total_departures"),
            col("dep_delay").mean().alias("_avg_dep"),
        )
        .with_columns(_round_half_away(ctx, col("_avg_dep"), 2).alias("avg_dep_delay"))
        .select(
            "origin",
            "airport_name",
            "city",
            "state",
            "unique_destinations",
            "serving_carriers",
            "total_departures",
            "avg_dep_delay",
        )
        .sort(["total_departures", "origin"], descending=[True, False])
        .limit(30)
    )


def day_of_week_expression_impl(ctx: DataFrameContext) -> Any:
    flights, col, lit = _date_window(ctx)

    return (
        flights.with_columns(
            ctx.when(col("day_of_week") == lit(1))
            .then(lit("Monday"))
            .when(col("day_of_week") == lit(2))
            .then(lit("Tuesday"))
            .when(col("day_of_week") == lit(3))
            .then(lit("Wednesday"))
            .when(col("day_of_week") == lit(4))
            .then(lit("Thursday"))
            .when(col("day_of_week") == lit(5))
            .then(lit("Friday"))
            .when(col("day_of_week") == lit(6))
            .then(lit("Saturday"))
            .otherwise(lit("Sunday"))
            .alias("day_name"),
            (col("cancelled") == lit(1)).cast(int).alias("_cancelled"),
            ((col("cancelled") == lit(0)) & (col("arr_delay") <= lit(15))).cast(int).alias("_ontime"),
            (col("cancelled") == lit(0)).cast(int).alias("_operated"),
            ctx.when(col("cancelled") == lit(0)).then(col("dep_delay")).otherwise(lit(None)).alias("_dep_nc"),
            ctx.when(col("cancelled") == lit(0)).then(col("arr_delay")).otherwise(lit(None)).alias("_arr_nc"),
        )
        .group_by("day_of_week", "day_name")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("_cancelled").sum().alias("cancelled_count"),
            col("_dep_nc").mean().alias("_avg_dep"),
            col("_arr_nc").mean().alias("_avg_arr"),
            col("_ontime").sum().alias("_ontime_sum"),
            col("_operated").sum().alias("_non_cancelled"),
        )
        .with_columns(
            ctx.when(col("_non_cancelled") > lit(0))
            .then(col("_non_cancelled"))
            .otherwise(lit(None))
            .alias("_non_cancelled_nz"),
        )
        .with_columns(
            _round_half_away(ctx, col("_avg_dep"), 2).alias("avg_dep_delay"),
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
            _round_half_away(ctx, lit(100.0) * col("_ontime_sum") / col("_non_cancelled_nz"), 2).alias("ontime_pct"),
        )
        .select(
            "day_of_week",
            "day_name",
            "total_flights",
            "cancelled_count",
            "avg_dep_delay",
            "avg_arr_delay",
            "ontime_pct",
        )
        .sort("day_of_week")
    )


def seasonal_trends_expression_impl(ctx: DataFrameContext) -> Any:
    flights, col, lit = _date_window(ctx)

    return (
        flights.with_columns(
            ctx.when(col("month") == lit(1))
            .then(lit("January"))
            .when(col("month") == lit(2))
            .then(lit("February"))
            .when(col("month") == lit(3))
            .then(lit("March"))
            .when(col("month") == lit(4))
            .then(lit("April"))
            .when(col("month") == lit(5))
            .then(lit("May"))
            .when(col("month") == lit(6))
            .then(lit("June"))
            .when(col("month") == lit(7))
            .then(lit("July"))
            .when(col("month") == lit(8))
            .then(lit("August"))
            .when(col("month") == lit(9))
            .then(lit("September"))
            .when(col("month") == lit(10))
            .then(lit("October"))
            .when(col("month") == lit(11))
            .then(lit("November"))
            .otherwise(lit("December"))
            .alias("month_name")
        )
        .with_columns(
            (col("cancelled") == lit(1)).cast(int).alias("_cancelled"),
            ((col("cancelled") == lit(0)) & (col("arr_delay") <= lit(15))).cast(int).alias("_ontime"),
            (col("cancelled") == lit(0)).cast(int).alias("_operated"),
            ctx.when(col("cancelled") == lit(0)).then(col("arr_delay")).otherwise(lit(None)).alias("_arr_nc"),
        )
        .group_by("month", "month_name")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("_cancelled").sum().alias("cancelled_count"),
            col("_arr_nc").mean().alias("_avg_arr"),
            col("_ontime").sum().alias("_ontime_sum"),
            col("_operated").sum().alias("_non_cancelled"),
        )
        .with_columns(
            ctx.when(col("_non_cancelled") > lit(0))
            .then(col("_non_cancelled"))
            .otherwise(lit(None))
            .alias("_non_cancelled_nz"),
        )
        .with_columns(
            _round_half_away(ctx, lit(100.0) * col("cancelled_count") / col("total_flights"), 2).alias(
                "cancellation_rate_pct"
            ),
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
            _round_half_away(ctx, lit(100.0) * col("_ontime_sum") / col("_non_cancelled_nz"), 2).alias("ontime_pct"),
        )
        .select(
            "month",
            "month_name",
            "total_flights",
            "cancelled_count",
            "cancellation_rate_pct",
            "avg_arr_delay",
            "ontime_pct",
        )
        .sort("month")
    )


def holiday_impact_expression_impl(ctx: DataFrameContext) -> Any:
    flights, col, lit = _date_window(ctx)

    thanksgiving = (col("month") == lit(11)) & (col("day_of_month") >= lit(21)) & (col("day_of_month") <= lit(27))
    christmas = (col("month") == lit(12)) & (col("day_of_month") >= lit(23)) & (col("day_of_month") <= lit(27))
    new_year = ((col("month") == lit(1)) & (col("day_of_month") <= lit(2))) | (
        (col("month") == lit(12)) & (col("day_of_month") == lit(31))
    )
    july4 = (col("month") == lit(7)) & (col("day_of_month") >= lit(3)) & (col("day_of_month") <= lit(5))
    memorial = (
        (col("month") == lit(5))
        & (col("day_of_week") == lit(1))
        & (col("day_of_month") >= lit(25))
        & (col("day_of_month") <= lit(31))
    )
    labor = (
        (col("month") == lit(9))
        & (col("day_of_week") == lit(1))
        & (col("day_of_month") >= lit(1))
        & (col("day_of_month") <= lit(7))
    )

    return (
        flights.with_columns(
            ctx.when(thanksgiving)
            .then(lit("Thanksgiving Week"))
            .when(christmas)
            .then(lit("Christmas"))
            .when(new_year)
            .then(lit("New Year"))
            .when(july4)
            .then(lit("July 4th"))
            .when(memorial)
            .then(lit("Memorial Day"))
            .when(labor)
            .then(lit("Labor Day"))
            .otherwise(lit("Regular Day"))
            .alias("period"),
        )
        .with_columns(
            (col("cancelled") == lit(1)).cast(int).alias("_cancelled"),
            ((col("cancelled") == lit(0)) & (col("arr_delay") <= lit(15))).cast(int).alias("_ontime"),
            (col("cancelled") == lit(0)).cast(int).alias("_operated"),
            ctx.when(col("cancelled") == lit(0)).then(col("arr_delay")).otherwise(lit(None)).alias("_arr_nc"),
        )
        .group_by("period")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("_arr_nc").mean().alias("_avg_arr"),
            col("_cancelled").sum().alias("_cancelled_sum"),
            col("_ontime").sum().alias("_ontime_sum"),
            col("_operated").sum().alias("_non_cancelled"),
        )
        .with_columns(
            ctx.when(col("_non_cancelled") > lit(0))
            .then(col("_non_cancelled"))
            .otherwise(lit(None))
            .alias("_non_cancelled_nz"),
        )
        .with_columns(
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
            _round_half_away(ctx, lit(100.0) * col("_cancelled_sum") / col("total_flights"), 2).alias(
                "cancellation_rate_pct"
            ),
            _round_half_away(ctx, lit(100.0) * col("_ontime_sum") / col("_non_cancelled_nz"), 2).alias("ontime_pct"),
        )
        .select("period", "total_flights", "avg_arr_delay", "cancellation_rate_pct", "ontime_pct")
        .sort("avg_arr_delay", descending=True, nulls_last=True)
    )


def time_of_day_expression_impl(ctx: DataFrameContext) -> Any:
    flights, col, lit = _date_window(ctx, lambda col, _lit: col("crs_dep_time").is_not_null())

    return (
        flights.with_columns(
            (col("crs_dep_time") / lit(100)).floor().cast(int).alias("hour_of_day"),
            (col("dep_delay") > lit(60)).cast(int).alias("_severe"),
            ((col("cancelled") == lit(0)) & (col("arr_delay") <= lit(15))).cast(int).alias("_ontime"),
            (col("cancelled") == lit(0)).cast(int).alias("_operated"),
            ctx.when(col("cancelled") == lit(0)).then(col("dep_delay")).otherwise(lit(None)).alias("_dep_nc"),
            ctx.when(col("cancelled") == lit(0)).then(col("arr_delay")).otherwise(lit(None)).alias("_arr_nc"),
        )
        .group_by("hour_of_day")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("_dep_nc").mean().alias("_avg_dep"),
            col("_arr_nc").mean().alias("_avg_arr"),
            col("_severe").sum().alias("severely_delayed"),
            col("_ontime").sum().alias("_ontime_sum"),
            col("_operated").sum().alias("_non_cancelled"),
        )
        .with_columns(
            ctx.when(col("_non_cancelled") > lit(0))
            .then(col("_non_cancelled"))
            .otherwise(lit(None))
            .alias("_non_cancelled_nz"),
        )
        .with_columns(
            _round_half_away(ctx, col("_avg_dep"), 2).alias("avg_dep_delay"),
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
            _round_half_away(ctx, lit(100.0) * col("_ontime_sum") / col("_non_cancelled_nz"), 2).alias("ontime_pct"),
        )
        .select(
            "hour_of_day",
            "total_flights",
            "avg_dep_delay",
            "avg_arr_delay",
            "severely_delayed",
            "ontime_pct",
        )
        .sort("hour_of_day")
    )


def carrier_ranking_expression_impl(ctx: DataFrameContext) -> Any:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit

    return (
        flights.join(_airline_names(ctx), left_on="reporting_airline", right_on="a_code", how="left")
        .filter(_date_condition(col, lit))
        .with_columns(
            (col("cancelled") == lit(0)).cast(int).alias("_operated"),
            (col("cancelled") == lit(1)).cast(int).alias("_cancelled"),
            ((col("cancelled") == lit(0)) & (col("arr_delay") <= lit(15))).cast(int).alias("_ontime"),
            ctx.when(col("cancelled") == lit(0)).then(col("dep_delay")).otherwise(lit(None)).alias("_dep_nc"),
            ctx.when(col("cancelled") == lit(0)).then(col("arr_delay")).otherwise(lit(None)).alias("_arr_nc"),
        )
        .group_by("reporting_airline", "airline_name")
        .agg(
            col("flight_id").count().alias("total_scheduled"),
            col("_operated").sum().alias("operated_flights"),
            col("_cancelled").sum().alias("cancelled_flights"),
            col("_dep_nc").mean().alias("_avg_dep"),
            col("_arr_nc").mean().alias("_avg_arr"),
            col("_ontime").sum().alias("_ontime_sum"),
            col("_operated").sum().alias("_non_cancelled"),
            col("distance").mean().alias("_avg_dist"),
        )
        .filter(col("total_scheduled") >= lit(1000))
        .with_columns(
            ctx.when(col("_non_cancelled") > lit(0))
            .then(col("_non_cancelled"))
            .otherwise(lit(None))
            .alias("_non_cancelled_nz"),
        )
        .with_columns(
            _round_half_away(ctx, lit(100.0) * col("cancelled_flights") / col("total_scheduled"), 2).alias(
                "cancellation_rate_pct"
            ),
            _round_half_away(ctx, col("_avg_dep"), 2).alias("avg_dep_delay"),
            _round_half_away(ctx, col("_avg_arr"), 2).alias("avg_arr_delay"),
            _round_half_away(ctx, lit(100.0) * col("_ontime_sum") / col("_non_cancelled_nz"), 2).alias("ontime_pct"),
            _round_half_away(ctx, col("_avg_dist"), 0).alias("avg_route_distance_miles"),
        )
        .select(
            "reporting_airline",
            "airline_name",
            "total_scheduled",
            "operated_flights",
            "cancelled_flights",
            "cancellation_rate_pct",
            "avg_dep_delay",
            "avg_arr_delay",
            "ontime_pct",
            "avg_route_distance_miles",
        )
        .sort(["ontime_pct", "reporting_airline"], descending=[True, False], nulls_last=True)
    )


def cancellation_rate_expression_impl(ctx: DataFrameContext) -> Any:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit

    return (
        flights.join(_airline_names(ctx), left_on="reporting_airline", right_on="a_code", how="left")
        .filter(_date_condition(col, lit))
        .with_columns(
            (col("cancelled") == lit(1)).cast(int).alias("_cancelled"),
            (col("cancellation_code") == lit("A")).cast(int).alias("_code_a"),
            (col("cancellation_code") == lit("B")).cast(int).alias("_code_b"),
            (col("cancellation_code") == lit("C")).cast(int).alias("_code_c"),
            (col("cancellation_code") == lit("D")).cast(int).alias("_code_d"),
        )
        .group_by("reporting_airline", "airline_name")
        .agg(
            col("flight_id").count().alias("total_flights"),
            col("_cancelled").sum().alias("total_cancelled"),
            col("_code_a").sum().alias("carrier_cancellations"),
            col("_code_b").sum().alias("weather_cancellations"),
            col("_code_c").sum().alias("nas_cancellations"),
            col("_code_d").sum().alias("security_cancellations"),
        )
        .filter(col("total_flights") >= lit(100))
        .with_columns(
            _round_half_away(ctx, lit(100.0) * col("total_cancelled") / col("total_flights"), 3).alias(
                "cancellation_rate_pct"
            )
        )
        .select(
            "reporting_airline",
            "airline_name",
            "total_flights",
            "total_cancelled",
            "cancellation_rate_pct",
            "carrier_cancellations",
            "weather_cancellations",
            "nas_cancellations",
            "security_cancellations",
        )
        .sort(["cancellation_rate_pct", "reporting_airline"], descending=[True, False])
    )


def market_share_expression_impl(ctx: DataFrameContext) -> Any:
    flights = ctx.get_table("flights")
    col, lit = ctx.col, ctx.lit

    carrier_totals = (
        flights.join(_airline_names(ctx), left_on="reporting_airline", right_on="a_code", how="left")
        .filter(_date_condition(col, lit, lambda col, lit: col("cancelled") == lit(0)))
        .with_columns((col("origin") + lit("-") + col("dest")).alias("route"))
        .group_by("reporting_airline", "airline_name")
        .agg(
            col("flight_id").count().alias("flight_count"),
            col("route").n_unique().alias("routes_served"),
            col("distance").mean().alias("_avg_dist"),
        )
    )

    return (
        carrier_totals.with_columns(col("flight_count").sum().alias("grand_total"))
        .with_columns(
            _round_half_away(ctx, lit(100.0) * col("flight_count") / col("grand_total"), 2).alias("market_share_pct"),
            _round_half_away(ctx, col("_avg_dist"), 0).alias("avg_distance_miles"),
        )
        .select(
            "reporting_airline",
            "airline_name",
            "flight_count",
            "routes_served",
            "avg_distance_miles",
            "market_share_pct",
        )
        .sort(["flight_count", "reporting_airline"], descending=[True, False])
        .limit(20)
    )


def _pandas_origin_airport(ctx: DataFrameContext, df: Any) -> Any:
    return df.merge(ctx.get_table("airports"), left_on="origin", right_on="code", how="left").rename(
        columns={"name": "airport_name"}
    )


def _pandas_route_airports(ctx: DataFrameContext, df: Any, *, state: bool = False) -> Any:
    cols = ["code", "city", "state"] if state else ["code", "city"]
    ao = ctx.get_table("airports")[cols].rename(
        columns={"code": "ao_code", "city": "origin_city", "state": "origin_state"}
    )
    ad = ctx.get_table("airports")[cols].rename(columns={"code": "ad_code", "city": "dest_city", "state": "dest_state"})
    return df.merge(ao, left_on="origin", right_on="ao_code", how="left").merge(
        ad, left_on="dest", right_on="ad_code", how="left"
    )


def _pandas() -> Any:
    import pandas as pd

    return pd


_DAY_NAMES = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday", 7: "Sunday"}
_MONTH_NAMES = dict(
    enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        1,
    )
)


def _bucket(series: Any, default: str, rules: tuple[tuple[str, float, str], ...]) -> Any:
    pd = _pandas()
    values = pd.Series(default, index=series.index, dtype="object")
    for op, threshold, label in rules:
        values.loc[series.lt(threshold) if op == "lt" else series.le(threshold)] = label
    return values


def _holiday_period(flights: Any) -> Any:
    pd = _pandas()
    period = pd.Series("Regular Day", index=flights.index, dtype="object")
    for label, mask in (
        ("Labor Day", (flights["month"] == 9) & (flights["day_of_week"] == 1) & (flights["day_of_month"] <= 7)),
        ("Memorial Day", (flights["month"] == 5) & (flights["day_of_week"] == 1) & (flights["day_of_month"] >= 25)),
        ("July 4th", (flights["month"] == 7) & flights["day_of_month"].between(3, 5)),
        (
            "New Year",
            ((flights["month"] == 1) & (flights["day_of_month"] <= 2))
            | ((flights["month"] == 12) & (flights["day_of_month"] == 31)),
        ),
        ("Christmas", (flights["month"] == 12) & flights["day_of_month"].between(23, 27)),
        ("Thanksgiving Week", (flights["month"] == 11) & flights["day_of_month"].between(21, 27)),
    ):
        period.loc[mask] = label
    return period


_PANDAS_EXTRAS = {
    "operated": lambda f: f["cancelled"] == 0,
    "operated_dep": lambda f: (f["cancelled"] == 0) & f["dep_delay"].notna(),
    "operated_dep_time": lambda f: (f["cancelled"] == 0) & f["dep_delay"].notna() & f["crs_dep_time"].notna(),
    "operated_arr": lambda f: (f["cancelled"] == 0) & f["arr_delay"].notna(),
    "operated_dep_arr": lambda f: (f["cancelled"] == 0) & f["dep_delay"].notna() & f["arr_delay"].notna(),
    "delayed_arr": lambda f: (f["cancelled"] == 0) & (f["arr_delay"] > 15),
    "dep_time": lambda f: f["crs_dep_time"].notna(),
}
_PANDAS_JOINS = {
    "airline": lambda ctx, df: df.merge(
        ctx.get_table("airlines"), left_on="reporting_airline", right_on="code", how="left"
    ).rename(columns={"name": "airline_name"}),
    "origin_airport": _pandas_origin_airport,
    "route_city": lambda ctx, df: _pandas_route_airports(ctx, df),
    "route_state": lambda ctx, df: _pandas_route_airports(ctx, df, state=True),
}
_DISTANCE_BUCKETS = (
    ("lt", 2000, "Cross-country (1000-1999 mi)"),
    ("lt", 1000, "Long-haul (500-999 mi)"),
    ("lt", 500, "Medium-haul (250-499 mi)"),
    ("lt", 250, "Short-haul (<250 mi)"),
)
_DELAY_BUCKETS = (
    ("le", 60, "31-60 min departure delay"),
    ("le", 30, "16-30 min departure delay"),
    ("le", 15, "1-15 min departure delay"),
    ("le", 0, "No departure delay"),
)
_PANDAS_DERIVED = {
    "dep_hour": ("dep_hour", lambda f: (f["crs_dep_time"] // 100).astype(int)),
    "hour_of_day": ("hour_of_day", lambda f: (f["crs_dep_time"] // 100).astype(int)),
    "day_name": ("day_name", lambda f: f["day_of_week"].map(_DAY_NAMES)),
    "month_name": ("month_name", lambda f: f["month"].map(_MONTH_NAMES)),
    "delay_bucket": ("delay_bucket", lambda f: _bucket(f["dep_delay"], "60+ min departure delay", _DELAY_BUCKETS)),
    "distance_bucket": (
        "distance_bucket",
        lambda f: _bucket(f["distance"], "Ultra-long (2000+ mi)", _DISTANCE_BUCKETS),
    ),
    "period": ("period", _holiday_period),
    "route": ("route", lambda f: f["origin"] + "-" + f["dest"]),
    "cancelled": ("_cancelled", lambda f: (f["cancelled"] == 1).astype(int)),
    "operated": ("_operated", lambda f: (f["cancelled"] == 0).astype(int)),
    "non_cancelled": ("_non_cancelled", lambda f: (f["cancelled"] == 0).astype(int)),
    "ontime": ("_ontime", lambda f: ((f["cancelled"] == 0) & (f["arr_delay"] <= 15)).astype(int)),
    "ontime_raw": ("_ontime", lambda f: (f["arr_delay"] <= 15).astype(int)),
    "delayed_dep": ("_delayed", lambda f: (f["dep_delay"] > 15).astype(int)),
    "delayed_arr_positive": ("_delayed_val", lambda f: f["arr_delay"].where(f["arr_delay"] > 0)),
    "dep_delay_nc": ("_dep_delay_nc", lambda f: f["dep_delay"].where(f["cancelled"] == 0)),
    "arr_delay_nc": ("_arr_delay_nc", lambda f: f["arr_delay"].where(f["cancelled"] == 0)),
    "minutes_recovered": ("_minutes_recovered", lambda f: f["dep_delay"] - f["arr_delay"]),
    "recovered": ("_recovered", lambda f: (f["arr_delay"] < f["dep_delay"]).astype(int)),
    "cascade": ("_cascade", lambda f: (f["late_aircraft_delay"] > 0).astype(int)),
    "cascade_val": ("_cascade_val", lambda f: f["late_aircraft_delay"].where(f["late_aircraft_delay"] > 0)),
    "late_tenths": (
        "_late_tenths",
        lambda f: (
            _pandas_round_half_away(f["late_aircraft_delay"] * 10, 0)
            .where(f["late_aircraft_delay"] > 0, 0)
            .astype("int64")
        ),
    ),
    "weather": ("_weather_flag", lambda f: (f["weather_delay"] > 0).astype(int)),
    "weather_val": ("_weather_val", lambda f: f["weather_delay"].where(f["weather_delay"] > 0)),
    "weather_tenths": (
        "_weather_tenths",
        lambda f: (_pandas_round_half_away(f["weather_delay"].fillna(0) * 10, 0)).astype("int64"),
    ),
    "severe_dep": ("_severely_delayed", lambda f: (f["dep_delay"] > 60).astype(int)),
    "code_a": ("_code_a", lambda f: (f["cancellation_code"] == "A").astype(int)),
    "code_b": ("_code_b", lambda f: (f["cancellation_code"] == "B").astype(int)),
    "code_c": ("_code_c", lambda f: (f["cancellation_code"] == "C").astype(int)),
    "code_d": ("_code_d", lambda f: (f["cancellation_code"] == "D").astype(int)),
}


def _csv(value: str) -> list[str]:
    return [item for item in value.split(",") if item]


def _parse_aggs(value: str) -> dict[str, tuple[str, str]]:
    return {out: (source, func) for out, source, func in (item.split(":") for item in value.split(";") if item)}


def _parse_order(value: str) -> bool | list[bool]:
    flags = [item == "True" for item in _csv(value)]
    return flags[0] if len(flags) == 1 else flags


def _apply_rates(result: Any, rates: str) -> None:
    for name, numerator, denominator, digits in (item.split(":") for item in rates.split(";") if item):
        divisor = result[numerator].sum() if denominator == "@sum" else result[denominator]
        result[name] = _pandas_round_half_away(100.0 * result[numerator] / divisor, int(digits))


def _make_pandas_impl(row: list[str]) -> Any:
    stem, extra, joins, derives, group, aggs, result_filter, rounds, rates, sort, asc, head, drop, cols, halfup = row

    def impl(ctx: DataFrameContext) -> Any:
        filtered = _pandas_window(ctx, copy=True, extra=_PANDAS_EXTRAS.get(extra))
        for join in _csv(joins):
            filtered = _PANDAS_JOINS[join](ctx, filtered)
        for name in _csv(derives):
            column, derive = _PANDAS_DERIVED[name]
            filtered[column] = derive(filtered)
        result = filtered.groupby(_csv(group) if "," in group else group, as_index=False, dropna=False).agg(
            **_parse_aggs(aggs)
        )
        if result_filter:
            column, threshold = result_filter.split(">=")
            result = result[result[column] >= int(threshold)]
        _apply_rates(result, rates)
        for column, digits in (item.split(":") for item in rounds.split(";") if item):
            result[column] = _pandas_round_half_away(result[column], int(digits))
        for out, src, div in (item.split(":") for item in halfup.split(";") if item):
            divisor = int(div)
            result[out] = (result[src] + divisor // 2) // divisor
        if sort:
            result = result.sort_values(_csv(sort) if "," in sort else sort, ascending=_parse_order(asc))
        if head:
            result = result.head(int(head))
        if drop:
            result = result.drop(columns=_csv(drop))
        if cols:
            result = result[_csv(cols)]
        return result.reset_index(drop=True)

    impl.__name__ = f"{stem}_pandas_impl"
    impl.__qualname__ = impl.__name__
    return impl


_PANDAS_QUERY_METADATA = """\
ontime_by_carrier|operated|airline|ontime_raw,delayed_arr_positive|reporting_airline,airline_name|total_flights:flight_id:count;ontime_flights:_ontime:sum;avg_delay_when_late:_delayed_val:mean||avg_delay_when_late:2|ontime_pct:ontime_flights:total_flights:2|ontime_pct,reporting_airline|False,True|||reporting_airline,airline_name,total_flights,ontime_flights,ontime_pct,avg_delay_when_late|
delay_by_airport|operated_dep|origin_airport|delayed_dep|origin,airport_name,city,state|total_flights:flight_id:count;avg_dep_delay:dep_delay:mean;avg_arr_delay:arr_delay:mean;delayed_flights:_delayed:sum|total_flights>=100|avg_dep_delay:2;avg_arr_delay:2||avg_dep_delay,origin|False,True|50|||
delay_by_hour|operated_dep_time||dep_hour,delayed_dep|dep_hour|total_flights:flight_id:count;avg_dep_delay:dep_delay:mean;avg_arr_delay:arr_delay:mean;delayed_count:_delayed:sum||avg_dep_delay:2;avg_arr_delay:2|delay_rate_pct:delayed_count:total_flights:2|dep_hour|True||||
best_routes|operated_arr|route_city|ontime_raw|origin,dest,origin_city,dest_city|total_flights:flight_id:count;_ontime_sum:_ontime:sum;avg_arr_delay:arr_delay:mean;avg_distance_miles:distance:mean|total_flights>=50|avg_arr_delay:2;avg_distance_miles:0|ontime_pct:_ontime_sum:total_flights:2|ontime_pct,origin,dest|False,True,True|25|_ontime_sum|origin,dest,origin_city,dest_city,total_flights,ontime_pct,avg_arr_delay,avg_distance_miles|
improvement_trend|||cancelled,ontime,non_cancelled,arr_delay_nc|year|total_flights:flight_id:count;cancelled_flights:_cancelled:sum;ontime_flights:_ontime:sum;_non_cancelled:_non_cancelled:sum;avg_arr_delay:_arr_delay_nc:mean||avg_arr_delay:2|cancellation_rate_pct:cancelled_flights:total_flights:2;ontime_pct:ontime_flights:_non_cancelled:2|year|True||_non_cancelled|year,total_flights,cancelled_flights,cancellation_rate_pct,ontime_flights,ontime_pct,avg_arr_delay|
cascade_delays|operated|airline|cascade,cascade_val,late_tenths|reporting_airline,airline_name|total_flights:flight_id:count;cascade_delayed:_cascade:sum;avg_cascade_delay:_cascade_val:mean;total_tenths:_late_tenths:sum||avg_cascade_delay:2|cascade_rate_pct:cascade_delayed:total_flights:2|cascade_rate_pct,reporting_airline|False,True|||reporting_airline,airline_name,total_flights,cascade_delayed,cascade_rate_pct,avg_cascade_delay,total_cascade_minutes|total_cascade_minutes:total_tenths:10
weather_impact|operated||weather,weather_val,weather_tenths|month|total_flights:flight_id:count;weather_delayed:_weather_flag:sum;avg_weather_delay_min:_weather_val:mean;total_tenths:_weather_tenths:sum||avg_weather_delay_min:2|weather_delay_rate_pct:weather_delayed:total_flights:2|month|True|||month,total_flights,weather_delayed,weather_delay_rate_pct,avg_weather_delay_min,total_weather_minutes|total_weather_minutes:total_tenths:10
recovery_time|operated_dep_arr||delay_bucket,minutes_recovered,recovered|delay_bucket|flight_count:flight_id:count;avg_dep_delay:dep_delay:mean;avg_arr_delay:arr_delay:mean;avg_minutes_recovered:_minutes_recovered:mean;_total:flight_id:count;_recovered_sum:_recovered:sum||avg_dep_delay:2;avg_arr_delay:2;avg_minutes_recovered:2|pct_recovered:_recovered_sum:_total:2|avg_dep_delay|True||_total,_recovered_sum||
busiest_routes|operated|route_state|ontime_raw|origin,dest,origin_city,origin_state,dest_city,dest_state|total_flights:flight_id:count;avg_distance_miles:distance:mean;avg_duration_min:actual_elapsed_time:mean;_ontime_sum:_ontime:sum||avg_distance_miles:0;avg_duration_min:0|ontime_pct:_ontime_sum:total_flights:2|total_flights,origin,dest|False,True,True|25|_ontime_sum||
route_reliability||route_city|cancelled,ontime,non_cancelled|origin,dest,origin_city,dest_city|total_scheduled:flight_id:count;cancelled_count:_cancelled:sum;ontime_count:_ontime:sum;_non_cancelled:_non_cancelled:sum;distance_miles:distance:mean|total_scheduled>=100|distance_miles:0|cancellation_rate_pct:cancelled_count:total_scheduled:2;ontime_pct:ontime_count:_non_cancelled:2|ontime_pct,cancellation_rate_pct,origin,dest|False,True,True,True|30|_non_cancelled|origin,dest,origin_city,dest_city,total_scheduled,cancelled_count,cancellation_rate_pct,ontime_count,ontime_pct,distance_miles|
distance_delay|operated_arr||distance_bucket,ontime_raw|distance_bucket|total_flights:flight_id:count;avg_distance_miles:distance:mean;avg_dep_delay:dep_delay:mean;avg_arr_delay:arr_delay:mean;_ontime_sum:_ontime:sum||avg_distance_miles:0;avg_dep_delay:2;avg_arr_delay:2|ontime_pct:_ontime_sum:total_flights:2|avg_distance_miles|True||_ontime_sum||
hub_connectivity|operated|origin_airport||origin,airport_name,city,state|unique_destinations:dest:nunique;serving_carriers:reporting_airline:nunique;total_departures:flight_id:count;avg_dep_delay:dep_delay:mean||avg_dep_delay:2||total_departures,origin|False,True|30|||
day_of_week|||day_name,cancelled,dep_delay_nc,arr_delay_nc,ontime,non_cancelled|day_of_week,day_name|total_flights:flight_id:count;cancelled_count:_cancelled:sum;avg_dep_delay:_dep_delay_nc:mean;avg_arr_delay:_arr_delay_nc:mean;_ontime:_ontime:sum;_non_cancelled:_non_cancelled:sum||avg_dep_delay:2;avg_arr_delay:2|ontime_pct:_ontime:_non_cancelled:2|day_of_week|True||_ontime,_non_cancelled||
seasonal_trends|||month_name,cancelled,arr_delay_nc,ontime,non_cancelled|month,month_name|total_flights:flight_id:count;cancelled_count:_cancelled:sum;avg_arr_delay:_arr_delay_nc:mean;_ontime:_ontime:sum;_non_cancelled:_non_cancelled:sum||avg_arr_delay:2|cancellation_rate_pct:cancelled_count:total_flights:2;ontime_pct:_ontime:_non_cancelled:2|month|True||_ontime,_non_cancelled|month,month_name,total_flights,cancelled_count,cancellation_rate_pct,avg_arr_delay,ontime_pct|
holiday_impact|||period,cancelled,arr_delay_nc,ontime,non_cancelled|period|total_flights:flight_id:count;avg_arr_delay:_arr_delay_nc:mean;_cancelled_sum:_cancelled:sum;_ontime:_ontime:sum;_non_cancelled:_non_cancelled:sum||avg_arr_delay:2|cancellation_rate_pct:_cancelled_sum:total_flights:2;ontime_pct:_ontime:_non_cancelled:2|avg_arr_delay|False||_cancelled_sum,_ontime,_non_cancelled||
time_of_day|dep_time||hour_of_day,dep_delay_nc,arr_delay_nc,severe_dep,ontime,non_cancelled|hour_of_day|total_flights:flight_id:count;avg_dep_delay:_dep_delay_nc:mean;avg_arr_delay:_arr_delay_nc:mean;severely_delayed:_severely_delayed:sum;_ontime:_ontime:sum;_non_cancelled:_non_cancelled:sum||avg_dep_delay:2;avg_arr_delay:2|ontime_pct:_ontime:_non_cancelled:2|hour_of_day|True||_ontime,_non_cancelled||
carrier_ranking||airline|operated,cancelled,dep_delay_nc,arr_delay_nc,ontime,non_cancelled|reporting_airline,airline_name|total_scheduled:flight_id:count;operated_flights:_operated:sum;cancelled_flights:_cancelled:sum;avg_dep_delay:_dep_delay_nc:mean;avg_arr_delay:_arr_delay_nc:mean;_ontime:_ontime:sum;_non_cancelled:_non_cancelled:sum;avg_route_distance_miles:distance:mean|total_scheduled>=1000|avg_dep_delay:2;avg_arr_delay:2;avg_route_distance_miles:0|cancellation_rate_pct:cancelled_flights:total_scheduled:2;ontime_pct:_ontime:_non_cancelled:2|ontime_pct,reporting_airline|False,True||_ontime,_non_cancelled|reporting_airline,airline_name,total_scheduled,operated_flights,cancelled_flights,cancellation_rate_pct,avg_dep_delay,avg_arr_delay,ontime_pct,avg_route_distance_miles|
cancellation_rate||airline|cancelled,code_a,code_b,code_c,code_d|reporting_airline,airline_name|total_flights:flight_id:count;total_cancelled:_cancelled:sum;carrier_cancellations:_code_a:sum;weather_cancellations:_code_b:sum;nas_cancellations:_code_c:sum;security_cancellations:_code_d:sum|total_flights>=100||cancellation_rate_pct:total_cancelled:total_flights:3|cancellation_rate_pct,reporting_airline|False,True|||reporting_airline,airline_name,total_flights,total_cancelled,cancellation_rate_pct,carrier_cancellations,weather_cancellations,nas_cancellations,security_cancellations|
market_share|operated|airline|route|reporting_airline,airline_name|flight_count:flight_id:count;routes_served:route:nunique;avg_distance_miles:distance:mean||avg_distance_miles:0|market_share_pct:flight_count:@sum:2|flight_count,reporting_airline|False,True|20|||
"""

globals().update(
    {
        f"{row[0]}_pandas_impl": _make_pandas_impl(row)
        for row in reader(_PANDAS_QUERY_METADATA.splitlines(), delimiter="|")
    }
)


def delay_causes_pandas_impl(ctx: DataFrameContext) -> Any:
    pd = _pandas()
    filtered = _pandas_window(ctx, extra=_PANDAS_EXTRAS["delayed_arr"])
    row: dict[str, Any] = {}
    for column, count_name, avg_name in (
        ("carrier_delay", "carrier_delay_count", "avg_carrier_delay"),
        ("weather_delay", "weather_delay_count", "avg_weather_delay"),
        ("nas_delay", "nas_delay_count", "avg_nas_delay"),
        ("security_delay", "security_delay_count", "avg_security_delay"),
        ("late_aircraft_delay", "late_aircraft_count", "avg_late_aircraft_delay"),
    ):
        positive = filtered[column] > 0
        row[count_name] = int(positive.sum())
        row[avg_name] = float(_pandas_round_half_away(filtered[column].where(positive).mean(), 2))
    row["total_delayed_flights"] = len(filtered)
    return pd.DataFrame(
        [row],
        columns=[
            "carrier_delay_count",
            "avg_carrier_delay",
            "weather_delay_count",
            "avg_weather_delay",
            "nas_delay_count",
            "avg_nas_delay",
            "security_delay_count",
            "avg_security_delay",
            "late_aircraft_count",
            "avg_late_aircraft_delay",
            "total_delayed_flights",
        ],
    )


FLIGHTDATA_DATAFRAME_QUERIES = QueryRegistry("FlightData DataFrame")

_CATEGORY_CODES = {
    "AG": QueryCategory.AGGREGATE,
    "FI": QueryCategory.FILTER,
    "GB": QueryCategory.GROUP_BY,
    "JO": QueryCategory.JOIN,
    "SO": QueryCategory.SORT,
}

_QUERY_METADATA = """\
ontime-by-carrier|On-Time Rate by Carrier|On-time arrival percentage by airline carrier|JO,AG,GB|ontime_by_carrier
delay-by-airport|Average Departure Delay by Origin Airport|Average departure delay in minutes by origin airport|JO,AG,GB,FI|delay_by_airport
delay-by-hour|Delay Pattern by Departure Hour|Average delay minutes by scheduled departure hour|AG,GB|delay_by_hour
best-routes|Best Performing Routes by On-Time Rate|Routes with highest on-time arrival percentage (min 50 flights)|JO,AG,GB,FI|best_routes
improvement-trend|On-Time Performance Trend Over Years|Annual on-time arrival percentage trend|AG,GB|improvement_trend
delay-causes|Delay Cause Breakdown|Distribution of delay causes across all delayed flights|AG,FI|delay_causes
cascade-delays|Late Aircraft (Cascade) Delay Patterns|Flights delayed due to late incoming aircraft by carrier|JO,AG,GB|cascade_delays
weather-impact|Weather Delay Impact by Month|Average weather delay by month showing seasonal patterns|AG,GB|weather_impact
recovery-time|Delay Recovery Analysis|How much of departure delay is recovered in flight|AG,GB|recovery_time
busiest-routes|Busiest Routes by Flight Count|Top 25 routes ranked by total flight count|JO,AG,GB,SO|busiest_routes
route-reliability|Route Reliability Ranking|Routes ranked by reliability (on-time + low cancellation)|JO,AG,GB,FI|route_reliability
distance-delay|Distance vs Delay Correlation|Average delay grouped by flight distance buckets|AG,GB|distance_delay
hub-connectivity|Hub Airport Connectivity|Number of unique destinations served from each origin airport|JO,AG,GB|hub_connectivity
day-of-week|Flight Patterns by Day of Week|Flight volume and delay patterns by day of week|AG,GB|day_of_week
seasonal-trends|Seasonal Performance Trends|Monthly flight volume and performance aggregated across all years|AG,GB|seasonal_trends
holiday-impact|Holiday Period Delay Impact|Compare performance during major holiday periods vs normal days|AG,GB,FI|holiday_impact
time-of-day|Hourly Congestion Pattern|Flight volume and delay by scheduled departure hour of day|AG,GB|time_of_day
carrier-ranking|Carrier On-Time Performance Ranking|Full carrier scorecard: on-time, delays, cancellations|JO,AG,GB,FI|carrier_ranking
cancellation-rate|Cancellation Rates by Carrier and Cause|Cancellation frequency and reason breakdown by carrier|JO,AG,GB|cancellation_rate
market-share|Carrier Market Share by Route|Top carriers ranked by flight volume and market share|JO,AG,GB,SO|market_share
"""


def _impl_for(stem: str, family: str) -> Any:
    return globals()[f"{stem}_{family}_impl"]


def _make_query(row: list[str]) -> DataFrameQuery:
    query_id, query_name, description, category_codes, impl_stem = row
    return DataFrameQuery(
        query_id=query_id,
        query_name=query_name,
        description=description,
        categories=[_CATEGORY_CODES[code] for code in category_codes.split(",")],
        expression_impl=_impl_for(impl_stem, "expression"),
        pandas_impl=_impl_for(impl_stem, "pandas"),
    )


_QUERIES = [_make_query(row) for row in reader(_QUERY_METADATA.splitlines(), delimiter="|")]

for _query in _QUERIES:
    FLIGHTDATA_DATAFRAME_QUERIES.register(_query)


def get_dataframe_queries() -> QueryRegistry:
    return FLIGHTDATA_DATAFRAME_QUERIES
