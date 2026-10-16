from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from benchbox.core.expected_results.models import ValidationMode, ValidationResult
from benchbox.core.expected_results.ssb_results import normalize_ssb_query_id, ssb_expected_row_counts
from benchbox.core.validation.query_validation import QueryValidator

_MAX_EVIDENCE_MESSAGE_CHARS = 500


@dataclass(frozen=True)
class DataFrameQueryValidationSummary:
    status: str
    details: dict[str, Any]


def validate_dataframe_query_results(
    query_results: list[dict[str, Any]],
    *,
    benchmark_name: str,
    scale_factor: float,
    validation_mode: str | None = None,
    seed: int | None = None,
) -> DataFrameQueryValidationSummary:

    benchmark_id = _normalize_benchmark_name(benchmark_name)
    mode = str(validation_mode or "exact").strip().lower()
    evidence = {
        "provider": "tpc_expected_row_counts",
        "benchmark": benchmark_id,
        "checked": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
    }

    measurement_rows = [row for row in query_results if _is_measurement(row)]
    execution_failures = sum(1 for row in measurement_rows if _is_execution_failure(row))
    selected_skips = sum(
        1
        for row in query_results
        if str(row.get("status", "")).upper() == "SKIPPED"
        and str(row.get("run_type") or "measurement").lower() != "warmup"
        and str(row.get("query_id", "")).upper() != "DF_SKIP_SUMMARY"
    )

    if mode == "disabled":
        evidence["reason"] = "validation disabled by run configuration"
        return DataFrameQueryValidationSummary("PARTIAL" if execution_failures else "NOT_RUN", evidence)

    successful_rows = [row for row in measurement_rows if str(row.get("status", "")).upper() == "SUCCESS"]
    if mode != "exact":
        reason = f"DataFrame publication evidence does not support validation mode {mode!r}"
        for row in successful_rows:
            _attach_skipped_validation(row, reason)
        evidence["skipped"] = len(successful_rows) + selected_skips
        evidence["reason"] = reason
        status = _unavailable_status(execution_failures, bool(successful_rows or selected_skips))
        return DataFrameQueryValidationSummary(status, evidence)

    if benchmark_id not in {"tpch", "tpcds", "ssb"}:
        evidence["provider"] = "none"
        evidence["reason"] = "no production DataFrame oracle is registered for this benchmark"
        return DataFrameQueryValidationSummary("PARTIAL" if execution_failures else "NOT_RUN", evidence)

    ssb_counts = ssb_expected_row_counts(scale_factor) if benchmark_id == "ssb" else None
    if benchmark_id == "ssb":
        evidence["provider"] = "ssb_expected_row_counts"
    reason = _unavailable_oracle_reason(benchmark_id, scale_factor, seed, ssb_counts)
    if reason:
        for row in successful_rows:
            _attach_skipped_validation(row, reason)
        evidence["skipped"] = len(successful_rows) + selected_skips
        evidence["reason"] = reason
        status = _unavailable_status(execution_failures, bool(successful_rows or selected_skips))
        return DataFrameQueryValidationSummary(status, evidence)

    initialization_error = None
    if ssb_counts is not None:
        _apply_row_count_oracle(
            successful_rows, lambda query_id, actual: _validate_ssb_row_count(ssb_counts, query_id, actual), evidence
        )
    else:
        initialization_error = _apply_expected_row_counts(successful_rows, benchmark_id, scale_factor, evidence)
    if initialization_error:
        evidence["reason"] = initialization_error
        status = _unavailable_status(execution_failures, bool(successful_rows))
        return DataFrameQueryValidationSummary(status, evidence)

    evidence["skipped"] += selected_skips
    if evidence["failed"]:
        status = "FAILED"
    elif execution_failures:
        status = "PARTIAL"
    elif evidence["skipped"] or evidence["errors"]:
        status = "UNCERTAIN"
    elif evidence["checked"] and evidence["checked"] == len(successful_rows):
        status = "PASSED"
    else:
        status = "NOT_RUN"
    return DataFrameQueryValidationSummary(status, evidence)


def _normalize_benchmark_name(value: str) -> str:
    return str(value).strip().lower().replace("tpc-h", "tpch").replace("tpc-ds", "tpcds")


def _unavailable_status(execution_failures: int, has_unavailable_evidence: bool) -> str:
    if execution_failures:
        return "PARTIAL"
    if has_unavailable_evidence:
        return "UNCERTAIN"
    return "NOT_RUN"


def _unavailable_oracle_reason(
    benchmark_id: str,
    scale_factor: float,
    seed: int | None,
    ssb_counts: dict[str, int] | None,
) -> str | None:
    if benchmark_id == "tpcds":
        return "TPC-DS expected row counts are not seed-aligned with this DataFrame run"
    if benchmark_id == "ssb":
        if ssb_counts is None:
            return f"SSB expected row counts are not recorded for scale factor {scale_factor}"
        return None
    if not _tpch_reference_context(scale_factor, seed):
        return "TPC-H answer-file row counts apply only to SF 1 runs with qgen default parameters (no seed)"
    return None


