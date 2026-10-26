from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

LIST_AGGREGATE_TPCHAVOC_QUERY_ID = "1_v7"

LIST_AGGREGATE_TPCHAVOC_PLATFORM_ALIASES = {
    "apache-doris": "doris",
    "azure-synapse": "synapse",
    "fabric-warehouse": "fabric-dw",
    "snowpark-connect-for-spark": "snowpark-connect",
    "snowparkconnectadapter": "snowpark-connect",
}

LIST_AGGREGATE_TPCHAVOC_SKIPS: dict[str, str] = {
    "databend": "No Databend form of the DuckDB `LIST` aggregate and `list_*` lambda functions this variant uses exists in the translator (the Snowflake-dialect translation leaves them in place).",
    "doris": "No Doris form of the DuckDB `LIST` aggregate and `list_*` lambda functions this variant uses exists in the translator.",
    "fabric-dw": "T-SQL has no array type, so the DuckDB `LIST` aggregate and `list_*` lambda functions this variant uses cannot be translated.",
    "fabric-lakehouse": "T-SQL has no array type, so the DuckDB `LIST` aggregate and `list_*` lambda functions this variant uses cannot be translated.",
    "questdb": "No QuestDB form of the DuckDB `LIST` aggregate and `list_*` lambda functions this variant uses exists in the translator.",
    "redshift": "No Redshift form of the DuckDB `LIST` aggregate and `list_*` lambda functions this variant uses exists in the translator (Redshift has no multi-argument `UNNEST`, so the PostgreSQL form does not apply).",
    "singlestore": "No SingleStore form of the DuckDB `LIST` aggregate and `list_*` lambda functions this variant uses exists in the translator.",
    "snowpark-connect": "The Snowflake-dialect translation keeps DuckDB's `LIST` aggregate and `list_*` lambda functions, which Snowflake does not support.",
    "starrocks": "No StarRocks form of the DuckDB `LIST` aggregate and `list_*` lambda functions this variant uses exists in the translator.",
    "synapse": "T-SQL has no array type, so the DuckDB `LIST` aggregate and `list_*` lambda functions this variant uses cannot be translated.",
}

for _platform, _reason in LIST_AGGREGATE_TPCHAVOC_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.{_platform}.tpchavoc.{LIST_AGGREGATE_TPCHAVOC_QUERY_ID}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(reason=_reason, query_id=LIST_AGGREGATE_TPCHAVOC_QUERY_ID),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        _platform,
        benchmark="tpchavoc",
        query_id=LIST_AGGREGATE_TPCHAVOC_QUERY_ID,
    )
