from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    PKCapabilityPayload,
    RewriteDDLPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import REGISTRY


def register_ddl_rewrite(
    *,
    platform: str,
    rule_name: str,
    transformer_id: str,
    description: str,
    reason: str,
    rule_platform: str | None = None,
    failure_mode: FailureMode = FailureMode.SYNTAX_ERROR,
    governance_only: bool = True,
) -> None:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"ddl_optimize.{rule_platform or platform}.all.{rule_name}",
            action=CompatAction.REWRITE_DDL,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=failure_mode,
            payload=RewriteDDLPayload(
                transformer_id=transformer_id,
                description=description,
                governance_only=governance_only,
            ),
            reason=reason,
        ),
        Phase.DDL_OPTIMIZE,
        platform,
    )


_PK_UNSUPPORTED_RULES: tuple[tuple[str, str, SupportLevel, FailureMode, bool, bool, str | None, str, str], ...] = (
    (
        "datafusion",
        "pk_lock_table_unsupported",
        SupportLevel.SKIPPED_DDL_FRAGMENT,
        FailureMode.SYNTAX_ERROR,
        False,
        False,
        None,
        "DataFusion does not parse PRIMARY KEY in CREATE TABLE",
        "DataFusion does not support PRIMARY KEY syntax; skip lock table DDL",
    ),
    (
        "ducklake",
        "pk_lock_table_unsupported",
        SupportLevel.SKIPPED_DDL_FRAGMENT,
        FailureMode.SYNTAX_ERROR,
        False,
        False,
        None,
        "DuckLake does not accept PRIMARY KEY/UNIQUE constraints in CREATE TABLE",
        "DuckLake rejects PRIMARY KEY syntax; skip lock table DDL and omit PK clauses",
    ),
    (
        "clickhouse",
        "pk_lock_table_unsupported",
        SupportLevel.REWRITTEN,
        FailureMode.UNSUPPORTED_FEATURE,
        False,
        False,
        None,
        "ClickHouse PRIMARY KEY is part of MergeTree ENGINE ORDER BY, not a SQL constraint",
        "ClickHouse does not support standard SQL PRIMARY KEY constraint; skip lock table DDL",
    ),
    (
        "starrocks",
        "pk_lock_table_unsupported",
        SupportLevel.INFORMATIONAL,
        FailureMode.SILENT_CORRUPTION,
        True,
        False,
        "first N columns only",
        "StarRocks silently ignores PRIMARY KEY unless columns are the first N in the table",
        "StarRocks accepts PRIMARY KEY DDL but does not enforce uniqueness; skip lock table DDL",
    ),
    (
        "doris",
        "pk_lock_table_unsupported",
        SupportLevel.INFORMATIONAL,
        FailureMode.SILENT_CORRUPTION,
        True,
        False,
        "DUP_KEYS table model required; PRIMARY KEY has different semantics",
        "Doris PRIMARY KEY model differs from standard SQL PRIMARY KEY constraint",
        "Doris does not enforce standard SQL PRIMARY KEY uniqueness; skip lock table DDL",
    ),
)

_PK_INFORMATIONAL_DIALECTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "snowflake",
        "snowflake",
        "Snowflake PRIMARY KEY is INFORMATIONAL by vendor documentation",
        "Snowflake accepts PRIMARY KEY DDL but explicitly documents uniqueness as not enforced "
        "(also covers Databend, which uses the Snowflake dialect)",
    ),
    (
        "redshift",
        "redshift",
        "Redshift PRIMARY KEY is INFORMATIONAL per ALTER TABLE docs",
        "Redshift accepts PRIMARY KEY DDL but documents uniqueness as not enforced",
    ),
    (
        "bigquery",
        "bigquery",
        "BigQuery PRIMARY KEY is NOT ENFORCED by default per DDL reference",
        "BigQuery accepts PRIMARY KEY DDL but uniqueness is advisory; both INSERTs succeed",
    ),
    (
        "databricks",
        "databricks",
        "Databricks Delta Lake PRIMARY KEY is informational (NOT ENFORCED) per docs",
        "Databricks accepts PRIMARY KEY DDL but uniqueness is not enforced by the Delta engine",
    ),
    (
        "tsql",
        "tsql",
        "Azure Synapse and Fabric Warehouse PRIMARY KEY is not enforced per Microsoft docs",
        "T-SQL DW platforms accept PRIMARY KEY for optimizer hints only; uniqueness not enforced "
        "(covers azure-synapse and fabric-warehouse dialects)",
    ),
    (
        "spark",
        "spark",
        "Apache Spark SQL / Delta Lake PRIMARY KEY constraint is not enforced at INSERT time",
        "Spark accepts PRIMARY KEY DDL (Delta format) but uniqueness not enforced "
        "(covers spark, pyspark, lakesail dialects)",
    ),
    (
        "trino",
        "trino",
        "Trino accepts PRIMARY KEY syntax but does not enforce uniqueness",
        "Trino / Starburst accept CREATE TABLE PRIMARY KEY but do not enforce it (covers trino and starburst dialects)",
    ),
    (
        "athena",
        "athena",
        "Athena accepts PRIMARY KEY syntax but does not enforce uniqueness",
        "Athena accepts CREATE TABLE PRIMARY KEY in the table DDL but does not enforce it",
    ),
    (
        "presto",
        "presto",
        "Presto accepts PRIMARY KEY syntax but does not enforce uniqueness",
        "PrestoDB accepts PRIMARY KEY DDL as advisory constraint; uniqueness not enforced",
    ),
)


def register_pk_capability_rules(benchmark: str) -> None:
    for platform, suffix, support, failure, ddl_accepted, enforced, conditions, detail, reason in _PK_UNSUPPORTED_RULES:
        _register_pk_rule(
            platform=platform,
            benchmark=benchmark,
            suffix=suffix,
            support_level=support,
            failure_mode=failure,
            ddl_accepted=ddl_accepted,
            uniqueness_enforced=enforced,
            conditions=conditions,
            failure_mode_detail=detail,
            reason=reason,
        )

    for dialect, slug, conditions, detail in _PK_INFORMATIONAL_DIALECTS:
        _register_pk_rule(
            platform=dialect,
            benchmark=benchmark,
            suffix="pk_not_enforced",
            slug=slug,
            support_level=SupportLevel.INFORMATIONAL,
            failure_mode=FailureMode.SILENT_CORRUPTION,
            ddl_accepted=True,
            uniqueness_enforced=False,
            conditions=conditions,
            failure_mode_detail=detail,
            reason=(
                f"Workload runs; PRIMARY KEY uniqueness is not enforced on {slug} - "
                "lock table bypass required to prevent concurrent double-population"
            ),
        )


def _register_pk_rule(
    *,
    platform: str,
    benchmark: str,
    suffix: str,
    support_level: SupportLevel,
    failure_mode: FailureMode,
    ddl_accepted: bool,
    uniqueness_enforced: bool,
    conditions: str | None,
    failure_mode_detail: str | None,
    reason: str,
    slug: str | None = None,
) -> None:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"schema_emit.{slug or platform}.{benchmark}.{suffix}",
            action=CompatAction.REWRITE_DDL,
            support_level=support_level,
            failure_mode=failure_mode,
            payload=PKCapabilityPayload(
                ddl_accepted=ddl_accepted,
                uniqueness_enforced=uniqueness_enforced,
                conditions=conditions,
                failure_mode_detail=failure_mode_detail,
            ),
            reason=reason,
        ),
        Phase.SCHEMA_EMIT,
        platform,
        benchmark=benchmark,
    )
