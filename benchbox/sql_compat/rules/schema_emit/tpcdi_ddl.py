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

TYPE_MAP_TRANSFORMER = "translate_ddl:type_map"

FLAG_BOOLEAN_PLATFORMS = ("duckdb",)
TIME_AS_STRING_TYPES = {
    "spark": "STRING",
    "lakesail": "STRING",
    "pyspark": "STRING",
    "velox": "STRING",
    "databricks": "STRING",
    "clickhouse": "String",
}

for _platform in FLAG_BOOLEAN_PLATFORMS:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"schema_emit.{_platform}.tpcdi.flag_columns_boolean",
            action=CompatAction.NATIVE,
            support_level=SupportLevel.NATIVE,
            failure_mode=FailureMode.NONE,
            payload=None,
            reason="SELECT true = 1 evaluates to true on this platform, so the specification's BOOLEAN flag "
            "columns are stored as BOOLEAN and the audit text that compares flags with 1 runs unmodified. "
            "Platforms without this rule store flags as a 0/1 SMALLINT through translate_ddl's type_map "
            "(Clauses 3.2.1 and 8.2.1.2).",
        ),
        Phase.SCHEMA_EMIT,
        _platform,
        benchmark="tpcdi",
    )

for _platform, _string_type in TIME_AS_STRING_TYPES.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"schema_emit.{_platform}.tpcdi.time_as_string",
            action=CompatAction.REWRITE_DDL,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=RewriteDDLPayload(
                transformer_id=TYPE_MAP_TRANSFORMER,
                description=f"TPC-DI TIME columns are declared {_string_type} through translate_ddl's type_map",
                governance_only=True,
            ),
            reason="The platform has no time-of-day column type, and the SQL translator would otherwise "
            "widen TIME to TIMESTAMP; the specification's TIME columns hold HH:MM:SS text that compares "
            "correctly as a string.",
        ),
        Phase.SCHEMA_EMIT,
        _platform,
        benchmark="tpcdi",
    )
