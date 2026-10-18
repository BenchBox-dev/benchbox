# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import subprocess
import sys
from datetime import date

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestQueryRegistration:
    def test_all_20_queries_registered(self):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        assert len(registry) == 20

    def test_registry_returns_consistent_object(self):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

        r1 = get_dataframe_queries()
        r2 = get_dataframe_queries()
        assert r1.get_query_ids() == r2.get_query_ids()

    def test_expected_query_ids_present(self):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        ids = registry.get_query_ids()
        expected = [
            "ontime-by-carrier",
            "delay-by-airport",
            "delay-by-hour",
            "best-routes",
            "improvement-trend",
            "delay-causes",
            "cascade-delays",
            "weather-impact",
            "recovery-time",
            "busiest-routes",
            "route-reliability",
            "distance-delay",
            "hub-connectivity",
            "day-of-week",
            "seasonal-trends",
            "holiday-impact",
            "time-of-day",
            "carrier-ranking",
            "cancellation-rate",
            "market-share",
        ]
        for qid in expected:
            assert qid in ids, f"Query {qid!r} not found in registry"

    def test_all_queries_have_both_impls(self):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        for query in registry.get_all_queries():
            assert query.has_expression_impl(), f"{query.query_id} missing expression_impl"
            assert query.has_pandas_impl(), f"{query.query_id} missing pandas_impl"

    def test_all_queries_have_categories(self):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        for query in registry.get_all_queries():
            assert query.categories, f"{query.query_id} has no categories"

    def test_benchmark_registry_supports_dataframe(self):
        from benchbox.core.benchmark_registry import BENCHMARK_METADATA

        assert BENCHMARK_METADATA["flightdata"]["supports_dataframe"] is True

    def test_benchmark_class_exposes_registry(self):
        from benchbox.core.flightdata.benchmark import FlightDataBenchmark

        bm = FlightDataBenchmark(scale_factor=0.01)
        registry = bm.get_dataframe_queries()
        assert len(registry) == 20

    def test_benchmark_exposes_registry_without_pandas_import(self):
        code = (
            "import sys; "
            "sys.modules['pandas'] = None; "
            "from benchbox.core.flightdata.benchmark import FlightDataBenchmark; "
            "registry = FlightDataBenchmark(scale_factor=0.01).get_dataframe_queries(); "
            "assert len(registry) == 20"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0, f"FlightData registry import required pandas:\n{result.stderr}"


class TestParameterOverrides:
    def test_default_parameters(self):
        from benchbox.core.flightdata.dataframe_queries import (
            FLIGHTDATA_DEFAULT_PARAMS,
            get_flightdata_parameters,
            set_parameter_overrides,
        )

        set_parameter_overrides(None)
        params = get_flightdata_parameters()
        assert params["start_date"] == FLIGHTDATA_DEFAULT_PARAMS["start_date"]
        assert params["end_date"] == FLIGHTDATA_DEFAULT_PARAMS["end_date"]

    def test_parameter_override(self):
        from benchbox.core.flightdata.dataframe_queries import (
            FLIGHTDATA_DEFAULT_PARAMS,
            get_flightdata_parameters,
            set_parameter_overrides,
        )

        custom_start = date(2020, 1, 1)
        custom_end = date(2021, 1, 1)
        set_parameter_overrides({"start_date": custom_start, "end_date": custom_end})

        params = get_flightdata_parameters()
        assert params["start_date"] == custom_start
        assert params["end_date"] == custom_end

        set_parameter_overrides(None)
        params = get_flightdata_parameters()
        assert params["start_date"] == FLIGHTDATA_DEFAULT_PARAMS["start_date"]

    def test_clear_overrides(self):
        from benchbox.core.flightdata.dataframe_queries import (
            FLIGHTDATA_DEFAULT_PARAMS,
            get_flightdata_parameters,
            set_parameter_overrides,
        )

        set_parameter_overrides({"start_date": date(2020, 6, 1)})
        set_parameter_overrides(None)
        params = get_flightdata_parameters()
        assert params["start_date"] == FLIGHTDATA_DEFAULT_PARAMS["start_date"]


@pytest.fixture
def pandas_ctx():
    pytest.importorskip("pandas")
    import pandas as pd

    flights = pd.DataFrame(
        {
            "flight_id": range(1, 11),
            "flight_date": [date(2024, 12, i) for i in range(1, 11)],
            "year": [2024] * 10,
            "month": [12] * 10,
            "day_of_month": list(range(1, 11)),
            "day_of_week": [1, 2, 3, 4, 5, 6, 7, 1, 2, 3],
            "reporting_airline": ["AA", "AA", "DL", "DL", "UA", "UA", "WN", "WN", "B6", "B6"],
            "origin": ["JFK", "LAX", "ORD", "ATL", "DFW", "SFO", "LAS", "PHX", "BOS", "MIA"],
            "dest": ["LAX", "JFK", "ATL", "ORD", "SFO", "DFW", "PHX", "LAS", "MIA", "BOS"],
            "crs_dep_time": [800, 1200, 1400, 900, 1600, 700, 1100, 1500, 800, 1800],
            "dep_time": [810.0, 1210.0, 1405.0, 900.0, 1620.0, 705.0, 1100.0, None, 800.0, 1815.0],
            "dep_delay": [10.0, 10.0, 5.0, 0.0, 20.0, 5.0, 0.0, None, 0.0, 15.0],
            "crs_arr_time": [1100, 1530, 1700, 1100, 1900, 1000, 1300, 1700, 1000, 2030],
            "arr_time": [1110.0, 1540.0, 1705.0, 1100.0, 1915.0, 1005.0, None, None, 1000.0, 2030.0],
            "arr_delay": [10.0, 10.0, 5.0, 0.0, 15.0, 5.0, None, None, 0.0, 0.0],
            "cancelled": [0, 0, 0, 0, 0, 0, 1, 1, 0, 0],
            "cancellation_code": [None, None, None, None, None, None, "A", "B", None, None],
            "diverted": [0] * 10,
            "crs_elapsed_time": [180.0] * 10,
            "actual_elapsed_time": [180.0, 180.0, 180.0, 180.0, 180.0, 180.0, None, None, 180.0, 180.0],
            "air_time": [160.0] * 10,
            "distance": [2500.0, 2500.0, 800.0, 800.0, 1500.0, 1500.0, 400.0, 400.0, 1200.0, 1200.0],
            "carrier_delay": [5.0, 0.0, 0.0, 0.0, 10.0, 0.0, None, None, 0.0, 0.0],
            "weather_delay": [0.0, 5.0, 0.0, 0.0, 0.0, 0.0, None, None, 0.0, 0.0],
            "nas_delay": [0.0, 0.0, 5.0, 0.0, 0.0, 5.0, None, None, 0.0, 0.0],
            "security_delay": [0.0] * 10,
            "late_aircraft_delay": [5.0, 5.0, 0.0, 0.0, 5.0, 0.0, None, None, 0.0, 0.0],
        }
    )
    airlines = pd.DataFrame(
        {"code": ["AA", "DL", "UA", "WN", "B6"], "name": ["American", "Delta", "United", "Southwest", "JetBlue"]}
    )
    airports = pd.DataFrame(
        {
            "code": ["JFK", "LAX", "ORD", "ATL", "DFW", "SFO", "LAS", "PHX", "BOS", "MIA"],
            "name": [
                "JFK Intl",
                "LAX Intl",
                "Chicago O'Hare",
                "Atlanta Hartsfield",
                "DFW Intl",
                "San Francisco",
                "Las Vegas",
                "Phoenix",
                "Boston Logan",
                "Miami Intl",
            ],
            "city": [
                "New York",
                "Los Angeles",
                "Chicago",
                "Atlanta",
                "Dallas",
                "San Francisco",
                "Las Vegas",
                "Phoenix",
                "Boston",
                "Miami",
            ],
            "state": ["NY", "CA", "IL", "GA", "TX", "CA", "NV", "AZ", "MA", "FL"],
            "latitude": [40.6, 33.9, 41.9, 33.6, 32.9, 37.6, 36.1, 33.4, 42.4, 25.8],
            "longitude": [-73.8, -118.4, -87.9, -84.4, -97.0, -122.4, -115.2, -112.0, -71.0, -80.3],
        }
    )

    tables = {"flights": flights, "airlines": airlines, "airports": airports}

    class SimplePandasContext:
        def get_table(self, name: str):
            return tables[name]

        def col(self, name: str):
            raise NotImplementedError("Expression API not available in pandas context")

        def lit(self, value):
            raise NotImplementedError("Expression API not available in pandas context")

    return SimplePandasContext()


@pytest.fixture
def polars_ctx():
    pl = pytest.importorskip("polars")

    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    adapter = PolarsDataFrameAdapter()
    ctx = adapter.create_context()
    ctx.register_table(
        "flights",
        pl.DataFrame(
            {
                "flight_id": range(1, 11),
                "flight_date": [date(2024, 12, i) for i in range(1, 11)],
                "month": [1, 2, 3, 4, 5, 6, 7, 11, 12, 12],
                "day_of_month": [1, 2, 3, 4, 26, 6, 4, 24, 24, 31],
                "day_of_week": [1, 2, 3, 4, 1, 6, 7, 4, 1, 2],
                "crs_dep_time": [800, 1200, 1400, 900, 1600, 700, 1100, 1500, 800, 1800],
                "distance": [100.0, 300.0, 750.0, 1500.0, 2500.0, 400.0, 2200.0, 180.0, 900.0, 1200.0],
                "dep_delay": [0.0, 12.0, 25.0, 45.0, 80.0, 5.0, 0.0, 30.0, 10.0, 15.0],
                "arr_delay": [0.0, 10.0, 20.0, 30.0, 60.0, 5.0, 0.0, 25.0, 8.0, 5.0],
                "cancelled": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                "carrier_delay": [0.0, 5.0, 10.0, 0.0, 30.0, 0.0, 0.0, 5.0, 0.0, 0.0],
                "weather_delay": [0.0, 0.0, 5.0, 10.0, 0.0, 0.0, 0.0, 5.0, 0.0, 0.0],
                "nas_delay": [0.0, 5.0, 0.0, 10.0, 20.0, 0.0, 0.0, 10.0, 0.0, 0.0],
                "security_delay": [0.0] * 10,
                "late_aircraft_delay": [0.0, 0.0, 5.0, 10.0, 10.0, 0.0, 0.0, 5.0, 0.0, 0.0],
            }
        ),
    )
    return ctx


class TestPandasImplExecute:
    def test_ontime_by_carrier(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import ontime_by_carrier_pandas_impl

        result = ontime_by_carrier_pandas_impl(pandas_ctx)
        assert result is not None
        assert len(result) > 0
        assert "total_flights" in result.columns
        assert "ontime_pct" in result.columns

    def test_delay_by_hour(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import delay_by_hour_pandas_impl

        result = delay_by_hour_pandas_impl(pandas_ctx)
        assert result is not None
        assert "dep_hour" in result.columns

    def test_improvement_trend(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import improvement_trend_pandas_impl

        result = improvement_trend_pandas_impl(pandas_ctx)
        assert result is not None
        assert "year" in result.columns
        assert "total_flights" in result.columns

    def test_hub_connectivity(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import hub_connectivity_pandas_impl

        result = hub_connectivity_pandas_impl(pandas_ctx)
        assert result is not None
        assert "unique_destinations" in result.columns

    def test_day_of_week(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import day_of_week_pandas_impl

        result = day_of_week_pandas_impl(pandas_ctx)
        assert result is not None
        assert "day_name" in result.columns

    def test_seasonal_trends(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import seasonal_trends_pandas_impl

        result = seasonal_trends_pandas_impl(pandas_ctx)
        assert result is not None
        assert "month_name" in result.columns

    def test_delay_causes(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import delay_causes_pandas_impl

        result = delay_causes_pandas_impl(pandas_ctx)
        assert result is not None

    def test_recovery_time(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import recovery_time_pandas_impl

        result = recovery_time_pandas_impl(pandas_ctx)
        assert result is not None
        assert "delay_bucket" in result.columns

    def test_distance_delay(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import distance_delay_pandas_impl

        result = distance_delay_pandas_impl(pandas_ctx)
        assert result is not None
        assert "distance_bucket" in result.columns

    def test_market_share(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import market_share_pandas_impl

        result = market_share_pandas_impl(pandas_ctx)
        assert result is not None
        assert "market_share_pct" in result.columns

    def test_holiday_impact(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import holiday_impact_pandas_impl

        result = holiday_impact_pandas_impl(pandas_ctx)
        assert result is not None
        assert "period" in result.columns
        assert "total_flights" in result.columns

    def test_date_range_filtering(self, pandas_ctx):

        from benchbox.core.flightdata.dataframe_queries import (
            improvement_trend_pandas_impl,
            set_parameter_overrides,
        )

        set_parameter_overrides({"start_date": date(2099, 1, 1), "end_date": date(2100, 1, 1)})
        result = improvement_trend_pandas_impl(pandas_ctx)
        assert len(result) == 0
        set_parameter_overrides(None)

    def test_distance_bucket_labels_consistent(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import distance_delay_pandas_impl

        expected = {
            "Short-haul (<250 mi)",
            "Medium-haul (250-499 mi)",
            "Long-haul (500-999 mi)",
            "Cross-country (1000-1999 mi)",
            "Ultra-long (2000+ mi)",
        }
        result = distance_delay_pandas_impl(pandas_ctx)
        actual = set(result["distance_bucket"].unique())
        assert actual.issubset(expected), f"Unexpected distance labels: {actual - expected}"

    def test_holiday_labels_consistent(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import holiday_impact_pandas_impl

        expected = {
            "Thanksgiving Week",
            "Christmas",
            "New Year",
            "July 4th",
            "Memorial Day",
            "Labor Day",
            "Regular Day",
        }
        result = holiday_impact_pandas_impl(pandas_ctx)
        actual = set(result["period"].unique())
        assert actual.issubset(expected), f"Unexpected holiday labels: {actual - expected}"

    def test_delay_causes_executes_via_pandas_adapter(self, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        query = get_dataframe_queries().get_or_raise("delay-causes")
        result = PandasDataFrameAdapter().execute_query(pandas_ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 1


class TestExpressionImplParity:
    def test_day_of_week_expression_includes_day_name(self, polars_ctx):
        from benchbox.core.flightdata.dataframe_queries import day_of_week_expression_impl

        result = day_of_week_expression_impl(polars_ctx).collect()
        assert "day_name" in result.columns

    def test_seasonal_trends_expression_includes_month_name(self, polars_ctx):
        from benchbox.core.flightdata.dataframe_queries import seasonal_trends_expression_impl

        result = seasonal_trends_expression_impl(polars_ctx).collect()
        assert "month_name" in result.columns

    def test_distance_delay_expression_labels_match_sql(self, polars_ctx):
        from benchbox.core.flightdata.dataframe_queries import distance_delay_expression_impl

        expected = {
            "Short-haul (<250 mi)",
            "Medium-haul (250-499 mi)",
            "Long-haul (500-999 mi)",
            "Cross-country (1000-1999 mi)",
            "Ultra-long (2000+ mi)",
        }
        result = distance_delay_expression_impl(polars_ctx).collect()
        actual = set(result["distance_bucket"].to_list())
        assert actual.issubset(expected), f"Unexpected distance labels: {actual - expected}"

    def test_holiday_impact_expression_labels_match_sql(self, polars_ctx):
        from benchbox.core.flightdata.dataframe_queries import holiday_impact_expression_impl

        expected = {
            "Thanksgiving Week",
            "Christmas",
            "New Year",
            "July 4th",
            "Memorial Day",
            "Labor Day",
            "Regular Day",
        }
        result = holiday_impact_expression_impl(polars_ctx).collect()
        actual = set(result["period"].to_list())
        assert actual.issubset(expected), f"Unexpected holiday labels: {actual - expected}"

    @pytest.mark.parametrize(
        ("query_func_name", "expected_column"),
        [
            ("delay_by_hour_expression_impl", "dep_hour"),
            ("delay_causes_expression_impl", "total_delayed_flights"),
            ("time_of_day_expression_impl", "hour_of_day"),
        ],
    )
    def test_expression_queries_used_by_sf1_smoke_execute(self, polars_ctx, query_func_name, expected_column):
        import benchbox.core.flightdata.dataframe_queries as flight_queries

        result = getattr(flight_queries, query_func_name)(polars_ctx).collect()

        assert expected_column in result.columns


class TestBenchmarkDateSync:
    def test_benchmark_syncs_sql_date_window_to_dataframe_queries(self):
        from benchbox.core.flightdata.benchmark import FlightDataBenchmark
        from benchbox.core.flightdata.dataframe_queries import (
            get_flightdata_parameters,
            set_parameter_overrides,
        )

        benchmark = FlightDataBenchmark(scale_factor=0.01)
        benchmark.get_dataframe_queries()
        params = get_flightdata_parameters()

        assert params["start_date"] == date.fromisoformat(benchmark._query_start_date)
        assert params["end_date"] == date.fromisoformat(benchmark._query_end_date)
        set_parameter_overrides(None)
