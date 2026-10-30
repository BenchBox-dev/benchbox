from __future__ import annotations

from benchbox.core.tpchavoc.dialect_compat import DORIS_FILTER_VARIANT_IDS, GROUP_BY_EMPTY_VARIANT_IDS
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
