from __future__ import annotations

from benchbox.core.tpchavoc.dialect_compat import (
    DORIS_CORRELATED_GROUP_VARIANTS,
    DORIS_FILTER_VARIANT_IDS,
    DORIS_NAMED_WINDOW_VARIANT_IDS,
    DORIS_ORDER_BY_PROJECTION_VARIANT_IDS,
    GROUP_BY_EMPTY_VARIANT_IDS,
)
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, RewriteQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_P = Phase.QUERY_ADAPTER
_B = "tpchavoc"

_FILTER_REASON = "Doris does not parse the FILTER clause on aggregates; use agg(IF(cond, x, NULL)) instead"
_EMPTY_GROUP_REASON = (
    "Doris does not parse the empty GROUP BY (); drop it while preserving the global aggregate and HAVING"
)

for _query_id in DORIS_FILTER_VARIANT_IDS:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_adapter.doris.tpchavoc.{_query_id}.filtered_aggregate",
            action=CompatAction.REWRITE_QUERY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=RewriteQueryPayload(
                transformer_id="filtered_aggregates_to_conditional", description=_FILTER_REASON
            ),
            reason=_FILTER_REASON,
        ),
        _P,
        "doris",
        benchmark=_B,
        query_id=_query_id,
    )

for _query_id in GROUP_BY_EMPTY_VARIANT_IDS:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_adapter.doris.tpchavoc.{_query_id}.empty_group",
            action=CompatAction.REWRITE_QUERY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.SYNTAX_ERROR,
            payload=RewriteQueryPayload(transformer_id="translate_sql_query", description=_EMPTY_GROUP_REASON),
            reason=_EMPTY_GROUP_REASON,
        ),
        _P,
        "doris",
        benchmark=_B,
        query_id=_query_id,
    )

_NAMED_WINDOW_REASON = "Doris does not parse the WINDOW clause; inline each named window into its OVER clauses"
_ORDER_BY_PROJECTION_REASON = (
    "Doris rejects a subquery or window function repeated in ORDER BY (the optimizer expands the select alias "
    "into it); order by the projected alias instead"
)
_CORRELATED_GROUP_REASON = (
    "Doris cannot decorrelate a scalar subquery with GROUP BY on a correlated key, nor a HAVING after its "
    "aggregate; the key is a single group and the HAVING guards the aggregate in a CASE"
)

for _query_id, _transformer, _suffix, _reason, _failure_mode in (
    *(
        (q, "inline_named_windows", "named_window", _NAMED_WINDOW_REASON, FailureMode.SYNTAX_ERROR)
        for q in sorted(DORIS_NAMED_WINDOW_VARIANT_IDS)
    ),
    *(
        (
            q,
            "alias_order_by_projections",
            "order_by_projection",
            _ORDER_BY_PROJECTION_REASON,
            FailureMode.UNSUPPORTED_FEATURE,
        )
        for q in sorted(DORIS_ORDER_BY_PROJECTION_VARIANT_IDS)
    ),
    *(
        (
            q,
            "drop_correlated_scalar_group_by+fold_scalar_having_into_case",
            "correlated_scalar_group_by",
            _CORRELATED_GROUP_REASON,
            FailureMode.UNSUPPORTED_FEATURE,
        )
        for q in sorted(DORIS_CORRELATED_GROUP_VARIANTS)
    ),
):
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_adapter.doris.tpchavoc.{_query_id}.{_suffix}",
            action=CompatAction.REWRITE_QUERY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=_failure_mode,
            payload=RewriteQueryPayload(transformer_id=_transformer, description=_reason),
            reason=_reason,
        ),
        _P,
        "doris",
        benchmark=_B,
        query_id=_query_id,
    )
