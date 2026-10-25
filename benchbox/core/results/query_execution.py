from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any, Callable

from benchbox.core.results.timing_fields import platform_timing_problems

if TYPE_CHECKING:
    from benchbox.core.results.models import QueryExecution

DURATION_CONSISTENCY_TOLERANCE_MS = 1.0
ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS = 500
ROW_COUNT_VALIDATION_STATUSES = frozenset({"PASSED", "FAILED", "SKIPPED", "ERROR"})
ROW_COUNT_VALIDATION_FIELDS = frozenset({"status", "expected", "actual", "error", "warning"})
ROW_COUNT_VALIDATION_REQUIRED_FIELDS = frozenset({"status", "expected", "actual"})

COMPACT_V2_QUERY_FIELDS = frozenset(
    {
        "id",
        "ms",
        "rows",
        "iter",
        "stream",
        "run_type",
        "test_type",
        "status",
        "digest",
        "row_count_validation",
        "dataframe_skip_summary",
        "plan_capture_error",
        "execution_engine",
        "request_count",
        "result_bytes",
        "platform_timing",
    }
)

LEGACY_IGNORED_EXTRA_FIELDS = frozenset(
    {
        "aborted",
        "aggregate_value",
        "avg_time",
        "bytes_billed",
        "bytes_per_second",
        "bytes_processed",
        "bytes_returned",
        "bytes_scanned",
        "cleanup_time",
        "columns",
        "comparison",
        "connection_id",
        "contained",
        "cost_estimated",
        "cost_usd",
        "cpu_time",
        "cpu_time_microsecs",
        "cv_percent",
        "description",
        "df_platform",
        "df_rows",
        "df_time_ms",
        "dry_run",
        "duration_microsecs",
        "execution_mode",
        "execution_only_time",
        "execution_sequence",
        "execution_time_original",
        "execution_time_variant",
        "execution_times_ms",
        "expected_sql",
        "fetch_time",
        "first_row",
        "function_id",
        "generated_sql",
        "generation_time_ms",
        "gpu_utilization_percent",
        "inputStages",
        "iterations",
        "job_id",
        "job_statistics",
        "maintenance_skipped",
        "match_type",
        "max_time",
        "max_time_ms",
        "mean_time_ms",
        "memory_peak",
        "memory_peak_mb",
        "memory_used_mb",
        "min_time",
        "min_time_ms",
        "name",
        "natural_language",
        "operation",
        "operation_type",
        "optimization_time",
        "outstanding_stream_ids",
        "p50_time_ms",
        "p95_time_ms",
        "parse_time",
        "peak_memory_mb",
        "performance_ratio",
        "platform",
        "platform_metrics",
        "platform_type",
        "position",
        "query_info",
        "query_name",
        "query_statistics",
        "query_text",
        "reason",
        "recordsRead",
        "recordsWritten",
        "records_loaded",
        "records_processed",
        "results",
        "results_match",
        "row_count",
        "rows_affected",
        "rows_per_second",
        "runtime_ms",
        "skip_reason",
        "slots",
        "speedup",
        "sql_platform",
        "sql_rows",
        "sql_text",
        "sql_time_ms",
        "std_time_ms",
        "steps",
        "suggestion",
        "table_name",
        "tables_accessed",
        "threshold",
        "thread_id",
        "timestamp",
        "timing_breakdown",
        "tokens_estimated",
        "tokens_used",
        "total_rows",
        "translated_query",
        "validation_passed",
        "validation_time",
        "variant_id",
        "warning_count",
        "wlm_slots",
    }
)

LEGACY_QUERY_FIELDS = frozenset(
    {
        "query_id",
        "id",
        "query",
        "status",
        "success",
        "execution_time_ms",
        "ms",
        "execution_time_seconds",
        "execution_time",
        "duration",
        "rows_returned",
        "rows",
        "result_count",
        "iteration",
        "iter",
        "stream_id",
        "stream",
        "execution_order",
        "position",
        "run_type",
        "runType",
        "is_warmup",
        "error_message",
        "error",
        "message",
        "error_type",
        "resource_usage",
        "row_count_validation",
        "cost",
        "query_plan",
        "plan_fingerprint",
        "plan_fingerprint_normalized",
        "plan_capture_time_ms",
        "plan_capture_error",
        "dataframe_skip_summary",
        "result_digest",
        "digest",
        "test_type",
        "execution_engine",
        "request_count",
        "result_bytes",
        "platform_timing",
    }
)


class QueryExecutionContractError(ValueError):
    pass


