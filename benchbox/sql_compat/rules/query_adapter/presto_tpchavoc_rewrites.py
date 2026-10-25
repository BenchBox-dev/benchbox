from __future__ import annotations

from benchbox.core.tpchavoc.dialect_compat import (
    LIST_AGGREGATE_VARIANT_IDS,
    PRESTO_ALIAS_VARIANT_IDS,
    PRESTO_WINDOW_FILTER_VARIANT_IDS,
)
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, RewriteQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_TRANSFORMERS = {
    "inline_select_alias": "inline_select_aliases",
    "window_filter_to_if": "filtered_aggregates_to_conditional",
    "array_aggregation": "reduce_list_aggregates",
}

for _platform, _engine_label in (("presto", "Presto"), ("trino", "Trino"), ("athena", "Athena")):
    for _query_ids, _name, _reason_template in (
        (
            PRESTO_ALIAS_VARIANT_IDS,
            "inline_select_alias",
            "Inline the aggregate expression where {engine} rejects a SELECT alias in HAVING or WHERE",
        ),
        (
            PRESTO_WINDOW_FILTER_VARIANT_IDS,
            "window_filter_to_if",
            "{engine} rejects FILTER on window aggregates; move the condition into the aggregate argument",
        ),
        (
            LIST_AGGREGATE_VARIANT_IDS,
            "array_aggregation",
            "Order the LIST aggregates as {engine} ARRAY_AGG and reduce the DuckDB list functions to {engine} array functions",
        ),
    ):
        _reason = _reason_template.format(engine=_engine_label)
        for _query_id in _query_ids:
            REGISTRY.register(
                CompatibilityDecision(
                    rule_id=f"query_adapter.{_platform}.tpchavoc.{_query_id}.{_name}",
                    action=CompatAction.REWRITE_QUERY,
                    support_level=SupportLevel.REWRITTEN,
                    failure_mode=FailureMode.UNSUPPORTED_FEATURE,
                    payload=RewriteQueryPayload(transformer_id=_TRANSFORMERS[_name], description=_reason),
                    reason=_reason,
                ),
                Phase.QUERY_ADAPTER,
                _platform,
                benchmark="tpchavoc",
                query_id=_query_id,
            )
