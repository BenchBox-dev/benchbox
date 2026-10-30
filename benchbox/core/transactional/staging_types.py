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
