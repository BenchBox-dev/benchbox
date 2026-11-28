from __future__ import annotations

import pytest

from benchbox.core.results.query_execution import normalize_row_count_validation
from benchbox.core.results.query_status import row_evidence_roll_up
from benchbox.core.runner.runner import _finalize_validation_metadata, _mark_unvalidated_queries
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


def test_passed_run_with_unvalidated_queries_is_not_reported_as_passed():
    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={"row_count_matches": True},
        query_results=[_row("1", "UNVALIDATED"), _row("2", "UNVALIDATED")],
    )

    finalized = _finalize_validation_metadata(result, [])

    assert finalized.validation_status == "NO_REFERENCE"
    assert finalized.validation_details["reason"] == "no row-count reference at this scale factor"
    assert any("no row-count reference" in warning for warning in finalized.validation_details["warnings"])


def test_aggregated_passed_records_do_not_restore_passed_over_unvalidated_queries():
    from benchbox.core.validation import ValidationResult

    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={"row_count_matches": True},
        query_results=[_row("1", "UNVALIDATED")],
    )

    finalized = _finalize_validation_metadata(result, [("post_load", ValidationResult(is_valid=True))])

    assert finalized.validation_status == "NO_REFERENCE"


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


def test_range_checked_pass_is_recorded_as_skipped_with_a_documented_reason():
    normalized = normalize_row_count_validation({"status": "PASSED", "expected": None, "actual": 42}, rows_returned=42)

    assert normalized["status"] == "SKIPPED"
    assert normalized["warning"].startswith("within range:")


def test_passed_run_whose_row_counts_were_all_skipped_is_not_reported_as_passed():
    result = make_benchmark_results(
        benchmark_name="TPC-H",
        scale_factor=1.0,
        validation_status="PASSED",
        validation_details={"row_count_matches": True},
        query_results=[_row("1", "SKIPPED"), _row("2", "SKIPPED")],
    )

    finalized = _finalize_validation_metadata(result, [])

    assert finalized.validation_status == "NOT_VALIDATED"


def test_passed_run_with_an_undocumented_skip_beside_passed_row_counts_is_not_reported_as_passed():
    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={"row_count_matches": True},
        query_results=[_row("1", "PASSED"), _row("2", "SKIPPED")],
    )

    assert _finalize_validation_metadata(result, []).validation_status == "NO_REFERENCE"


@pytest.mark.parametrize(
    ("scale_factor", "rows", "expected"),
    [
        (0.01, [_row("1", "UNVALIDATED")], "NOT_VALIDATED"),
        (
            0.01,
            [{"query_id": "1", "status": "SUCCESS", "run_type": "measurement", "rows_returned": 4}],
            "NOT_VALIDATED",
        ),
        (10.0, [_row("1", "UNVALIDATED")], "NO_REFERENCE"),
        (10.0, [{"query_id": "1", "status": "SUCCESS", "run_type": "measurement", "rows_returned": 4}], "NO_REFERENCE"),
    ],
)
def test_no_reference_is_recorded_only_where_the_reference_data_records_none(scale_factor, rows, expected):
    result = make_benchmark_results(
        benchmark_name="TPC-H",
        scale_factor=scale_factor,
        validation_status="PASSED",
        validation_details={},
        query_results=rows,
    )

    finalized = _mark_unvalidated_queries(result)

    assert finalized.validation_status == expected
    assert ("reason" in finalized.validation_details) is (expected == "NO_REFERENCE")
