from __future__ import annotations

from typing import Any

from benchbox.core.results.platform_timing import arrow_result_bytes
from benchbox.core.results.timer_scope import outside_query_timer


def dataframe_result_bytes(result: Any) -> int | None:
    with outside_query_timer():
        arrow_bytes = arrow_result_bytes(result)
        if arrow_bytes is not None:
            return arrow_bytes
        estimated_size = getattr(result, "estimated_size", None)
        if callable(estimated_size):
            return int(estimated_size())
        memory_usage = getattr(result, "memory_usage", None)
        if callable(memory_usage):
            usage = memory_usage(deep=True)
            total = usage.sum() if hasattr(usage, "sum") else usage
            return int(total)
    return None


def build_success_result_dict(
    query_id: str,
    execution_time_seconds: float,
    row_count: int,
    first_row: tuple[Any, ...] | None,
    request_count: int | None = None,
    result_bytes: int | None = None,
) -> dict[str, Any]:
    result = {
        "query_id": query_id,
        "status": "SUCCESS",
        "execution_time_seconds": execution_time_seconds,
        "rows_returned": row_count,
        "first_row": first_row,
    }
    if request_count is not None:
        result["request_count"] = request_count
    if result_bytes is not None:
        result["result_bytes"] = result_bytes
    return result


def build_failure_result_dict(
    query_id: str,
    execution_time_seconds: float,
    error_message: str,
) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "status": "FAILED",
        "execution_time_seconds": execution_time_seconds,
        "error": error_message,
    }
