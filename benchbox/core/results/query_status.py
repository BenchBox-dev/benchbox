from __future__ import annotations

from typing import Any


def bundle_failed_query_count(data: dict[str, Any]) -> int:
    summary = data.get("summary")
    if isinstance(summary, dict):
        queries = summary.get("queries")
        if isinstance(queries, dict):
            failed = int_or_none(queries.get("failed"))
            if failed is not None and failed > 0:
                return failed

            total = int_or_none(queries.get("total"))
            passed = int_or_none(queries.get("passed"))
            skipped = int_or_none(queries.get("skipped")) or 0
            if total is not None and passed is not None and total > passed + skipped:
                return max(total - passed - skipped, 0)

    query_rows = data.get("queries")
    if isinstance(query_rows, list):
        return sum(1 for query in query_rows if _query_row_failed(query))
    return 0


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def has_failed_query_validation(query_rows: Any) -> bool:
    if not isinstance(query_rows, list):
        return False
    return any(
        isinstance(query, dict)
        and isinstance(evidence := query.get("row_count_validation"), dict)
        and str(evidence.get("status") or "").strip().upper() == "FAILED"
        for query in query_rows
    )


def _query_row_failed(query: Any) -> bool:
    if not isinstance(query, dict):
        return False
    run_type = str(query.get("run_type") or "measurement").strip().lower()
    if run_type != "measurement":
        return False
    status = query.get("status")
    return status is not None and str(status).upper() not in {"SUCCESS", "SKIPPED"}
