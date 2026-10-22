from __future__ import annotations

import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import RewriteDDLPayload, RewriteQueryPayload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

import benchbox.sql_compat.rules.ddl_optimize.starrocks_ddl_rewrites
import benchbox.sql_compat.rules.query_adapter.starrocks_query_rewrites
from benchbox.sql_compat.registry import REGISTRY


def test_starrocks_query_adapter_rule_registered():
    rules = [
        (key, entry) for key, entry in REGISTRY.all_rules() if key[0] is Phase.QUERY_ADAPTER and key[1] == "starrocks"
    ]
    assert len(rules) == 1, (
        f"Expected 1 starrocks query_adapter rule, got {len(rules)}: {[e.rule_id for _, e in rules]}"
    )
    rule_id = rules[0][1].rule_id
    assert rule_id == "query_adapter.starrocks.all.inject_subquery_aliases"


def test_starrocks_ddl_optimize_rule_registered():
    rules = [
        (key, entry) for key, entry in REGISTRY.all_rules() if key[0] is Phase.DDL_OPTIMIZE and key[1] == "starrocks"
    ]
    assert len(rules) == 1, f"Expected 1 starrocks ddl_optimize rule, got {len(rules)}: {[e.rule_id for _, e in rules]}"
    rule_id = rules[0][1].rule_id
    assert rule_id == "ddl_optimize.starrocks.all.optimize_table_definition"


@pytest.mark.parametrize(
    "bmark,query_id",
    [
        ("tpcds", "1"),
        ("tpcds", "23a"),
        ("tpch", "11"),
        ("h2odb", "Q1"),
    ],
)
def test_starrocks_rewrite_query_action_and_payload(bmark: str, query_id: str):
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version=None,
        benchmark=bmark,
        query_id=query_id,
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect="starrocks",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for starrocks/{bmark}/{query_id}"
    assert decision.action is CompatAction.REWRITE_QUERY
    assert isinstance(decision.payload, RewriteQueryPayload)
    assert decision.payload.transformer_id == "starrocks_subquery_alias_injector"


@pytest.mark.parametrize(
    "bmark,query_id",
    [
        ("tpcds", None),
        ("tpch", None),
        ("h2odb", None),
    ],
)
def test_starrocks_rewrite_ddl_action_and_payload(bmark: str, query_id: str | None):
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version=None,
        benchmark=bmark,
        query_id=query_id,
        phase=Phase.DDL_OPTIMIZE,
        mode="sql",
        dialect="starrocks",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for starrocks DDL_OPTIMIZE/{bmark}"
    assert decision.action is CompatAction.REWRITE_DDL
    assert isinstance(decision.payload, RewriteDDLPayload)
    assert decision.payload.transformer_id == "translate_ddl:key_model"


@pytest.mark.parametrize(
    "bmark,query_id",
    [
        ("tpcds", "23a"),
        ("tpch", "11"),
        ("h2odb", "Q1"),
    ],
)
def test_starrocks_rewrite_query_registry_returns_rewrite(bmark: str, query_id: str):
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version=None,
        benchmark=bmark,
        query_id=query_id,
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect="starrocks",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None and decision.action is CompatAction.REWRITE_QUERY


@pytest.mark.parametrize("bmark", ["tpcds", "tpch", "h2odb"])
def test_starrocks_rewrite_ddl_registry_returns_rewrite(bmark: str):
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version=None,
        benchmark=bmark,
        query_id=None,
        phase=Phase.DDL_OPTIMIZE,
        mode="sql",
        dialect="starrocks",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None and decision.action is CompatAction.REWRITE_DDL


@pytest.mark.parametrize("platform", ["duckdb", "clickhouse", "datafusion"])
def test_non_starrocks_has_no_query_adapter_rule_from_this_module(platform: str):
    rules = [
        (key, entry)
        for key, entry in REGISTRY.all_rules()
        if key[0] is Phase.QUERY_ADAPTER and key[1] == platform and "starrocks" in entry.rule_id
    ]
    assert len(rules) == 0, f"{platform} should have no starrocks-origin QUERY_ADAPTER rules"


@pytest.mark.parametrize("platform", ["duckdb", "datafusion"])
def test_non_starrocks_has_no_ddl_optimize_rule(platform: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="tpch",
        query_id=None,
        phase=Phase.DDL_OPTIMIZE,
        mode="sql",
        dialect=platform,
    )
    assert REGISTRY.resolve(ctx) is None, f"Unexpected DDL_OPTIMIZE rule for {platform}"


@pytest.mark.parametrize(
    "platform,bmark,query_id",
    [
        ("starrocks", "tpcds", "1"),
        ("starrocks", "tpch", "11"),
        ("duckdb", "tpch", "1"),
        ("clickhouse", "tpcds", "23a"),
    ],
)
def test_execution_filter_registry_silent(platform: str, bmark: str, query_id: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark=bmark,
        query_id=query_id,
        phase=Phase.EXECUTION_FILTER,
        mode="sql",
        dialect=platform,
    )
    assert REGISTRY.resolve(ctx) is None


@pytest.mark.parametrize(
    "platform,bmark,query_id",
    [
        ("starrocks", "tpch", "Q1"),
        ("duckdb", "tpch", "Q1"),
    ],
)
def test_dataframe_filter_registry_silent(platform: str, bmark: str, query_id: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark=bmark,
        query_id=query_id,
        phase=Phase.DATAFRAME_FILTER,
        mode="dataframe",
        dialect=platform,
    )
    assert REGISTRY.resolve(ctx) is None
