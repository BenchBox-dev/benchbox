from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

STARROCKS_TPCHAVOC_SKIPS: dict[str, str] = {
    "8_v1": (
        "StarRocks 3.5 cannot resolve an outer column referenced inside a derived table in the FROM of a scalar "
        "subquery: the statement fails with \"Column '`all_nations`.`o_year`' cannot be resolved.\"."
    ),
}

for _query_id, _reason in STARROCKS_TPCHAVOC_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.starrocks.tpchavoc.{_query_id}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(reason=_reason, query_id=_query_id),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        "starrocks",
        benchmark="tpchavoc",
        query_id=_query_id,
    )
