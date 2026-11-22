from __future__ import annotations

import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import SelectVariantPayload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

import benchbox.sql_compat.rules.query_source.nyctaxi_variants
from benchbox.sql_compat.registry import REGISTRY

_NYCTAXI_STARROCKS_RULES = [
    ("query_source.starrocks.nyctaxi.trips_by_dow_dayofweek_variant", "trips-by-day-of-week"),
    ("query_source.starrocks.nyctaxi.weekday_weekend_dayofweek_variant", "weekday-weekend-comparison"),
    ("query_source.starrocks.nyctaxi.rush_hour_timestampdiff_variant", "rush-hour-analysis"),
    ("query_source.starrocks.nyctaxi.trip_duration_timestampdiff_variant", "trip-duration-analysis"),
    ("query_source.starrocks.nyctaxi.fhv_base_volume_timestampdiff_variant", "fhv-base-volume"),
]
_NYCTAXI_CLICKHOUSE_RULES = [
    ("query_source.clickhouse.nyctaxi.trips_by_dow_todayofweek_variant", "trips-by-day-of-week"),
    ("query_source.clickhouse.nyctaxi.weekday_weekend_tohour_variant", "weekday-weekend-comparison"),
    ("query_source.clickhouse.nyctaxi.rush_hour_datediff_variant", "rush-hour-analysis"),
    ("query_source.clickhouse.nyctaxi.trip_duration_datediff_variant", "trip-duration-analysis"),
    ("query_source.clickhouse.nyctaxi.fhv_base_volume_datediff_variant", "fhv-base-volume"),
]


def test_nyctaxi_variant_rules_registered():
    rules = [
        (key, entry) for key, entry in REGISTRY.all_rules() if key[0] is Phase.QUERY_SOURCE and key[2] == "nyctaxi"
    ]
    assert len(rules) == 10, f"Expected 10 nyctaxi rules, got {len(rules)}: {[e.rule_id for _, e in rules]}"
    rule_ids = {entry.rule_id for _, entry in rules}
    for rule_id, _ in _NYCTAXI_STARROCKS_RULES + _NYCTAXI_CLICKHOUSE_RULES:
        assert rule_id in rule_ids, f"Missing rule: {rule_id}"


@pytest.mark.parametrize(
    "platform,query_id,expected_snippet",
    [
        ("starrocks", "trips-by-day-of-week", "DAYOFWEEK"),
        ("starrocks", "weekday-weekend-comparison", "DAYOFWEEK"),
        ("starrocks", "rush-hour-analysis", "TIMESTAMPDIFF"),
        ("starrocks", "trip-duration-analysis", "TIMESTAMPDIFF"),
        ("starrocks", "fhv-base-volume", "TIMESTAMPDIFF"),
        ("clickhouse", "trips-by-day-of-week", "toDayOfWeek"),
        ("clickhouse", "weekday-weekend-comparison", "toHour"),
        ("clickhouse", "rush-hour-analysis", "dateDiff"),
        ("clickhouse", "trip-duration-analysis", "dateDiff"),
        ("clickhouse", "fhv-base-volume", "dateDiff"),
    ],
)
def test_nyctaxi_rule_action_and_payload(platform: str, query_id: str, expected_snippet: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="nyctaxi",
        query_id=query_id,
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for {platform}/nyctaxi/{query_id}"
    assert decision.action is CompatAction.SELECT_VARIANT
    assert isinstance(decision.payload, SelectVariantPayload)
    assert expected_snippet in decision.payload.variant_sql


@pytest.mark.parametrize("query_id", ["trips-by-day-of-week", "rush-hour-analysis"])
def test_nyctaxi_duckdb_has_no_rule(query_id: str):
    ctx = CompatibilityContext(
        platform="duckdb",
        platform_version=None,
        benchmark="nyctaxi",
        query_id=query_id,
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="duckdb",
    )
    assert REGISTRY.resolve(ctx) is None, f"Unexpected rule for duckdb/nyctaxi/{query_id}"


def test_nyctaxi_clickhouse_has_trip_duration_variant():
    ctx = CompatibilityContext(
        platform="clickhouse",
        platform_version=None,
        benchmark="nyctaxi",
        query_id="trip-duration-analysis",
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="clickhouse",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert "dateDiff('second'" in decision.payload.variant_sql


@pytest.mark.parametrize(
    "platform,query_id",
    [
        ("starrocks", "trips-by-day-of-week"),
        ("starrocks", "weekday-weekend-comparison"),
        ("starrocks", "rush-hour-analysis"),
        ("starrocks", "trip-duration-analysis"),
        ("clickhouse", "trips-by-day-of-week"),
        ("clickhouse", "weekday-weekend-comparison"),
        ("clickhouse", "rush-hour-analysis"),
        ("clickhouse", "trip-duration-analysis"),
    ],
)
def test_nyctaxi_registry_returns_variant(platform: str, query_id: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="nyctaxi",
        query_id=query_id,
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for {platform}/nyctaxi/{query_id}"
    assert decision.action is CompatAction.SELECT_VARIANT
