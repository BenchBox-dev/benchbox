from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import benchbox.core.expected_results  # noqa: F401
from benchbox.core.expected_results.models import ValidationMode, ValidationResult
from benchbox.core.expected_results.registry import LoadOutcome, get_registry
from benchbox.core.expected_results.ssb_results import normalize_ssb_query_id
from benchbox.core.expected_results.tpcds_results import (
    TPCDS_PARAMETER_SENSITIVE_QUERY_IDS as _TPCDS_PARAMETER_SENSITIVE_QUERY_IDS,
)
from benchbox.core.expected_results.tpch_results import (
    PARAMETER_SENSITIVE_QUERY_IDS as _TPCH_PARAMETER_SENSITIVE_QUERY_IDS,
)

logger = logging.getLogger(__name__)

_PARAMETER_SENSITIVE_QUERY_IDS_BY_BENCHMARK: dict[str, frozenset[str]] = {
    "tpch": _TPCH_PARAMETER_SENSITIVE_QUERY_IDS,
    "tpc-h": _TPCH_PARAMETER_SENSITIVE_QUERY_IDS,
    "tpcds": _TPCDS_PARAMETER_SENSITIVE_QUERY_IDS,
    "tpc-ds": _TPCDS_PARAMETER_SENSITIVE_QUERY_IDS,
}


_NON_REFERENCE_SEED_EXCLUDED_BENCHMARKS: frozenset[str] = frozenset({"tpcds"})
_stream_seed_override_warned: set[str] = set()
_stream_seed_override_lock = threading.Lock()


def reset_stream_seed_override_warnings() -> None:
    with _stream_seed_override_lock:
        _stream_seed_override_warned.clear()


def _warn_stream_seed_override_once(benchmark_type: str, requested_mode: ValidationMode | None) -> None:
    if requested_mode is None or requested_mode is ValidationMode.SKIP:
        return
    key = f"{benchmark_type.lower()}:{requested_mode.value}"
    with _stream_seed_override_lock:
        if key in _stream_seed_override_warned:
            return
        _stream_seed_override_warned.add(key)
    logger.warning(
        "Row-count validation mode '%s' was requested for '%s', but queries run with stream-seeded parameters "
        "that differ from the answer-set parameters, so answer-set row counts are not checked for them.",
        requested_mode.value,
        benchmark_type,
    )


def get_parameter_sensitive_query_ids(benchmark_type: str) -> frozenset[str]:
    return _PARAMETER_SENSITIVE_QUERY_IDS_BY_BENCHMARK.get(benchmark_type.lower(), frozenset())


_reference_seed_state = threading.local()


def _set_thread_context(state: threading.local, attribute: str, value: object) -> None:
    setattr(state, attribute, value)


def set_reference_seed_context(is_reference_seed: bool | None) -> None:
    _set_thread_context(_reference_seed_state, "is_reference_seed", is_reference_seed)


def get_reference_seed_context() -> bool | None:
    return getattr(_reference_seed_state, "is_reference_seed", None)


def clear_reference_seed_context() -> None:
    _reference_seed_state.is_reference_seed = None


_validation_mode_state = threading.local()


def set_validation_mode_context(mode: ValidationMode | None) -> None:
    _validation_mode_state.validation_mode = mode


def get_validation_mode_context() -> ValidationMode | None:
    return getattr(_validation_mode_state, "validation_mode", None)


def clear_validation_mode_context() -> None:
    _validation_mode_state.validation_mode = None


@contextmanager
def validation_mode_context(mode: ValidationMode | None) -> Iterator[None]:
    if mode is None:
        yield
        return
    previous = get_validation_mode_context()
    set_validation_mode_context(mode)
    try:
        yield
    finally:
        set_validation_mode_context(previous)


