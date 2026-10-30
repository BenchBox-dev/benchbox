from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

LAKESAIL_TPCHAVOC_SKIPS: dict[str, str] = {
    "1_v1": "Sail physical planning rejects the correlated scalar subqueries in the select list with `Physical plan does not support logical expression ScalarSubquery`.",
    "1_v7": "Sail rejects the LIST aggregate variant with `unknown function: LIST`.",
    "1_v8": "Sail rejects the named-window variant with `named window`.",
    "2_v5": "Sail reports an ambiguous `ps_partkey` attribute for the variant shape.",
    "2_v7": "Sail rejects the nested MIN window expression as an invalid window function.",
    "3_v1": "Sail planner rejects this correlated scalar subquery placement.",
    "4_v7": "Sail physical planning rejects EXISTS in this variant shape.",
    "4_v10": "Sail physical planning rejects EXISTS in this variant shape.",
    "5_v4": "Sail physical planning rejects the aggregate expression in this variant shape.",
    "6_v2": "Sail parser rejects the generated empty subquery syntax.",
    "7_v1": "Sail loses qualified field names in this variant and cannot resolve `l2.l_shipdate`.",
    "8_v1": "Sail loses projected field names in this variant and cannot resolve the subquery field.",
    "9_v1": "Sail loses qualified field names in this variant and cannot resolve `n2.n_name`.",
    "10_v1": "Sail planner rejects this correlated scalar subquery placement.",
    "11_v4": "Sail physical planning rejects the aggregate expression in this variant shape.",
    "11_v9": "Sail planner reports `Unsupported predicate type`.",
    "12_v1": "Sail reports an ambiguous `__always_true` field for the optimizer-flattening variant.",
    "14_v2": "Sail parser rejects the generated empty subquery syntax.",
    "14_v8": "Sail physical planning rejects EXISTS in this variant shape.",
    "16_v1": "Sail planner rejects this correlated scalar subquery placement.",
    "16_v7": "Sail reports duplicate unqualified field names for the DISTINCT/FILTER variant.",
    "16_v10": "Sail reports duplicate unqualified field names for the DISTINCT CASE variant.",
    "17_v2": "Sail reports an ambiguous `l_partkey` attribute for the variant shape.",
    "17_v4": "Sail has no `dual` table for this scalar-subquery variant.",
    "17_v7": "Sail physical planning rejects the scalar subquery expression.",
    "17_v10": "Sail physical planning rejects the scalar subquery expression.",
}

for _query_id, _reason in LAKESAIL_TPCHAVOC_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.lakesail.tpchavoc.{_query_id}",
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
        "lakesail",
        benchmark="tpchavoc",
        query_id=_query_id,
    )
