from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_write_primitive_skips

_NO_RETURNING = (
    "Doris 4.0.3 has no RETURNING clause: the statement fails with \"mismatched input 'RETURNING' expecting {<EOF>, "
    "';'}\"."
)

DORIS_WRITE_PRIMITIVES_CATEGORY_SKIPS = {
    "bulk_load": (
        "Doris 4.0.3 has no SQL statement that reads a client-side CSV or Parquet file: `COPY t FROM '<path>'` fails "
        "with \"mismatched input ''<path>'' expecting {'(', '@'}\" and read_csv_auto/read_parquet are not "
        "functions; files load through Stream Load over HTTP or Broker/S3 Load."
    ),
}

DORIS_WRITE_PRIMITIVES_OPERATION_SKIPS = {
    "insert_returning_clause": _NO_RETURNING,
    "update_returning": _NO_RETURNING,
    "delete_returning": _NO_RETURNING,
    "merge_returning_clause": _NO_RETURNING,
    "merge_upsert_with_delete": (
        'Doris 4.0.3 MERGE INTO has no WHEN NOT MATCHED BY SOURCE clause: the statement fails with "mismatched '
        "input 'BY' expecting {'AND', 'THEN'}\"."
    ),
}


def doris_write_primitive_skip_reason(operation) -> str | None:
    operation_reason = DORIS_WRITE_PRIMITIVES_OPERATION_SKIPS.get(operation.id)
    if operation_reason is not None:
        return operation_reason
    return DORIS_WRITE_PRIMITIVES_CATEGORY_SKIPS.get(getattr(operation, "category", ""))


register_write_primitive_skips("doris", doris_write_primitive_skip_reason)
