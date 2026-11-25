from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_ONE_DML_PER_TABLE = (
    "StarRocks 4.1 allows one DML statement per table in an explicit transaction: a second statement against "
    "the same table fails with 'NOT allowed to read or write tables that have been subjected to DML operations "
    "before' (error 5303)."
)
_NO_SAVEPOINT = (
    "StarRocks 4.1 has no SAVEPOINT statement: `SAVEPOINT sp1` fails with \"Unexpected input 'SAVEPOINT', the most "
    "similar input is {'EXPORT', 'EXPLAIN', 'ADMIN', 'BEGIN', 'GRANT', '(', ';'}\"."
)

STARROCKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS = {
    "transaction_commit_small": _ONE_DML_PER_TABLE,
    "transaction_rollback_small": _ONE_DML_PER_TABLE,
    "transaction_isolation_read_committed": _ONE_DML_PER_TABLE,
    "transaction_isolation_repeatable_read": _ONE_DML_PER_TABLE,
    "transaction_isolation_serializable": _ONE_DML_PER_TABLE,
    "transaction_multi_table_writes": _ONE_DML_PER_TABLE,
    "transaction_commit_large": _ONE_DML_PER_TABLE,
    "transaction_savepoint_nested": _NO_SAVEPOINT,
    "transaction_savepoint_deep_nesting": _NO_SAVEPOINT,
    "transaction_mixed_dml_small": _ONE_DML_PER_TABLE,
    "transaction_mixed_dml_medium": _ONE_DML_PER_TABLE,
    "transaction_insert_update_chain": _ONE_DML_PER_TABLE,
    "transaction_delete_insert_same_key": _ONE_DML_PER_TABLE,
    "transaction_read_your_writes": _ONE_DML_PER_TABLE,
    "transaction_insert_with_subquery": (
        "StarRocks 4.1 explicit transactions cannot read a table written earlier in the same transaction: the "
        "INSERT ... SELECT fails with \"SELECT cannot read table 'txn_orders' modified earlier in the same "
        'transaction." (error 5307).'
    ),
    "transaction_long_running_mixed": _ONE_DML_PER_TABLE,
    "transaction_truncate_in_transaction": (
        "StarRocks 4.1 rejects TRUNCATE inside an explicit transaction: the statement fails with 'Explicit "
        "transaction only support begin/commit/rollback/insert/update/delete/set/select/show statements.' "
        "(error 5305)."
    ),
    "transaction_create_temp_table": (
        "StarRocks 4.1 does not parse CREATE TEMP TABLE and rejects DDL inside an explicit transaction: the "
        "statement fails with \"No viable statement for input 'CREATE TEMP'\"."
    ),
    "transaction_with_cte": (
        'StarRocks 4.1 does not parse WITH before INSERT: the statement fails with "No viable statement for input '
        "'WITH order_data AS\". With the CTE moved after INSERT INTO, the UPDATE of the same table would still "
        "exceed one DML statement per table in an explicit transaction."
    ),
    "transaction_nested_subquery_updates": _ONE_DML_PER_TABLE,
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