def _finite_non_negative_number(field: str, raw_value: Any) -> float:
    if isinstance(raw_value, bool):
        raise QueryExecutionContractError(f"{field} must be numeric, got {raw_value!r}")
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise QueryExecutionContractError(f"{field} must be numeric, got {raw_value!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise QueryExecutionContractError(f"{field} must be a finite non-negative number, got {raw_value!r}")
    return value


def normalize_duration_ms(
    *,
    execution_time_ms: Any = None,
    execution_time_seconds: Any = None,
    execution_time: Any = None,
    duration: Any = None,
) -> float | None:
    normalized: list[tuple[str, float]] = []
    for field, raw_value, multiplier in (
        ("execution_time_ms", execution_time_ms, 1.0),
        ("execution_time_seconds", execution_time_seconds, 1000.0),
        ("execution_time", execution_time, 1000.0),
        ("duration", duration, 1000.0),
    ):
        if raw_value is None:
            continue
        normalized.append((field, _finite_non_negative_number(field, raw_value) * multiplier))

    if not normalized:
        return None

    reference_field, reference_ms = normalized[0]
    for field, value_ms in normalized[1:]:
        if not math.isclose(
            reference_ms,
            value_ms,
            rel_tol=0.0,
            abs_tol=DURATION_CONSISTENCY_TOLERANCE_MS,
        ):
            raise QueryExecutionContractError(
                "Conflicting query duration representations: "
                f"{reference_field}={reference_ms} ms, {field}={value_ms} ms"
            )

    for preferred_field in ("execution_time_seconds", "execution_time", "duration"):
        for field, value_ms in normalized:
            if field == preferred_field:
                return value_ms
    return reference_ms


def _legacy_duration_ms(source: Mapping[str, Any]) -> float | None:
    duration_ms_alias = _resolve_alias(
        source,
        "millisecond duration",
        ("execution_time_ms", "ms"),
        transform=lambda raw: _finite_non_negative_number("execution_time_ms", raw),
    )
    return normalize_duration_ms(
        execution_time_ms=duration_ms_alias,
        execution_time_seconds=source.get("execution_time_seconds"),
        execution_time=source.get("execution_time"),
        duration=source.get("duration"),
    )


def query_duration_ms_from_legacy(value: Any) -> float | None:
    return _legacy_duration_ms(legacy_query_execution_mapping(value))


def _resolve_alias(
    source: Mapping[str, Any],
    semantic_name: str,
    aliases: tuple[str, ...],
    *,
    transform: Callable[[Any], Any] | None = None,
) -> Any:
    values: list[tuple[str, Any]] = []
    for alias in aliases:
        raw_value = source.get(alias)
        if raw_value is None:
            continue
        value = transform(raw_value) if transform is not None else raw_value
        values.append((alias, value))

    if not values:
        return None

    canonical_alias, canonical_value = values[0]
    for alias, value in values[1:]:
        if value != canonical_value:
            raise QueryExecutionContractError(
                f"Conflicting {semantic_name} representations: {canonical_alias}={canonical_value!r}, {alias}={value!r}"
            )
    return canonical_value


def _coerce_integer(field: str, raw_value: Any) -> int:
    if isinstance(raw_value, bool):
        raise QueryExecutionContractError(f"{field} must be an integer, got {raw_value!r}")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise QueryExecutionContractError(f"{field} must be an integer, got {raw_value!r}") from exc
    try:
        numeric = float(raw_value)
    except (TypeError, ValueError):
        numeric = float(value)
    if not math.isfinite(numeric) or numeric != value:
        raise QueryExecutionContractError(f"{field} must be an integer, got {raw_value!r}")
    return value


def normalize_non_negative_integer(field: str, raw_value: Any) -> int:
    value = _coerce_integer(field, raw_value)
    if value < 0:
        raise QueryExecutionContractError(f"{field} must be non-negative, got {raw_value!r}")
    return value


def normalize_platform_timing(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    problems = platform_timing_problems(value)
    if problems:
        raise QueryExecutionContractError(f"platform_timing {problems[0]}")
    return dict(value)


def normalize_row_count_validation(value: Any, *, rows_returned: int | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise QueryExecutionContractError("row_count_validation must be an object")

    unknown = set(value) - ROW_COUNT_VALIDATION_FIELDS
    if unknown:
        raise QueryExecutionContractError(f"Unknown row_count_validation fields: {sorted(unknown)!r}")
    missing = ROW_COUNT_VALIDATION_REQUIRED_FIELDS - set(value)
    if missing:
        raise QueryExecutionContractError(f"row_count_validation missing fields: {sorted(missing)!r}")

    raw_status = value.get("status")
    if not isinstance(raw_status, str):
        raise QueryExecutionContractError("row_count_validation.status must be a string")
    status = raw_status.strip().upper()
    if status not in ROW_COUNT_VALIDATION_STATUSES:
        raise QueryExecutionContractError(f"Unknown row_count_validation.status: {raw_status!r}")

    counts: dict[str, int | None] = {}
    for field in ("expected", "actual"):
        raw_count = value.get(field)
        counts[field] = (
            None if raw_count is None else normalize_non_negative_integer(f"row_count_validation.{field}", raw_count)
        )

    if counts["actual"] != rows_returned:
        raise QueryExecutionContractError(
            f"row_count_validation.actual ({counts['actual']!r}) must match rows_returned ({rows_returned!r})"
        )
    if status == "PASSED" and counts["expected"] is None:
        status = "SKIPPED"
    elif status == "PASSED" and counts["actual"] != counts["expected"]:
        raise QueryExecutionContractError(
            "PASSED row_count_validation requires equal integer expected and actual counts"
        )

    normalized: dict[str, Any] = {"status": status, **counts}
    for field in ("error", "warning"):
        if field not in value:
            continue
        message = value[field]
        if not isinstance(message, str):
            raise QueryExecutionContractError(f"row_count_validation.{field} must be a string")
        if len(message) > ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS:
            raise QueryExecutionContractError(
                f"row_count_validation.{field} exceeds {ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS} characters"
            )
        normalized[field] = message
    return normalized


def normalize_status(raw_status: Any) -> str:
    if raw_status is None:
        return "UNKNOWN"
    if isinstance(raw_status, bool):
        raise QueryExecutionContractError(f"status must be a string, got {raw_status!r}")
    status = str(raw_status)
    upper = status.upper()
    if upper in {"SUCCESS", "SUCCEEDED", "OK", "PASS", "PASSED"}:
        return "SUCCESS"
    if upper in {"FAILED", "FAIL", "ERROR"}:
        return "FAILED"
    return status


def normalize_stream_id(raw_value: Any) -> str | int | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, bool):
        raise QueryExecutionContractError(f"stream_id must be a string or non-negative integer, got {raw_value!r}")
    if isinstance(raw_value, int):
        return normalize_non_negative_integer("stream_id", raw_value)
    if isinstance(raw_value, float):
        return normalize_non_negative_integer("stream_id", raw_value)
    return str(raw_value)


def validate_query_execution(execution: QueryExecution) -> QueryExecution:
    from benchbox.core.results.models import QueryExecution

    if not isinstance(execution, QueryExecution):
        raise QueryExecutionContractError(f"Expected QueryExecution, got {type(execution).__name__}")
    return QueryExecution(
        query_id=execution.query_id,
        stream_id=execution.stream_id,
        execution_order=execution.execution_order,
        execution_time_ms=execution.execution_time_ms,
        status=execution.status,
        rows_returned=execution.rows_returned,
        resource_usage=execution.resource_usage,
        error_message=execution.error_message,
        iteration=execution.iteration,
        run_type=execution.run_type,
        row_count_validation=execution.row_count_validation,
        cost=execution.cost,
        query_plan=execution.query_plan,
        plan_fingerprint=execution.plan_fingerprint,
        plan_fingerprint_normalized=execution.plan_fingerprint_normalized,
        plan_capture_time_ms=execution.plan_capture_time_ms,
        plan_capture_error=execution.plan_capture_error,
        dataframe_skip_summary=execution.dataframe_skip_summary,
        result_digest=execution.result_digest,
        test_type=execution.test_type,
        error_type=execution.error_type,
        execution_engine=execution.execution_engine,
        request_count=execution.request_count,
        result_bytes=execution.result_bytes,
        platform_timing=execution.platform_timing,
    )


def legacy_query_execution_mapping(value: Any) -> Mapping[str, Any]:
    from benchbox.core.results.models import QueryExecution

    if isinstance(value, QueryExecution):
        return query_execution_to_legacy_dict(value)
    if isinstance(value, Mapping):
        return _validate_legacy_mapping(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _validate_legacy_mapping(asdict(value))
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, Mapping):
            return _validate_legacy_mapping(dumped)
    if hasattr(value, "dict"):
        dumped = value.dict()
        if isinstance(dumped, Mapping):
            return _validate_legacy_mapping(dumped)
    contract_fields = (
        "query_id",
        "id",
        "query",
        "status",
        "execution_time_ms",
        "execution_time_seconds",
        "execution_time",
        "duration",
        "rows_returned",
        "rows",
        "result_count",
        "iteration",
        "iter",
        "stream_id",
        "stream",
        "execution_order",
        "run_type",
        "runType",
        "is_warmup",
        "error_message",
        "error",
        "message",
        "error_type",
        "resource_usage",
        "row_count_validation",
        "cost",
        "query_plan",
        "plan_fingerprint",
        "plan_fingerprint_normalized",
        "plan_capture_time_ms",
        "plan_capture_error",
        "dataframe_skip_summary",
        "result_digest",
        "digest",
        "test_type",
        "execution_engine",
        "request_count",
        "result_bytes",
        "platform_timing",
    )
    extracted = {field: getattr(value, field) for field in contract_fields if hasattr(value, field)}
    if extracted:
        return extracted
    raise QueryExecutionContractError(f"Unsupported query result type: {type(value).__name__}")


def _validate_legacy_mapping(source: Mapping[str, Any]) -> Mapping[str, Any]:
    unknown = set(source) - LEGACY_QUERY_FIELDS - LEGACY_IGNORED_EXTRA_FIELDS
    if unknown:
        raise QueryExecutionContractError(f"Unknown legacy query-result fields: {sorted(unknown)!r}")
    return source


def query_execution_from_legacy_dict(
    value: Any,
    *,
    default_iteration: int | None = None,
    default_stream_id: int | str | None = None,
    normalize_query_id: Callable[[str | int], str] | None = None,
) -> QueryExecution:
    from benchbox.core.results.models import (
        QUERY_RUN_TYPE_MEASUREMENT,
        QUERY_RUN_TYPE_WARMUP,
        QueryExecution,
    )

    if isinstance(value, QueryExecution):
        return validate_query_execution(value)
    source = legacy_query_execution_mapping(value)

    raw_query_id = _resolve_alias(source, "query identity", ("query_id", "id", "query"), transform=str)
    query_id = "" if raw_query_id is None else raw_query_id
    if normalize_query_id is not None:
        query_id = normalize_query_id(query_id)

    duration_ms = _legacy_duration_ms(source)
    rows_returned = _resolve_alias(
        source,
        "row count",
        ("rows_returned", "rows", "result_count"),
        transform=lambda raw: normalize_non_negative_integer("rows_returned", raw),
    )
    iteration = _resolve_alias(
        source,
        "iteration",
        ("iteration", "iter"),
        transform=lambda raw: normalize_non_negative_integer("iteration", raw),
    )
    if iteration is None:
        iteration = default_iteration
    stream_id = _resolve_alias(source, "stream", ("stream_id", "stream"), transform=normalize_stream_id)
    if stream_id is None:
        stream_id = default_stream_id

    status_value = source.get("status")
    success_value = source.get("success")
    if success_value is not None:
        if not isinstance(success_value, bool):
            raise QueryExecutionContractError(f"success must be a boolean, got {success_value!r}")
        success_status = "SUCCESS" if success_value else "FAILED"
        if status_value is not None and normalize_status(status_value) != success_status:
            raise QueryExecutionContractError(
                f"Conflicting status representations: status={status_value!r}, success={success_value!r}"
            )
        status_value = success_status
    status = normalize_status("UNKNOWN" if status_value is None else status_value)
    run_type = _resolve_alias(source, "run type", ("run_type", "runType"), transform=str)
    if run_type is None:
        if source.get("is_warmup") is True:
            run_type = QUERY_RUN_TYPE_WARMUP
        elif iteration is not None:
            run_type = QUERY_RUN_TYPE_WARMUP if iteration == 0 else QUERY_RUN_TYPE_MEASUREMENT

    error_message = _resolve_alias(
        source,
        "error message",
        ("error_message", "error", "message"),
        transform=str,
    )
    result_digest = _resolve_alias(
        source,
        "result digest",
        ("result_digest", "digest"),
        transform=str,
    )

    return QueryExecution(
        query_id=query_id,
        stream_id=stream_id,
        execution_order=(
            _resolve_alias(
                source,
                "execution order",
                ("execution_order",),
                transform=lambda raw: normalize_non_negative_integer("execution_order", raw),
            )
        ),
        execution_time_ms=duration_ms,
        status=status,
        rows_returned=rows_returned,
        resource_usage=source.get("resource_usage"),
        error_message=error_message,
        iteration=iteration,
        run_type=run_type,
        row_count_validation=source.get("row_count_validation"),
        cost=source.get("cost"),
        query_plan=source.get("query_plan"),
        plan_fingerprint=source.get("plan_fingerprint"),
        plan_fingerprint_normalized=source.get("plan_fingerprint_normalized"),
        plan_capture_time_ms=source.get("plan_capture_time_ms"),
        plan_capture_error=source.get("plan_capture_error"),
        dataframe_skip_summary=source.get("dataframe_skip_summary"),
        result_digest=result_digest,
        test_type=source.get("test_type"),
        error_type=source.get("error_type"),
        execution_engine=source.get("execution_engine"),
        request_count=source.get("request_count"),
        result_bytes=source.get("result_bytes"),
        platform_timing=source.get("platform_timing"),
    )


def query_execution_to_legacy_dict(
    execution: QueryExecution,
    *,
    include_milliseconds: bool = True,
    include_seconds: bool = False,
    include_legacy_seconds_alias: bool = False,
    error_field: str = "error_message",
) -> dict[str, Any]:
    if error_field not in {"error_message", "error"}:
        raise ValueError(f"Unsupported error field: {error_field!r}")
    execution = validate_query_execution(execution)
    result: dict[str, Any] = {"query_id": execution.query_id, "status": execution.status}
    if execution.execution_time_ms is not None:
        if include_milliseconds:
            result["execution_time_ms"] = execution.execution_time_ms
        if include_seconds:
            result["execution_time_seconds"] = execution.execution_time_ms / 1000.0
        if include_legacy_seconds_alias:
            result["execution_time"] = execution.execution_time_ms / 1000.0

    optional_fields = (
        "rows_returned",
        "iteration",
        "stream_id",
        "run_type",
        "execution_order",
        "resource_usage",
        "error_type",
        "row_count_validation",
        "cost",
        "query_plan",
        "plan_fingerprint",
        "plan_fingerprint_normalized",
        "plan_capture_time_ms",
        "plan_capture_error",
        "dataframe_skip_summary",
        "result_digest",
        "test_type",
        "execution_engine",
        "request_count",
        "result_bytes",
        "platform_timing",
    )
    for field in optional_fields:
        value = getattr(execution, field)
        if value is not None:
            result[field] = value
    if execution.error_message is not None:
        result[error_field] = execution.error_message
    return result


def query_execution_from_compact_v2(value: Mapping[str, Any]) -> QueryExecution:
    unknown = set(value) - COMPACT_V2_QUERY_FIELDS
    if unknown:
        raise QueryExecutionContractError(f"Unknown compact schema-v2 query fields: {sorted(unknown)!r}")
    source = dict(value)
    source.setdefault("status", "SUCCESS")
    execution = query_execution_from_legacy_dict(source)
    execution.row_count_validation = normalize_row_count_validation(
        execution.row_count_validation,
        rows_returned=execution.rows_returned,
    )
    return execution


def query_execution_to_compact_v2(execution: QueryExecution) -> dict[str, Any]:
    execution = validate_query_execution(execution)
    result: dict[str, Any] = {"id": execution.query_id}
    if execution.execution_time_ms is not None:
        result["ms"] = execution.execution_time_ms
    if execution.rows_returned is not None:
        result["rows"] = execution.rows_returned
    if execution.iteration is not None:
        result["iter"] = execution.iteration
    if execution.stream_id is not None:
        result["stream"] = execution.stream_id
    if execution.run_type is not None:
        result["run_type"] = execution.run_type
    if execution.test_type is not None:
        result["test_type"] = execution.test_type
    result["status"] = execution.status
    if execution.execution_engine is not None:
        result["execution_engine"] = execution.execution_engine
    if execution.result_digest is not None:
        result["digest"] = execution.result_digest
    row_count_validation = normalize_row_count_validation(
        execution.row_count_validation,
        rows_returned=execution.rows_returned,
    )
    if row_count_validation is not None:
        result["row_count_validation"] = row_count_validation
    if execution.dataframe_skip_summary is not None:
        result["dataframe_skip_summary"] = execution.dataframe_skip_summary
    if execution.plan_capture_error is not None:
        result["plan_capture_error"] = execution.plan_capture_error
    for field in ("request_count", "result_bytes", "platform_timing"):
        value = getattr(execution, field)
        if value is not None:
            result[field] = value
    return result


__all__ = [
    "COMPACT_V2_QUERY_FIELDS",
    "DURATION_CONSISTENCY_TOLERANCE_MS",
    "LEGACY_IGNORED_EXTRA_FIELDS",
    "LEGACY_QUERY_FIELDS",
    "QueryExecutionContractError",
    "legacy_query_execution_mapping",
    "normalize_duration_ms",
    "normalize_non_negative_integer",
    "normalize_platform_timing",
    "normalize_row_count_validation",
    "normalize_status",
    "normalize_stream_id",
    "query_duration_ms_from_legacy",
    "query_execution_from_compact_v2",
    "query_execution_from_legacy_dict",
    "query_execution_to_compact_v2",
    "query_execution_to_legacy_dict",
    "validate_query_execution",
]
