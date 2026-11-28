import pytest

from benchbox.core.expected_results.models import ValidationMode
from benchbox.core.expected_results.tpch_results import (
    TPCH_RANGE_ROW_COUNT_BOUNDS,
    get_tpch_expected_results,
)
from benchbox.core.validation.query_validation import QueryValidator
from benchbox.core.validation.stream_checks import QueryGeneration, query_generation_context

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryValidator:
    def test_validate_tpch_query_exact_match(self):
        validator = QueryValidator()
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="1",
            actual_row_count=4,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 4
        assert result.actual_row_count == 4
        assert result.validation_mode == ValidationMode.EXACT

    def test_validate_tpch_query_mismatch(self):
        validator = QueryValidator()
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="1",
            actual_row_count=5,
            scale_factor=1.0,
        )
        assert not result.is_valid
        assert result.expected_row_count == 4
        assert result.actual_row_count == 5
        assert result.difference == 1
        assert result.error_message is not None

    def test_validate_unknown_benchmark(self):
        validator = QueryValidator()
        result = validator.validate_query_result(
            benchmark_type="unknown_benchmark",
            query_id="1",
            actual_row_count=100,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.unvalidated
        assert result.validation_mode == ValidationMode.SKIP
        assert result.expected_row_count is None
        assert result.warning_message is not None

    def test_validate_unknown_query(self):
        validator = QueryValidator()
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="999",
            actual_row_count=100,
            scale_factor=1.0,
        )
        assert not result.is_valid
        assert not result.unvalidated
        assert result.validation_mode == ValidationMode.EXACT
        assert result.error_message is not None
        assert "lookup failed" in result.error_message
        assert "'999'" in result.error_message

    def test_validate_tpcds_query(self):
        from benchbox.core.expected_results.row_count_references import load_row_count_reference

        recorded = load_row_count_reference("tpcds", 1.0).streams[0].row_counts["1"]
        validator = QueryValidator()
        with query_generation_context(QueryGeneration(seed=1001)):
            result = validator.validate_query_result(
                benchmark_type="tpcds",
                query_id="1",
                actual_row_count=recorded,
                scale_factor=1.0,
            )
        assert result.is_valid
        assert result.validation_mode == ValidationMode.EXACT
        assert result.actual_row_count == recorded
        assert result.expected_row_count == recorded


SEEDED = QueryGeneration(seed=7)


def _validate(query_id: str, count: int, generation: QueryGeneration | None = None):
    with query_generation_context(generation):
        return QueryValidator().validate_query_result("tpch", query_id, count, 1.0)


class TestParameterSensitiveValidation:
    def test_tpch_provider_assigns_exact_mode_with_ride_along_bounds(self):
        results = get_tpch_expected_results(scale_factor=1.0)
        assert results is not None

        for query_id, (minimum, maximum) in TPCH_RANGE_ROW_COUNT_BOUNDS.items():
            result = results.get_expected_result(query_id)
            assert result is not None
            assert result.validation_mode == ValidationMode.EXACT
            assert result.expected_row_count is not None
            assert minimum <= result.expected_row_count <= maximum
            assert result.expected_row_count_min == minimum
            assert result.expected_row_count_max == maximum

        q16 = results.get_expected_result("16")
        assert q16 is not None
        assert q16.validation_mode == ValidationMode.EXACT
        assert q16.expected_row_count == 18_314
        assert q16.loose_tolerance_percent == 50.0

        for query_id, result in results.query_results.items():
            assert result.validation_mode == ValidationMode.EXACT

    @pytest.mark.parametrize("query_id,bounds", sorted(TPCH_RANGE_ROW_COUNT_BOUNDS.items()))
    def test_range_queries_accept_both_documented_bounds_for_other_parameters(self, query_id, bounds):
        for actual_count in bounds:
            result = _validate(query_id, actual_count, SEEDED)
            assert result.is_valid
            assert result.check == "range" and result.seed == 7

    @pytest.mark.parametrize(
        "query_id,actual_count",
        [(query_id, minimum - 1) for query_id, (minimum, _) in sorted(TPCH_RANGE_ROW_COUNT_BOUNDS.items())]
        + [(query_id, maximum + 1) for query_id, (_, maximum) in sorted(TPCH_RANGE_ROW_COUNT_BOUNDS.items())],
    )
    def test_range_queries_reject_counts_outside_documented_bounds(self, query_id, actual_count):
        result = _validate(query_id, actual_count, SEEDED)

        assert not result.is_valid
        assert result.check == "range"

    def test_q13_row_count_from_a_non_default_word_pair_validates(self):
        result = _validate("13", 41, SEEDED)

        assert result.is_valid and result.check == "range"

    def test_q13_default_parameters_still_require_the_answer_file_count(self):
        assert _validate("13", 42).is_valid
        assert not _validate("13", 41).is_valid

    @pytest.mark.parametrize("query_id", ["2", "9", "15", "21"])
    def test_queries_with_fixed_sf1_cardinality_stay_exact_for_other_parameters(self, query_id):
        expected = QueryValidator().registry.get_expected_result("tpch", query_id, 1.0)
        count = expected.get_expected_count(1.0)

        assert _validate(query_id, count, SEEDED).is_valid
        assert not _validate(query_id, count + 1, SEEDED).is_valid

    def test_loose_query_uses_tolerance_for_other_parameters(self):
        accepted = _validate("16", 18_000, SEEDED)
        rejected = _validate("16", 9_156, SEEDED)

        assert accepted.is_valid and accepted.check == "range"
        assert not rejected.is_valid and rejected.check == "range"

    @pytest.mark.parametrize("query_id,bounds", sorted(TPCH_RANGE_ROW_COUNT_BOUNDS.items()))
    def test_range_query_exact_compared_at_default_parameters(self, query_id, bounds):
        minimum, maximum = bounds
        expected = QueryValidator().registry.get_expected_result("tpch", query_id, 1.0)
        reference_count = expected.get_expected_count(1.0)
        assert minimum <= reference_count <= maximum
        in_range_wrong = maximum if reference_count != maximum else minimum

        rejected = _validate(query_id, in_range_wrong)
        accepted = _validate(query_id, reference_count)

        assert not rejected.is_valid and rejected.check == "exact"
        assert accepted.is_valid and accepted.check == "exact"

    def test_loose_query_exact_compared_at_default_parameters(self):
        result = _validate("16", 18_000)

        assert not result.is_valid and result.check == "exact"

    def test_a_fixed_query_is_exact_at_default_parameters(self):
        result = _validate("1", 5)

        assert not result.is_valid and result.check == "exact"
