from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_NO_DELETE = "QuestDB 10.0.1 has no DELETE statement: `DELETE FROM t WHERE ...` fails with 'unexpected token [FROM]'."
_NO_SAVEPOINT = (
    "QuestDB 10.0.1 has no SAVEPOINT statement: `SAVEPOINT sp1` is parsed as a query and fails with "
    "'table does not exist [table=SAVEPOINT]'."
)
_NO_UPDATE_SUBQUERY = "QuestDB 10.0.1 rejects subqueries in UPDATE SET expressions with 'query is not allowed here'."

QUESTDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS = {
    "transaction_savepoint_nested": _NO_SAVEPOINT,
    "transaction_savepoint_deep_nesting": _NO_SAVEPOINT,
    "transaction_mixed_dml_small": _NO_DELETE,
    "transaction_mixed_dml_medium": _NO_DELETE,
    "transaction_delete_insert_same_key": _NO_DELETE,
    "transaction_long_running_mixed": _NO_DELETE,
    "transaction_read_your_writes": _NO_UPDATE_SUBQUERY,
    "transaction_nested_subquery_updates": _NO_UPDATE_SUBQUERY,
    "transaction_insert_with_subquery": (
        "QuestDB 10.0.1 does not show a transaction's uncommitted INSERTs to its own later statements: inside "
        "BEGIN, `INSERT INTO t VALUES (1, 'a'); SELECT COUNT(*) FROM t` returns 0, so INSERT ... SELECT over "
        "rows written earlier in the transaction inserts nothing."
    ),
    "transaction_truncate_in_transaction": (
        "QuestDB 10.0.1 cannot TRUNCATE a table that has pending transactional INSERTs: the statement fails with "
        "'table busy [reason=insert]'."
    ),
    "transaction_create_temp_table": (
        "QuestDB 10.0.1 has no temporary tables: `CREATE TEMP TABLE t AS ...` fails with "
        "\"'atomic' or 'table' or 'batch' expected\"."
    ),
}

for _query_id, _reason in QUESTDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.questdb.transaction_primitives.{_query_id}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(reason=_reason, query_id=_query_id),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        "questdb",
        benchmark="transaction_primitives",
        query_id=_query_id,
    )
