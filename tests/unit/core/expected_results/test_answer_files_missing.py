from __future__ import annotations

import pytest

from benchbox.core.expected_results.models import BenchmarkExpectedResults, ExpectedQueryResult, ValidationMode
from benchbox.core.expected_results.registry import ExpectedResultsRegistry, LoadOutcome
from benchbox.core.results.query_status import (
    NO_REFERENCE_VALIDATION_STATUS,
    NOT_VALIDATED_VALIDATION_STATUS,
    row_evidence_roll_up,
)
from benchbox.core.validation.query_validation import QueryValidator

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _missing_files(_scale_factor: float) -> BenchmarkExpectedResults:
    raise FileNotFoundError("answer files are not installed")


def _no_reference(_scale_factor: float) -> BenchmarkExpectedResults | None:
    return None


def _has_reference(scale_factor: float) -> BenchmarkExpectedResults:
    return BenchmarkExpectedResults(
        benchmark_name="demo",
        scale_factor=scale_factor,
        query_results={
            "1": ExpectedQueryResult(query_id="1", expected_row_count=4, validation_mode=ValidationMode.EXACT)
        },
    )


def _validator(provider) -> QueryValidator:
    validator = QueryValidator()
    registry = ExpectedResultsRegistry()
    registry.register_provider("demo", provider)
    validator.registry = registry
    return validator


def test_missing_answer_files_are_not_reported_as_no_reference() -> None:
    registry = ExpectedResultsRegistry()
    registry.register_provider("demo", _missing_files)

    assert registry.has_row_count_reference("demo", 1.0)
    for _ in range(2):
        _, outcome = registry.get_expected_result_detailed("demo", "1", 1.0)
        assert outcome is LoadOutcome.ANSWER_FILES_MISSING


def test_a_scale_with_no_reference_is_reported_as_no_reference() -> None:
    registry = ExpectedResultsRegistry()
    registry.register_provider("demo", _no_reference)

    assert not registry.has_row_count_reference("demo", 10.0)
    _, outcome = registry.get_expected_result_detailed("demo", "1", 10.0)
    assert outcome is LoadOutcome.NO_ANSWER_SET


def test_missing_answer_files_record_a_skipped_row_and_the_not_validated_run_status() -> None:
    result = _validator(_missing_files).validate_query_result("demo", "1", 4, 1.0)
    row = {
        "run_type": "measurement",
        "row_count_validation": {"status": "SKIPPED" if not result.unvalidated else "UNVALIDATED"},
    }

    assert result.is_valid
    assert not result.unvalidated
    assert "not installed" in (result.warning_message or "")
    assert row_evidence_roll_up([row], reference_exists=True)[0] == NOT_VALIDATED_VALIDATION_STATUS


def test_no_reference_at_a_scale_records_an_unvalidated_row_and_the_no_reference_run_status() -> None:
    result = _validator(_no_reference).validate_query_result("demo", "1", 4, 10.0)
    row = {
        "run_type": "measurement",
        "row_count_validation": {"status": "UNVALIDATED" if result.unvalidated else "SKIPPED"},
    }

    assert result.unvalidated
    assert row_evidence_roll_up([row], reference_exists=False)[0] == NO_REFERENCE_VALIDATION_STATUS


def test_a_reference_present_validates_normally() -> None:
    result = _validator(_has_reference).validate_query_result("demo", "1", 4, 1.0)

    assert result.is_valid and not result.unvalidated and result.expected_row_count == 4


def test_skipped_rows_without_reference_are_no_reference() -> None:
    rows = [{"row_count_validation": {"status": "SKIPPED"}}]
    assert row_evidence_roll_up(rows, reference_exists=False)[0] == NO_REFERENCE_VALIDATION_STATUS
    assert row_evidence_roll_up(rows, reference_exists=True)[0] == NOT_VALIDATED_VALIDATION_STATUS


def test_positive_stream_without_reference_is_unvalidated() -> None:
    validator = QueryValidator()
    registry = ExpectedResultsRegistry()
    registry.register_provider("tpch", _no_reference)
    validator.registry = registry
    result = validator.validate_query_result("tpch", "1", 4, 10.0, stream_id=1)
    assert result.unvalidated
