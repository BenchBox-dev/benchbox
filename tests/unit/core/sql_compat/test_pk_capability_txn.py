from __future__ import annotations

import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import PKCapabilityPayload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

import benchbox.sql_compat.rules.schema_emit.pk_capability_txn
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


def test_pk_capability_txn_rules_registered():
    pk_rules = [
        (key, entry)
        for key, entry in REGISTRY.all_rules()
        if key[0] is Phase.SCHEMA_EMIT and key[2] == "transaction_primitives"
    ]
    rule_ids = {entry.rule_id for _, entry in pk_rules}
    expected = {
        f"schema_emit.{d}.transaction_primitives.pk_lock_table_unsupported" for d in _EXPECTED_LOCK_TABLE_DIALECTS
    } | {f"schema_emit.{d}.transaction_primitives.pk_not_enforced" for d in _EXPECTED_INFORMATIONAL_DIALECTS}
    missing = expected - rule_ids
    assert not missing, f"Missing PK rules: {sorted(missing)}"


@pytest.mark.parametrize("platform", ["datafusion", "clickhouse", "starrocks", "doris", "ducklake"])
def test_pk_txn_rule_action_is_rewrite_ddl(platform: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="transaction_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for {platform}"
    assert decision.action is CompatAction.REWRITE_DDL
    assert isinstance(decision.payload, PKCapabilityPayload)


def test_starrocks_txn_rule_is_registry_backed():
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version=None,
        benchmark="transaction_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="starrocks",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.action is CompatAction.REWRITE_DDL
    assert decision.rule_id == "schema_emit.starrocks.transaction_primitives.pk_lock_table_unsupported"


@pytest.mark.parametrize("platform", ["datafusion", "clickhouse", "starrocks", "doris", "ducklake"])
def test_lock_platforms_have_rewrite_ddl_rule(platform: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="transaction_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for {platform}"
    assert decision.action != CompatAction.NATIVE, f"{platform} should have REWRITE_DDL"


def test_doris_release_bug_fixed_by_registry():
    ctx = CompatibilityContext(
        platform="doris",
        platform_version=None,
        benchmark="transaction_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="doris",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.action != CompatAction.NATIVE
    assert decision.rule_id == "schema_emit.doris.transaction_primitives.pk_lock_table_unsupported"


def test_starrocks_txn_pk_rule_resolves():
    ctx = CompatibilityContext(
        platform="starrocks",
        platform_version=None,
        benchmark="transaction_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="starrocks",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.rule_id == "schema_emit.starrocks.transaction_primitives.pk_lock_table_unsupported"


def test_duckdb_has_no_txn_pk_rule():
    ctx = CompatibilityContext(
        platform="duckdb",
        platform_version=None,
        benchmark="transaction_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="duckdb",
    )
    assert REGISTRY.resolve(ctx) is None
