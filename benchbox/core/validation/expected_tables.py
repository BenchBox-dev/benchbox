from __future__ import annotations

from typing import Any


def expected_table_names(benchmark: Any) -> list[str] | None:
    try:
        if hasattr(benchmark, "get_schema") and callable(benchmark.get_schema):
            schema = benchmark.get_schema()
            if isinstance(schema, list) and schema and isinstance(schema[0], dict) and "name" in schema[0]:
                return [str(t["name"]).lower() for t in schema]
            if isinstance(schema, list) and schema and not isinstance(schema[0], dict):
                return [str(t).lower() for t in schema]
            if isinstance(schema, dict):
                return [str(t).lower() for t in schema]
    except Exception:
        pass
    try:
        if hasattr(benchmark, "get_available_tables") and callable(benchmark.get_available_tables):
            return [str(t).lower() for t in benchmark.get_available_tables()]
        if hasattr(benchmark, "get_table_names") and callable(benchmark.get_table_names):
            return [str(t).lower() for t in benchmark.get_table_names()]
    except Exception:
        pass
    if hasattr(benchmark, "tables") and benchmark.tables and hasattr(benchmark.tables, "keys"):
        try:
            return [str(t).lower() for t in benchmark.tables.keys()]
        except (TypeError, AttributeError):
            pass
    return None
