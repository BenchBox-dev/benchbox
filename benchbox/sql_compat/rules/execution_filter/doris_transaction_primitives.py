from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_NO_VALUES_AND_SELECT = (
    "Doris 4.0.3 explicit transactions cannot mix INSERT ... VALUES with UPDATE, DELETE or INSERT ... SELECT on the "
    "same table: the statement fails with 'Transaction insert can not insert into values and insert into select at "
    "the same time'."
)
_NO_SAVEPOINT = (
    "Doris 4.0.3 has no SAVEPOINT statement: `SAVEPOINT sp1` fails with \"mismatched input 'SAVEPOINT' expecting "
    "{<EOF>, ';', '(', 'ADMIN', 'ALTER', ...}\"."
)

DORIS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS = {
    "transaction_savepoint_nested": _NO_SAVEPOINT,
    "transaction_savepoint_deep_nesting": _NO_SAVEPOINT,
    "transaction_mixed_dml_small": _NO_VALUES_AND_SELECT,
    "transaction_delete_insert_same_key": _NO_VALUES_AND_SELECT,
    "transaction_insert_update_chain": _NO_VALUES_AND_SELECT,
    "transaction_insert_with_subquery": _NO_VALUES_AND_SELECT,
    "transaction_read_your_writes": _NO_VALUES_AND_SELECT,
    "transaction_multi_table_writes": (
        "Doris 4.0.3 explicit transactions write one table only: a second INSERT target fails with 'Only one table "
        "can be inserted in one transaction.'."
    ),
    "transaction_truncate_in_transaction": (
        "Doris 4.0.3 explicit transactions accept only INSERT, UPDATE, DELETE, COMMIT and ROLLBACK: TRUNCATE fails "
        "with 'This is in a transaction, only insert, update, delete, commit, rollback is acceptable.'."
    ),
    "transaction_create_temp_table": (
        "Doris 4.0.3 does not parse CREATE TEMP TABLE (`CREATE TEMPORARY TABLE` exists only outside a transaction, "
        "and DDL inside one fails with 'This is in a transaction, only insert, update, delete, commit, rollback is "
        "acceptable.'): the statement fails with \"no viable alternative at input 'CREATE TEMP'\"."
    ),
}

for _query_id, _reason in DORIS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.doris.transaction_primitives.{_query_id}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(reason=_reason, query_id=_query_id),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        "doris",
        benchmark="transaction_primitives",
        query_id=_query_id,
    )
