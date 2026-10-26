from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.results.query_status import documented_exclusions_cover_failures
from benchbox.core.results.status import (
    CLI_FAILURE_VALIDATION_STATUSES,
    NON_CLEAN_VALIDATION_STATUSES,
    UNVALIDATED_VALIDATION_STATUSES,
    bundle_failed_query_count,
    bundle_is_clean_pass,
    result_cli_failure_reason,
    result_failed_query_count,
    result_is_clean_pass,
    result_non_clean_reason,
    result_unvalidated_reason,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_bundle_skipped_queries_are_not_failures() -> None:
    data = {
        "summary": {"validation": "passed", "queries": {"total": 2, "passed": 1, "failed": 0, "skipped": 1}},
        "queries": [
            {"id": "Q1", "status": "SUCCESS", "run_type": "measurement"},
            {"id": "Q2", "status": "SKIPPED", "run_type": "measurement"},
        ],
    }

    assert bundle_failed_query_count(data) == 0
    assert bundle_is_clean_pass(data) is True


def test_bundle_padded_run_type_does_not_hide_failed_measurement() -> None:
    data = {
        "queries": [
            {"id": "Q1", "status": "FAILED", "run_type": "measurement "},
            {"id": "Q2", "status": "SUCCESS", "run_type": "measurement"},
        ],
    }

    assert bundle_failed_query_count(data) == 1
    assert bundle_is_clean_pass(data) is False


def test_bundle_explicit_failed_count_takes_precedence_over_rows() -> None:
    data = {
        "summary": {"queries": {"total": 3, "passed": 2, "failed": 1}},
        "queries": [
            {"status": "FAILED", "run_type": "measurement"},
            {"status": "FAILED", "run_type": "measurement"},
        ],
    }

    assert bundle_failed_query_count(data) == 1


@pytest.mark.parametrize(
    ("failed", "expected"),
    [(1.5, 1), (True, 1), ("2", 2)],
)
def test_bundle_failed_count_numeric_contract(failed: object, expected: int) -> None:
    data = {
        "summary": {"queries": {"total": 1, "passed": 1, "failed": failed}},
        "queries": [{"status": "FAILED", "run_type": "measurement"}],
    }

    assert bundle_failed_query_count(data) == expected


def test_result_failed_query_count_trusts_explicit_zero_failed_count() -> None:
    result = SimpleNamespace(total_queries=2, successful_queries=1, failed_queries=0, validation_status="PASSED")

    assert result_failed_query_count(result) == 0


@pytest.mark.parametrize("status", ["not_run", "uncertain", "not_validated", "unknown"])
def test_bundle_unchecked_validation_statuses_are_not_clean(status: str) -> None:
    data = {
        "summary": {"validation": status, "queries": {"total": 1, "passed": 1, "failed": 0}},
        "queries": [{"id": "Q1", "status": "SUCCESS", "run_type": "measurement"}],
    }

    assert bundle_is_clean_pass(data) is False


@pytest.mark.parametrize("status", ["fallback", "failed"])
def test_bundle_non_clean_translation_statuses_are_not_clean(status: str) -> None:
    data = {
        "summary": {"validation": "passed", "queries": {"total": 1, "passed": 1, "failed": 0}},
        "execution": {"translation": {"status": status}},
        "queries": [{"id": "Q1", "status": "SUCCESS", "run_type": "measurement"}],
    }

    assert bundle_is_clean_pass(data) is False


@pytest.mark.parametrize("status", ["NOT_RUN", "UNCERTAIN", "NOT_VALIDATED", "UNKNOWN"])
def test_result_unchecked_validation_statuses_are_not_clean(status: str) -> None:
    result = SimpleNamespace(total_queries=1, successful_queries=1, failed_queries=0, validation_status=status)

    assert result_is_clean_pass(result) is False


@pytest.mark.parametrize("status", ["fallback", "failed"])
def test_result_non_clean_translation_statuses_are_not_clean(status: str) -> None:
    result = SimpleNamespace(
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        validation_status="PASSED",
        execution_metadata={"translation": {"status": status}},
    )

    assert result_is_clean_pass(result) is False


@pytest.mark.parametrize("status", ["not_run", "NOT_RUN", "not_validated", "uncertain", "unknown"])
def test_unexecuted_validation_is_not_a_cli_failure(status: str) -> None:

    result = SimpleNamespace(total_queries=1, successful_queries=1, failed_queries=0, validation_status=status)
    assert not result_is_clean_pass(result)
    assert result_cli_failure_reason(result) is None


@pytest.mark.parametrize("status", ["failed", "interrupted", "partial", "error"])
def test_executed_validation_failures_are_cli_failures(status: str) -> None:
    result = SimpleNamespace(total_queries=1, successful_queries=1, failed_queries=0, validation_status=status)
    assert result_cli_failure_reason(result) == f"validation_status={status}"


def test_failed_queries_are_cli_failures_regardless_of_validation() -> None:
    result = SimpleNamespace(total_queries=2, successful_queries=1, failed_queries=1, validation_status="not_run")
    assert result_cli_failure_reason(result) == "1 failed query"


def test_translation_fallback_is_a_cli_failure() -> None:
    result = SimpleNamespace(
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        validation_status="passed",
        execution_metadata={"translation": {"status": "fallback"}},
    )
    assert result_cli_failure_reason(result) == "translation_status=fallback"


def test_validation_status_set_partition() -> None:

    assert CLI_FAILURE_VALIDATION_STATUSES < NON_CLEAN_VALIDATION_STATUSES
    assert UNVALIDATED_VALIDATION_STATUSES == NON_CLEAN_VALIDATION_STATUSES - CLI_FAILURE_VALIDATION_STATUSES
    assert UNVALIDATED_VALIDATION_STATUSES
    assert not (UNVALIDATED_VALIDATION_STATUSES & CLI_FAILURE_VALIDATION_STATUSES)


@pytest.mark.parametrize("status", sorted(UNVALIDATED_VALIDATION_STATUSES))
def test_result_unvalidated_reason_for_unvalidated_statuses(status: str) -> None:
    result = SimpleNamespace(total_queries=1, successful_queries=1, failed_queries=0, validation_status=status)
    assert result_unvalidated_reason(result) == f"validation_status={status}"


def test_result_unvalidated_reason_none_when_query_failed() -> None:
    result = SimpleNamespace(total_queries=2, successful_queries=1, failed_queries=1, validation_status="not_run")
    assert result_unvalidated_reason(result) is None


@pytest.mark.parametrize("status", sorted(CLI_FAILURE_VALIDATION_STATUSES))
def test_result_unvalidated_reason_none_for_cli_failure_statuses(status: str) -> None:
    result = SimpleNamespace(total_queries=1, successful_queries=1, failed_queries=0, validation_status=status)
    assert result_unvalidated_reason(result) is None


@pytest.mark.parametrize("run_type", ["warmup", "measurement"])
def test_failed_query_validation_prevents_a_clean_pass_without_changing_measurement_counts(run_type):
    query_rows = [
        {
            "id": "Q18",
            "status": "FAILED",
            "run_type": run_type,
            "row_count_validation": {"status": "FAILED", "expected": 57, "actual": 9},
        },
        {"id": "Q18", "status": "SUCCESS", "run_type": "measurement"},
    ]
    result = SimpleNamespace(
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        validation_status="PASSED",
        query_results=query_rows,
    )
    assert result_failed_query_count(result) == 0
    assert not result_is_clean_pass(result)
    assert result_non_clean_reason(result) == "query validation failed"
    assert result_cli_failure_reason(result) == "query validation failed"
    assert not bundle_is_clean_pass(
        {"summary": {"validation": "passed", "queries": {"total": 1, "passed": 1, "failed": 0}}, "queries": query_rows}
    )


@pytest.mark.parametrize("status", ["PASSED", "SKIPPED"])
def test_warmup_without_failed_validation_preserves_clean_measurements(status):
    result = SimpleNamespace(
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        validation_status="PASSED",
        query_results=[
            {"status": "SUCCESS", "run_type": "warmup", "row_count_validation": {"status": status}},
            {"status": "SUCCESS", "run_type": "measurement"},
        ],
    )
    assert result_is_clean_pass(result)
    assert result_cli_failure_reason(result) is None


def _obt_bundle(platform: str, failed_ids: list[str], driver_version: str = "4.1.6") -> dict:
    rows = [{"id": str(i), "status": "SUCCESS", "run_type": "measurement"} for i in range(1, 4)]
    rows += [{"id": qid, "status": "FAILED", "run_type": "measurement"} for qid in failed_ids]
    return {
        "benchmark": {"id": "tpcds_obt"},
        "platform": {"name": platform},
        "execution": {"driver_version_actual": driver_version},
        "summary": {"validation": "partial", "queries": {"total": len(rows), "passed": 3, "failed": len(failed_ids)}},
        "queries": rows,
    }


def test_documented_exclusions_cover_only_the_evidenced_failures():
    assert documented_exclusions_cover_failures(_obt_bundle("ClickHouse Local", ["41", "44"]))
    assert documented_exclusions_cover_failures(_obt_bundle("ClickHouse Local", ["44"]))


@pytest.mark.parametrize(
    ("platform", "failed_ids", "driver_version"),
    [
        ("ClickHouse Local", ["41", "45"], "4.1.6"),
        ("DuckDB", ["41", "44"], "4.1.6"),
        ("ClickHouse Local", [], "4.1.6"),
        ("ClickHouse Local", ["41", "44"], "4.2.0"),
    ],
)
def test_documented_exclusions_reject_everything_else(platform, failed_ids, driver_version):
    assert not documented_exclusions_cover_failures(_obt_bundle(platform, failed_ids, driver_version))


@pytest.mark.parametrize("field", ["failed", "total"])
def test_documented_exclusions_require_consistent_summary_counts(field):
    bundle = _obt_bundle("ClickHouse Local", ["41", "44"])
    bundle["summary"]["queries"][field] += 3
    assert not documented_exclusions_cover_failures(bundle)


def test_documented_exclusions_do_not_hide_failed_queries():
    bundle = _obt_bundle("ClickHouse Local", ["41", "44"])
    assert bundle_failed_query_count(bundle) == 2
    assert not bundle_is_clean_pass(bundle)
