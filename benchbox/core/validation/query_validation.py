from __future__ import annotations

import importlib
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

importlib.import_module("benchbox.core.expected_results")
from benchbox.core.expected_results.models import ExpectedQueryResult, ValidationMode, ValidationResult
from benchbox.core.expected_results.registry import LoadOutcome, get_registry
from benchbox.core.expected_results.ssb_results import normalize_ssb_query_id
from benchbox.core.results.query_status import DATA_REFRESHED_REASON
from benchbox.core.validation.stream_checks import canonical_benchmark, current_query_generation, decide_row_count

logger = logging.getLogger(__name__)

_data_state_state = threading.local()


def set_data_refreshed_context(data_refreshed: bool) -> None:
    _data_state_state.data_refreshed = data_refreshed


def get_data_refreshed_context() -> bool:
    return getattr(_data_state_state, "data_refreshed", False)


def clear_data_refreshed_context() -> None:
    _data_state_state.data_refreshed = False


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


def validation_skipped_by_configuration(benchmark_type: str) -> bool:
    if benchmark_type.lower() not in ("tpcds", "tpc-ds"):
        return False
    context_mode = get_validation_mode_context()
    if context_mode is not None:
        return context_mode is ValidationMode.SKIP
    return os.environ.get("BENCHBOX_QUERY_VALIDATION_MODE", "").strip().lower() in ("skip", "disabled")


class QueryValidator:
    def __init__(self):
        from benchbox.core.expected_results import register_all_providers

        register_all_providers()

        self.registry = get_registry()

    def _normalize_query_id(self, benchmark_type: str, query_id: str | int) -> str:
        import re

        query_id_str = str(query_id)

        if benchmark_type.lower() in ("tpcds", "tpc-ds"):
            match = re.fullmatch(r"[Qq]?(\d+)([a-dA-D]?)", query_id_str.strip())
            if match:
                query_num = str(int(match.group(1)))
                variant = match.group(2).lower()
                return f"{query_num}{variant}"
        elif benchmark_type.lower() in ("tpch", "tpc-h"):
            match = re.search(r"(\d+)", query_id_str)
            if match:
                return str(int(match.group(1)))

        elif benchmark_type.lower() == "ssb":
            return normalize_ssb_query_id(query_id_str)

        return query_id_str

    @staticmethod
    def _without_reference(
        benchmark_type: str,
        query_id: str,
        actual_row_count: int,
        scale_factor: float | None,
        stream_id: int | None,
        load_outcome: LoadOutcome,
    ) -> ValidationResult:
        if load_outcome is LoadOutcome.ANSWER_FILES_MISSING:
            warning = (
                f"Reference answer files for benchmark '{benchmark_type}' at scale factor {scale_factor or 1.0} "
                f"are not installed (query '{query_id}'). Row counts were not checked. "
                f"Actual rows returned: {actual_row_count}"
            )
            unvalidated = False
        else:
            warning = (
                f"Unvalidated: no reference answers exist for benchmark '{benchmark_type}' at scale factor "
                f"{scale_factor or 1.0} (query '{query_id}'). The row count was not checked and is not "
                f"counted as passed. Actual rows returned: {actual_row_count}"
            )
            unvalidated = True
        return ValidationResult(
            is_valid=True,
            query_id=query_id,
            expected_row_count=None,
            actual_row_count=actual_row_count,
            validation_mode=ValidationMode.SKIP,
            warning_message=warning,
            unvalidated=unvalidated,
        )

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

        if canonical_benchmark(benchmark_type) is not None:
            if validation_skipped_by_configuration(benchmark_type):
                return ValidationResult(
                    is_valid=True,
                    query_id=str(query_id),
                    expected_row_count=None,
                    actual_row_count=actual_row_count,
                    validation_mode=ValidationMode.SKIP,
                    warning_message="TPC-DS query validation is skipped by configuration",
                )
            return decide_row_count(
                benchmark_type,
                query_id,
                actual_row_count,
                scale_factor,
                generation=current_query_generation(),
                data_refreshed=get_data_refreshed_context(),
            )

        query_id_str = str(query_id)

        query_id_normalized = self._normalize_query_id(benchmark_type, query_id)

        if get_data_refreshed_context():
            return ValidationResult(
                is_valid=True,
                query_id=query_id_str,
                expected_row_count=None,
                actual_row_count=actual_row_count,
                validation_mode=ValidationMode.SKIP,
                warning_message=(
                    f"{DATA_REFRESHED_REASON}: query '{query_id}' ran after refresh functions changed the "
                    f"'{benchmark_type}' data, so the base-data answer set does not apply. "
                    f"Row-count validation excluded. Actual rows returned: {actual_row_count}"
                ),
            )

        expected_result, load_outcome = self.registry.get_expected_result_detailed(
            benchmark_type, query_id_normalized, scale_factor, stream_id
        )

        if load_outcome in (LoadOutcome.PROVIDER_FAILED, LoadOutcome.PROVIDER_TIMEOUT):
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
                validation_mode=ValidationMode.SKIP,
                error_message=error_msg,
            )

        if load_outcome is LoadOutcome.QUERY_NOT_FOUND:
            return ValidationResult(
                is_valid=False,
                query_id=query_id_str,
                expected_row_count=None,
                actual_row_count=actual_row_count,
                validation_mode=ValidationMode.EXACT,
                error_message=(
                    f"Row-count lookup failed: query '{query_id}' (normalized '{query_id_normalized}') has no "
                    f"expected row count in the '{benchmark_type}' reference answers at scale factor "
                    f"{scale_factor or 1.0}, although that benchmark has reference answers. Check the query id "
                    f"and variant key. The query is not treated as validated. "
                    f"Actual rows returned: {actual_row_count}"
                ),
            )

        if expected_result is None:
            return self._without_reference(
                benchmark_type, query_id_str, actual_row_count, scale_factor, stream_id, load_outcome
            )

        result = self._validate_against_reference(
            benchmark_type, query_id, query_id_str, query_id_normalized, actual_row_count, scale_factor, expected_result
        )
        result.reference_kind = expected_result.reference_kind
        result.reference_label = expected_result.reference_label
        if expected_result.reference_label and not result.is_valid and result.error_message:
            result.error_message = f"{result.error_message}\n  Reference: {expected_result.reference_label}"
        return result

    def _validate_against_reference(
        self,
        benchmark_type: str,
        query_id: str | int,
        query_id_str: str,
        query_id_normalized: str,
        actual_row_count: int,
        scale_factor: float | None,
        expected_result: ExpectedQueryResult,
    ) -> ValidationResult:
        expected_count = expected_result.get_expected_count(scale_factor)

        if expected_result.validation_mode == ValidationMode.EXACT and expected_count is None:
            return ValidationResult(
                is_valid=False,
                query_id=query_id_str,
                expected_row_count=None,
                actual_row_count=actual_row_count,
                validation_mode=ValidationMode.EXACT,
                error_message=(
                    f"Row-count lookup failed for query '{query_id}' in benchmark '{benchmark_type}' at scale "
                    f"factor {scale_factor or 1.0}: the reference entry has EXACT validation mode but no "
                    f"expected count. This indicates a variant lookup defect or a missing answer file; the "
                    f"query is not treated as validated. Actual rows returned: {actual_row_count}"
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
