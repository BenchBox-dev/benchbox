from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.dataframe.query_validation import validate_dataframe_query_results
from benchbox.core.expected_results.ssb_results import SSB_EXPECTED_ROW_COUNTS

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _measurement(query_id: str = "Q1", rows: int | None = 4, status: str = "SUCCESS") -> dict[str, object]:
    result: dict[str, object] = {
        "query_id": query_id,
        "status": status,
        "run_type": "measurement",
        "stream_id": 3,
        "iteration": 2,
    }
    if rows is not None:
        result["rows_returned"] = rows
    return result


def test_tpch_reference_result_records_real_pass_evidence() -> None:
    warmup = {**_measurement(rows=999), "run_type": "warmup"}
    measurement = _measurement()

    summary = validate_dataframe_query_results(
        [warmup, measurement],
        benchmark_name="tpch",
        scale_factor=1.0,
    )

    assert summary.status == "PASSED"
    assert summary.details == {
        "provider": "tpc_expected_row_counts",
        "benchmark": "tpch",
        "checked": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
    }
    assert "row_count_validation" not in warmup
    assert measurement["row_count_validation"] == {"expected": 4, "actual": 4, "status": "PASSED"}


def test_skipped_warmup_is_not_validation_evidence() -> None:
    warmup = {**_measurement(query_id="Q2", rows=None, status="SKIPPED"), "run_type": "warmup"}

    summary = validate_dataframe_query_results(
        [warmup, _measurement()],
        benchmark_name="tpch",
        scale_factor=1.0,
    )

    assert summary.status == "PASSED"
    assert summary.details["skipped"] == 0
    assert "row_count_validation" not in warmup


def test_tpch_mismatch_marks_the_query_and_run_failed() -> None:
    measurement = _measurement(rows=3)

    summary = validate_dataframe_query_results(
        [measurement],
        benchmark_name="TPC-H",
        scale_factor=1.0,
    )

    assert summary.status == "FAILED"
    assert summary.details["failed"] == 1
    assert measurement["status"] == "FAILED"
    assert measurement["row_count_validation"]["status"] == "FAILED"


def test_tpcds_is_uncertain_until_its_parameters_are_seed_aligned() -> None:
    measurement = _measurement(rows=1)

    summary = validate_dataframe_query_results(
        [measurement],
        benchmark_name="tpcds",
        scale_factor=1.0,
    )

    assert summary.status == "UNCERTAIN"
    assert summary.details["checked"] == 0
    assert summary.details["skipped"] == 1
    assert measurement["status"] == "SUCCESS"
    assert measurement["row_count_validation"]["status"] == "SKIPPED"


@pytest.mark.parametrize(
    ("benchmark_name", "scale_factor", "seed", "expected_status"),
    [
        ("clickbench", 1.0, None, "NOT_RUN"),
        ("ssb", 0.5, None, "UNCERTAIN"),
        ("tpch", 0.5, None, "UNCERTAIN"),
        ("tpch", 1.0, 123, "UNCERTAIN"),
        ("tpch", 1.0, 17039360, "UNCERTAIN"),
    ],
)
def test_unsupported_or_unaligned_oracles_never_claim_passed(
    benchmark_name: str,
    scale_factor: float,
    seed: int | None,
    expected_status: str,
) -> None:
    summary = validate_dataframe_query_results(
        [_measurement()],
        benchmark_name=benchmark_name,
        scale_factor=scale_factor,
        seed=seed,
    )

    assert summary.status == expected_status
    assert summary.details["checked"] == 0


def test_disabled_validation_is_not_run() -> None:
    measurement = _measurement()

    summary = validate_dataframe_query_results(
        [measurement],
        benchmark_name="tpch",
        scale_factor=1.0,
        validation_mode="disabled",
    )

    assert summary.status == "NOT_RUN"
    assert "row_count_validation" not in measurement


def test_unsupported_validation_mode_is_uncertain() -> None:
    measurement = _measurement()

    summary = validate_dataframe_query_results(
        [measurement],
        benchmark_name="tpch",
        scale_factor=1.0,
        validation_mode="loose",
    )

    assert summary.status == "UNCERTAIN"
    assert measurement["row_count_validation"]["status"] == "SKIPPED"


def test_missing_row_count_is_a_failed_success_contract() -> None:
    measurement = _measurement(rows=None)

    summary = validate_dataframe_query_results(
        [measurement],
        benchmark_name="tpch",
        scale_factor=1.0,
    )

    assert summary.status == "FAILED"
    assert summary.details["errors"] == 1
    assert measurement["status"] == "FAILED"
    assert measurement["row_count_validation"]["status"] == "ERROR"


