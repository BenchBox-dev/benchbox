import importlib

import pytest

from benchbox.sql_compat import (
    REGISTRY,
    CompatAction,
    CompatibilityContext,
    FailureMode,
    Phase,
    RewriteQueryPayload,
    SupportLevel,
)
from benchbox.utils.dialect_utils import apply_rewrite

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(
    "platform, benchmark_name, query_id",
    [
        ("sqlite", "tpch", "6"),
        ("sqlite", "datavault", "6"),
        ("clickhouse", "datavault", "6"),
        ("clickhouse", "tpch", "6"),
        ("sqlite", "ssb", "1.1"),
        ("duckdb", "tpch", "6"),
    ],
)
def test_exact_literal_bounds_rule_scope_and_transformer(platform, benchmark_name, query_id):
    importlib.import_module("benchbox.sql_compat.rules.query_compile.exact_literal_bounds")
    context = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark=benchmark_name,
        query_id=query_id,
        phase=Phase.QUERY_COMPILE,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(context)
    if platform not in {"sqlite", "clickhouse"}:
        assert decision is None
        return
    assert decision.rule_id == f"query_compile.{platform}.all.exact_literal_bounds"
    assert decision.action == CompatAction.REWRITE_QUERY
    assert decision.support_level == SupportLevel.REWRITTEN
    assert decision.failure_mode == FailureMode.SILENT_CORRUPTION
    assert isinstance(decision.payload, RewriteQueryPayload)
    query = "SELECT * FROM sat_lineitem WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"
    rewritten = apply_rewrite(decision.payload.transformer_id, query, platform)
    assert rewritten == "SELECT * FROM sat_lineitem WHERE l_discount BETWEEN 0.05 AND 0.07"
