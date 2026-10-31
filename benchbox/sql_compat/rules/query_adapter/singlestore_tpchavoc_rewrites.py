from __future__ import annotations

from benchbox.core.tpchavoc.dialect_compat import (
    GROUP_BY_EMPTY_VARIANT_IDS,
    SINGLESTORE_AGGREGATE_SUBQUERY_VARIANT_IDS,
    SINGLESTORE_ALIAS_QUALIFIED_COLUMNS,
    SINGLESTORE_CORRELATED_GROUP_VARIANTS,
    SINGLESTORE_FILTER_VARIANT_IDS,
    SINGLESTORE_GROUPING_SET_VARIANT_IDS,
    SINGLESTORE_ORDER_SUBQUERY_VARIANT_IDS,
    SINGLESTORE_SOURCELESS_WHERE_VARIANT_IDS,
)
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, RewriteQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_RULES = (
    (
        "filtered_aggregate",
        "filtered_aggregates_to_conditional",
        SINGLESTORE_FILTER_VARIANT_IDS,
        "SingleStore does not parse the FILTER clause on aggregates; use a conditional aggregate instead",
    ),
    (
        "empty_group",
        "drop_empty_group_by",
        GROUP_BY_EMPTY_VARIANT_IDS,
        "SingleStore does not parse the empty GROUP BY (); drop it while preserving the global aggregate and HAVING",
    ),
    (
        "single_grouping_set",
        "unwrap_single_grouping_set",
        SINGLESTORE_GROUPING_SET_VARIANT_IDS,
        "SingleStore does not parse GROUPING SETS; a single grouping set is a plain GROUP BY",
    ),
    (
        "sourceless_where",
        "add_dual_source_to_filtered_selects",
        SINGLESTORE_SOURCELESS_WHERE_VARIANT_IDS,
        "SingleStore requires FROM DUAL on a SELECT that has a WHERE clause but no table source",
    ),
    (
        "alias_column_capture",
        "qualify_aggregated_columns",
        frozenset(SINGLESTORE_ALIAS_QUALIFIED_COLUMNS),
        "Qualify the aggregated column so a same-named select alias is not resolved as a nested aggregate",
    ),
    (
        "order_by_subquery_alias",
        "wrap_order_by_subquery_aliases",
        SINGLESTORE_ORDER_SUBQUERY_VARIANT_IDS,
        "SingleStore rejects ORDER BY on an alias of a correlated subselect; order the rows in an enclosing query",
    ),
    (
        "aggregate_subquery",
        "hoist_aggregate_subqueries",
        SINGLESTORE_AGGREGATE_SUBQUERY_VARIANT_IDS,
        "SingleStore rejects subselects inside aggregate functions; compute them as columns of a derived table",
    ),
    (
        "correlated_scalar_group_by",
        "drop_correlated_scalar_group_by",
        frozenset(SINGLESTORE_CORRELATED_GROUP_VARIANTS),
        "SingleStore cannot decorrelate a scalar subselect with GROUP BY on a correlated key; the key is a single group",
    ),
)

_RULES_BY_QUERY: dict[str, list[tuple[str, str, str]]] = {}
for _suffix, _transformer, _query_ids, _reason in _RULES:
    for _query_id in _query_ids:
        _RULES_BY_QUERY.setdefault(_query_id, []).append((_suffix, _transformer, _reason))

for _query_id in sorted(_RULES_BY_QUERY):
    _entries = _RULES_BY_QUERY[_query_id]
    _suffix = "+".join(entry[0] for entry in _entries)
    _transformer = "+".join(entry[1] for entry in _entries)
    _reason = "; ".join(entry[2] for entry in _entries)
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_adapter.singlestore.tpchavoc.{_query_id}.{_suffix}",
            action=CompatAction.REWRITE_QUERY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=RewriteQueryPayload(transformer_id=_transformer, description=_reason),
            reason=_reason,
        ),
        Phase.QUERY_ADAPTER,
        "singlestore",
        benchmark="tpchavoc",
        query_id=_query_id,
    )
