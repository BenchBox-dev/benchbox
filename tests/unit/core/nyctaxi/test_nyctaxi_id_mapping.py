# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import re

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

EXPECTED_MAPPING = {
    "Q1": "trips-per-hour",
    "Q2": "trips-per-day",
    "Q3": "trips-per-month",
    "Q4": "trips-by-day-of-week",
    "Q5": "top-pickup-zones",
    "Q6": "top-dropoff-zones",
    "Q7": "top-routes",
    "Q8": "borough-summary",
    "Q9": "revenue-by-payment-type",
    "Q10": "fare-distribution",
    "Q11": "tip-analysis",
    "Q12": "surcharge-revenue",
    "Q13": "distance-distribution",
    "Q14": "passenger-count-analysis",
    "Q15": "trip-duration-analysis",
    "Q16": "rate-code-summary",
    "Q17": "airport-trips",
    "Q18": "vendor-comparison",
    "Q19": "hourly-zone-heatmap",
    "Q20": "weekday-weekend-comparison",
    "Q21": "rush-hour-analysis",
    "Q22": "monthly-year-over-year",
    "Q23": "single-day-summary",
    "Q24": "zone-detail",
    "Q25": "full-scan-count",
}


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def test_dataframe_titles_slugify_to_sql_ids() -> None:
    from benchbox.core.benchmark_loader import get_core_benchmark_class
    from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES

    sql_ids = {str(q) for q in get_core_benchmark_class("nyctaxi")(scale_factor=0.01).get_queries()}
    assert len(sql_ids) == 25
    for query in NYCTAXI_DATAFRAME_QUERIES.get_all_queries():
        assert _slug(query.query_name) in sql_ids, f"{query.query_id} ({query.query_name!r}) matches no SQL query"


def test_each_dataframe_id_maps_to_its_own_title_slug() -> None:
    from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES

    for query in NYCTAXI_DATAFRAME_QUERIES.get_all_queries():
        assert EXPECTED_MAPPING[query.query_id] == _slug(query.query_name), (
            f"{query.query_id} maps to {EXPECTED_MAPPING[query.query_id]!r} "
            f"but its title slugifies to {_slug(query.query_name)!r}"
        )


def test_mapping_table_is_exact_and_total() -> None:
    from benchbox.core.benchmark_loader import get_core_benchmark_class
    from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES

    sql_ids = {str(q) for q in get_core_benchmark_class("nyctaxi")(scale_factor=0.01).get_queries()}
    df_ids = {str(q) for q in NYCTAXI_DATAFRAME_QUERIES.get_query_ids()}
    assert set(EXPECTED_MAPPING) == df_ids
    assert sorted(EXPECTED_MAPPING.values()) == sorted(sql_ids)


