from __future__ import annotations

from benchbox.core.tpchavoc.cloud_compat import BIGQUERY_FILTER_IDS
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, RewriteQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_RULES = (
    *(
        ("bigquery", query_id, "filtered_aggregate", "Use conditional aggregates instead of FILTER")
        for query_id in BIGQUERY_FILTER_IDS
    ),
    *(
        ("bigquery", query_id, "one_row_source", "Add a one-row source to subqueries without FROM")
        for query_id in ("7_v8", "12_v8", "13_v8", "18_v4")
    ),
    *(
        ("bigquery", query_id, "qualified_having", "Qualify the input column hidden by a SELECT alias")
        for query_id in ("5_v4", "11_v4")
    ),
    ("bigquery", "3_v9", "window_null_order", "Remove explicit null ordering from the window order key"),
    ("snowflake", "6_v2", "empty_group", "Drop the unsupported empty grouping set"),
    ("snowflake", "14_v2", "empty_group", "Drop the unsupported empty grouping set"),
)

_TRANSFORMERS = {
    "filtered_aggregate": "filtered_aggregates_to_conditional",
    "one_row_source": "add_row_source_to_fromless_selects",
    "qualified_having": "qualify_columns_by_name",
    "window_null_order": "translate_sql_query",
    "empty_group": "translate_sql_query",
}

for _platform, _query_id, _slug, _reason in _RULES:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_adapter.{_platform}.tpchavoc.{_query_id}.{_slug}",
            action=CompatAction.REWRITE_QUERY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=RewriteQueryPayload(transformer_id=_TRANSFORMERS[_slug], description=_reason),
            reason=_reason,
        ),
        Phase.QUERY_ADAPTER,
        _platform,
        benchmark="tpchavoc",
        query_id=_query_id,
    )
