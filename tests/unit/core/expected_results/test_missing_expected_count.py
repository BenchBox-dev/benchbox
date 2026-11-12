import pytest

from benchbox.core.expected_results.models import (
    BenchmarkExpectedResults,
    ExpectedQueryResult,
    ValidationMode,
)
from benchbox.core.expected_results.registry import ExpectedResultsRegistry, LoadOutcome
from benchbox.core.results.query_execution import ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS
from benchbox.core.validation.query_validation import QueryValidator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _validator_with(provider) -> QueryValidator:
    validator = QueryValidator()
    registry = ExpectedResultsRegistry()
    registry.register_provider("demo", provider)
    validator.registry = registry
    return validator


def _demo_results(scale_factor: float, *, known: frozenset[str] | None = None) -> BenchmarkExpectedResults:
    return BenchmarkExpectedResults(
        benchmark_name="demo",
        scale_factor=scale_factor,
        query_results={
            "1": ExpectedQueryResult(query_id="1", expected_row_count=4, validation_mode=ValidationMode.EXACT)
        },
        known_query_ids=known,
    )


def test_known_expectation_match_passes_and_is_not_unvalidated():
    validator = _validator_with(lambda sf: _demo_results(sf))

    result = validator.validate_query_result("demo", "1", 4, 1.0)

    assert result.is_valid
    assert not result.unvalidated
    assert result.validation_mode == ValidationMode.EXACT
    assert result.expected_row_count == 4


def test_known_expectation_mismatch_fails():
    validator = _validator_with(lambda sf: _demo_results(sf))

    result = validator.validate_query_result("demo", "1", 5, 1.0)

    assert not result.is_valid
    assert not result.unvalidated
    assert result.expected_row_count == 4


def test_lookup_miss_in_benchmark_with_reference_answers_fails_with_named_error():
    validator = _validator_with(lambda sf: _demo_results(sf))

    result = validator.validate_query_result("demo", "7", 4, 1.0)

    assert not result.is_valid
    assert not result.unvalidated
    assert result.expected_row_count is None
    assert result.error_message is not None
    assert "Row-count lookup failed" in result.error_message
    assert "'7'" in result.error_message
    assert "'demo'" in result.error_message
    assert len(result.error_message) < ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS


def test_gap_in_recorded_scale_listed_as_known_is_unvalidated_not_a_failure():
    validator = _validator_with(lambda sf: _demo_results(sf, known=frozenset({"1", "7"})))

    gap = validator.validate_query_result("demo", "7", 4, 0.1)
    typo = validator.validate_query_result("demo", "8", 4, 0.1)

    assert gap.is_valid
    assert gap.unvalidated
    assert not typo.is_valid
    assert not typo.unvalidated


def test_benchmark_without_a_provider_is_unvalidated():
    validator = QueryValidator()

    result = validator.validate_query_result("no_such_benchmark", "1", 4, 1.0)

    assert result.is_valid
    assert result.unvalidated
    assert result.validation_mode == ValidationMode.SKIP
    assert result.expected_row_count is None
    assert "not counted as passed" in (result.warning_message or "")
    assert len(result.warning_message or "") < ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS


def test_scale_without_reference_answers_is_unvalidated():
    validator = QueryValidator()

    result = validator.validate_query_result("tpch", "2", 4, 10.0)
    miss_at_unreferenced_scale = validator.validate_query_result("tpch", "999", 4, 10.0)

    assert result.is_valid
    assert miss_at_unreferenced_scale.is_valid
    assert result.unvalidated
    assert miss_at_unreferenced_scale.unvalidated


def test_provider_with_missing_answer_file_is_unvalidated():
    def provider(scale_factor):
        raise FileNotFoundError("no answer files")

    validator = _validator_with(provider)

    result = validator.validate_query_result("demo", "1", 4, 1.0)

    assert result.is_valid
    assert result.unvalidated


def test_tpch_typo_fails_and_real_query_passes():
    validator = QueryValidator()

    typo = validator.validate_query_result("tpch", "99", 4, 1.0)
    real = validator.validate_query_result("tpch", "1", 4, 1.0)

    assert not typo.is_valid
    assert real.is_valid
    assert not real.unvalidated


def test_tpcds_subscale_gap_is_unvalidated_and_unknown_variant_fails():
    validator = QueryValidator()

    gap = validator.validate_query_result("tpcds", "21", 3, 0.1)
    typo = validator.validate_query_result("tpcds", "14c", 3, 0.1)

    assert gap.is_valid
    assert gap.unvalidated
    assert not typo.is_valid
    assert not typo.unvalidated


def test_non_reference_stream_remains_a_skip_not_unvalidated():
    validator = QueryValidator()

    result = validator.validate_query_result("tpch", "1", 4, 1.0, stream_id=3)

    assert result.is_valid
    assert not result.unvalidated
    assert result.validation_mode == ValidationMode.SKIP
    assert "stream 3" in (result.warning_message or "")


def test_exact_entry_without_an_expected_count_fails_validation():
    entry = ExpectedQueryResult(query_id="1", expected_row_count=4, validation_mode=ValidationMode.EXACT)
    entry.expected_row_count = None
    validator = _validator_with(
        lambda sf: BenchmarkExpectedResults(benchmark_name="demo", scale_factor=sf, query_results={"1": entry})
    )

    result = validator.validate_query_result("demo", "1", 4, 1.0)

    assert not result.is_valid
    assert not result.unvalidated
    assert result.validation_mode == ValidationMode.EXACT
    assert "no expected count" in (result.error_message or "")
    assert len(result.error_message or "") < ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS


def test_classified_provider_failure_is_unchanged():
    def provider(scale_factor):
        raise RuntimeError("provider exploded")

    validator = _validator_with(provider)

    result = validator.validate_query_result("demo", "1", 4, 1.0)

    assert not result.is_valid
    assert not result.unvalidated
    assert result.validation_mode == ValidationMode.SKIP
    assert "provider failed" in (result.error_message or "")


def test_classified_timeout_is_unchanged(monkeypatch):
    validator = QueryValidator()
    monkeypatch.setattr(
        validator.registry,
        "get_expected_result_detailed",
        lambda *args, **kwargs: (None, LoadOutcome.PROVIDER_TIMEOUT),
    )

    result = validator.validate_query_result("tpch", "1", 4, 1.0)

    assert not result.is_valid
    assert not result.unvalidated
    assert result.validation_mode == ValidationMode.SKIP
    assert "Timed out" in (result.error_message or "")
