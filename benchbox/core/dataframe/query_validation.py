from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from benchbox.core.expected_results import has_row_count_reference
from benchbox.core.expected_results.models import ValidationMode, ValidationResult, row_count_validation_record
from benchbox.core.expected_results.row_count_references import RowCountReference, load_row_count_reference
from benchbox.core.expected_results.ssb_results import normalize_ssb_query_id
from benchbox.core.results.query_status import (
    NO_REFERENCE_REASON,
    NO_REFERENCE_VALIDATION_STATUS,
    NOT_VALIDATED_VALIDATION_STATUS,
    PASSED_VALIDATION_STATUS,
    row_count_skip_is_documented,
    row_evidence_roll_up,
)
from benchbox.core.validation.query_validation import validation_skipped_by_configuration
from benchbox.core.validation.stream_checks import QueryGeneration, decide_row_count

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

    if benchmark_id == "ssb":
        evidence["provider"] = "ssb_expected_row_counts"
    if benchmark_id == "tpcds":
        evidence["provider"] = "tpcds_row_count_reference"

    reason = _reference_unavailable_reason(benchmark_id, scale_factor, seed)
    if reason:
        reference_absent = reason == NO_REFERENCE_REASON
        for row in successful_rows:
            _attach_skipped_validation(row, reason, "UNVALIDATED" if reference_absent else "SKIPPED")
        evidence["skipped"] = len(successful_rows) + selected_skips
        evidence["reason"] = reason
        status = _unavailable_status(
            execution_failures,
            bool(successful_rows or selected_skips),
            NO_REFERENCE_VALIDATION_STATUS if reference_absent else "UNCERTAIN",
        )
        return DataFrameQueryValidationSummary(status, evidence)

    initialization_error = _apply_reference_row_counts(successful_rows, benchmark_id, scale_factor, evidence, seed)
    if initialization_error:
        evidence["reason"] = initialization_error
        status = _unavailable_status(execution_failures, bool(successful_rows))
        return DataFrameQueryValidationSummary(status, evidence)

    evidence["skipped"] += selected_skips
    status, _ = row_evidence_roll_up(successful_rows, reference_exists=True, failed_queries=execution_failures)
    if status == NOT_VALIDATED_VALIDATION_STATUS or (
        status == PASSED_VALIDATION_STATUS and evidence["skipped"] > evidence.get("documented_skips", 0)
    ):
        status = "UNCERTAIN" if successful_rows or evidence["skipped"] else "NOT_RUN"
    return DataFrameQueryValidationSummary(status, evidence)


def _normalize_benchmark_name(value: str) -> str:
    return str(value).strip().lower().replace("tpc-h", "tpch").replace("tpc-ds", "tpcds")


def _unavailable_status(
    execution_failures: int, has_unavailable_evidence: bool, evidence_status: str = "UNCERTAIN"
) -> str:
    if execution_failures:
        return "PARTIAL"
    if has_unavailable_evidence:
        return evidence_status
    return "NOT_RUN"


def _reference_unavailable_reason(benchmark_id: str, scale_factor: float, seed: int | None) -> str | None:
    if not has_row_count_reference(benchmark_id, scale_factor):
        return NO_REFERENCE_REASON
    return None


def _apply_reference_row_counts(
    successful_rows: list[dict[str, Any]],
    benchmark_id: str,
    scale_factor: float,
    evidence: dict[str, Any],
    seed: int | None = None,
) -> str | None:
    if benchmark_id == "ssb":
        try:
            reference = load_row_count_reference(benchmark_id, scale_factor)
        except Exception as exc:
            return _record_oracle_initialization_error(successful_rows, evidence, exc)
        _apply_row_count_oracle(
            successful_rows,
            lambda query_id, actual, row: _validate_ssb_row_count(reference, query_id, actual),
            evidence,
        )
        return None
    _apply_row_count_oracle(
        successful_rows,
        lambda query_id, actual, row: _decide_stream_row(benchmark_id, scale_factor, query_id, actual, row, seed),
        evidence,
    )
    return None


def _row_stream(row: dict[str, Any]) -> int:
    stream_id = row.get("stream_id", row.get("stream"))
    if isinstance(stream_id, int) and not isinstance(stream_id, bool) and stream_id >= 0:
        return stream_id
    return 0


