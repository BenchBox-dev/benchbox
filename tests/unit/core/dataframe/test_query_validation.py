from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.dataframe.query_validation import _apply_row_count_oracle, validate_dataframe_query_results
from benchbox.core.expected_results.models import ValidationMode, ValidationResult
from benchbox.core.expected_results.ssb_results import ssb_expected_row_counts
from benchbox.core.results.query_status import (
    NO_REFERENCE_REASON,
    NO_REFERENCE_VALIDATION_STATUS,
    unevidenced_validation_status,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(autouse=True)
def _isolated_validation_mode_context():
    from benchbox.core.validation.query_validation import clear_validation_mode_context

    clear_validation_mode_context()
    yield
    clear_validation_mode_context()


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
        "reference_kinds": {"tpc_answer_set": {"label": "TPC answer set", "rows": 1}},
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


def test_tpcds_scale_one_is_checked_against_the_row_count_of_the_stream_that_ran() -> None:
    from benchbox.core.expected_results.row_count_references import load_row_count_reference

    reference = load_row_count_reference("tpcds", 1.0)
    measurement = _measurement(rows=reference.streams[3].row_counts["1"])

    summary = validate_dataframe_query_results([measurement], benchmark_name="tpcds", scale_factor=1.0)

    assert summary.status == "PASSED"
    assert summary.details["checked"] == 1
    assert measurement["row_count_validation"]["expected"] == reference.streams[3].row_counts["1"]
    assert summary.details["reference_kinds"]["engine_agreement"]["rows"] == 1


@pytest.mark.parametrize(
    ("benchmark_name", "scale_factor", "seed", "expected_status"),
    [
        ("clickbench", 1.0, None, "NOT_RUN"),
        ("ssb", 0.5, None, "NO_REFERENCE"),
        ("tpch", 0.5, None, "NO_REFERENCE"),
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


@pytest.mark.parametrize(("benchmark_name", "scale_factor"), [("tpch", 100.0), ("tpcds", 100.0), ("ssb", 0.5)])
def test_dataframe_and_sql_runs_without_a_reference_record_the_same_status_and_reason(
    benchmark_name: str, scale_factor: float
) -> None:
    measurement = _measurement()
    sql_rows = [{"query_id": "1", "run_type": "measurement", "row_count_validation": {"status": "UNVALIDATED"}}]

    summary = validate_dataframe_query_results([measurement], benchmark_name=benchmark_name, scale_factor=scale_factor)

    assert (
        summary.status
        == unevidenced_validation_status(sql_rows, reference_exists=False)
        == NO_REFERENCE_VALIDATION_STATUS
    )
    assert summary.details["reason"] == NO_REFERENCE_REASON
    assert measurement["row_count_validation"]["status"] == "UNVALIDATED"
    assert measurement["row_count_validation"]["warning"] == NO_REFERENCE_REASON


def test_the_no_reference_reason_is_reported_only_where_no_reference_exists() -> None:
    referenced = validate_dataframe_query_results([_measurement(rows=1)], benchmark_name="tpcds", scale_factor=1.0)
    unreferenced = validate_dataframe_query_results([_measurement()], benchmark_name="tpcds", scale_factor=100.0)

    assert referenced.details.get("reason") != NO_REFERENCE_REASON
    assert unreferenced.details["reason"] == NO_REFERENCE_REASON


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


@pytest.mark.parametrize("benchmark_name", ["ssb", "tpcds"])
def test_a_refused_reference_is_recorded_as_a_validation_error(benchmark_name: str) -> None:
    measurement = _measurement("Q1.1" if benchmark_name == "ssb" else "Q1")

    with patch(
        "benchbox.core.dataframe.query_validation.load_row_count_reference",
        side_effect=ValueError("reference data stamp no longer matches"),
    ):
        summary = validate_dataframe_query_results([measurement], benchmark_name=benchmark_name, scale_factor=1.0)

    assert summary.status == "UNCERTAIN"
    assert summary.details["errors"] == 1
    assert "data stamp" in summary.details["reason"]
    assert measurement["status"] == "SUCCESS"
    assert measurement["row_count_validation"]["status"] == "ERROR"
    assert "data stamp" in measurement["row_count_validation"]["error"]


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


def _ssb_label(scale_factor: float) -> str:
    from benchbox.core.expected_results.row_count_references import load_row_count_reference

    return load_row_count_reference("ssb", scale_factor).label


@pytest.mark.parametrize("scale_factor", [0.01, 0.1, 1.0])
def test_ssb_expected_row_counts_record_a_pass(scale_factor: float) -> None:
    counts = ssb_expected_row_counts(scale_factor)
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
        "reference_kinds": {
            "engine_agreement": {"label": _ssb_label(scale_factor), "rows": 13},
        },
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


def test_ssb_unknown_query_fails_the_lookup_and_the_run() -> None:
    measurement = _measurement(query_id="9.9", rows=1)

    summary = validate_dataframe_query_results([measurement], benchmark_name="ssb", scale_factor=0.1)

    assert summary.status == "FAILED"
    assert summary.details["checked"] == 1
    assert summary.details["failed"] == 1
    assert measurement["status"] == "FAILED"
    assert measurement["row_count_validation"]["status"] == "FAILED"
    assert "Row-count lookup failed" in measurement["error"]


def test_ssb_unrecorded_scale_factor_is_unvalidated_not_passed() -> None:
    measurement = _measurement(query_id="1.1", rows=1)

    summary = validate_dataframe_query_results([measurement], benchmark_name="ssb", scale_factor=0.5)

    assert summary.status == NO_REFERENCE_VALIDATION_STATUS
    assert summary.details["provider"] == "ssb_expected_row_counts"
    assert summary.details["reason"] == NO_REFERENCE_REASON
    assert measurement["row_count_validation"]["status"] == "UNVALIDATED"


def test_tpcds_every_query_has_a_count_and_unknown_variant_fails() -> None:
    from benchbox.core.expected_results.row_count_references import load_row_count_reference

    recorded = load_row_count_reference("tpcds", 0.1).streams[0].row_counts["21"]
    gap = {**_measurement(query_id="21", rows=recorded), "stream_id": 0}
    typo = {**_measurement(query_id="14c", rows=1), "stream_id": 0}

    summary = validate_dataframe_query_results([gap, typo], benchmark_name="tpcds", scale_factor=0.1, seed=None)

    assert summary.status == "FAILED"
    assert gap["row_count_validation"]["status"] == "PASSED"
    assert gap["status"] == "SUCCESS"
    assert typo["row_count_validation"]["status"] == "FAILED"
    assert typo["status"] == "FAILED"


@pytest.mark.parametrize("scale_factor", [0.01, 0.1])
def test_tpch_subscale_reference_counts_record_a_pass(scale_factor: float) -> None:
    from benchbox.core.expected_results.row_count_references import load_row_count_reference

    counts = load_row_count_reference("tpch", scale_factor).streams[0].row_counts
    measurements = [_measurement(query_id=f"Q{query_id}", rows=rows) for query_id, rows in counts.items()]

    summary = validate_dataframe_query_results(measurements, benchmark_name="tpch", scale_factor=scale_factor)

    assert summary.status == "PASSED"
    assert summary.details["checked"] == 22
    assert summary.details["passed"] == 22
    assert {row["row_count_validation"]["status"] for row in measurements} == {"PASSED"}


def test_tpch_subscale_wrong_count_fails_the_query_and_run() -> None:
    from benchbox.core.expected_results.row_count_references import load_row_count_reference

    counts = load_row_count_reference("tpch", 0.01).streams[0].row_counts
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
    from benchbox.core.expected_results.row_count_references import load_row_count_reference

    reference = load_row_count_reference("tpch", 0.01)
    measurements = [
        _measurement(query_id=f"Q{qid}", rows=rows) for qid, rows in reference.streams[0].row_counts.items()
    ]

    summary = validate_dataframe_query_results(measurements, benchmark_name="tpch", scale_factor=0.01, seed=7)

    statuses = {row["query_id"]: row["row_count_validation"]["status"] for row in measurements}
    assert summary.status == "PASSED"
    assert summary.details["skipped"] == len(reference.parameter_sensitive_query_ids)
    assert summary.details["documented_skips"] == len(reference.parameter_sensitive_query_ids)
    assert summary.details["checked"] == 22 - len(reference.parameter_sensitive_query_ids)
    assert statuses["Q2"] == "SKIPPED"
    assert statuses["Q4"] == "PASSED"


def test_seeded_subscale_run_with_only_parameter_sensitive_queries_is_not_a_pass() -> None:
    summary = validate_dataframe_query_results(
        [_measurement(query_id="Q2", rows=3)], benchmark_name="tpch", scale_factor=0.01, seed=7
    )

    assert summary.status == "UNCERTAIN"
    assert summary.details["checked"] == 0


def test_seeded_subscale_run_with_a_deselected_query_stays_uncertain() -> None:
    from benchbox.core.expected_results.row_count_references import load_row_count_reference

    reference = load_row_count_reference("tpch", 0.01)
    measurements = [
        _measurement(query_id=f"Q{qid}", rows=rows) for qid, rows in reference.streams[0].row_counts.items()
    ]
    deselected = {"query_id": "Q23", "status": "SKIPPED", "run_type": "metadata"}

    summary = validate_dataframe_query_results(
        [*measurements, deselected], benchmark_name="tpch", scale_factor=0.01, seed=7
    )

    assert summary.status == "UNCERTAIN"


def test_seeded_subscale_sql_rows_pass_the_bundle_consistency_check() -> None:
    from benchbox.core.expected_results.row_count_references import load_row_count_reference
    from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
    from benchbox.core.results.platform_info import PlatformInfoInput
    from benchbox.core.results.query_normalizer import normalize_query_result
    from benchbox.core.results.schema import build_result_payload
    from benchbox.validation.bundle import ValidationResult as BundleValidationResult, _validate_execution_consistency

    reference = load_row_count_reference("tpch", 0.01)
    measurements = [
        _measurement(query_id=f"Q{qid}", rows=rows) for qid, rows in reference.streams[0].row_counts.items()
    ]
    validate_dataframe_query_results(measurements, benchmark_name="tpch", scale_factor=0.01, seed=7)
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=0.01),
        platform=PlatformInfoInput(name="duckdb", platform_version="1.3.2"),
    )
    builder.add_query_results(
        [normalize_query_result({**row, "iteration": 1, "execution_time_seconds": 0.5}) for row in measurements]
    )
    payload = build_result_payload(builder.build())
    check = BundleValidationResult("test")
    _validate_execution_consistency(payload, check)

    assert payload["summary"]["validation"] == "passed"
    assert check.errors == []


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


@pytest.mark.parametrize(("benchmark_id", "scale_factor"), [("tpch", 10.0), ("tpcds", 10.0), ("ssb", 0.5)])
def test_dataframe_scales_without_reference_counts_are_unvalidated(benchmark_id: str, scale_factor: float) -> None:
    measurement = _measurement(query_id="1", rows=1)

    validate_dataframe_query_results([measurement], benchmark_name=benchmark_id, scale_factor=scale_factor)

    assert measurement["row_count_validation"]["status"] == "UNVALIDATED"


def test_loose_tolerance_match_is_not_recorded_as_passed() -> None:
    row = _measurement(query_id="16", rows=17900)
    evidence = {"checked": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}

    _apply_row_count_oracle(
        [row],
        lambda query_id, actual, _row: ValidationResult(
            is_valid=True,
            query_id=query_id,
            expected_row_count=18314,
            actual_row_count=actual,
            validation_mode=ValidationMode.LOOSE,
        ),
        evidence,
    )

    validation = row["row_count_validation"]
    assert validation["status"] == "SKIPPED"
    assert (validation["expected"], validation["actual"]) == (18314, 17900)
    assert "not an exact match" in validation["warning"]
    assert evidence == {"checked": 0, "passed": 0, "failed": 0, "skipped": 1, "errors": 0, "documented_skips": 1}


def _tpch_sf1_reference_rows() -> list[dict[str, object]]:
    from benchbox.core.expected_results.tpch_results import get_tpch_expected_results

    reference = get_tpch_expected_results(1.0)
    return [
        _measurement(query_id=f"Q{query_id}", rows=result.expected_row_count)
        for query_id, result in reference.query_results.items()
    ]


@pytest.mark.parametrize("seed", [42, 123, 17039360])
def test_seeded_sf1_run_validates_through_the_sql_reference(seed: int) -> None:
    measurements = _tpch_sf1_reference_rows()

    summary = validate_dataframe_query_results(measurements, benchmark_name="tpch", scale_factor=1.0, seed=seed)

    assert summary.status == "PASSED"
    assert summary.details["failed"] == 0
    assert summary.details["checked"] == len(measurements)
    assert {row["row_count_validation"]["status"] for row in measurements} == {"PASSED"}


def test_seeded_sf1_parameter_sensitive_query_is_range_checked() -> None:
    sensitive = _measurement(query_id="Q11", rows=600)
    fixed = _measurement(query_id="Q4", rows=5)

    summary = validate_dataframe_query_results([sensitive, fixed], benchmark_name="tpch", scale_factor=1.0, seed=42)

    assert summary.status == "PASSED"
    assert sensitive["row_count_validation"]["status"] == "PASSED"
    assert fixed["row_count_validation"]["status"] == "PASSED"


def test_seeded_sf1_parameter_sensitive_query_outside_its_range_fails() -> None:
    sensitive = _measurement(query_id="Q11", rows=3)

    summary = validate_dataframe_query_results([sensitive], benchmark_name="tpch", scale_factor=1.0, seed=42)

    assert summary.status == "FAILED"


def test_seeded_sf1_run_fails_a_wrong_fixed_count() -> None:
    measurement = _measurement(query_id="Q4", rows=4)

    summary = validate_dataframe_query_results([measurement], benchmark_name="tpch", scale_factor=1.0, seed=42)

    assert summary.status == "FAILED"
    assert measurement["row_count_validation"]["status"] == "FAILED"
    assert measurement["row_count_validation"]["expected"] == 5


def test_unseeded_sf1_run_still_checks_every_query_against_the_reference() -> None:
    measurements = _tpch_sf1_reference_rows()

    summary = validate_dataframe_query_results(measurements, benchmark_name="tpch", scale_factor=1.0)

    assert summary.status == "PASSED"
    assert summary.details["skipped"] == 0
