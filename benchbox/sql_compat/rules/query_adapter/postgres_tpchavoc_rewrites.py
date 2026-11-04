from __future__ import annotations

from benchbox.core.tpchavoc.dialect_compat import (
    POSTGRES_ALIAS_VARIANT_IDS,
    POSTGRES_DECORRELATED_VARIANT_IDS,
    POSTGRES_DUAL_VARIANT_IDS,
    POSTGRES_QUALIFIED_COLUMNS,
)
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, RewriteQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

for _platform in ("pg-duckdb", "pg-mooncake", "timescaledb"):
    for _query_id in POSTGRES_ALIAS_VARIANT_IDS:
        _reason = "Inline the aggregate expression where PostgreSQL rejects a SELECT alias in HAVING or WHERE"
        REGISTRY.register(
            CompatibilityDecision(
                rule_id=f"query_adapter.{_platform}.tpchavoc.{_query_id}.inline_select_alias",
                action=CompatAction.REWRITE_QUERY,
                support_level=SupportLevel.REWRITTEN,
                failure_mode=FailureMode.UNSUPPORTED_FEATURE,
                payload=RewriteQueryPayload(transformer_id="inline_select_aliases", description=_reason),
                reason=_reason,
            ),
            Phase.QUERY_ADAPTER,
            _platform,
            benchmark="tpchavoc",
            query_id=_query_id,
        )
    for _query_id in POSTGRES_QUALIFIED_COLUMNS:
        _reason = "Qualify translated column references that are ambiguous to PostgreSQL"
        REGISTRY.register(
            CompatibilityDecision(
                rule_id=f"query_adapter.{_platform}.tpchavoc.{_query_id}.qualify_columns",
                action=CompatAction.REWRITE_QUERY,
                support_level=SupportLevel.REWRITTEN,
                failure_mode=FailureMode.UNSUPPORTED_FEATURE,
                payload=RewriteQueryPayload(transformer_id="qualify_columns_by_name", description=_reason),
                reason=_reason,
            ),
            Phase.QUERY_ADAPTER,
            _platform,
            benchmark="tpchavoc",
            query_id=_query_id,
        )
    for _query_id in POSTGRES_DUAL_VARIANT_IDS:
        _reason = "Drop the one-row dual-style source so the SELECT needs no FROM"
        REGISTRY.register(
            CompatibilityDecision(
                rule_id=f"query_adapter.{_platform}.tpchavoc.{_query_id}.drop_row_source",
                action=CompatAction.REWRITE_QUERY,
                support_level=SupportLevel.REWRITTEN,
                failure_mode=FailureMode.UNSUPPORTED_FEATURE,
                payload=RewriteQueryPayload(transformer_id="drop_literal_row_source", description=_reason),
                reason=_reason,
            ),
            Phase.QUERY_ADAPTER,
            _platform,
            benchmark="tpchavoc",
            query_id=_query_id,
        )
    for _query_id in POSTGRES_DECORRELATED_VARIANT_IDS:
        _reason = "Join a pre-aggregated lineitem subquery instead of re-scanning lineitem for every partsupp row"
        REGISTRY.register(
            CompatibilityDecision(
                rule_id=f"query_adapter.{_platform}.tpchavoc.{_query_id}.decorrelate_subqueries",
                action=CompatAction.REWRITE_QUERY,
                support_level=SupportLevel.REWRITTEN,
                failure_mode=FailureMode.UNSUPPORTED_FEATURE,
                payload=RewriteQueryPayload(transformer_id="decorrelate_subqueries", description=_reason),
                reason=_reason,
            ),
            Phase.QUERY_ADAPTER,
            _platform,
            benchmark="tpchavoc",
            query_id=_query_id,
        )
