from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

DATAFUSION_TPCHAVOC_SKIPS: dict[str, str] = {
    "4_v7": "DataFusion physical planning does not support EXISTS in this variant shape.",
    "4_v10": "DataFusion physical planning does not support EXISTS in this variant shape.",
    "7_v1": "DataFusion physical planning does not support the scalar sub-query in this variant shape.",
    "8_v1": "DataFusion `scalar_subquery_to_join` optimizer rule fails to resolve a sub-query field for this variant.",
    "9_v1": "DataFusion physical planning does not support the scalar sub-query in this variant shape.",
    "12_v1": "DataFusion `scalar_subquery_to_join` optimizer rule reports an ambiguous `count(*)` for this variant.",
    "14_v8": "DataFusion physical planning does not support EXISTS in this variant shape.",
    "16_v7": "DataFusion physical planning does not support the IN sub-query in this variant shape.",
    "16_v10": "DataFusion physical planning does not support the IN sub-query in this variant shape.",
    "17_v7": "DataFusion physical planning does not support the scalar sub-query in this variant shape.",
    "17_v10": "DataFusion physical planning does not support the scalar sub-query in this variant shape.",
}

for _query_id, _reason in DATAFUSION_TPCHAVOC_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.datafusion.tpchavoc.{_query_id}",
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
        "datafusion",
        benchmark="tpchavoc",
        query_id=_query_id,
    )
