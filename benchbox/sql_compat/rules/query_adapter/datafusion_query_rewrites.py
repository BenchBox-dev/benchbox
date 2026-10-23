from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    RewriteQueryPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import REGISTRY

_P = Phase.QUERY_ADAPTER
_B = "tpch"

_TRANSFORMERS = {
    "11": "decorrelate_subqueries",
    "16": "not_in_subquery_to_not_exists",
    "18": "in_aggregate_subquery_to_exists",
    "20": "decorrelate_subqueries",
}

_REWRITES = [
    (
        "11",
        "q11_having_threshold_cte",
        "DataFusion miscomputes HAVING with scalar subquery threshold; extract threshold to CTE",
    ),
    (
        "16",
        "q16_not_in_to_not_exists",
        "DataFusion NOT IN with potential NULLs gives wrong results; rewrite to NOT EXISTS",
    ),
    (
        "18",
        "q18_in_having_to_exists",
        "DataFusion IN subquery with HAVING clause decorrelates incorrectly; rewrite to EXISTS wrapper",
    ),
    (
        "20",
        "q20_correlated_to_cte",
        "DataFusion nested correlated IN produces wrong results; rewrite using pre-aggregating CTEs",
    ),
]

for _qid, _slug, _reason in _REWRITES:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_adapter.datafusion.tpch.{_slug}",
            action=CompatAction.REWRITE_QUERY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.SILENT_CORRUPTION,
            payload=RewriteQueryPayload(
                transformer_id=_TRANSFORMERS[_qid],
                description=_reason,
            ),
            reason=_reason,
        ),
        _P,
        "datafusion",
        benchmark=_B,
        query_id=_qid,
    )
