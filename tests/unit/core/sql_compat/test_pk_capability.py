from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import PKCapabilityPayload, SupportLevel
from benchbox.sql_compat.registry import CompatibilityRegistry

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

import benchbox.sql_compat.rules.schema_emit.pk_capability
from benchbox.sql_compat.registry import REGISTRY

_EXPECTED_LOCK_TABLE_DIALECTS = ("datafusion", "clickhouse", "starrocks", "doris", "ducklake")
_EXPECTED_INFORMATIONAL_DIALECTS = (
    "snowflake",
    "redshift",
    "bigquery",
    "databricks",
    "tsql",
    "spark",
    "trino",
    "presto",
)


def test_pk_capability_rules_registered():
    pk_rules = [
        (key, entry)
        for key, entry in REGISTRY.all_rules()
        if key[0] is Phase.SCHEMA_EMIT and key[2] == "write_primitives"
    ]
    rule_ids = {entry.rule_id for _, entry in pk_rules}
    expected = {
        f"schema_emit.{d}.write_primitives.pk_lock_table_unsupported" for d in _EXPECTED_LOCK_TABLE_DIALECTS
    } | {f"schema_emit.{d}.write_primitives.pk_not_enforced" for d in _EXPECTED_INFORMATIONAL_DIALECTS}
    missing = expected - rule_ids
    assert not missing, f"Missing PK rules: {sorted(missing)}"


@pytest.mark.parametrize("platform", ["datafusion", "clickhouse", "starrocks", "doris", "ducklake"])
def test_pk_rule_action_is_rewrite_ddl(platform: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="write_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for {platform}"
    assert decision.action is CompatAction.REWRITE_DDL
    assert isinstance(decision.payload, PKCapabilityPayload)


def test_datafusion_pk_rule_payload():
    ctx = CompatibilityContext(
        platform="datafusion",
        platform_version=None,
        benchmark="write_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="datafusion",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    payload = decision.payload
    assert isinstance(payload, PKCapabilityPayload)
    assert payload.ddl_accepted is False
    assert payload.uniqueness_enforced is False
    assert payload.conditions is None


def test_ducklake_pk_rule_payload():
    ctx = CompatibilityContext(
        platform="ducklake",
        platform_version=None,
        benchmark="write_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="ducklake",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    payload = decision.payload
    assert isinstance(payload, PKCapabilityPayload)
    assert payload.ddl_accepted is False
    assert payload.uniqueness_enforced is False
    assert payload.conditions is None


def test_starrocks_pk_rule_payload():
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version=None,
        benchmark="write_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="starrocks",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    payload = decision.payload
    assert isinstance(payload, PKCapabilityPayload)
    assert payload.ddl_accepted is True
    assert payload.uniqueness_enforced is False
    assert payload.conditions == "first N columns only"


@pytest.mark.parametrize("platform", ["datafusion", "clickhouse", "starrocks", "doris", "ducklake"])
def test_all_lock_platforms_have_rewrite_ddl_rule(platform: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="write_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for {platform} acquire/release"
    assert decision.action != CompatAction.NATIVE, f"{platform} should have REWRITE_DDL, not NATIVE"


def test_doris_release_bug_fixed_by_registry():
    ctx = CompatibilityContext(
        platform="doris",
        platform_version=None,
        benchmark="write_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="doris",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.action != CompatAction.NATIVE
    assert decision.rule_id == "schema_emit.doris.write_primitives.pk_lock_table_unsupported"
