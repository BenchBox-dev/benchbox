import pytest

from benchbox.core.expected_results.models import ValidationMode
from benchbox.core.expected_results.tpch_results import (
    PARAMETER_SENSITIVE_QUERY_IDS,
    TPCH_LOOSE_QUERY_IDS,
    TPCH_RANGE_ROW_COUNT_BOUNDS,
    get_tpch_expected_results,
)
from benchbox.core.validation.query_validation import (
    QueryValidator,
    clear_reference_seed_context,
    get_parameter_sensitive_query_ids,
    get_reference_seed_context,
    set_reference_seed_context,
)

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
        assert result.validation_mode == ValidationMode.SKIP
        assert result.warning_message is not None

    def test_validate_unknown_query(self):
        validator = QueryValidator()
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="999",
            actual_row_count=100,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.validation_mode == ValidationMode.SKIP
        assert result.warning_message is not None

    def test_validate_tpcds_query(self):
        validator = QueryValidator()
        result = validator.validate_query_result(
            benchmark_type="tpcds",
            query_id="1",
            actual_row_count=101,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.validation_mode == ValidationMode.SKIP
        assert result.actual_row_count == 101
        assert result.warning_message is not None
        assert "SKIP" in result.warning_message or "skip" in result.warning_message


class TestParameterSensitiveValidation:
    @pytest.fixture(autouse=True)
    def _reset_reference_seed_context(self):
        clear_reference_seed_context()
        yield
        clear_reference_seed_context()

    def test_parameter_sensitive_query_ids_constant(self):
        assert frozenset({"11", "13", "16", "18", "20"}) == PARAMETER_SENSITIVE_QUERY_IDS
        assert set(TPCH_RANGE_ROW_COUNT_BOUNDS) | set(TPCH_LOOSE_QUERY_IDS) == PARAMETER_SENSITIVE_QUERY_IDS

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
        assert q16.expected_row_count_min is None
        assert q16.expected_row_count_max is None
        assert q16.loose_tolerance_percent == 50.0

        for query_id, result in results.query_results.items():
            assert result.validation_mode == ValidationMode.EXACT

    def test_get_parameter_sensitive_query_ids_tpch(self):
        assert get_parameter_sensitive_query_ids("tpch") == PARAMETER_SENSITIVE_QUERY_IDS
        assert get_parameter_sensitive_query_ids("tpc-h") == PARAMETER_SENSITIVE_QUERY_IDS
        assert get_parameter_sensitive_query_ids("TPCH") == PARAMETER_SENSITIVE_QUERY_IDS

    def test_get_parameter_sensitive_query_ids_unknown_benchmark_is_empty(self):
        assert get_parameter_sensitive_query_ids("unknown_benchmark") == frozenset()

    def test_context_default_is_none(self):
        assert get_reference_seed_context() is None

    def test_context_set_get_clear_round_trip(self):
        set_reference_seed_context(True)
        assert get_reference_seed_context() is True
        set_reference_seed_context(False)
        assert get_reference_seed_context() is False
        clear_reference_seed_context()
        assert get_reference_seed_context() is None

    @pytest.mark.parametrize("query_id,bounds", sorted(TPCH_RANGE_ROW_COUNT_BOUNDS.items()))
    def test_range_queries_accept_both_documented_bounds_under_non_reference_context(self, query_id, bounds):
        set_reference_seed_context(False)
        validator = QueryValidator()
        minimum, maximum = bounds

        for actual_count in (minimum, maximum):
            result = validator.validate_query_result(
                benchmark_type="tpch",
                query_id=query_id,
                actual_row_count=actual_count,
                scale_factor=1.0,
            )
            assert result.is_valid
            assert result.validation_mode == ValidationMode.RANGE

    @pytest.mark.parametrize(
        "query_id,actual_count",
        [(query_id, minimum - 1) for query_id, (minimum, _) in sorted(TPCH_RANGE_ROW_COUNT_BOUNDS.items())]
        + [(query_id, maximum + 1) for query_id, (_, maximum) in sorted(TPCH_RANGE_ROW_COUNT_BOUNDS.items())],
    )
    def test_range_queries_reject_counts_outside_documented_bounds(self, query_id, actual_count):
        set_reference_seed_context(False)
        result = QueryValidator().validate_query_result(
            benchmark_type="tpch",
            query_id=query_id,
            actual_row_count=actual_count,
            scale_factor=1.0,
        )
        assert not result.is_valid
        assert result.validation_mode == ValidationMode.RANGE

    def test_q13_row_count_from_a_non_reference_word_pair_validates(self):
        set_reference_seed_context(False)
        result = QueryValidator().validate_query_result(
            benchmark_type="tpch",
            query_id="13",
            actual_row_count=41,
            scale_factor=1.0,
        )

        assert result.is_valid
        assert result.validation_mode == ValidationMode.RANGE
        assert result.expected_row_count is None or result.expected_row_count != 41

    def test_q13_reference_seed_still_requires_the_answer_file_count(self):
        set_reference_seed_context(True)
        validator = QueryValidator()

        assert validator.validate_query_result("tpch", "13", 42, 1.0).is_valid
        assert not validator.validate_query_result("tpch", "13", 41, 1.0).is_valid

    @pytest.mark.parametrize("query_id", ["2", "9", "15", "21"])
    def test_queries_with_fixed_sf1_cardinality_stay_exact_under_non_reference_context(self, query_id):
        set_reference_seed_context(False)
        validator = QueryValidator()
        expected = validator.registry.get_expected_result("tpch", query_id, 1.0)
        assert expected is not None
        count = expected.get_expected_count(1.0)
        assert count is not None

        assert query_id not in PARAMETER_SENSITIVE_QUERY_IDS
        assert validator.validate_query_result("tpch", query_id, count, 1.0).is_valid
        assert not validator.validate_query_result("tpch", query_id, count + 1, 1.0).is_valid

    def test_loose_query_uses_tolerance_under_non_reference_context(self):
        set_reference_seed_context(False)
        validator = QueryValidator()

        accepted = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="16",
            actual_row_count=18_000,
            scale_factor=1.0,
        )
        rejected = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="16",
            actual_row_count=9_156,
            scale_factor=1.0,
        )

        assert accepted.is_valid
        assert accepted.validation_mode == ValidationMode.LOOSE
        assert not rejected.is_valid
        assert rejected.validation_mode == ValidationMode.LOOSE

    @pytest.mark.parametrize("query_id,bounds", sorted(TPCH_RANGE_ROW_COUNT_BOUNDS.items()))
    def test_range_query_exact_compared_at_reference_seed(self, query_id, bounds):
        minimum, maximum = bounds
        set_reference_seed_context(True)
        validator = QueryValidator()

        expected = validator.registry.get_expected_result("tpch", query_id, 1.0)
        assert expected is not None
        reference_count = expected.get_expected_count(1.0)
        assert reference_count is not None
        assert minimum <= reference_count <= maximum

        in_range_wrong = maximum if reference_count != maximum else minimum
        assert minimum <= in_range_wrong <= maximum
        assert in_range_wrong != reference_count

        rejected = validator.validate_query_result(
            benchmark_type="tpch",
            query_id=query_id,
            actual_row_count=in_range_wrong,
            scale_factor=1.0,
        )
        assert not rejected.is_valid
        assert rejected.validation_mode == ValidationMode.EXACT

        accepted = validator.validate_query_result(
            benchmark_type="tpch",
            query_id=query_id,
            actual_row_count=reference_count,
            scale_factor=1.0,
        )
        assert accepted.is_valid
        assert accepted.validation_mode == ValidationMode.EXACT

    def test_parameter_sensitive_query_exact_when_context_unset(self):
        assert get_reference_seed_context() is None
        validator = QueryValidator()
        expected = validator.registry.get_expected_result("tpch", "11", 1.0)
        assert expected is not None
        reference_count = expected.get_expected_count(1.0)
        assert reference_count is not None

        minimum, maximum = TPCH_RANGE_ROW_COUNT_BOUNDS["11"]
        in_range_wrong = maximum if reference_count != maximum else minimum
        assert in_range_wrong != reference_count

        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="11",
            actual_row_count=in_range_wrong,
            scale_factor=1.0,
        )
        assert not result.is_valid
        assert result.validation_mode == ValidationMode.EXACT

    def test_loose_query_exact_compared_at_reference_seed(self):
        set_reference_seed_context(True)
        validator = QueryValidator()
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="16",
            actual_row_count=18_000,
            scale_factor=1.0,
        )
        assert not result.is_valid
        assert result.validation_mode == ValidationMode.EXACT

    def test_non_boundary_query_not_excluded_when_non_reference_seed(self):
        set_reference_seed_context(False)
        validator = QueryValidator()
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="1",
            actual_row_count=5,
            scale_factor=1.0,
        )
        assert not result.is_valid
        assert result.validation_mode == ValidationMode.EXACT

    def test_tpcds_non_reference_seed_context_excludes_row_count_validation(self):
        set_reference_seed_context(False)
        validator = QueryValidator()
        result = validator.validate_query_result(
            benchmark_type="tpcds",
            query_id="1",
            actual_row_count=101,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.validation_mode == ValidationMode.SKIP
        assert "excluded" in result.warning_message
