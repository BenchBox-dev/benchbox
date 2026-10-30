from __future__ import annotations

import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import SelectVariantPayload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

import benchbox.sql_compat.rules.query_source.coffeeshop_variants
import benchbox.sql_compat.rules.query_source.h2odb_variants
from benchbox.sql_compat.registry import REGISTRY


def test_h2odb_variant_rules_registered():
    rules = [(key, entry) for key, entry in REGISTRY.all_rules() if key[0] is Phase.QUERY_SOURCE and key[2] == "h2odb"]
    assert len(rules) == 6, f"Expected 6 h2odb rules, got {len(rules)}: {[e.rule_id for _, e in rules]}"
    rule_ids = [entry.rule_id for _, entry in rules]
    assert "query_source.sqlite.h2odb.q9_percentile_cont_variant" in rule_ids
    assert "query_source.clickhouse.h2odb.q9_quantile_variant" in rule_ids
    assert "query_source.starrocks.h2odb.q9_percentile_approx_variant" in rule_ids
    assert "query_source.bigquery.h2odb.q9_percentile_window_variant" in rule_ids
    assert "query_source.doris.h2odb.q9_percentile_approx_variant" in rule_ids
    assert "query_source.questdb.h2odb.q9_approx_percentile_variant" in rule_ids


@pytest.mark.parametrize(
    "platform,expected_snippet",
    [
        ("clickhouse", "quantile"),
        ("starrocks", "PERCENTILE_APPROX"),
        ("doris", "PERCENTILE_APPROX"),
        ("questdb", "approx_percentile"),
    ],
)
def test_h2odb_rule_action_and_payload(platform: str, expected_snippet: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="h2odb",
        query_id="Q9",
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for {platform}/h2odb/Q9"
    assert decision.action is CompatAction.SELECT_VARIANT
    assert isinstance(decision.payload, SelectVariantPayload)
    assert expected_snippet in decision.payload.variant_sql


def test_h2odb_duckdb_has_no_rule():
    ctx = CompatibilityContext(
        platform="duckdb",
        platform_version=None,
        benchmark="h2odb",
        query_id="Q9",
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="duckdb",
    )
    assert REGISTRY.resolve(ctx) is None


def test_coffeeshop_variant_rules_registered():
    rules = [
        (key, entry) for key, entry in REGISTRY.all_rules() if key[0] is Phase.QUERY_SOURCE and key[2] == "coffeeshop"
    ]
    assert len(rules) == 3, f"Expected 3 coffeeshop rules, got {len(rules)}: {[e.rule_id for _, e in rules]}"
    rule_ids = [entry.rule_id for _, entry in rules]
    assert "query_source.clickhouse.coffeeshop.sa4_cross_join_variant" in rule_ids
    assert "query_source.clickhouse.coffeeshop.tm1_todatetime_variant" in rule_ids
    assert "query_source.questdb.coffeeshop.tm1_varchar_time_variant" in rule_ids


@pytest.mark.parametrize(
    "query_id,expected_snippet",
    [
        ("SA4", "CROSS JOIN"),
        ("TM1", "toHour(toDateTime"),
    ],
)
def test_coffeeshop_rule_action_and_payload(query_id: str, expected_snippet: str):
    ctx = CompatibilityContext(
        platform="clickhouse",
        platform_version=None,
        benchmark="coffeeshop",
        query_id=query_id,
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="clickhouse",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for clickhouse/coffeeshop/{query_id}"
    assert decision.action is CompatAction.SELECT_VARIANT
    assert isinstance(decision.payload, SelectVariantPayload)
    assert expected_snippet in decision.payload.variant_sql


@pytest.mark.parametrize("query_id", ["SA4", "TM1"])
def test_coffeeshop_starrocks_has_no_rule(query_id: str):
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version=None,
        benchmark="coffeeshop",
        query_id=query_id,
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="starrocks",
    )
    assert REGISTRY.resolve(ctx) is None, f"Unexpected rule for starrocks/coffeeshop/{query_id}"
