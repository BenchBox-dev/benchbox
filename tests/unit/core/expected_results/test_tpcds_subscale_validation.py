import pytest

from benchbox.core.dataframe.query_validation import validate_dataframe_query_results
from benchbox.core.expected_results.models import ValidationMode
from benchbox.core.expected_results.tpcds_results import (
    TPCDS_PARAMETER_SENSITIVE_QUERY_IDS,
    TPCDS_SUBSCALE_PROVENANCE,
    TPCDS_SUBSCALE_ROW_COUNTS,
    get_tpcds_expected_results,
    tpcds_subscale_expected_row_counts,
)
from benchbox.core.validation.query_validation import (
    QueryValidator,
    clear_reference_seed_context,
    set_reference_seed_context,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def validator():
    from benchbox.core.expected_results.tpcds_results import set_query_validation_mode

    set_query_validation_mode(ValidationMode.EXACT)
    yield QueryValidator()
    set_query_validation_mode(None)


@pytest.fixture(autouse=True)
def _clear_reference_seed():
    clear_reference_seed_context()
    yield
    clear_reference_seed_context()


def _measurement(query_id: str, rows: int) -> dict[str, object]:
    return {
        "query_id": query_id,
        "status": "SUCCESS",
        "run_type": "measurement",
        "stream_id": 0,
        "iteration": 1,
        "rows_returned": rows,
    }


@pytest.mark.parametrize("scale_factor", sorted(TPCDS_SUBSCALE_ROW_COUNTS))
def test_tpcds_subscale_provider_returns_recorded_counts(scale_factor: float) -> None:
    results = get_tpcds_expected_results(scale_factor=scale_factor)

    assert results is not None
    assert results.benchmark_name == "tpcds"
    assert results.scale_factor == scale_factor
    assert {query_id: result.expected_row_count for query_id, result in results.query_results.items()} == (
        TPCDS_SUBSCALE_ROW_COUNTS[scale_factor]
    )
    assert {result.validation_mode for result in results.query_results.values()} == {ValidationMode.EXACT}
    assert results.metadata["provenance"] == TPCDS_SUBSCALE_PROVENANCE


def test_tpcds_subscale_counts_cover_every_query_except_engine_disagreements() -> None:
    assert set(TPCDS_SUBSCALE_ROW_COUNTS) == {0.01, 0.1}
    assert len(TPCDS_SUBSCALE_ROW_COUNTS[0.01]) == 102
    assert len(TPCDS_SUBSCALE_ROW_COUNTS[0.1]) == 101
    assert "21" not in TPCDS_SUBSCALE_ROW_COUNTS[0.01]
    assert "21" not in TPCDS_SUBSCALE_ROW_COUNTS[0.1]
    assert "34" not in TPCDS_SUBSCALE_ROW_COUNTS[0.1]
    assert tpcds_subscale_expected_row_counts(0.5) is None


def test_tpcds_provider_returns_none_for_unrecorded_scale() -> None:
    assert get_tpcds_expected_results(scale_factor=0.5) is None


def test_tpcds_sensitive_and_fixed_sets_partition_the_corpus() -> None:
    recorded = set(TPCDS_SUBSCALE_ROW_COUNTS[0.01]) | set(TPCDS_SUBSCALE_ROW_COUNTS[0.1])
    fixed = recorded - TPCDS_PARAMETER_SENSITIVE_QUERY_IDS

    assert "1" in TPCDS_PARAMETER_SENSITIVE_QUERY_IDS
    assert "98" in TPCDS_PARAMETER_SENSITIVE_QUERY_IDS
    assert "86" in fixed
    assert "93" in fixed
    assert len(fixed) == 47


def test_tpcds_subscale_sql_fixed_query_validates(validator) -> None:
    result = validator.validate_query_result(
        benchmark_type="tpcds",
        query_id="Q93",
        actual_row_count=TPCDS_SUBSCALE_ROW_COUNTS[0.01]["93"],
        scale_factor=0.01,
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.EXACT
    assert result.expected_row_count == TPCDS_SUBSCALE_ROW_COUNTS[0.01]["93"]


def test_tpcds_subscale_sql_mutated_count_fails(validator) -> None:
    expected = TPCDS_SUBSCALE_ROW_COUNTS[0.1]["86"]

    result = validator.validate_query_result(
        benchmark_type="tpcds",
        query_id="86",
        actual_row_count=expected + 1,
        scale_factor=0.1,
    )

    assert not result.is_valid
    assert result.validation_mode == ValidationMode.EXACT
    assert result.expected_row_count == expected


def test_tpcds_subscale_sql_sensitive_query_validates_at_reference_parameters(validator) -> None:
    result = validator.validate_query_result(
        benchmark_type="tpcds",
        query_id="1",
        actual_row_count=TPCDS_SUBSCALE_ROW_COUNTS[0.01]["1"],
        scale_factor=0.01,
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.EXACT


def test_tpcds_subscale_sql_sensitive_query_skipped_for_non_reference_parameters(validator) -> None:
    set_reference_seed_context(False)

    result = validator.validate_query_result(
        benchmark_type="tpcds",
        query_id="1",
        actual_row_count=999,
        scale_factor=0.01,
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.SKIP
    assert result.expected_row_count is None


def test_tpcds_subscale_sql_non_reference_seed_skips_validation(validator) -> None:
    set_reference_seed_context(False)
    expected = TPCDS_SUBSCALE_ROW_COUNTS[0.01]["93"]

    result = validator.validate_query_result(
        benchmark_type="tpcds",
        query_id="93",
        actual_row_count=expected + 1,
        scale_factor=0.01,
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.SKIP
    assert result.expected_row_count is None


def test_tpcds_subscale_variant_normalization(validator) -> None:
    result = validator.validate_query_result(
        benchmark_type="tpcds",
        query_id="Q14A",
        actual_row_count=TPCDS_SUBSCALE_ROW_COUNTS[0.01]["14a"],
        scale_factor=0.01,
    )
    assert result.is_valid
    assert result.validation_mode == ValidationMode.EXACT


def test_tpcds_subscale_default_mode_is_exact_without_configuration() -> None:
    validator = QueryValidator()
    result = validator.validate_query_result("tpcds", "86", TPCDS_SUBSCALE_ROW_COUNTS[0.01]["86"], 0.01)
    assert result.is_valid
    assert result.validation_mode == ValidationMode.EXACT


def test_tpcds_subscale_validation_mode_opt_out(validator) -> None:
    from benchbox.core.validation.query_validation import set_validation_mode_context

    set_validation_mode_context(ValidationMode.SKIP)
    try:
        result = validator.validate_query_result(
            benchmark_type="tpcds",
            query_id="86",
            actual_row_count=999,
            scale_factor=0.01,
        )
        assert result.is_valid
        assert result.validation_mode == ValidationMode.SKIP
    finally:
        set_validation_mode_context(None)


def test_tpcds_subscale_invalid_env_mode_does_not_opt_out(monkeypatch) -> None:
    from benchbox.core.validation.query_validation import set_validation_mode_context

    set_validation_mode_context(None)
    monkeypatch.setenv("BENCHBOX_QUERY_VALIDATION_MODE", "invalid_mode_override")
    validator = QueryValidator()
    result = validator.validate_query_result(
        benchmark_type="tpcds",
        query_id="86",
        actual_row_count=TPCDS_SUBSCALE_ROW_COUNTS[0.01]["86"],
        scale_factor=0.01,
    )
    assert result.is_valid
    assert result.validation_mode == ValidationMode.EXACT


def test_tpcds_subscale_corrupt_reference_degrades_gracefully() -> None:
    from unittest.mock import patch

    from benchbox.core.expected_results.tpcds_results import _load_subscale_reference

    with patch("pathlib.Path.read_text", side_effect=OSError("disk error")):
        assert _load_subscale_reference() == {}


def test_tpcds_unrecorded_scale_skips_not_fails(validator) -> None:
    result = validator.validate_query_result(
        benchmark_type="tpcds",
        query_id="Q1",
        actual_row_count=999,
        scale_factor=0.5,
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.SKIP
    assert result.expected_row_count is None


@pytest.mark.parametrize("seed", [None, 1])
def test_tpcds_dataframe_subscale_fixed_query_passes_at_reference_seed(seed: int | None) -> None:
    counts = TPCDS_SUBSCALE_ROW_COUNTS[0.01]
    measurements = [_measurement(query_id=f"Q{qid}", rows=rows) for qid, rows in counts.items() if qid in {"86", "93"}]

    summary = validate_dataframe_query_results(
        measurements,
        benchmark_name="tpcds",
        scale_factor=0.01,
        seed=seed,
    )

    assert summary.status == "PASSED"
    assert summary.details["provider"] == "tpcds_subscale_expected_row_counts"
    assert summary.details["checked"] == 2
    assert summary.details["passed"] == 2
    assert {row["row_count_validation"]["status"] for row in measurements} == {"PASSED"}


def test_tpcds_dataframe_subscale_mutated_count_fails() -> None:
    correct = _measurement(query_id="86", rows=TPCDS_SUBSCALE_ROW_COUNTS[0.01]["86"])
    wrong = _measurement(query_id="93", rows=TPCDS_SUBSCALE_ROW_COUNTS[0.01]["93"] + 1)

    summary = validate_dataframe_query_results(
        [correct, wrong],
        benchmark_name="tpcds",
        scale_factor=0.01,
    )

    assert summary.status == "FAILED"
    assert summary.details["failed"] == 1
    assert correct["status"] == "SUCCESS"
    assert wrong["status"] == "FAILED"
    assert wrong["row_count_validation"]["expected"] == TPCDS_SUBSCALE_ROW_COUNTS[0.01]["93"]


def test_tpcds_dataframe_subscale_non_reference_seed_skips() -> None:
    measurement = _measurement(query_id="93", rows=TPCDS_SUBSCALE_ROW_COUNTS[0.01]["93"])

    summary = validate_dataframe_query_results(
        [measurement],
        benchmark_name="tpcds",
        scale_factor=0.01,
        seed=42,
    )

    assert summary.status == "UNCERTAIN"
    assert summary.details["checked"] == 0
    assert summary.details["skipped"] == 1
    assert measurement["row_count_validation"]["status"] == "SKIPPED"


def test_tpcds_dataframe_subscale_non_reference_stream_skips() -> None:
    measurement = {**_measurement(query_id="93", rows=TPCDS_SUBSCALE_ROW_COUNTS[0.01]["93"]), "stream_id": 2}

    summary = validate_dataframe_query_results(
        [measurement],
        benchmark_name="tpcds",
        scale_factor=0.01,
    )

    assert summary.status == "UNCERTAIN"
    assert summary.details["checked"] == 0
    assert summary.details["skipped"] == 1
    assert measurement["row_count_validation"]["status"] == "SKIPPED"


def test_tpcds_dataframe_sensitive_query_checked_at_reference_seed() -> None:
    measurement = _measurement(query_id="98", rows=TPCDS_SUBSCALE_ROW_COUNTS[0.1]["98"])

    summary = validate_dataframe_query_results(
        [measurement],
        benchmark_name="tpcds",
        scale_factor=0.1,
    )

    assert summary.status == "PASSED"
    assert measurement["row_count_validation"] == {
        "expected": TPCDS_SUBSCALE_ROW_COUNTS[0.1]["98"],
        "actual": TPCDS_SUBSCALE_ROW_COUNTS[0.1]["98"],
        "status": "PASSED",
    }


def test_tpcds_dataframe_query_without_agreed_count_is_uncertain() -> None:
    measurement = _measurement(query_id="21", rows=6)

    summary = validate_dataframe_query_results(
        [measurement],
        benchmark_name="tpcds",
        scale_factor=0.01,
    )

    assert summary.status == "UNCERTAIN"
    assert summary.details["checked"] == 0
    assert summary.details["skipped"] == 1
    assert measurement["row_count_validation"]["status"] == "SKIPPED"


def test_tpcds_dataframe_unrecorded_scale_remains_uncertain() -> None:
    measurement = _measurement(query_id="1", rows=1)

    summary = validate_dataframe_query_results(
        [measurement],
        benchmark_name="tpcds",
        scale_factor=0.5,
    )

    assert summary.status == "UNCERTAIN"
    assert summary.details["checked"] == 0
    assert measurement["row_count_validation"]["status"] == "SKIPPED"


def test_tpcds_dataframe_subscale_validation_mode_skip_opt_out() -> None:
    from benchbox.core.validation.query_validation import set_validation_mode_context

    set_validation_mode_context(ValidationMode.SKIP)
    try:
        measurement = _measurement(query_id="93", rows=999)
        summary = validate_dataframe_query_results(
            [measurement],
            benchmark_name="tpcds",
            scale_factor=0.01,
        )
        assert summary.status == "UNCERTAIN"
        assert summary.details["checked"] == 0
        assert summary.details["skipped"] == 1
        assert measurement["row_count_validation"]["status"] == "SKIPPED"
    finally:
        set_validation_mode_context(None)


def test_tpcds_subscale_malformed_values_degrade_gracefully() -> None:
    from unittest.mock import patch

    from benchbox.core.expected_results.tpcds_results import _materialize_subscale_reference

    with patch(
        "benchbox.core.expected_results.tpcds_results._load_subscale_reference",
        return_value={"scales": {"not-a-number": {"1": "not-an-int"}}},
    ):
        counts, sensitive, provenance, rngseed = _materialize_subscale_reference()
        assert counts == {}
        assert sensitive == frozenset()
        assert provenance == {}
        assert rngseed == 1001
