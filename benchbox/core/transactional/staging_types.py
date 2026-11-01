from __future__ import annotations

import re

_QUESTDB_PARAMETERIZED_STRING = re.compile(r"^(?:VARCHAR|CHAR)\s*\(\s*\d+\s*\)$", re.IGNORECASE)
_QUESTDB_DECIMAL = re.compile(r"^(?:DECIMAL|NUMERIC)(?:\s*\(\s*\d+\s*(?:,\s*\d+\s*)?\))?$", re.IGNORECASE)


def translate_staging_column_type(type_str: str, dialect: str) -> str:
    if dialect.lower() == "questdb":
        stripped = type_str.strip()
        if _QUESTDB_PARAMETERIZED_STRING.match(stripped):
            return "VARCHAR"
        if _QUESTDB_DECIMAL.match(stripped):
            return "DOUBLE"
    return type_str


def staging_nullable_type(type_sql: str, dialect: str, nullable: bool) -> str:
    if nullable and dialect.lower() == "clickhouse":
        return f"Nullable({type_sql})"
    return type_sql


def staging_key_columns(table: dict) -> list[str]:
    composite = table.get("primary_key")
    if isinstance(composite, list) and composite:
        return [str(name) for name in composite]
    return [col["name"] for col in table["columns"] if col.get("primary_key")]


def staging_table_suffix(dialect: str, table: dict) -> str:
    if dialect.lower() != "clickhouse":
        return ""
    keys = staging_key_columns(table)
    return f" ENGINE = MergeTree ORDER BY ({', '.join(keys)})" if keys else " ENGINE = MergeTree ORDER BY tuple()"
