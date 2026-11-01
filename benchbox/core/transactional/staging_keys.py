from __future__ import annotations

import functools

_EXTRA_UNIQUE_KEYS = {"merge_ops_summary_target": ["o_orderkey", "o_custkey"]}


@functools.cache
def staging_primary_keys() -> dict[str, list[str]]:
    from benchbox.core.transaction_primitives.schema import STAGING_TABLES as TRANSACTION_TABLES
    from benchbox.core.write_primitives.schema import STAGING_TABLES as WRITE_TABLES

    keys: dict[str, list[str]] = {}
    for tables in (WRITE_TABLES, TRANSACTION_TABLES):
        for name, spec in tables.items():
            declared = spec.get("primary_key")
            if not isinstance(declared, list):
                declared = [column["name"] for column in spec["columns"] if column.get("primary_key")]
            if declared:
                keys[name.lower()] = [column.lower() for column in declared]
    return keys | _EXTRA_UNIQUE_KEYS


def leading_key_columns(table_name: str, columns: list[str]) -> list[str]:
    key = staging_primary_keys().get(table_name)
    if not key or not set(key) <= set(columns):
        return []
    return columns[: max(columns.index(column) for column in key) + 1]
