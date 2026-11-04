from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_COLUMNS: dict[str, dict[str, str]] = {}
_NULLABLE: set[str] = set()
_NOT_NULL: set[str] = set()


def register_benchmark_schema(tables: Iterable[Any]) -> None:
    for table in tables:
        columns = _COLUMNS.setdefault(table.name.lower(), {})
        for column in table.columns:
            name = column.name.lower()
            columns[name] = column.get_sql_type()
            (_NULLABLE if column.nullable else _NOT_NULL).add(name)


def registered_schema() -> tuple[dict[str, dict[str, str]], frozenset[str]]:
    return _COLUMNS, frozenset(_NOT_NULL - _NULLABLE)
