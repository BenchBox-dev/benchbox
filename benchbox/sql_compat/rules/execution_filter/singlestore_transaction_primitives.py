from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_NO_SAVEPOINT = (
    "SingleStore transaction control is limited to BEGIN/START TRANSACTION, COMMIT and ROLLBACK: its SQL reference "
    "has no SAVEPOINT or ROLLBACK TO SAVEPOINT statement."
)
_ISOLATION_REASON = (
    "SingleStore provides only the READ COMMITTED isolation level (SingleStore FAQ: 'What isolation levels does "
    "SingleStore provide?'); {level} cannot be requested."
)

SINGLESTORE_TRANSACTION_PRIMITIVES_OPERATION_SKIPS = {
    "transaction_savepoint_nested": _NO_SAVEPOINT,
    "transaction_savepoint_deep_nesting": _NO_SAVEPOINT,
    "transaction_isolation_repeatable_read": _ISOLATION_REASON.format(level="REPEATABLE READ"),
    "transaction_isolation_serializable": _ISOLATION_REASON.format(level="SERIALIZABLE"),
    "transaction_truncate_in_transaction": (
        "The operation catalog marks TRUNCATE TABLE inside an explicit transaction unsupported on SingleStore "
        "(platform override write_sql: null)."
    ),
}

for _query_id, _reason in SINGLESTORE_TRANSACTION_PRIMITIVES_OPERATION_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.singlestore.transaction_primitives.{_query_id}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(reason=_reason, query_id=_query_id),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        "singlestore",
        benchmark="transaction_primitives",
        query_id=_query_id,
    )
