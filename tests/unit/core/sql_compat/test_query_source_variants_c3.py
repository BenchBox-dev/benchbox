from __future__ import annotations

import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import SelectVariantPayload, SkipQueryPayload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

import benchbox.sql_compat.rules.query_source.vector_search_variants
from benchbox.sql_compat.registry import REGISTRY


def test_vector_search_rule_registration_count():
    rules = [
        (key, entry)
        for key, entry in REGISTRY.all_rules()
        if key[0] is Phase.QUERY_SOURCE and key[2] == "vector_search"
    ]
    assert len(rules) == 44, f"Expected 44 vector_search rules, got {len(rules)}: {[e.rule_id for _, e in rules]}"


def test_vector_search_all_platforms_registered():
    for platform in ("spark", "lakesail", "starrocks", "doris", "postgresql", "clickhouse", "snowflake"):
        rules = [
            (key, entry)
            for key, entry in REGISTRY.all_rules()
            if key[0] is Phase.QUERY_SOURCE and key[2] == "vector_search" and key[1] == platform
        ]
        expected = 8 if platform == "starrocks" else 6
        assert len(rules) == expected, f"{platform}: expected {expected} rules, got {len(rules)}"


@pytest.mark.parametrize(
    "platform,query_id,expected_snippet",
    [
        ("starrocks", "Q1", "cosine_similarity"),
        ("starrocks", "Q2", "l2_distance"),
        ("starrocks", "Q3", "cosine_similarity"),
        ("starrocks", "Q6", "cosine_similarity"),
        ("doris", "Q1", "cosine_distance"),
        ("doris", "Q2", "l2_distance"),
        ("doris", "Q6", "cosine_distance"),
        ("postgresql", "Q1", "<=>"),
        ("postgresql", "Q2", "<->"),
        ("clickhouse", "Q1", "cosineDistance"),
        ("clickhouse", "Q2", "L2Distance"),
        ("snowflake", "Q1", "VECTOR_COSINE_SIMILARITY"),
        ("snowflake", "Q2", "VECTOR_L2_DISTANCE"),
        ("spark", "Q1", "zip_with"),
        ("spark", "Q2", "aggregate"),
    ],
)
def test_vector_search_rule_action_and_payload(platform: str, query_id: str, expected_snippet: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="vector_search",
        query_id=query_id,
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for {platform}/vector_search/{query_id}"
    assert decision.action is CompatAction.SELECT_VARIANT
    assert isinstance(decision.payload, SelectVariantPayload)
    assert expected_snippet in decision.payload.variant_sql


def test_vector_search_lakesail_rules_skip_queries():
    ctx = CompatibilityContext(
        platform="lakesail",
        platform_version=None,
        benchmark="vector_search",
        query_id="Q1",
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="lakesail",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.action is CompatAction.SKIP_QUERY
    assert isinstance(decision.payload, SkipQueryPayload)
    assert "lambda fallback" in decision.payload.reason


def test_starrocks_q2_no_version_returns_select_variant():
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version=None,
        benchmark="vector_search",
        query_id="Q2",
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="starrocks",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.action is CompatAction.SELECT_VARIANT
    assert decision.rule_id == "query_source.starrocks.vector_search.q2_variant"


def test_starrocks_q2_version_lt_32_returns_skip():
    for version in ("3.0", "3.1"):
        ctx = CompatibilityContext(
            platform="starrocks",
            platform_version=version,
            benchmark="vector_search",
            query_id="Q2",
            phase=Phase.QUERY_SOURCE,
            mode="sql",
            dialect="starrocks",
        )
        decision = REGISTRY.resolve(ctx)
        assert decision is not None, f"No rule for starrocks/{version}/Q2"
        assert decision.action is CompatAction.SKIP_QUERY, f"Expected SKIP_QUERY for version {version}"
        assert isinstance(decision.payload, SkipQueryPayload)
        assert decision.rule_id == "query_source.starrocks.vector_search.q2_lt_32_skip"


def test_starrocks_q2_version_ge_32_returns_versioned_variant():
    for version in ("3.2", "3.3", "4.0"):
        ctx = CompatibilityContext(
            platform="starrocks",
            platform_version=version,
            benchmark="vector_search",
            query_id="Q2",
            phase=Phase.QUERY_SOURCE,
            mode="sql",
            dialect="starrocks",
        )
        decision = REGISTRY.resolve(ctx)
        assert decision is not None, f"No rule for starrocks/{version}/Q2"
        assert decision.action is CompatAction.SELECT_VARIANT, f"Expected SELECT_VARIANT for version {version}"
        assert decision.rule_id == "query_source.starrocks.vector_search.q2_ge_32_variant"
        assert isinstance(decision.payload, SelectVariantPayload)
        assert "l2_distance" in decision.payload.variant_sql


def test_starrocks_q2_non_pep440_engine_version_uses_versioned_variant():
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version="3.3.0-starrocks",
        benchmark="vector_search",
        query_id="Q2",
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="starrocks",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.action is CompatAction.SELECT_VARIANT
    assert decision.rule_id == "query_source.starrocks.vector_search.q2_ge_32_variant"


def test_starrocks_q2_invalid_engine_version_falls_back_to_unversioned_variant():
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version="Athena (version unknown)",
        benchmark="vector_search",
        query_id="Q2",
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="starrocks",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.action is CompatAction.SELECT_VARIANT
    assert decision.rule_id == "query_source.starrocks.vector_search.q2_variant"


@pytest.mark.parametrize("query_id", ["Q1", "Q2", "Q3"])
def test_vector_search_duckdb_has_no_rule(query_id: str):
    ctx = CompatibilityContext(
        platform="duckdb",
        platform_version=None,
        benchmark="vector_search",
        query_id=query_id,
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect="duckdb",
    )
    assert REGISTRY.resolve(ctx) is None, f"Unexpected rule for duckdb/vector_search/{query_id}"


@pytest.mark.parametrize(
    "platform,query_id",
    [
        ("starrocks", "Q1"),
        ("starrocks", "Q2"),
        ("doris", "Q1"),
        ("postgresql", "Q1"),
        ("clickhouse", "Q1"),
        ("snowflake", "Q1"),
        ("snowflake", "Q6"),
    ],
)
def test_vector_search_registry_returns_variant(platform: str, query_id: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="vector_search",
        query_id=query_id,
        phase=Phase.QUERY_SOURCE,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for {platform}/vector_search/{query_id}"
    assert decision.action is CompatAction.SELECT_VARIANT
