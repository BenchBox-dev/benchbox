from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

SQLITE_TPCHAVOC_SKIPS: dict[str, str] = {
    "5_v8": (
        "SQLite cannot decorrelate this variant: it cross-joins nation with a full lineitem scan and evaluates a "
        "correlated EXISTS/scalar subquery per row, and a single execution exceeds 300s at scale 0.1."
    ),
}

for _query_id, _reason in SQLITE_TPCHAVOC_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.sqlite.tpchavoc.{_query_id}",
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
        "sqlite",
        benchmark="tpchavoc",
        query_id=_query_id,
    )
