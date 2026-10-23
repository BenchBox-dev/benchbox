from __future__ import annotations

from benchbox.core.tpchavoc.dialect_compat import CLICKHOUSE_FILTER_VARIANT_IDS
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, RewriteQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

for _platform in ("clickhouse-local", "clickhouse-server", "clickhouse-cloud"):
    for _query_id in CLICKHOUSE_FILTER_VARIANT_IDS:
        _reason = "Use ClickHouse countIf and null-preserving aggregate If combinators instead of FILTER"
        REGISTRY.register(
            CompatibilityDecision(
                rule_id=f"query_adapter.{_platform}.tpchavoc.{_query_id}.filtered_aggregate",
                action=CompatAction.REWRITE_QUERY,
                support_level=SupportLevel.REWRITTEN,
                failure_mode=FailureMode.UNSUPPORTED_FEATURE,
                payload=RewriteQueryPayload(transformer_id="filtered_aggregates_to_conditional", description=_reason),
                reason=_reason,
            ),
            Phase.QUERY_ADAPTER,
            _platform,
            benchmark="tpchavoc",
            query_id=_query_id,
        )
