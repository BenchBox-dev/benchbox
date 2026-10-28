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
import benchbox.sql_compat.rules.query_source.tpcdi_variants
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


def test_tpcdi_variant_rules_registered():
    rules = [(key, entry) for key, entry in REGISTRY.all_rules() if key[0] is Phase.QUERY_SOURCE and key[2] == "tpcdi"]
    assert len(rules) == 12, f"Expected 12 tpcdi rules, got {len(rules)}: {[e.rule_id for _, e in rules]}"
    rule_ids = {entry.rule_id for _, entry in rules}
    for rule_id in (
        "query_source.clickhouse.tpcdi.aq7_variant",
        "query_source.clickhouse.tpcdi.aq8_variant",
        "query_source.clickhouse.tpcdi.aq10_variant",
        "query_source.clickhouse.tpcdi.eq7_variant",
    ):
        assert rule_id in rule_ids
    assert "query_source.datafusion.tpcdi.aq9_projection_variant" in rule_ids
    assert "query_source.datafusion.tpcdi.eq7_derived_table_variant" in rule_ids
    assert "query_source.datafusion.tpcdi.vq6_projection_variant" in rule_ids
    assert "query_source.starrocks.tpcdi.eq7_derived_table_variant" in rule_ids
    assert "query_source.doris.tpcdi.eq7_derived_table_variant" in rule_ids
    assert "query_source.bigquery.tpcdi.eq7_derived_table_variant" in rule_ids
    assert "query_source.databricks.tpcdi.eq7_derived_table_variant" in rule_ids
    assert "query_source.snowflake.tpcdi.eq7_derived_table_variant" in rule_ids


@pytest.mark.parametrize(
    "platform,query_id,expected_snippet",
    [
        ("clickhouse", "AQ7", "dateDiff"),
        ("clickhouse", "AQ8", "dateDiff"),
        ("clickhouse", "AQ10", "dateDiff"),
        ("clickhouse", "EQ7", "quality_metrics"),
        ("datafusion", "AQ9", "avg_sell_price"),
        ("datafusion", "EQ7", "quality_metrics"),
        ("datafusion", "VQ6", "grouped_positions"),
        ("starrocks", "EQ7", "quality_metrics"),
        ("doris", "EQ7", "quality_metrics"),
        ("databricks", "EQ7", "quality_metrics"),
        ("snowflake", "EQ7", "quality_metrics"),
    ],
)
def test_tpcdi_rule_action_and_payload(platform: str, query_id: str, expected_snippet: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="tpcdi",
        query_id=query_id,
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for {platform}/tpcdi/{query_id}"
    assert decision.action is CompatAction.SELECT_VARIANT
    assert isinstance(decision.payload, SelectVariantPayload)
    assert expected_snippet in decision.payload.variant_sql


@pytest.mark.parametrize("query_id", ["AQ6", "AQ9", "EQ7", "VQ6"])
def test_tpcdi_duckdb_has_no_rule(query_id: str):
    ctx = CompatibilityContext(
        platform="duckdb",
        platform_version=None,
        benchmark="tpcdi",
        query_id=query_id,
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="duckdb",
    )
    assert REGISTRY.resolve(ctx) is None, f"Unexpected rule for duckdb/tpcdi/{query_id}"


def test_tpcdi_eq7_sql_has_no_format_placeholders():
    from benchbox.sql_compat.rules.query_source.tpcdi_variants import (
        DATAFUSION_AQ9_SQL,
        DATAFUSION_EQ7_SQL,
        DATAFUSION_VQ6_SQL,
        DORIS_EQ7_SQL,
        STARROCKS_EQ7_SQL,
    )

    assert "{" not in DATAFUSION_AQ9_SQL, "DATAFUSION_AQ9_SQL should not contain format placeholders"
    assert "{" not in DATAFUSION_EQ7_SQL, "DATAFUSION_EQ7_SQL should not contain format placeholders"
    assert "{" not in DATAFUSION_VQ6_SQL, "DATAFUSION_VQ6_SQL should not contain format placeholders"
    assert "{" not in STARROCKS_EQ7_SQL, "STARROCKS_EQ7_SQL should not contain format placeholders"
    assert "{" not in DORIS_EQ7_SQL, "DORIS_EQ7_SQL should not contain format placeholders"
    assert DORIS_EQ7_SQL == STARROCKS_EQ7_SQL
    assert "IsCurrent IS TRUE" in DATAFUSION_EQ7_SQL


def test_tpcdi_clickhouse_aq6_has_no_variant_rule():
    ctx = CompatibilityContext(
        platform="clickhouse",
        platform_version=None,
        benchmark="tpcdi",
        query_id="AQ6",
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="clickhouse",
    )
    assert REGISTRY.resolve(ctx) is None


def test_tpcdi_datafusion_eq7_executes_with_fractional_scores():
    datafusion = pytest.importorskip("datafusion")
    pa = pytest.importorskip("pyarrow")
    from benchbox.sql_compat.rules.query_source.tpcdi_variants import DATAFUSION_EQ7_SQL

    rows = 20
    ids = list(range(1, rows + 1))
    ctx = datafusion.SessionContext()

    def register(name: str, columns: dict) -> None:
        lowered = {key.lower(): values for key, values in columns.items()}
        ctx.register_record_batches(name, [[pa.record_batch(lowered)]])

    register(
        "DimCustomer",
        {
            "CustomerID": ids,
            "TaxID": [None] + ["t"] * (rows - 1),
            "Status": ["Active"] * rows,
            "Gender": ["M"] * rows,
            "Tier": [2] * rows,
            "CreditRating": [700] * rows,
            "NetWorth": [500000] * rows,
            "IsCurrent": [True] * rows,
        },
    )
    register(
        "DimAccount",
        {
            "AccountID": ids,
            "SK_CustomerID": ids,
            "Status": ["Active"] * rows,
            "TaxStatus": [1] * rows,
            "IsCurrent": [True] * rows,
        },
    )
    register(
        "FactTrade",
        {
            "TradeID": ids,
            "SK_CustomerID": ids,
            "SK_SecurityID": ids,
            "Status": ["Completed"] * rows,
            "Type": ["Buy"] * rows,
            "TradePrice": [10.0] * rows,
            "Quantity": [5] * rows,
            "Fee": [1.0] * rows,
            "Commission": [1.0] * rows,
        },
    )

    result = ctx.sql(DATAFUSION_EQ7_SQL).to_pydict()
    ratings = dict(zip(result["table_name"], result["quality_rating"], strict=True))
    scores = dict(zip(result["table_name"], result["completeness_score"], strict=True))
    assert ratings == {"DimCustomer": "EXCELLENT", "DimAccount": "EXCELLENT", "FactTrade": "EXCELLENT"}
    assert scores["DimCustomer"] == pytest.approx(95.0)


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
