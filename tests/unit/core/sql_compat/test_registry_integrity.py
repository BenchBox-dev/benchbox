from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    RewriteDDLPayload,
    SelectVariantPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import (
    REGISTRY,
    CompatibilityRegistry,
    CompatibilityRegistryConflict,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _variant_decision(rule_id: str, *, sql: str) -> CompatibilityDecision:
    return CompatibilityDecision(
        rule_id=rule_id,
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(variant_key="test", variant_sql=sql),
        reason="test rule",
    )


def test_registry_allows_identical_duplicate_registration():
    registry = CompatibilityRegistry()
    decision = _variant_decision("query_source.testbench.test.q1_variant", sql="SELECT 1")

    registry.register(decision, Phase.QUERY_SOURCE, "duckdb", benchmark="testbench", query_id="Q1")
    registry.register(decision, Phase.QUERY_SOURCE, "duckdb", benchmark="testbench", query_id="Q1")

    rules = list(registry.all_rules())
    assert len(rules) == 1
    assert rules[0][1].decision == decision


def test_registry_rejects_duplicate_rule_id_with_different_semantics():
    registry = CompatibilityRegistry()
    first = _variant_decision("query_source.testbench.test.q1_variant", sql="SELECT 1")
    second = _variant_decision("query_source.testbench.test.q1_variant", sql="SELECT 2")

    registry.register(first, Phase.QUERY_SOURCE, "duckdb", benchmark="testbench", query_id="Q1")

    with pytest.raises(CompatibilityRegistryConflict, match="Duplicate rule_id"):
        registry.register(second, Phase.QUERY_SOURCE, "duckdb", benchmark="testbench", query_id="Q1")


def test_registry_idempotent_on_module_reimport():
    module_name = "benchbox.sql_compat.rules.ddl_optimize.velox_ddl_rewrites"
    rule_id = "ddl_optimize.velox.all.optimize_table_definition"

    importlib.import_module(module_name)
    count_before = sum(1 for _, entry in REGISTRY.all_rules() if entry.decision.rule_id == rule_id)
    assert count_before >= 1

    sys.modules.pop(module_name, None)
    importlib.import_module(module_name)

    count_after = sum(1 for _, entry in REGISTRY.all_rules() if entry.decision.rule_id == rule_id)
    assert count_after == count_before


@pytest.mark.parametrize(
    "platform,rule_id_prefix",
    [
        ("clickhouse", "ddl_optimize.clickhouse.all."),
        ("databricks", "ddl_optimize.databricks.all."),
        ("databend", "ddl_optimize.databend.all."),
        ("firebolt", "ddl_optimize.firebolt.all."),
        ("lakesail", "ddl_optimize.lakesail.all."),
        ("presto", "ddl_optimize.presto.all."),
        ("redshift", "ddl_optimize.redshift.all."),
        ("snowflake", "ddl_optimize.snowflake.all."),
        ("spark", "ddl_optimize.spark.all."),
        ("starrocks", "ddl_optimize.starrocks.all."),
        ("trino", "ddl_optimize.trino.all."),
        ("velox", "ddl_optimize.velox.all."),
        ("synapse", "ddl_optimize.azure_synapse.all."),
    ],
)
def test_ddl_optimize_rule_payload_is_populated(platform: str, rule_id_prefix: str):
    import pkgutil

    import benchbox.sql_compat.rules.ddl_optimize as _ddl_pkg

    for _, mod_name, _ispkg in pkgutil.walk_packages(_ddl_pkg.__path__, _ddl_pkg.__name__ + "."):
        if not _ispkg:
            importlib.import_module(mod_name)

    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="tpch",
        query_id=None,
        phase=Phase.DDL_OPTIMIZE,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No DDL_OPTIMIZE rule registered for platform={platform!r}"
    assert decision.rule_id.startswith(rule_id_prefix), (
        f"Expected rule_id starting with {rule_id_prefix!r}, got {decision.rule_id!r}"
    )
    assert isinstance(decision.payload, RewriteDDLPayload)
    assert decision.payload.transformer_id, "transformer_id must be non-empty"
    assert decision.payload.description, "description must be non-empty"


def test_synapse_ddl_optimize_rule_uses_canonical_platform_key():
    import benchbox.sql_compat.rules.ddl_optimize.synapse_ddl_rewrites

    ctx = CompatibilityContext(
        platform="synapse",
        platform_version=None,
        benchmark="tpch",
        query_id=None,
        phase=Phase.DDL_OPTIMIZE,
        mode="sql",
        dialect="tsql",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.rule_id == "ddl_optimize.azure_synapse.all.optimize_table_definition"


def test_ddl_optimize_baseline_matches_rule_files():
    from benchbox.sql_compat.baseline_tool import _ddl_optimize_records

    records = _ddl_optimize_records()
    rule_dir = Path(__file__).parents[4] / "benchbox" / "sql_compat" / "rules" / "ddl_optimize"
    platform_files = [f for f in rule_dir.glob("*.py") if f.name != "__init__.py"]

    assert len(records) == len(platform_files), (
        f"_ddl_optimize_records() has {len(records)} entries but "
        f"{len(platform_files)} rule files exist in {rule_dir}. "
        "Update baseline_tool._ddl_optimize_records() when adding or removing ddl_optimize rules."
    )


