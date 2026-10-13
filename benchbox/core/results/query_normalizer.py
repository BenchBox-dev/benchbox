# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
from typing import Any

from benchbox.core.results.models import (
    QUERY_RUN_TYPE_MEASUREMENT,
    QueryExecution,
)
from benchbox.core.results.query_execution import query_execution_from_legacy_dict

_QUERY_WORD_PREFIX_PATTERN = re.compile(r"query_?(?=\d)", re.IGNORECASE)
_LEGACY_UPPER_Q_PREFIX_PATTERN = re.compile(r"Q(?=\d|[a-z])")
_LOWER_Q_PREFIX_PATTERN = re.compile(r"q(?=\d)")
_LEADING_PREFIX_PATTERNS = (
    _QUERY_WORD_PREFIX_PATTERN,
    _LEGACY_UPPER_Q_PREFIX_PATTERN,
    _LOWER_Q_PREFIX_PATTERN,
)


def _strip_leading_prefix(query_id: str) -> str:
    for pattern in _LEADING_PREFIX_PATTERNS:
        match = pattern.match(query_id)
        if match:
            return query_id[match.end() :]
    return query_id


def normalize_query_id(query_id: str | int) -> str:
    if isinstance(query_id, int):
        return str(query_id)

    normalized = _strip_leading_prefix(str(query_id).strip())
    normalized = normalized.strip()

    if "." in normalized:
        base, _, ext = normalized.rpartition(".")
        if ext.isalpha():
            normalized = base.strip()

    match = re.fullmatch(r"(\d+)([A-Za-z]+)?", normalized)
    if match:
        digits, suffix = match.groups()
        return f"{digits}{suffix.lower() if suffix else ''}"

    return normalized


def format_query_id(query_id: str | int, with_prefix: bool = True) -> str:
    normalized = normalize_query_id(query_id)
    if with_prefix:
        return f"Q{normalized}"
    return normalized


class QueryResultInput(QueryExecution):
    def __init__(
        self,
        query_id: str,
        execution_time_seconds: float | None,
        rows_returned: int | None,
        status: str,
        iteration: int | None = 1,
        stream_id: int | str | None = 0,
        run_type: str | None = QUERY_RUN_TYPE_MEASUREMENT,
        error_message: str | None = None,
        cost: float | None = None,
        row_count_validation: dict[str, Any] | None = None,
        dataframe_skip_summary: dict[str, Any] | None = None,
        query_plan: Any | None = None,
        plan_fingerprint: str | None = None,
        plan_fingerprint_normalized: str | None = None,
        plan_capture_time_ms: float | None = None,
        plan_capture_error: str | None = None,
        result_digest: str | None = None,
        test_type: str | None = None,
        error_type: str | None = None,
        resource_usage: dict[str, Any] | None = None,
        execution_order: int | None = None,
        execution_time_ms: float | None = None,
    ) -> None:
        super().__init__(
            query_id=query_id,
            stream_id=stream_id,
            execution_order=execution_order,
            execution_time_ms=execution_time_ms,
            execution_time_seconds=execution_time_seconds,
            status=status,
            rows_returned=rows_returned,
            resource_usage=resource_usage,
            error_message=error_message,
            iteration=iteration,
            run_type=run_type,
            row_count_validation=row_count_validation,
            cost=cost,
            query_plan=query_plan,
            plan_fingerprint=plan_fingerprint,
            plan_fingerprint_normalized=plan_fingerprint_normalized,
            plan_capture_time_ms=plan_capture_time_ms,
            plan_capture_error=plan_capture_error,
            dataframe_skip_summary=dataframe_skip_summary,
            result_digest=result_digest,
            test_type=test_type,
            error_type=error_type,
        )

    @classmethod
    def from_execution(cls, execution: QueryExecution) -> QueryResultInput:
        return cls(
            query_id=execution.query_id,
            execution_time_seconds=None,
            execution_time_ms=execution.execution_time_ms,
            rows_returned=execution.rows_returned,
            status=execution.status,
            iteration=execution.iteration,
            stream_id=execution.stream_id,
            run_type=execution.run_type,
            error_message=execution.error_message,
            cost=execution.cost,
            row_count_validation=execution.row_count_validation,
            dataframe_skip_summary=execution.dataframe_skip_summary,
            query_plan=execution.query_plan,
            plan_fingerprint=execution.plan_fingerprint,
            plan_fingerprint_normalized=execution.plan_fingerprint_normalized,
            plan_capture_time_ms=execution.plan_capture_time_ms,
            plan_capture_error=execution.plan_capture_error,
            result_digest=execution.result_digest,
            test_type=execution.test_type,
            error_type=execution.error_type,
            resource_usage=execution.resource_usage,
            execution_order=execution.execution_order,
        )


def normalize_query_result(
    raw_result: dict[str, Any],
    default_iteration: int = 1,
    default_stream_id: int = 0,
) -> QueryResultInput:
    return QueryResultInput.from_execution(
        query_execution_from_legacy_dict(
            raw_result,
            default_iteration=default_iteration,
            default_stream_id=default_stream_id,
            normalize_query_id=normalize_query_id,
        )
    )


def normalize_query_results(
    raw_results: list[dict[str, Any]],
    default_stream_id: int = 0,
) -> list[QueryResultInput]:
    normalized = []
    for i, raw in enumerate(raw_results, start=1):
        default_iter = raw.get("iteration", i)
        normalized.append(
            normalize_query_result(
                raw,
                default_iteration=default_iter,
                default_stream_id=default_stream_id,
            )
        )
    return normalized
