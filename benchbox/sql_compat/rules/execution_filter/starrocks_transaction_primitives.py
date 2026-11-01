from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_NO_UPDATE_DELETE = (
    "StarRocks 3.5 explicit transactions accept only INSERT: UPDATE, DELETE and TRUNCATE inside BEGIN ... COMMIT "
    "fail with 'Explicit transaction only support begin/commit/rollback/insert/update/delete/set/select "
    "statements.' (error 5305)."
)
_NO_SAVEPOINT = (
    "StarRocks 3.5 has no SAVEPOINT statement: `SAVEPOINT sp1` fails with \"Unexpected input 'SAVEPOINT', the most "
    "similar input is {'EXPORT', 'EXPLAIN', 'ADMIN', 'BEGIN', 'GRANT', '(', ';'}\"."
)

STARROCKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS = {
    "transaction_commit_large": _NO_UPDATE_DELETE,
    "transaction_savepoint_nested": _NO_SAVEPOINT,
    "transaction_savepoint_deep_nesting": _NO_SAVEPOINT,
    "transaction_mixed_dml_small": _NO_UPDATE_DELETE,
    "transaction_mixed_dml_medium": _NO_UPDATE_DELETE,
    "transaction_insert_update_chain": _NO_UPDATE_DELETE,
    "transaction_delete_insert_same_key": _NO_UPDATE_DELETE,
    "transaction_read_your_writes": _NO_UPDATE_DELETE,
    "transaction_insert_with_subquery": (
        "StarRocks 3.5 explicit transactions cannot read a table written earlier in the same transaction: the "
        "INSERT ... SELECT fails with \"SELECT cannot read table 'txn_orders' modified earlier in the same "
        'transaction." (error 5307).'
    ),
    "transaction_long_running_mixed": _NO_UPDATE_DELETE,
    "transaction_truncate_in_transaction": _NO_UPDATE_DELETE,
    "transaction_create_temp_table": (
        "StarRocks 3.5 does not parse CREATE TEMP TABLE and rejects DDL inside an explicit transaction: the "
        "statement fails with \"No viable statement for input 'CREATE TEMP'\"."
    ),
    "transaction_with_cte": _NO_UPDATE_DELETE,
    "transaction_nested_subquery_updates": _NO_UPDATE_DELETE,
}

for _query_id, _reason in STARROCKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.starrocks.transaction_primitives.{_query_id}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(reason=_reason, query_id=_query_id),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        "starrocks",
        benchmark="transaction_primitives",
        query_id=_query_id,
    )