class QueryValidator:
    def __init__(self):
        from benchbox.core.expected_results import register_all_providers

        register_all_providers()

        self.registry = get_registry()

    @staticmethod
    def _resolve_tpcds_run_mode(benchmark_type: str) -> ValidationMode | None:
        if benchmark_type.lower() != "tpcds":
            return None
        context_mode = get_validation_mode_context()
        if context_mode is not None:
            return context_mode
        from benchbox.core.expected_results.tpcds_results import get_query_validation_mode

        return get_query_validation_mode()

    def _normalize_query_id(self, benchmark_type: str, query_id: str | int) -> str:
        import re

        query_id_str = str(query_id)

        if benchmark_type.lower() in ("tpcds", "tpc-ds"):
            match = re.search(r"(\d+)([a-d]?)", query_id_str)
            if match:
                query_num = str(int(match.group(1)))
                variant = match.group(2)
                return f"{query_num}{variant}"

        elif benchmark_type.lower() in ("tpch", "tpc-h"):
            match = re.search(r"(\d+)", query_id_str)
            if match:
                return str(int(match.group(1)))

        elif benchmark_type.lower() == "ssb":
            return normalize_ssb_query_id(query_id_str)

        return query_id_str

    def validate_query_result(
        self,
        benchmark_type: str,
        query_id: str | int,
        actual_row_count: int,
        scale_factor: float | None = None,
        stream_id: int | None = None,
    ) -> ValidationResult:
        if actual_row_count < 0:
            raise ValueError(f"actual_row_count must be non-negative, got {actual_row_count} for query '{query_id}'")

        query_id_str = str(query_id)

        query_id_normalized = self._normalize_query_id(benchmark_type, query_id)

        if (
            get_reference_seed_context() is False
            and benchmark_type.lower() in _NON_REFERENCE_SEED_EXCLUDED_BENCHMARKS
            and (
                query_id_normalized in get_parameter_sensitive_query_ids(benchmark_type) or (scale_factor or 1.0) == 1.0
            )
        ):
            _warn_stream_seed_override_once(benchmark_type, self._resolve_tpcds_run_mode(benchmark_type))
            return ValidationResult(
                is_valid=True,
                query_id=query_id_str,
                expected_row_count=None,
                actual_row_count=actual_row_count,
                validation_mode=ValidationMode.SKIP,
                warning_message=(
                    f"Query '{query_id}' ran with stream-seeded parameters that differ from the "
                    f"'{benchmark_type}' answer-set parameters. Row-count validation excluded. "
                    f"Actual rows returned: {actual_row_count}"
                ),
            )

        expected_result, load_outcome = self.registry.get_expected_result_detailed(
            benchmark_type, query_id_normalized, scale_factor, stream_id
        )

        if load_outcome in (LoadOutcome.PROVIDER_FAILED, LoadOutcome.PROVIDER_TIMEOUT):
            requested_mode = self._resolve_tpcds_run_mode(benchmark_type)
            if load_outcome is LoadOutcome.PROVIDER_FAILED:
                error_msg = (
                    f"Expected-results provider failed for query '{query_id}' in benchmark "
                    f"'{benchmark_type}' at scale factor {scale_factor or 1.0}. "
                    f"The outcome is unknown; it is not treated as a validation skip. "
                    f"Actual rows returned: {actual_row_count}"
                )
            else:
                error_msg = (
                    f"Timed out waiting for expected results for query '{query_id}' in benchmark "
                    f"'{benchmark_type}' at scale factor {scale_factor or 1.0}. "
                    f"The load continues in the background; the outcome is unknown and is not "
                    f"treated as a validation skip. Actual rows returned: {actual_row_count}"
                )
            return ValidationResult(
                is_valid=False,
                query_id=query_id_str,
                expected_row_count=None,
                actual_row_count=actual_row_count,
                validation_mode=requested_mode or ValidationMode.SKIP,
                error_message=error_msg,
            )

        if expected_result is not None and benchmark_type.lower() == "tpcds" and (scale_factor or 1.0) == 1.0:
            run_mode = self._resolve_tpcds_run_mode(benchmark_type)
            if run_mode is not None and run_mode != expected_result.validation_mode:
                import dataclasses

                expected_result = dataclasses.replace(expected_result, validation_mode=run_mode)

        if expected_result is None:
            if stream_id is not None and stream_id > 0:
                warning_msg = (
                    f"Query '{query_id}' executed on stream {stream_id}. "
                    f"Answer files only available for stream 0 in '{benchmark_type}' benchmark. "
                    f"Validation skipped. Actual rows returned: {actual_row_count}"
                )
            else:
                warning_msg = (
                    f"No expected row count defined for query '{query_id}' in benchmark '{benchmark_type}'. "
                    f"Validation skipped. Actual rows returned: {actual_row_count}"
                )

            return ValidationResult(
                is_valid=True,
                query_id=query_id_str,
                expected_row_count=None,
                actual_row_count=actual_row_count,
                validation_mode=ValidationMode.SKIP,
                warning_message=warning_msg,
            )

        if get_reference_seed_context() is False and query_id_normalized in get_parameter_sensitive_query_ids(
            benchmark_type
        ):
            if (
                expected_result.expected_row_count_min is not None
                and expected_result.expected_row_count_max is not None
            ):
                return self._validate_range(
                    query_id_str,
                    expected_result.expected_row_count_min,
                    expected_result.expected_row_count_max,
                    actual_row_count,
                )
            return expected_result.validate_loose(actual_row_count, scale_factor)

        expected_count = expected_result.get_expected_count(scale_factor)

        if expected_result.validation_mode == ValidationMode.EXACT and expected_count is None:
            return ValidationResult(
                is_valid=True,
                query_id=query_id_str,
                expected_row_count=None,
                actual_row_count=actual_row_count,
                validation_mode=ValidationMode.SKIP,
                warning_message=(
                    f"Query '{query_id}' has EXACT validation mode but no expected count available. "
                    f"Validation skipped. This may indicate a variant lookup issue or missing answer file. "
                    f"Actual rows returned: {actual_row_count}"
                ),
            )

        if expected_result.validation_mode == ValidationMode.SKIP:
            return ValidationResult(
                is_valid=True,
                query_id=query_id_str,
                expected_row_count=expected_count,
                actual_row_count=actual_row_count,
                validation_mode=ValidationMode.SKIP,
                warning_message=f"Validation skipped for query '{query_id}' (marked as skip in expected results)",
            )

        elif expected_result.validation_mode == ValidationMode.EXACT:
            return self._validate_exact(query_id_str, expected_count, actual_row_count)

        elif expected_result.validation_mode == ValidationMode.RANGE:
            return self._validate_range(
                query_id_str,
                expected_result.expected_row_count_min,
                expected_result.expected_row_count_max,
                actual_row_count,
            )

        elif expected_result.validation_mode == ValidationMode.LOOSE:
            return expected_result.validate_loose(actual_row_count, scale_factor)

        else:
            return ValidationResult(
                is_valid=False,
                query_id=query_id_str,
                expected_row_count=expected_count,
                actual_row_count=actual_row_count,
                validation_mode=expected_result.validation_mode,
                error_message=f"Unknown validation mode: {expected_result.validation_mode}",
            )

    def _validate_exact(self, query_id: str, expected_count: int | None, actual_count: int) -> ValidationResult:
        if expected_count is None:
            return ValidationResult(
                is_valid=False,
                query_id=query_id,
                expected_row_count=None,
                actual_row_count=actual_count,
                validation_mode=ValidationMode.EXACT,
                error_message=f"Expected count is None for query '{query_id}' in EXACT validation mode",
            )

        is_valid = expected_count == actual_count

        if is_valid:
            return ValidationResult(
                is_valid=True,
                query_id=query_id,
                expected_row_count=expected_count,
                actual_row_count=actual_count,
                validation_mode=ValidationMode.EXACT,
            )
        else:
            difference = actual_count - expected_count
            difference_percent = (difference / expected_count * 100.0) if expected_count > 0 else 0.0

            return ValidationResult(
                is_valid=False,
                query_id=query_id,
                expected_row_count=expected_count,
                actual_row_count=actual_count,
                validation_mode=ValidationMode.EXACT,
                error_message=(
                    f"Query validation FAILED: {query_id}\n"
                    f"  Expected: {expected_count:,} rows\n"
                    f"  Actual:   {actual_count:,} rows\n"
                    f"  Difference: {difference:+,} row(s) ({difference_percent:+.1f}%)\n"
                    f"  This indicates the query did not execute correctly."
                ),
                difference=difference,
                difference_percent=difference_percent,
            )

    def _validate_range(
        self, query_id: str, min_count: int | None, max_count: int | None, actual_count: int
    ) -> ValidationResult:
        if min_count is None or max_count is None:
            return ValidationResult(
                is_valid=False,
                query_id=query_id,
                expected_row_count=None,
                actual_row_count=actual_count,
                validation_mode=ValidationMode.RANGE,
                error_message=f"Min/max counts not defined for query '{query_id}' in RANGE validation mode",
            )

        is_valid = min_count <= actual_count <= max_count

        if is_valid:
            return ValidationResult(
                is_valid=True,
                query_id=query_id,
                expected_row_count=None,
                actual_row_count=actual_count,
                validation_mode=ValidationMode.RANGE,
            )
        else:
            if actual_count < min_count:
                difference = actual_count - min_count
                difference_percent = (difference / min_count * 100.0) if min_count > 0 else 0.0
                error_msg = (
                    f"Query validation FAILED: {query_id}\n"
                    f"  Expected: {min_count:,}-{max_count:,} rows (non-deterministic)\n"
                    f"  Actual:   {actual_count:,} rows\n"
                    f"  Difference: {difference:+,} rows (below minimum by {abs(difference_percent):.1f}%)"
                )
            else:
                difference = actual_count - max_count
                difference_percent = (difference / max_count * 100.0) if max_count > 0 else 0.0
                error_msg = (
                    f"Query validation FAILED: {query_id}\n"
                    f"  Expected: {min_count:,}-{max_count:,} rows (non-deterministic)\n"
                    f"  Actual:   {actual_count:,} rows\n"
                    f"  Difference: {difference:+,} rows (exceeds maximum by {difference_percent:.1f}%)"
                )

            return ValidationResult(
                is_valid=False,
                query_id=query_id,
                expected_row_count=None,
                actual_row_count=actual_count,
                validation_mode=ValidationMode.RANGE,
                error_message=error_msg,
                difference=difference,
                difference_percent=difference_percent,
            )
