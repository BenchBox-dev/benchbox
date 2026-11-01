from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

VELOX_READ_PRIMITIVES_SKIPS: dict[str, str] = {
    "approx_top_k_lineitem": (
        "The Velox image runs Spark 4.0.2, which has no APPROX_TOP_K: `SELECT APPROX_TOP_K(l_shipmode, 5) FROM "
        "lineitem` fails with `[UNRESOLVED_ROUTINE] Cannot resolve routine `APPROX_TOP_K``."
    ),
}

for _query_id, _reason in VELOX_READ_PRIMITIVES_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.velox.read_primitives.{_query_id}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(reason=_reason, query_id=_query_id),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        "velox",
        benchmark="read_primitives",
        query_id=_query_id,
    )
