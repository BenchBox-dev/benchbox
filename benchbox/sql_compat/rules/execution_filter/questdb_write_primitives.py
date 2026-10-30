from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

QUESTDB_WRITE_PRIMITIVES_CATEGORY_SKIPS = {
    "delete": (
        "QuestDB 10.0.1 has no DELETE statement: `DELETE FROM t WHERE ...` fails with 'unexpected token [FROM]'."
    ),
    "merge": (
        "QuestDB 10.0.1 has no MERGE statement: `MERGE INTO t ...` fails with 'table and column names that are "
        'SQL keywords have to be enclosed in double quotes, such as "INTO"\'.'
    ),
    "bulk_load": (
        "QuestDB 10.0.1 resolves COPY and read_parquet paths under its server import root and rejects the catalog's "
        "`COPY ... WITH (FORMAT ...)` options with 'unexpected option'; read_csv_auto is an unknown function."
    ),
}

QUESTDB_WRITE_PRIMITIVES_OPERATION_SKIPS = {
    "insert_on_conflict_ignore": (
        "QuestDB 10.0.1 has no INSERT ... ON CONFLICT: the statement fails with 'unexpected token [ON]'."
    ),
    "insert_returning_clause": (
        "QuestDB 10.0.1 has no RETURNING clause on INSERT: the statement fails with \"',' expected\"."
    ),
    "update_returning": (
        "QuestDB 10.0.1 has no RETURNING clause on UPDATE: the statement fails with 'unexpected token [RETURNING]'."
    ),
    "update_with_subquery": (
        "QuestDB 10.0.1 rejects subqueries in UPDATE SET expressions with 'query is not allowed here'."
    ),
    "update_with_aggregate": (
        "QuestDB 10.0.1 rejects subqueries in UPDATE SET expressions with 'query is not allowed here'."
    ),
    "update_from_select": (
        "QuestDB 10.0.1 rejects correlated EXISTS subqueries that reference the UPDATE target with "
        "'Invalid table name or alias'."
    ),
    "ddl_create_table_with_constraints": (
        "QuestDB 10.0.1 does not parse PRIMARY KEY in CREATE TABLE: the statement fails with "
        "\"found [tok='PRIMARY', len=7] ',' or ')' expected\"."
    ),
    "ddl_create_table_with_index": (
        "QuestDB 10.0.1 has no CREATE INDEX statement (indexes are declared on SYMBOL columns with ALTER TABLE): "
        "the statement fails with \"'atomic' or 'table' or 'batch' expected\"."
    ),
    "ddl_create_index_on_existing": (
        "QuestDB 10.0.1 has no CREATE INDEX statement (indexes are declared on SYMBOL columns with ALTER TABLE): "
        "the statement fails with \"'atomic' or 'table' or 'batch' expected\"."
    ),
    "ddl_drop_index": (
        "QuestDB 10.0.1 has no CREATE INDEX or DROP INDEX statement (indexes are declared on SYMBOL columns with "
        "ALTER TABLE): the statement fails with \"'atomic' or 'table' or 'batch' expected\"."
    ),
}


def questdb_write_primitive_skip_reason(operation) -> str | None:
    category_reason = QUESTDB_WRITE_PRIMITIVES_CATEGORY_SKIPS.get(getattr(operation, "category", ""))
    if category_reason is not None:
        return category_reason
    return QUESTDB_WRITE_PRIMITIVES_OPERATION_SKIPS.get(operation.id)


def _register_skips() -> None:
    try:
        from benchbox.core.write_primitives.catalog import load_write_primitives_catalog

        catalog = load_write_primitives_catalog()
    except Exception:
        return

    for op_id, operation in catalog.operations.items():
        reason = questdb_write_primitive_skip_reason(operation)
        if reason is None:
            continue
        REGISTRY.register(
            CompatibilityDecision(
                rule_id=f"execution_filter.questdb.write_primitives.{op_id}",
                action=CompatAction.SKIP_QUERY,
                support_level=SupportLevel.SKIPPED_QUERY,
                failure_mode=FailureMode.UNSUPPORTED_FEATURE,
                payload=SkipQueryPayload(reason=reason, query_id=op_id),
                reason=reason,
            ),
            Phase.EXECUTION_FILTER,
            "questdb",
            benchmark="write_primitives",
            query_id=op_id,
        )


_register_skips()
