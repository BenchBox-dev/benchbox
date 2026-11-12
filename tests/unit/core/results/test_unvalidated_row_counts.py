from __future__ import annotations

import pytest

from benchbox.core.results.query_execution import normalize_row_count_validation
from benchbox.core.results.query_status import unvalidated_measurement_query_ids
from benchbox.core.runner.runner import _finalize_validation_metadata
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _row(query_id: str, status: str, *, run_type: str = "measurement") -> dict:
    return {
        "query_id": query_id,
        "status": "SUCCESS",
        "run_type": run_type,
        "rows_returned": 4,
        "row_count_validation": {"status": status, "expected": None if status != "PASSED" else 4, "actual": 4},
    }


def test_unvalidated_status_is_a_valid_row_count_validation_status():
    normalized = normalize_row_count_validation(
        {"status": "UNVALIDATED", "expected": None, "actual": 4, "warning": "no reference answers"},
        rows_returned=4,
    )

    assert normalized == {
        "status": "UNVALIDATED",
        "expected": None,
        "actual": 4,
        "warning": "no reference answers",
    }


def test_unvalidated_measurement_query_ids_ignores_warmups_and_other_statuses():
    rows = [
        _row("1", "UNVALIDATED"),
        _row("2", "PASSED"),
        _row("3", "SKIPPED"),
        _row("4", "UNVALIDATED", run_type="warmup"),
    ]

    assert unvalidated_measurement_query_ids(rows) == ["1"]


def test_passed_run_with_unvalidated_queries_is_not_reported_as_passed():
    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={"row_count_matches": True},
        query_results=[_row("1", "UNVALIDATED"), _row("2", "UNVALIDATED")],
    )

    finalized = _finalize_validation_metadata(result, [])

    assert finalized.validation_status == "NOT_VALIDATED"
    assert any("2 queries unvalidated" in warning for warning in finalized.validation_details["warnings"])


def test_aggregated_passed_records_do_not_restore_passed_over_unvalidated_queries():
    from benchbox.core.validation import ValidationResult

    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={"row_count_matches": True},
        query_results=[_row("1", "UNVALIDATED")],
    )

    finalized = _finalize_validation_metadata(result, [("post_load", ValidationResult(is_valid=True))])

    assert finalized.validation_status == "NOT_VALIDATED"


def test_fully_validated_run_stays_passed():
    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={"row_count_matches": True},
        query_results=[_row("1", "PASSED")],
    )

    finalized = _finalize_validation_metadata(result, [])

    assert finalized.validation_status == "PASSED"


def test_failed_validation_is_not_overwritten_by_unvalidated_queries():
    result = make_benchmark_results(
        validation_status="FAILED",
        validation_details={"row_count_matches": False},
        query_results=[_row("1", "UNVALIDATED")],
    )

    finalized = _finalize_validation_metadata(result, [])

    assert finalized.validation_status == "FAILED"