def _row_generation(benchmark_id: str, row: dict[str, Any], seed: int | None) -> QueryGeneration:
    recorded = row.get("generation")
    if isinstance(recorded, QueryGeneration):
        return recorded
    stream = _row_stream(row)
    if benchmark_id == "tpcds":
        from benchbox.core.tpcds.power_test import power_parameter_seed

        return QueryGeneration(seed=power_parameter_seed(seed, stream))
    from benchbox.core.tpch.benchmark import power_stream_seed

    return QueryGeneration(seed=power_stream_seed(seed, stream))


def _decide_stream_row(
    benchmark_id: str, scale_factor: float, query_id: str, actual: int, row: dict[str, Any], seed: int | None
) -> ValidationResult:
    if validation_skipped_by_configuration(benchmark_id):
        return ValidationResult(
            is_valid=True,
            query_id=query_id,
            expected_row_count=None,
            actual_row_count=actual,
            validation_mode=ValidationMode.SKIP,
            warning_message="TPC-DS query validation is skipped by configuration",
        )
    return decide_row_count(
        benchmark_id,
        query_id,
        actual,
        scale_factor,
        generation=_row_generation(benchmark_id, row, seed),
        data_refreshed=bool(row.get("data_refreshed")),
    )


def _validate_ssb_row_count(reference: RowCountReference, query_id: str, actual_row_count: int) -> ValidationResult:
    expected = reference.streams[0].row_counts.get(normalize_ssb_query_id(query_id))
    if expected is None:
        return ValidationResult(
            is_valid=False,
            query_id=query_id,
            expected_row_count=None,
            actual_row_count=actual_row_count,
            validation_mode=ValidationMode.EXACT,
            error_message=(
                f"Row-count lookup failed: SSB query {query_id!r} has no expected row count in the "
                "reference answers. Check the query id. The query is not treated as validated."
            ),
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
        reference_kind=reference.kind,
        reference_label=reference.label,
    )


def _record_oracle_initialization_error(
    successful_rows: list[dict[str, Any]], evidence: dict[str, Any], exc: Exception
) -> str:
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


def _apply_row_count_oracle(
    successful_rows: list[dict[str, Any]],
    validate: Callable[[str, int, dict[str, Any]], ValidationResult],
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
            result = validate(str(row.get("query_id", "")), actual_row_count, row)
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

        row_count_validation = row_count_validation_record(result, actual_row_count)
        for message_field in ("error", "warning"):
            if message_field in row_count_validation:
                row_count_validation[message_field] = _bounded_message(row_count_validation[message_field] or "")
        if result.reference_kind and result.reference_label and result.expected_row_count is not None:
            kinds = evidence.setdefault("reference_kinds", {})
            kinds[result.reference_kind] = {
                "label": result.reference_label,
                "rows": kinds.get(result.reference_kind, {}).get("rows", 0) + 1,
            }
        status = row_count_validation["status"]
        if status == "PASSED":
            evidence["checked"] += 1
            evidence["passed"] += 1
        elif status == "FAILED":
            row_count_validation["error"] = row_count_validation.get("error") or "Row-count validation failed"
            row["status"] = "FAILED"
            row["error"] = row_count_validation["error"]
            evidence["checked"] += 1
            evidence["failed"] += 1
        else:
            evidence["skipped"] += 1
        if status == "SKIPPED" and row_count_skip_is_documented(row_count_validation):
            evidence["documented_skips"] = evidence.get("documented_skips", 0) + 1
        row["row_count_validation"] = row_count_validation


def _bounded_message(value: str) -> str:
    if len(value) <= _MAX_EVIDENCE_MESSAGE_CHARS:
        return value
    return value[: _MAX_EVIDENCE_MESSAGE_CHARS - 3] + "..."


def _is_measurement(row: dict[str, Any]) -> bool:
    return str(row.get("run_type") or "measurement").lower() == "measurement"


def _is_execution_failure(row: dict[str, Any]) -> bool:
    return str(row.get("status", "")).upper() not in {"SUCCESS", "SKIPPED"}


def _attach_skipped_validation(row: dict[str, Any], warning: str, status: str = "SKIPPED") -> None:
    row["row_count_validation"] = {
        "status": status,
        "expected": None,
        "actual": row.get("rows_returned"),
        "warning": warning,
    }


def _attach_validation_error(row: dict[str, Any], message: str) -> None:
    row["rows_returned"] = None
    row["row_count_validation"] = {
        "status": "FAILED",
        "expected": None,
        "actual": None,
        "error": message,
    }
    row["status"] = "FAILED"
    row["error"] = message