def test_oracle_error_makes_the_run_uncertain_without_condemning_the_result() -> None:
    measurement = _measurement()

    with patch(
        "benchbox.core.dataframe.query_validation.QueryValidator.validate_query_result",
        side_effect=RuntimeError("oracle unavailable"),
    ):
        summary = validate_dataframe_query_results(
            [measurement],
            benchmark_name="tpch",
            scale_factor=1.0,
        )

    assert summary.status == "UNCERTAIN"
    assert summary.details["errors"] == 1
    assert measurement["status"] == "SUCCESS"
    assert measurement["row_count_validation"]["status"] == "ERROR"


def test_oracle_error_evidence_is_bounded() -> None:
    measurement = _measurement()

    with patch(
        "benchbox.core.dataframe.query_validation.QueryValidator.validate_query_result",
        side_effect=RuntimeError("x" * 2_000),
    ):
        validate_dataframe_query_results(
            [measurement],
            benchmark_name="tpch",
            scale_factor=1.0,
        )

    error = measurement["row_count_validation"]["error"]
    assert len(error) == 500
    assert error.endswith("...")


def test_oracle_initialization_error_is_uncertain() -> None:
    measurement = _measurement()

    with patch("benchbox.core.dataframe.query_validation.QueryValidator", side_effect=RuntimeError("no registry")):
        summary = validate_dataframe_query_results(
            [measurement],
            benchmark_name="tpch",
            scale_factor=1.0,
        )

    assert summary.status == "UNCERTAIN"
    assert summary.details["errors"] == 1
    assert measurement["status"] == "SUCCESS"
    assert measurement["row_count_validation"]["status"] == "ERROR"


def test_execution_failure_keeps_an_unvalidated_run_partial() -> None:
    failed = _measurement(rows=None, status="FAILED")

    summary = validate_dataframe_query_results(
        [failed],
        benchmark_name="ssb",
        scale_factor=1.0,
    )

    assert summary.status == "PARTIAL"


def test_selected_dataframe_skip_prevents_a_clean_pass() -> None:
    skipped = {
        "query_id": "Q2",
        "status": "SKIPPED",
        "run_type": "metadata",
    }

    summary = validate_dataframe_query_results(
        [_measurement(), skipped],
        benchmark_name="tpch",
        scale_factor=1.0,
    )

    assert summary.status == "UNCERTAIN"
    assert summary.details["checked"] == 1
    assert summary.details["skipped"] == 1


@pytest.mark.parametrize("seed", [0, 1, 42, 17039360, 101000000])
def test_tpch_answer_set_context_is_false_for_any_seed(seed: int) -> None:
    from benchbox.core.dataframe.query_validation import _tpch_reference_context

    assert _tpch_reference_context(1.0, seed) is False
    assert _tpch_reference_context(1.0, None) is True
    assert _tpch_reference_context(0.01, None) is True
    assert _tpch_reference_context(0.1, None) is True
    assert _tpch_reference_context(0.5, None) is False


@pytest.mark.parametrize("scale_factor", [0.01, 0.1, 1.0, 10.0])
def test_ssb_expected_row_counts_record_a_pass(scale_factor: float) -> None:
    counts = SSB_EXPECTED_ROW_COUNTS[scale_factor]
    measurements = [_measurement(query_id=query_id, rows=rows) for query_id, rows in counts.items()]

    summary = validate_dataframe_query_results(
        measurements,
        benchmark_name="ssb",
        scale_factor=scale_factor,
        seed=7,
    )

    assert summary.status == "PASSED"
    assert summary.details == {
        "provider": "ssb_expected_row_counts",
        "benchmark": "ssb",
        "checked": 13,
        "passed": 13,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
    }
    assert {row["row_count_validation"]["status"] for row in measurements} == {"PASSED"}


def test_ssb_query_ids_with_a_q_prefix_match_the_expected_counts() -> None:
    measurement = _measurement(query_id="Q4.3", rows=242)

    summary = validate_dataframe_query_results([measurement], benchmark_name="ssb", scale_factor=0.1)

    assert summary.status == "PASSED"
    assert measurement["row_count_validation"] == {"expected": 242, "actual": 242, "status": "PASSED"}


def test_ssb_wrong_row_count_fails_the_query_and_run() -> None:
    correct = _measurement(query_id="2.1", rows=280)
    wrong = _measurement(query_id="3.2", rows=397)

    summary = validate_dataframe_query_results([correct, wrong], benchmark_name="ssb", scale_factor=0.1)

    assert summary.status == "FAILED"
    assert summary.details["checked"] == 2
    assert summary.details["failed"] == 1
    assert correct["status"] == "SUCCESS"
    assert wrong["status"] == "FAILED"
    assert wrong["row_count_validation"]["status"] == "FAILED"
    assert wrong["row_count_validation"]["expected"] == 398
    assert wrong["row_count_validation"]["actual"] == 397
    assert "expected 398" in wrong["error"]