def _validate_ssb_row_count(counts: dict[str, int], query_id: str, actual_row_count: int) -> ValidationResult:
    expected = counts.get(normalize_ssb_query_id(query_id))
    if expected is None:
        return ValidationResult(
            is_valid=True,
            query_id=query_id,
            expected_row_count=None,
            actual_row_count=actual_row_count,
            validation_mode=ValidationMode.SKIP,
            warning_message=f"No expected SSB row count is recorded for query {query_id!r}",
        )
    is_valid = actual_row_count == expected
    return ValidationResult(
        is_valid=is_valid,
        query_id=query_id,
        expected_row_count=expected,
        actual_row_count=actual_row_count,
        validation_mode=ValidationMode.EXACT,
        error_message=None
        if is_valid
        else f"Query {query_id} returned {actual_row_count} rows; expected {expected} for this scale factor",
    )


def _apply_expected_row_counts(
    successful_rows: list[dict[str, Any]],
    benchmark_id: str,
    scale_factor: float,
    evidence: dict[str, Any],
) -> str | None:

    try:
        validator = QueryValidator()
    except Exception as exc:
        message = _bounded_message(f"Row-count oracle failed to initialize: {type(exc).__name__}: {exc}")
        for row in successful_rows:
            row["row_count_validation"] = {
                "status": "ERROR",
                "expected": None,
                "actual": row.get("rows_returned"),
                "error": message,
            }
        evidence["errors"] = len(successful_rows)
        return message

    _apply_row_count_oracle(
        successful_rows,
        lambda query_id, actual: validator.validate_query_result(
            benchmark_type=benchmark_id,
            query_id=query_id,
            actual_row_count=actual,
            scale_factor=scale_factor,
        ),
        evidence,
    )
    return None


def _apply_row_count_oracle(
    successful_rows: list[dict[str, Any]],
    validate: Callable[[str, int], ValidationResult],
    evidence: dict[str, Any],
) -> None:
    for row in successful_rows:
        actual_row_count = row.get("rows_returned")
        if isinstance(actual_row_count, bool) or not isinstance(actual_row_count, int) or actual_row_count < 0:
            message = "Successful DataFrame query did not report a valid non-negative rows_returned value"
            _attach_validation_error(row, message)
            evidence["failed"] += 1
            evidence["errors"] += 1
            continue

        try:
            result = validate(str(row.get("query_id", "")), actual_row_count)
        except Exception as exc:
            message = _bounded_message(f"Row-count oracle failed: {type(exc).__name__}: {exc}")
            row["row_count_validation"] = {
                "status": "ERROR",
                "expected": None,
                "actual": actual_row_count,
                "error": message,
            }
            evidence["errors"] += 1
            continue

        row_count_validation: dict[str, Any] = {
            "expected": result.expected_row_count,
            "actual": actual_row_count,
        }
        if result.validation_mode == ValidationMode.SKIP:
            row_count_validation["status"] = "SKIPPED"
            if result.warning_message:
                row_count_validation["warning"] = _bounded_message(result.warning_message)
            evidence["skipped"] += 1
        elif result.is_valid:
            row_count_validation["status"] = "PASSED"
            evidence["checked"] += 1
            evidence["passed"] += 1
        else:
            message = _bounded_message(result.error_message or "Row-count validation failed")
            row_count_validation["status"] = "FAILED"
            row_count_validation["error"] = message
            row["status"] = "FAILED"
            row["error"] = message
            evidence["checked"] += 1
            evidence["failed"] += 1
        row["row_count_validation"] = row_count_validation


def _tpch_reference_context(scale_factor: float, seed: int | None) -> bool:
    from benchbox.core.tpch.benchmark import binds_answer_set_parameters, has_answer_set

    return has_answer_set(scale_factor) and binds_answer_set_parameters(seed)


def _bounded_message(value: str) -> str:
    if len(value) <= _MAX_EVIDENCE_MESSAGE_CHARS:
        return value
    return value[: _MAX_EVIDENCE_MESSAGE_CHARS - 3] + "..."


def _is_measurement(row: dict[str, Any]) -> bool:
    return str(row.get("run_type") or "measurement").lower() == "measurement"


def _is_execution_failure(row: dict[str, Any]) -> bool:
    return str(row.get("status", "")).upper() not in {"SUCCESS", "SKIPPED"}


def _attach_skipped_validation(row: dict[str, Any], warning: str) -> None:
    row["row_count_validation"] = {
        "status": "SKIPPED",
        "expected": None,
        "actual": row.get("rows_returned"),
        "warning": warning,
    }


def _attach_validation_error(row: dict[str, Any], message: str) -> None:
    row["rows_returned"] = None
    row["row_count_validation"] = {
        "status": "ERROR",
        "expected": None,
        "actual": None,
        "error": message,
    }
    row["status"] = "FAILED"
    row["error"] = message
