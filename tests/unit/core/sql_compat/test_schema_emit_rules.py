from __future__ import annotations

import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import RewriteDDLPayload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

import benchbox.sql_compat.rules.schema_emit.nyctaxi_ddl
import benchbox.sql_compat.rules.schema_emit.tsbs_devops_ddl
from benchbox.sql_compat.registry import REGISTRY


def test_tsbs_devops_schema_emit_rules_registered():
    rules = [
        (key, entry) for key, entry in REGISTRY.all_rules() if key[0] is Phase.SCHEMA_EMIT and key[2] == "tsbs_devops"
    ]
    rule_ids = [entry.rule_id for _, entry in rules]
    assert len(rules) == 3, f"Expected 3 tsbs_devops SCHEMA_EMIT rules, got {len(rules)}: {rule_ids}"
    expected = {
        "schema_emit.clickhouse.tsbs_devops.all.mergetree_ddl",
        "schema_emit.timescale.tsbs_devops.all.hypertable_ddl",
        "schema_emit.timescale.tsbs_devops.tags.native_ddl",
    }
    assert set(rule_ids) == expected, f"Unexpected rule IDs: {rule_ids}"


def test_nyctaxi_schema_emit_rules_registered():
    rules = [(key, entry) for key, entry in REGISTRY.all_rules() if key[0] is Phase.SCHEMA_EMIT and key[2] == "nyctaxi"]
    rule_ids = [entry.rule_id for _, entry in rules]
    assert len(rules) == 4, f"Expected 4 nyctaxi SCHEMA_EMIT rules, got {len(rules)}: {rule_ids}"
    expected = {
        "schema_emit.clickhouse.nyctaxi.all.mergetree_ddl",
        "schema_emit.postgres.nyctaxi.all.range_partition_ddl",
        "schema_emit.postgresql.nyctaxi.all.range_partition_ddl",
        "schema_emit.timescale.nyctaxi.all.range_partition_ddl",
    }
    assert set(rule_ids) == expected, f"Unexpected rule IDs: {rule_ids}"


@pytest.mark.parametrize("table_name", ["cpu", "mem", "disk", "net", "tags"])
def test_tsbs_devops_clickhouse_rewrite_ddl(table_name: str):
    ctx = CompatibilityContext(
        platform="clickhouse",
        platform_version=None,
        benchmark="tsbs_devops",
        query_id=table_name,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="clickhouse",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No SCHEMA_EMIT rule for clickhouse/tsbs_devops/{table_name}"
    assert decision.action is CompatAction.REWRITE_DDL
    assert isinstance(decision.payload, RewriteDDLPayload)
    assert decision.payload.transformer_id == "tsbs_devops_clickhouse_ddl"


@pytest.mark.parametrize("table_name", ["cpu", "mem", "disk", "net"])
def test_tsbs_devops_timescale_non_tags_rewrite_ddl(table_name: str):
    ctx = CompatibilityContext(
        platform="timescale",
        platform_version=None,
        benchmark="tsbs_devops",
        query_id=table_name,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="timescale",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No SCHEMA_EMIT rule for timescale/tsbs_devops/{table_name}"
    assert decision.action is CompatAction.REWRITE_DDL
    assert isinstance(decision.payload, RewriteDDLPayload)
    assert decision.payload.transformer_id == "tsbs_devops_timescale_hypertable"


def test_tsbs_devops_timescale_tags_native_override():
    ctx = CompatibilityContext(
        platform="timescale",
        platform_version=None,
        benchmark="tsbs_devops",
        query_id="tags",
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="timescale",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, "No SCHEMA_EMIT rule for timescale/tsbs_devops/tags"
    assert decision.action is CompatAction.NATIVE, (
        f"Expected NATIVE for timescale/tags, got {decision.action}. "
        "Tier-1 tags override must win over tier-2 hypertable rule."
    )
    assert decision.rule_id == "schema_emit.timescale.tsbs_devops.tags.native_ddl"


@pytest.mark.parametrize("table_name", ["trips", "green_trips", "hvfhv_trips", "taxi_zones"])
def test_nyctaxi_clickhouse_rewrite_ddl(table_name: str):
    ctx = CompatibilityContext(
        platform="clickhouse",
        platform_version=None,
        benchmark="nyctaxi",
        query_id=table_name,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect="clickhouse",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No SCHEMA_EMIT rule for clickhouse/nyctaxi/{table_name}"
    assert decision.action is CompatAction.REWRITE_DDL
    assert isinstance(decision.payload, RewriteDDLPayload)
    assert decision.payload.transformer_id == "nyctaxi_clickhouse_ddl"


@pytest.mark.parametrize(
    "platform,table_name",
    [
        ("postgres", "trips"),
        ("postgres", "taxi_zones"),
        ("postgresql", "trips"),
        ("postgresql", "green_trips"),
        ("timescale", "trips"),
        ("timescale", "hvfhv_trips"),
    ],
)
def test_nyctaxi_postgres_family_rewrite_ddl(platform: str, table_name: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="nyctaxi",
        query_id=table_name,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect=platform,
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No SCHEMA_EMIT rule for {platform}/nyctaxi/{table_name}"
    assert decision.action is CompatAction.REWRITE_DDL
    assert isinstance(decision.payload, RewriteDDLPayload)
    assert decision.payload.transformer_id == "nyctaxi_postgres_partition_ddl"


@pytest.mark.parametrize(
    "platform,bmark",
    [
        ("duckdb", "tsbs_devops"),
        ("duckdb", "nyctaxi"),
        ("starrocks", "tsbs_devops"),
        ("starrocks", "nyctaxi"),
        ("datafusion", "nyctaxi"),
    ],
)
def test_other_platforms_no_schema_emit_rule(platform: str, bmark: str):
    ctx = CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark=bmark,
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect=platform,
    )
    assert REGISTRY.resolve(ctx) is None, f"Unexpected SCHEMA_EMIT rule for {platform}/{bmark}"