def test_ssb_unknown_query_is_uncertain_not_passed() -> None:
    measurement = _measurement(query_id="9.9", rows=1)

    summary = validate_dataframe_query_results([measurement], benchmark_name="ssb", scale_factor=0.1)

    assert summary.status == "UNCERTAIN"
    assert summary.details["checked"] == 0
    assert summary.details["skipped"] == 1
    assert measurement["status"] == "SUCCESS"
    assert measurement["row_count_validation"]["status"] == "SKIPPED"


def test_ssb_unrecorded_scale_factor_is_uncertain() -> None:
    measurement = _measurement(query_id="1.1", rows=1)

    summary = validate_dataframe_query_results([measurement], benchmark_name="ssb", scale_factor=0.5)

    assert summary.status == "UNCERTAIN"
    assert summary.details["provider"] == "ssb_expected_row_counts"
    assert "scale factor 0.5" in summary.details["reason"]
    assert measurement["row_count_validation"]["status"] == "SKIPPED"


@pytest.mark.parametrize("scale_factor", [0.01, 0.1])
def test_tpch_subscale_reference_counts_record_a_pass(scale_factor: float) -> None:
    from benchbox.core.expected_results.loader import load_tpch_subscale_row_counts

    counts = load_tpch_subscale_row_counts(scale_factor).row_counts
    measurements = [_measurement(query_id=f"Q{query_id}", rows=rows) for query_id, rows in counts.items()]

    summary = validate_dataframe_query_results(measurements, benchmark_name="tpch", scale_factor=scale_factor)

    assert summary.status == "PASSED"
    assert summary.details["checked"] == 22
    assert summary.details["passed"] == 22
    assert {row["row_count_validation"]["status"] for row in measurements} == {"PASSED"}


def test_tpch_subscale_wrong_count_fails_the_query_and_run() -> None:
    from benchbox.core.expected_results.loader import load_tpch_subscale_row_counts

    counts = load_tpch_subscale_row_counts(0.01).row_counts
    measurements = [_measurement(query_id=query_id, rows=rows) for query_id, rows in counts.items()]
    wrong = next(row for row in measurements if row["query_id"] == "15")
    wrong["rows_returned"] = 0

    summary = validate_dataframe_query_results(measurements, benchmark_name="tpch", scale_factor=0.01)

    assert summary.status == "FAILED"
    assert summary.details["failed"] == 1
    assert wrong["status"] == "FAILED"
    assert wrong["row_count_validation"] == {
        "expected": 1,
        "actual": 0,
        "status": "FAILED",
        "error": wrong["row_count_validation"]["error"],
    }


def test_seeded_subscale_run_checks_fixed_queries_and_skips_parameter_sensitive_ones() -> None:
    from benchbox.core.expected_results.loader import load_tpch_subscale_row_counts

    reference = load_tpch_subscale_row_counts(0.01)
    measurements = [_measurement(query_id=f"Q{qid}", rows=rows) for qid, rows in reference.row_counts.items()]

    summary = validate_dataframe_query_results(measurements, benchmark_name="tpch", scale_factor=0.01, seed=7)

    statuses = {row["query_id"]: row["row_count_validation"]["status"] for row in measurements}
    assert summary.status == "UNCERTAIN"
    assert summary.details["skipped"] == len(reference.parameter_sensitive_query_ids)
    assert summary.details["checked"] == 22 - len(reference.parameter_sensitive_query_ids)
    assert statuses["Q2"] == "SKIPPED"
    assert statuses["Q4"] == "PASSED"


def test_seeded_subscale_run_fails_a_wrong_fixed_cardinality_count() -> None:
    measurement = _measurement(query_id="Q4", rows=4)

    summary = validate_dataframe_query_results([measurement], benchmark_name="tpch", scale_factor=0.01, seed=7)

    assert summary.status == "FAILED"
    assert measurement["row_count_validation"]["expected"] == 5


def test_seeded_validation_leaves_the_reference_seed_context_unchanged() -> None:
    from benchbox.core.validation.query_validation import get_reference_seed_context

    validate_dataframe_query_results(
        [_measurement(query_id="Q4", rows=5)], benchmark_name="tpch", scale_factor=0.01, seed=7
    )

    assert get_reference_seed_context() is None
