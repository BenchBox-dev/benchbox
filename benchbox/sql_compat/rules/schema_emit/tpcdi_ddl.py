from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    RewriteDDLPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import REGISTRY

FLAG_BOOLEAN_RULE_SUFFIX = "flag_columns_boolean"
TIME_AS_STRING_TRANSFORMER = "tpcdi_time_as_string"
TIME_AS_CLICKHOUSE_STRING_TRANSFORMER = "tpcdi_time_as_clickhouse_string"

FLAG_BOOLEAN_PLATFORMS = ("duckdb",)
TIME_AS_STRING_PLATFORMS = ("spark", "lakesail", "pyspark", "velox", "databricks")
TIME_AS_CLICKHOUSE_STRING_PLATFORMS = ("clickhouse",)

for _platform in FLAG_BOOLEAN_PLATFORMS:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"ddl_optimize.{_platform}.tpcdi.{FLAG_BOOLEAN_RULE_SUFFIX}",
            action=CompatAction.NATIVE,
            support_level=SupportLevel.NATIVE,
            failure_mode=FailureMode.NONE,
            payload=None,
            reason="SELECT true = 1 evaluates to true on this platform, so the specification's BOOLEAN flag "
            "columns are stored as BOOLEAN and the audit text that compares flags with 1 runs unmodified. "
            "Platforms without this rule store flags as a 0/1 SMALLINT (Clauses 3.2.1 and 8.2.1.2).",
        ),
        Phase.DDL_OPTIMIZE,
        _platform,
        benchmark="tpcdi",
    )

for _platform in TIME_AS_STRING_PLATFORMS:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"ddl_optimize.{_platform}.tpcdi.time_as_string",
            action=CompatAction.REWRITE_DDL,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=RewriteDDLPayload(
                transformer_id=TIME_AS_STRING_TRANSFORMER,
                description="TPC-DI TIME columns are declared STRING",
                governance_only=True,
            ),
            reason="The platform has no time-of-day column type; the specification's TIME columns hold "
            "HH:MM:SS text that compares correctly as a string.",
        ),
        Phase.DDL_OPTIMIZE,
        _platform,
        benchmark="tpcdi",
    )

for _platform in TIME_AS_CLICKHOUSE_STRING_PLATFORMS:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"ddl_optimize.{_platform}.tpcdi.time_as_string",
            action=CompatAction.REWRITE_DDL,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=RewriteDDLPayload(
                transformer_id=TIME_AS_CLICKHOUSE_STRING_TRANSFORMER,
                description="TPC-DI TIME columns are declared String",
                governance_only=True,
            ),
            reason="ClickHouse has no time-of-day column type; the specification's TIME columns hold "
            "HH:MM:SS text that compares correctly as a string.",
        ),
        Phase.DDL_OPTIMIZE,
        _platform,
        benchmark="tpcdi",
    )
