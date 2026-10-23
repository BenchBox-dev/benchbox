from __future__ import annotations

import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import RewriteQueryPayload, SetSessionPolicyPayload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

import benchbox.sql_compat.rules.query_adapter.clickhouse_session_policy
import benchbox.sql_compat.rules.query_adapter.datafusion_query_rewrites
from benchbox.sql_compat.registry import REGISTRY


def test_clickhouse_tpcds_session_policy_rules_registered():
    rules = [
        (key, entry)
        for key, entry in REGISTRY.all_rules()
        if key[0] is Phase.QUERY_ADAPTER and key[1] == "clickhouse" and key[2] == "tpcds"
    ]
    assert len(rules) == 3, f"Expected 3 clickhouse/tpcds rules, got {len(rules)}: {[e.rule_id for _, e in rules]}"
    rule_ids = {entry.rule_id for _, entry in rules}
    assert "query_adapter.clickhouse.tpcds.q23a_joined_subquery_alias_policy" in rule_ids
    assert "query_adapter.clickhouse.tpcds.q23b_joined_subquery_alias_policy" in rule_ids
    assert "query_adapter.clickhouse.tpcds.q87_joined_subquery_alias_policy" in rule_ids


@pytest.mark.parametrize("query_id", ["23a", "23b", "87"])
def test_clickhouse_session_policy_action_and_payload(query_id: str):
    ctx = CompatibilityContext(
        platform="clickhouse",
        platform_version=None,
        benchmark="tpcds",
        query_id=query_id,
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect="clickhouse",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for clickhouse/tpcds/{query_id}"
    assert decision.action is CompatAction.SET_SESSION_POLICY
    assert isinstance(decision.payload, SetSessionPolicyPayload)
    assert ("joined_subquery_requires_alias", "0") in decision.payload.settings


@pytest.mark.parametrize("query_id", ["1", "22", "42"])
def test_clickhouse_tpcds_other_queries_have_no_adapter_rule(query_id: str):
    ctx = CompatibilityContext(
        platform="clickhouse",
        platform_version=None,
        benchmark="tpcds",
        query_id=query_id,
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect="clickhouse",
    )
    assert REGISTRY.resolve(ctx) is None, f"Unexpected rule for clickhouse/tpcds/{query_id}"


def test_datafusion_tpch_rewrite_rules_registered():
    rules = [
        (key, entry)
        for key, entry in REGISTRY.all_rules()
        if key[0] is Phase.QUERY_ADAPTER and key[1] == "datafusion" and key[2] == "tpch"
    ]
    assert len(rules) == 4, f"Expected 4 datafusion/tpch rules, got {len(rules)}: {[e.rule_id for _, e in rules]}"
    rule_ids = {entry.rule_id for _, entry in rules}
    assert "query_adapter.datafusion.tpch.q11_having_threshold_cte" in rule_ids
    assert "query_adapter.datafusion.tpch.q16_not_in_to_not_exists" in rule_ids
    assert "query_adapter.datafusion.tpch.q18_in_having_to_exists" in rule_ids
    assert "query_adapter.datafusion.tpch.q20_correlated_to_cte" in rule_ids


@pytest.mark.parametrize(
    "query_id,expected_transformer",
    [
        ("11", "decorrelate_subqueries"),
        ("16", "not_in_subquery_to_not_exists"),
        ("18", "in_aggregate_subquery_to_exists"),
        ("20", "decorrelate_subqueries"),
    ],
)
def test_datafusion_rewrite_action_and_payload(query_id: str, expected_transformer: str):
    ctx = CompatibilityContext(
        platform="datafusion",
        platform_version=None,
        benchmark="tpch",
        query_id=query_id,
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect="datafusion",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for datafusion/tpch/{query_id}"
    assert decision.action is CompatAction.REWRITE_QUERY
    assert isinstance(decision.payload, RewriteQueryPayload)
    assert decision.payload.transformer_id == expected_transformer


@pytest.mark.parametrize("query_id", ["1", "6", "22"])
def test_datafusion_tpch_unaffected_queries_have_no_rule(query_id: str):
    ctx = CompatibilityContext(
        platform="datafusion",
        platform_version=None,
        benchmark="tpch",
        query_id=query_id,
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect="datafusion",
    )
    assert REGISTRY.resolve(ctx) is None, f"Unexpected rule for datafusion/tpch/{query_id}"


@pytest.mark.parametrize("query_id", ["23a", "23b", "87"])
def test_clickhouse_session_policy_registry_returns_decision(query_id: str):
    ctx = CompatibilityContext(
        platform="clickhouse",
        platform_version=None,
        benchmark="tpcds",
        query_id=query_id,
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect="clickhouse",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None and decision.action is CompatAction.SET_SESSION_POLICY


@pytest.mark.parametrize("query_id", ["11", "16", "18", "20"])
def test_datafusion_rewrite_registry_returns_decision(query_id: str):
    ctx = CompatibilityContext(
        platform="datafusion",
        platform_version=None,
        benchmark="tpch",
        query_id=query_id,
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect="datafusion",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None and decision.action is CompatAction.REWRITE_QUERY


def test_duckdb_has_no_query_adapter_rules():
    for benchmark, query_id in [("tpcds", "23a"), ("tpch", "11")]:
        ctx = CompatibilityContext(
            platform="duckdb",
            platform_version=None,
            benchmark=benchmark,
            query_id=query_id,
            phase=Phase.QUERY_ADAPTER,
            mode="sql",
            dialect="duckdb",
        )
        assert REGISTRY.resolve(ctx) is None
