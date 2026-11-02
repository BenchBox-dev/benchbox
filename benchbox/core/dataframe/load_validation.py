from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from benchbox.core.dataframe.query_validation import DataFrameQueryValidationSummary
from benchbox.core.validation.expected_tables import expected_table_names

_LOADED_STATUSES = frozenset({"SUCCESS", "REUSED", "COMPLETED"})


def validate_dataframe_loaded_tables(
    table_stats: Mapping[str, int],
    per_table_stats: Mapping[str, Any],
    benchmark: Any,
    expected_tables: Sequence[str] | None = None,
) -> DataFrameQueryValidationSummary:
    loaded = {str(name).lower(): int(rows) for name, rows in table_stats.items()}
    expected = [str(name).lower() for name in expected_tables] if expected_tables else expected_table_names(benchmark)

    missing = sorted(name for name in (expected or ()) if name not in loaded)
    empty = sorted(name for name, rows in loaded.items() if rows <= 0)
    failed = sorted(
        str(name).lower()
        for name, stats in per_table_stats.items()
        if str(getattr(stats, "status", "SUCCESS")).upper() not in _LOADED_STATUSES
    )

    details: dict[str, Any] = {
        "provider": "loaded_table_checks",
        "total_tables_validated": len(loaded),
        "tables_with_data": len(loaded) - len(empty),
    }
    if missing:
        details["missing_tables"] = missing
    if empty:
        details["empty_tables"] = empty
    if failed:
        details["failed_tables"] = failed

    status = "FAILED" if (missing or empty or failed or not loaded) else "PASSED"
    if not loaded:
        details["reason"] = "no tables were loaded"
    return DataFrameQueryValidationSummary(status, details)


def combine_dataframe_validation(
    query_summary: DataFrameQueryValidationSummary,
    load_summary: DataFrameQueryValidationSummary | None,
) -> DataFrameQueryValidationSummary:
    if load_summary is None:
        return query_summary

    details = {**query_summary.details, "load": load_summary.details}
    statuses = {query_summary.status, load_summary.status}
    if "FAILED" in statuses:
        return DataFrameQueryValidationSummary("FAILED", details)
    if "PARTIAL" in statuses:
        return DataFrameQueryValidationSummary("PARTIAL", details)
    if query_summary.status == "NOT_RUN" and query_summary.details.get("provider") == "none":
        return DataFrameQueryValidationSummary(load_summary.status, details)
    return DataFrameQueryValidationSummary(query_summary.status, details)


def validate_managed_dataframe_tables(benchmark: Any, ctx: Any, adapter: Any) -> DataFrameQueryValidationSummary | None:
    hook = getattr(benchmark, "dataframe_loaded_tables", None)
    if not callable(hook):
        return None
    loaded = hook(ctx=ctx, adapter=adapter)
    if loaded is None:
        return None
    table_stats, expected_tables = loaded
    return validate_dataframe_loaded_tables(table_stats, {}, benchmark, expected_tables)
