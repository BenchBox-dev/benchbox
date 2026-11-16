from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.query_status import unchecked_row_count_query_ids
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
        "iteration": 1,
        "execution_time_seconds": 0.5,
        "rows_returned": 4,
        "row_count_validation": {"status": status, "expected": 4 if status == "PASSED" else None, "actual": 4},
    }


def _built(rows: list[dict]):
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=1.0),
        platform=PlatformInfoInput(name="duckdb", platform_version="1.3.2"),
    )
    builder.add_query_results([normalize_query_result(row) for row in rows])
    return builder.build()


def test_unchecked_ids_cover_skipped_and_errored_rows_once_each():
    rows = [
        _row("1", "PASSED"),
        _row("2", "SKIPPED", run_type="warmup"),
        _row("2", "SKIPPED"),
        _row("3", "ERROR"),
        _row("4", "UNVALIDATED"),
        {"query_id": "5", "status": "SUCCESS"},
    ]

    assert unchecked_row_count_query_ids(rows) == ["2", "3"]


@pytest.mark.parametrize("unchecked_status", ["SKIPPED", "ERROR"])
def test_built_run_with_an_unchecked_row_count_is_uncertain_and_accepted_by_the_bundle_validator(
    unchecked_status: str,
):
    from benchbox.core.results.schema import build_result_payload
    from benchbox.core.results.status import result_cli_failure_reason, result_unvalidated_reason
    from benchbox.validation.bundle import ValidationResult, _validate_execution_consistency

    result = _built([_row("1", "PASSED"), _row("16", unchecked_status)])
    payload = build_result_payload(result)
    validation = ValidationResult("test")
    _validate_execution_consistency(payload, validation)

    assert result.validation_status == "UNCERTAIN"
    assert payload["summary"]["validation"] == "uncertain"
    assert validation.errors == []
    assert result_cli_failure_reason(result) is None
    assert result_unvalidated_reason(result) == "validation_status=uncertain"


def test_built_run_with_every_row_count_checked_is_passed_and_accepted_by_the_bundle_validator():
    from benchbox.core.results.schema import build_result_payload
    from benchbox.validation.bundle import ValidationResult, _validate_execution_consistency

    result = _built([_row("1", "PASSED"), _row("2", "PASSED")])
    payload = build_result_payload(result)
    validation = ValidationResult("test")
    _validate_execution_consistency(payload, validation)

    assert payload["summary"]["validation"] == "passed"
    assert validation.errors == []


def test_unvalidated_rows_take_precedence_over_unchecked_rows():
    assert _built([_row("1", "UNVALIDATED"), _row("2", "SKIPPED")]).validation_status == "NOT_VALIDATED"


def test_aggregated_passed_records_do_not_restore_passed_over_an_unchecked_row_count():
    from benchbox.core.validation import ValidationResult

    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={"row_count_matches": True},
        query_results=[_row("1", "PASSED"), _row("16", "SKIPPED")],
    )

    finalized = _finalize_validation_metadata(result, [("post_load", ValidationResult(is_valid=True))])

    assert finalized.validation_status == "UNCERTAIN"
    assert any("1 query had a row count" in warning for warning in finalized.validation_details["warnings"])


def test_failed_validation_is_not_overwritten_by_an_unchecked_row_count():
    result = make_benchmark_results(
        validation_status="FAILED",
        validation_details={"row_count_matches": False},
        query_results=[_row("16", "SKIPPED")],
    )

    assert _finalize_validation_metadata(result, []).validation_status == "FAILED"


@pytest.mark.parametrize(
    ("validation_status", "successful", "expected"),
    [
        ("UNCERTAIN", 22, ("UNVALIDATED", "yellow")),
        ("NOT_VALIDATED", 22, ("UNVALIDATED", "yellow")),
        ("UNCERTAIN", 21, ("FAILED", "red")),
        ("FAILED", 22, ("FAILED", "red")),
        ("PASSED", 22, ("PASSED", "green")),
    ],
)
def test_console_status_separates_an_unvalidated_run_from_a_failed_one(
    validation_status: str, successful: int, expected: tuple[str, str]
):
    from benchbox.cli.output import ConsoleResultFormatter

    result = SimpleNamespace(
        total_queries=22,
        successful_queries=successful,
        failed_queries=22 - successful,
        validation_status=validation_status,
        query_results=[],
    )

    assert ConsoleResultFormatter._determine_benchmark_status(result) == expected
