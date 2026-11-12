# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.validation.query_validation import QueryValidator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryIDNormalization:
    @pytest.fixture
    def validator(self):
        return QueryValidator()

    def test_normalize_integer_query_id(self, validator):

        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id=1,
            actual_row_count=4,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 4

    def test_normalize_string_numeric_query_id(self, validator):

        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="1",
            actual_row_count=4,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 4

    def test_normalize_prefixed_query_id_uppercase_q(self, validator):
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="Q1",
            actual_row_count=4,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 4

    def test_normalize_prefixed_query_id_lowercase_q(self, validator):
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="q1",
            actual_row_count=4,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 4

    def test_normalize_query_prefix_format(self, validator):
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="query1",
            actual_row_count=4,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 4

    def test_normalize_variant_suffix_single_letter(self, validator):
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="15a",
            actual_row_count=1,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 1

    def test_normalize_variant_suffix_multi_letter(self, validator):
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="15abc",
            actual_row_count=1,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 1

    def test_normalize_combined_prefix_and_variant(self, validator):
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="Q15a",
            actual_row_count=1,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 1

        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="query15b",
            actual_row_count=1,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 1

    def test_normalize_multi_digit_query_ids(self, validator):

        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="15",
            actual_row_count=1,
            scale_factor=1.0,
        )
        assert result.is_valid

        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="Q15",
            actual_row_count=1,
            scale_factor=1.0,
        )
        assert result.is_valid

    def test_normalize_tpcds_query_ids(self, validator):

        result = validator.validate_query_result(
            benchmark_type="tpcds",
            query_id="Q1",
            actual_row_count=101,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.validation_mode.value == "skip"
        assert result.expected_row_count is None

    def test_normalize_non_tpc_benchmark_preserves_id(self, validator):
        result = validator.validate_query_result(
            benchmark_type="custom_benchmark",
            query_id="MY_CUSTOM_ID_123",
            actual_row_count=100,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.validation_mode.value == "skip"

    def test_normalize_leading_zeros_extracted(self, validator):
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="01",
            actual_row_count=4,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.validation_mode.value == "exact"
        assert result.expected_row_count == 4

    def test_normalize_whitespace_handling(self, validator):

        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="Q 1",
            actual_row_count=4,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 4

    def test_normalize_mixed_case_prefix(self, validator):

        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="Query1",
            actual_row_count=4,
            scale_factor=1.0,
        )
        assert result.is_valid
        assert result.expected_row_count == 4

    def test_normalize_id_with_no_digits_fails_the_lookup(self, validator):

        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="INVALID_NO_DIGITS",
            actual_row_count=100,
            scale_factor=1.0,
        )
        assert not result.is_valid
        assert result.error_message is not None
        assert "lookup failed" in result.error_message

    def test_normalize_consistency_across_formats(self, validator):

        formats = ["1", 1, "01", "Q1", "q1", "query1", "Query1"]

        results = []
        for query_id_format in formats:
            result = validator.validate_query_result(
                benchmark_type="tpch",
                query_id=query_id_format,
                actual_row_count=4,
                scale_factor=1.0,
            )
            results.append(result)

        assert all(r.is_valid for r in results)
        expected_counts = [r.expected_row_count for r in results]
        assert len(set(expected_counts)) == 1
        assert expected_counts[0] == 4

    def test_internal_normalize_method_directly(self, validator):

        assert validator._normalize_query_id("tpch", 1) == "1"
        assert validator._normalize_query_id("tpch", "1") == "1"
        assert validator._normalize_query_id("tpch", "Q1") == "1"
        assert validator._normalize_query_id("tpch", "q1") == "1"
        assert validator._normalize_query_id("tpch", "query15") == "15"
        assert validator._normalize_query_id("tpch", "15a") == "15"
        assert validator._normalize_query_id("tpch", "Q15b") == "15"

        assert validator._normalize_query_id("tpch", "01") == "1"
        assert validator._normalize_query_id("tpch", "001") == "1"
        assert validator._normalize_query_id("tpch", "Q01") == "1"

        assert validator._normalize_query_id("custom", "MY_ID") == "MY_ID"
        assert validator._normalize_query_id("custom", 123) == "123"

    def test_negative_row_count_validation(self, validator):
        import pytest

        with pytest.raises(ValueError, match="actual_row_count must be non-negative"):
            validator.validate_query_result(
                benchmark_type="tpch",
                query_id="1",
                actual_row_count=-1,
                scale_factor=1.0,
            )

    def test_query_id_type_consistency(self, validator):
        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id=1,
            actual_row_count=4,
            scale_factor=1.0,
        )
        assert isinstance(result.query_id, str)
        assert result.query_id == "1"

        result = validator.validate_query_result(
            benchmark_type="tpch",
            query_id="15",
            actual_row_count=1,
            scale_factor=1.0,
        )
        assert isinstance(result.query_id, str)
        assert result.query_id == "15"
