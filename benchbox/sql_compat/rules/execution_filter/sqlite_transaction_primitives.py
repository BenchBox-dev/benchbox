from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_ISOLATION_REASON = (
    "SQLite transactions are always SERIALIZABLE and have no SET TRANSACTION ISOLATION LEVEL; "
    "{level} cannot be requested."
)

SQLITE_TRANSACTION_PRIMITIVES_OPERATION_SKIPS = {
    "transaction_isolation_read_committed": _ISOLATION_REASON.format(level="READ COMMITTED"),
    "transaction_isolation_repeatable_read": _ISOLATION_REASON.format(level="REPEATABLE READ"),
}

for _query_id, _reason in SQLITE_TRANSACTION_PRIMITIVES_OPERATION_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.sqlite.transaction_primitives.{_query_id}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(
                reason=_reason,
                query_id=_query_id,
            ),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        "sqlite",
        benchmark="transaction_primitives",
        query_id=_query_id,
    )
