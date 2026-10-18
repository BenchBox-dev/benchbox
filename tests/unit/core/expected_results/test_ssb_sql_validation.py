import pytest

from benchbox.core.expected_results.models import ValidationMode
from benchbox.core.expected_results.ssb_results import SSB_EXPECTED_ROW_COUNTS, get_ssb_expected_results
from benchbox.core.validation.query_validation import QueryValidator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def validator():
    return QueryValidator()


@pytest.mark.parametrize("scale_factor", sorted(SSB_EXPECTED_ROW_COUNTS))
def test_ssb_provider_returns_recorded_counts(scale_factor: float) -> None:
    results = get_ssb_expected_results(scale_factor=scale_factor)

    assert results is not None
    assert results.benchmark_name == "ssb"
    assert results.scale_factor == scale_factor
    assert {query_id: result.expected_row_count for query_id, result in results.query_results.items()} == (
        SSB_EXPECTED_ROW_COUNTS[scale_factor]
    )
    assert {result.validation_mode for result in results.query_results.values()} == {ValidationMode.EXACT}


def test_ssb_provider_returns_none_for_unrecorded_scale() -> None:
    assert get_ssb_expected_results(scale_factor=0.5) is None


def test_ssb_sql_query_validates_against_recorded_count(validator) -> None:
    result = validator.validate_query_result(
        benchmark_type="ssb",
        query_id="Q2.1",
        actual_row_count=280,
        scale_factor=1.0,
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.EXACT
    assert result.expected_row_count == 280
    assert result.actual_row_count == 280


def test_ssb_sql_query_without_q_prefix_validates(validator) -> None:
    result = validator.validate_query_result(
        benchmark_type="ssb",
        query_id="4.3",
        actual_row_count=242,
        scale_factor=0.1,
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.EXACT
    assert result.expected_row_count == 242


def test_ssb_sql_query_wrong_count_fails(validator) -> None:
    result = validator.validate_query_result(
        benchmark_type="ssb",
        query_id="Q3.2",
        actual_row_count=397,
        scale_factor=0.1,
    )

    assert not result.is_valid
    assert result.validation_mode == ValidationMode.EXACT
    assert result.expected_row_count == 398
    assert result.actual_row_count == 397
    assert result.error_message is not None


def test_ssb_unrecorded_scale_skips_not_fails(validator) -> None:
    result = validator.validate_query_result(
        benchmark_type="ssb",
        query_id="Q1.1",
        actual_row_count=999,
        scale_factor=0.5,
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.SKIP
    assert result.expected_row_count is None
    assert result.warning_message is not None
