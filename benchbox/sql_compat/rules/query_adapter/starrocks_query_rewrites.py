from __future__ import annotations

from benchbox.core.tpchavoc.dialect_compat import (
    GROUP_BY_EMPTY_VARIANT_IDS,
    STARROCKS_CORRELATED_GROUP_VARIANTS,
    STARROCKS_FILTER_VARIANT_IDS,
    STARROCKS_NAMED_WINDOW_VARIANT_IDS,
    STARROCKS_ORDER_BY_PROJECTION_VARIANT_IDS,
    STARROCKS_SOURCELESS_EXISTS_VARIANT_IDS,
)
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    RewriteQueryPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import REGISTRY

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_adapter.starrocks.all.inject_subquery_aliases",
        action=CompatAction.REWRITE_QUERY,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.SYNTAX_ERROR,
        payload=RewriteQueryPayload(
            transformer_id="starrocks_subquery_alias_injector",
            description="StarRocks requires aliases on all FROM/JOIN derived tables; inject AS _sqN aliases for unaliased subqueries",
        ),
        reason=(
            "StarRocks inherits MySQL's requirement that every derived table carries an alias. "
            "Unaliased subqueries produce a syntax error at parse time."
        ),
    ),
    Phase.QUERY_ADAPTER,
    "starrocks",
)

_P = Phase.QUERY_ADAPTER
_B = "tpchavoc"

_FILTER_REASON = "StarRocks does not parse the FILTER clause on aggregates; use agg(IF(cond, x, NULL)) instead"
_EMPTY_GROUP_REASON = (
    "StarRocks does not parse the empty GROUP BY (); drop it while preserving the global aggregate and HAVING"
)
_NAMED_WINDOW_REASON = "StarRocks does not parse the WINDOW clause; inline each named window into its OVER clauses"
_ORDER_BY_PROJECTION_REASON = (
    "StarRocks rejects a subquery or window function repeated in ORDER BY ('ORDER BY clause cannot contain "
    "subquery'); order by the projected alias instead"
)
_CORRELATED_GROUP_REASON = (
    "StarRocks cannot decorrelate a scalar subquery with GROUP BY on a correlated key ('Not support the "
    "subquery!'), nor a HAVING after its aggregate; the key is a single group and the HAVING guards the "
    "aggregate in a CASE"
)
_SOURCELESS_EXISTS_REASON = (
    "StarRocks rejects EXISTS over a sourceless SELECT ('Not support exists correlation subquery with Non-EQ "
    "predicate'); EXISTS(SELECT 1 WHERE cond) is the predicate cond itself"
)

for _query_id, _transformer, _suffix, _reason, _failure_mode in (
    *(
        (q, "filtered_aggregates_to_conditional", "filtered_aggregate", _FILTER_REASON, FailureMode.UNSUPPORTED_FEATURE)
        for q in sorted(STARROCKS_FILTER_VARIANT_IDS)
    ),
    *(
        (q, "translate_sql_query", "empty_group", _EMPTY_GROUP_REASON, FailureMode.SYNTAX_ERROR)
        for q in sorted(GROUP_BY_EMPTY_VARIANT_IDS)
    ),
    *(
        (q, "inline_named_windows", "named_window", _NAMED_WINDOW_REASON, FailureMode.SYNTAX_ERROR)
        for q in sorted(STARROCKS_NAMED_WINDOW_VARIANT_IDS)
    ),
    *(
        (
            q,
            "alias_order_by_projections",
            "order_by_projection",
            _ORDER_BY_PROJECTION_REASON,
            FailureMode.UNSUPPORTED_FEATURE,
        )
        for q in sorted(STARROCKS_ORDER_BY_PROJECTION_VARIANT_IDS)
    ),
    *(
        (
            q,
            "drop_correlated_scalar_group_by+fold_scalar_having_into_case",
            "correlated_scalar_group_by",
            _CORRELATED_GROUP_REASON,
            FailureMode.UNSUPPORTED_FEATURE,
        )
        for q in sorted(STARROCKS_CORRELATED_GROUP_VARIANTS)
    ),
    *(
        (
            q,
            "inline_sourceless_exists",
            "sourceless_exists",
            _SOURCELESS_EXISTS_REASON,
            FailureMode.UNSUPPORTED_FEATURE,
        )
        for q in sorted(STARROCKS_SOURCELESS_EXISTS_VARIANT_IDS)
    ),
):
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_adapter.starrocks.tpchavoc.{_query_id}.{_suffix}",
            action=CompatAction.REWRITE_QUERY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=_failure_mode,
            payload=RewriteQueryPayload(transformer_id=_transformer, description=_reason),
            reason=_reason,
        ),
        _P,
        "starrocks",
        benchmark=_B,
        query_id=_query_id,
    )
