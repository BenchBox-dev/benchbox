from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

POSTGRES_TPCHAVOC_SKIPS: dict[str, str] = {
    "5_v9": "PostgreSQL-family engines reject aggregate expressions inside window definitions.",
    "7_v9": "PostgreSQL-family engines reject aggregate expressions inside window definitions.",
    "9_v9": "PostgreSQL-family engines reject aggregate expressions inside window definitions.",
    "10_v9": "PostgreSQL-family engines reject aggregate expressions inside window definitions.",
    "11_v9": "PostgreSQL-family engines reject this window expression in WHERE.",
    "13_v9": "PostgreSQL-family engines reject aggregate expressions inside window definitions.",
}

POSTGRES_DUCKDB_BACKED_TPCHAVOC_SKIPS: dict[str, str] = {
    "1_v7": (
        "The PostgreSQL form of the LIST aggregate variant reads its arrays with a multi-argument `UNNEST`, which "
        "DuckDB rejects (`No function matches the given name and argument types 'unnest(DECIMAL(15,2)[], "
        "DECIMAL(15,2)[])'`), and this engine can plan a query in DuckDB."
    ),
}

_POSTGRES_TPCHAVOC_PLATFORM_SKIPS: dict[str, dict[str, str]] = {
    "pg-duckdb": {**POSTGRES_TPCHAVOC_SKIPS, **POSTGRES_DUCKDB_BACKED_TPCHAVOC_SKIPS},
    "pg-mooncake": {**POSTGRES_TPCHAVOC_SKIPS, **POSTGRES_DUCKDB_BACKED_TPCHAVOC_SKIPS},
    "timescaledb": POSTGRES_TPCHAVOC_SKIPS,
}

for _platform, _platform_skips in _POSTGRES_TPCHAVOC_PLATFORM_SKIPS.items():
    for _query_id, _reason in _platform_skips.items():
        REGISTRY.register(
            CompatibilityDecision(
                rule_id=f"execution_filter.{_platform}.tpchavoc.{_query_id}",
                action=CompatAction.SKIP_QUERY,
                support_level=SupportLevel.SKIPPED_QUERY,
                failure_mode=FailureMode.UNSUPPORTED_FEATURE,
                payload=SkipQueryPayload(
                    reason=_reason,
                    query_id=_query_id,
                ),
                reason=_reason,
            ),
            Phase.EXECUTION_FILTER,
            _platform,
            benchmark="tpchavoc",
            query_id=_query_id,
        )
