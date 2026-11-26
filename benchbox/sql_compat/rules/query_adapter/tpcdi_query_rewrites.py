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

EXACT_DECIMAL_QUOTIENTS = "exact_decimal_quotients"
EXACT_DECIMAL_QUOTIENT_PLATFORMS = ("duckdb",)

_REASON = (
    "DuckDB computes DECIMAL / DECIMAL in DOUBLE, so a quotient cast to DECIMAL can round a half-cent tie the "
    "wrong way; the rewrite divides scaled integers exactly and rounds half away from zero."
)

for _platform in EXACT_DECIMAL_QUOTIENT_PLATFORMS:
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_adapter.{_platform}.tpcdi.exact_decimal_quotients",
            action=CompatAction.REWRITE_QUERY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.SILENT_CORRUPTION,
            payload=RewriteQueryPayload(transformer_id=EXACT_DECIMAL_QUOTIENTS, description=_REASON),
            reason=_REASON,
        ),
        Phase.QUERY_ADAPTER,
        _platform,
        benchmark="tpcdi",
    )
