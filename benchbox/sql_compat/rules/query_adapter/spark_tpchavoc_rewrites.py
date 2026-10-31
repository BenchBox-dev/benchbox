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
_B = "tpchavoc"

_REWRITES = [
    (
        "1_v1",
        "q01_scalar_group_by_first",
        "Spark rejects the correlated scalar subquery in this aggregated SELECT list; wrap it in first()",
    ),
    (
        "3_v1",
        "q03_scalar_group_by_first",
        "Spark rejects the correlated scalar subquery in this aggregated SELECT list; wrap it in first()",
    ),
    (
        "2_v7",
        "q02_window_filter_to_if",
        "Velox rejects FILTER on window aggregates; move the condition into the aggregate argument",
    ),
    (
        "2_v10",
        "q02_char_case_trim",
        "Spark pads CHAR columns on read but compares a CASE result as an unpadded string; trim both sides",
    ),
    (
        "6_v2",
        "q06_group_by_empty_drop",
        "Spark parser rejects the empty GROUP BY () in this variant; drop it",
    ),
    (
        "7_v1",
        "q07_scalar_group_by_first",
        "Spark rejects the correlated scalar subquery in this aggregated SELECT list; wrap it in first()",
    ),
    (
        "8_v1",
        "q08_scalar_group_by_first",
        "Spark rejects the correlated scalar subqueries in this aggregated SELECT list; wrap them in first()",
    ),
    (
        "9_v1",
        "q09_scalar_group_by_first",
        "Spark rejects the correlated scalar subquery in this aggregated SELECT list; wrap it in first()",
    ),
    (
        "10_v1",
        "q10_scalar_group_by_first",
        "Spark rejects the correlated scalar subquery in this aggregated SELECT list; wrap it in first()",
    ),
    (
        "12_v1",
        "q12_scalar_group_by_first",
        "Spark rejects the correlated scalar subqueries in this aggregated SELECT list; wrap them in first()",
    ),
    (
        "14_v2",
        "q14_group_by_empty_drop",
        "Spark parser rejects the empty GROUP BY () in this variant; drop it",
    ),
    (
        "16_v1",
        "q16_scalar_group_by_first",
        "Spark rejects the correlated scalar subquery in this aggregated SELECT list; wrap it in first()",
    ),
    (
        "17_v4",
        "q17_dual_column_alias",
        "Spark-Connect engines need an explicit column alias on the (SELECT 1) dual leg; add one",
    ),
]

_TRANSFORMERS = {
    "scalar_group_by_first": "wrap_grouped_scalar_subqueries",
    "group_by_empty_drop": "translate_sql_query",
    "dual_column_alias": "name_literal_subquery_columns",
    "window_filter_to_if": "filtered_aggregates_to_conditional",
    "char_case_trim": "trim_char_case_comparisons",
}

for _platform in ("spark", "velox", "lakesail", "databricks"):
    for _qid, _slug, _reason in _REWRITES:
        REGISTRY.register(
            CompatibilityDecision(
                rule_id=f"query_adapter.{_platform}.tpchavoc.{_slug}",
                action=CompatAction.REWRITE_QUERY,
                support_level=SupportLevel.REWRITTEN,
                failure_mode=FailureMode.UNSUPPORTED_FEATURE,
                payload=RewriteQueryPayload(
                    transformer_id=_TRANSFORMERS[_slug.split("_", 1)[1]],
                    description=_reason,
                ),
                reason=_reason,
            ),
            _P,
            _platform,
            benchmark=_B,
            query_id=_qid,
        )
