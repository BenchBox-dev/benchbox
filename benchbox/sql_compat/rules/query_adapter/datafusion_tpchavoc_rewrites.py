from __future__ import annotations

from benchbox.core.tpchavoc.dialect_compat import DATAFUSION_EMPTY_GROUP_VARIANT_IDS
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, RewriteQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

for _query_id in DATAFUSION_EMPTY_GROUP_VARIANT_IDS:
    _reason = "Drop unsupported empty GROUP BY () while preserving the global aggregate and HAVING"
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_adapter.datafusion.tpchavoc.{_query_id}.empty_group",
            action=CompatAction.REWRITE_QUERY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=RewriteQueryPayload(transformer_id="translate_sql_query", description=_reason),
            reason=_reason,
        ),
        Phase.QUERY_ADAPTER,
        "datafusion",
        benchmark="tpchavoc",
        query_id=_query_id,
    )
