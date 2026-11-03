from __future__ import annotations

import re

from benchbox.sql_compat.rules._registration import register_write_primitive_skips

_NO_RETURNING = "StarRocks 3.5 has no RETURNING clause: the statement fails with \"Unexpected input 'RETURNING'\"."
_NO_MERGE = (
    "StarRocks 3.5 has no MERGE statement: `MERGE INTO ...` fails with \"Unexpected input 'MERGE', the most "
    "similar input is {'DESCRIBE', 'BEGIN', 'TRACE', 'DELETE', 'EXPORT', '(', ';'}\"."
)

STARROCKS_WRITE_PRIMITIVES_CATEGORY_SKIPS = {
    "bulk_load": (
        "StarRocks 3.5 has no SQL statement that reads a client-side CSV or Parquet file: `COPY t FROM '<path>'` "
        "fails with \"Unexpected input 'COPY'\"; files load through Stream Load over HTTP or Broker Load."
    ),
}

STARROCKS_WRITE_PRIMITIVES_OPERATION_SKIPS = {
    "insert_returning_clause": _NO_RETURNING,
    "update_returning": _NO_RETURNING,
    "delete_returning": _NO_RETURNING,
    "merge_returning_clause": _NO_RETURNING,
}

_MERGE_INTO_RE = re.compile(r"\bMERGE\s+INTO\b", re.IGNORECASE)


def starrocks_write_primitive_skip_reason(operation) -> str | None:
    operation_reason = STARROCKS_WRITE_PRIMITIVES_OPERATION_SKIPS.get(operation.id)
    if operation_reason is not None:
        return operation_reason
    category_reason = STARROCKS_WRITE_PRIMITIVES_CATEGORY_SKIPS.get(getattr(operation, "category", ""))
    if category_reason is not None:
        return category_reason
    return _NO_MERGE if _MERGE_INTO_RE.search(operation.write_sql) else None


register_write_primitive_skips("starrocks", starrocks_write_primitive_skip_reason)
