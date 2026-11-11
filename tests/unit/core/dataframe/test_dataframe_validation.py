# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import polars as pl
import pytest

from benchbox.core.dataframe.validation import (
    ComparisonStatus,
    DataFrameValidator,
    ValidationConfig,
    ValidationLevel,
    ValidationResult,
    compare_dataframes,
    compare_with_sql,
    fuzzy_float_compare,
    validate_column_names,
    validate_query_result,
    validate_row_count,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestValidationResult:
    def test_success_creates_valid_result(self):
        result = ValidationResult.success()

        assert result.is_valid is True
        assert result.status == ComparisonStatus.MATCH
        assert result.errors == []
        assert result.warnings == []

    def test_success_with_metrics(self):
        metrics = {"rows": 100, "columns": 5}
        result = ValidationResult.success(metrics)

        assert result.is_valid is True
        assert result.metrics == metrics

    def test_failure_creates_invalid_result(self):
        result = ValidationResult.failure("Row count mismatch")

        assert result.is_valid is False
        assert result.status == ComparisonStatus.MISMATCH
        assert "Row count mismatch" in result.errors

    def test_error_creates_error_result(self):
        result = ValidationResult.error("Cannot compare DataFrames")

        assert result.is_valid is False
        assert result.status == ComparisonStatus.ERROR
        assert "Cannot compare DataFrames" in result.errors

    def test_add_error_marks_invalid(self):
        result = ValidationResult.success()
        result.add_error("Something went wrong")

        assert result.is_valid is False
        assert result.status == ComparisonStatus.MISMATCH
        assert "Something went wrong" in result.errors

    def test_add_warning_keeps_valid(self):
        result = ValidationResult.success()
        result.add_warning("Minor issue")

        assert result.is_valid is True
        assert result.status == ComparisonStatus.PARTIAL_MATCH
        assert "Minor issue" in result.warnings

    def test_bool_conversion(self):

        valid = ValidationResult.success()
        invalid = ValidationResult.failure("Error")

        assert bool(valid) is True
        assert bool(invalid) is False

    def test_merge_combines_results(self):
        result1 = ValidationResult.success({"rows": 100})
        result1.add_warning("Warning 1")

        result2 = ValidationResult.success({"columns": 5})
        result2.add_warning("Warning 2")

        merged = result1.merge(result2)

        assert merged.is_valid is True
        assert merged.metrics["rows"] == 100
        assert merged.metrics["columns"] == 5
        assert len(merged.warnings) == 2

    def test_merge_propagates_failure(self):
        result1 = ValidationResult.success()
        result2 = ValidationResult.failure("Error")

        merged = result1.merge(result2)

        assert merged.is_valid is False
        assert merged.status == ComparisonStatus.MISMATCH


class TestValidateRowCount:
    def test_exact_match(self):

        result = validate_row_count(100, 100)

        assert result.is_valid is True
        assert result.metrics["actual_rows"] == 100
        assert result.metrics["expected_rows"] == 100

    def test_mismatch(self):

        result = validate_row_count(100, 200)

        assert result.is_valid is False
        assert "mismatch" in result.errors[0].lower()

    def test_within_tolerance(self):

        result = validate_row_count(105, 100, tolerance_percent=10)

        assert result.is_valid is True
        assert len(result.warnings) == 1
        assert "5.00%" in result.warnings[0]

    def test_outside_tolerance(self):

        result = validate_row_count(120, 100, tolerance_percent=10)

        assert result.is_valid is False

    def test_zero_expected_with_tolerance(self):

        result = validate_row_count(0, 0, tolerance_percent=10)

        assert result.is_valid is True


class TestValidateColumnNames:
    def test_exact_match(self):

        result = validate_column_names(["a", "b", "c"], ["a", "b", "c"])

        assert result.is_valid is True

    def test_different_order_allowed(self):

        result = validate_column_names(["c", "b", "a"], ["a", "b", "c"])

        assert result.is_valid is True

    def test_different_order_not_allowed(self):

        config = ValidationConfig(ignore_column_order=False)
        result = validate_column_names(["c", "b", "a"], ["a", "b", "c"], config=config)

        assert result.is_valid is True
        assert len(result.warnings) == 1

    def test_missing_columns(self):

        result = validate_column_names(["a", "b"], ["a", "b", "c"])

        assert result.is_valid is False
        assert "Missing columns" in result.errors[0]

    def test_extra_columns(self):

        result = validate_column_names(["a", "b", "c", "d"], ["a", "b", "c"])

        assert result.is_valid is False
        assert "Extra columns" in result.errors[0]

    def test_case_insensitive(self):

        config = ValidationConfig(ignore_case=True)
        result = validate_column_names(["A", "B", "C"], ["a", "b", "c"], config=config)

        assert result.is_valid is True


class TestFuzzyFloatCompare:
    def test_exact_match(self):

        assert fuzzy_float_compare(1.0, 1.0) is True

    def test_within_relative_tolerance(self):

        assert fuzzy_float_compare(1.0000001, 1.0) is True

    def test_outside_tolerance(self):

        assert fuzzy_float_compare(1.1, 1.0, rel_tolerance=1e-3) is False

    def test_nan_handling(self):

        assert fuzzy_float_compare(float("nan"), float("nan")) is True
        assert fuzzy_float_compare(float("nan"), 1.0) is False

    def test_infinity_handling(self):

        assert fuzzy_float_compare(float("inf"), float("inf")) is True
        assert fuzzy_float_compare(float("-inf"), float("-inf")) is True
        assert fuzzy_float_compare(float("inf"), float("-inf")) is False
        assert fuzzy_float_compare(float("inf"), 1e308) is False

    def test_near_zero(self):

        assert fuzzy_float_compare(1e-12, 0.0, abs_tolerance=1e-10) is True
        assert fuzzy_float_compare(1e-8, 0.0, abs_tolerance=1e-10) is False


class TestCompareDataframes:
    def test_identical_dataframes(self):

        df1 = pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        df2 = pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})

        result = compare_dataframes(df1, df2)

        assert result.is_valid is True

    def test_different_row_count(self):

        df1 = pl.DataFrame({"a": [1, 2, 3]})
        df2 = pl.DataFrame({"a": [1, 2]})

        result = compare_dataframes(df1, df2)

        assert result.is_valid is False
        assert "Row count mismatch" in result.errors[0]

    def test_different_column_names(self):

        df1 = pl.DataFrame({"a": [1, 2, 3]})
        df2 = pl.DataFrame({"b": [1, 2, 3]})

        result = compare_dataframes(df1, df2)

        assert result.is_valid is False
        assert "Missing columns" in result.errors[0] or "Extra columns" in result.errors[0]

    def test_different_values(self):

        df1 = pl.DataFrame({"a": [1, 2, 3]})
        df2 = pl.DataFrame({"a": [1, 2, 4]})

        result = compare_dataframes(df1, df2)

        assert result.is_valid is False
        assert "value mismatches" in result.errors[0]

    def test_float_tolerance(self):

        df1 = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        df2 = pl.DataFrame({"a": [1.0000001, 2.0, 3.0]})

        result = compare_dataframes(df1, df2)

        assert result.is_valid is True

    def test_sort_invariant(self):
        df1 = pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        df2 = pl.DataFrame({"a": [3, 1, 2], "b": [6, 4, 5]})

        result = compare_dataframes(df1, df2)

        assert result.is_valid is True

    def test_null_handling(self):

        df1 = pl.DataFrame({"a": [1, None, 3]})
        df2 = pl.DataFrame({"a": [1, None, 3]})

        result = compare_dataframes(df1, df2)

        assert result.is_valid is True

    def test_loose_validation(self):

        config = ValidationConfig(level=ValidationLevel.LOOSE)
        df1 = pl.DataFrame({"a": [1, 2, 3]})
        df2 = pl.DataFrame({"b": [4, 5, 6]})

        result = compare_dataframes(df1, df2, config=config)

        assert result.metrics["actual_rows"] == 3
        assert result.metrics["expected_rows"] == 3

    def test_pandas_dataframe(self):

        pd = pytest.importorskip("pandas")

        df1 = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        df2 = pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})

        result = compare_dataframes(df1, df2)

        assert result.is_valid is True

    def test_dict_input(self):

        df1 = {"a": [1, 2, 3], "b": [4, 5, 6]}
        df2 = pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})

        result = compare_dataframes(df1, df2)

        assert result.is_valid is True


class TestCompareWithSql:
    def test_adds_query_context(self):

        df1 = pl.DataFrame({"a": [1, 2, 3]})
        df2 = pl.DataFrame({"a": [1, 2, 3]})

        result = compare_with_sql(df1, df2, query_id="Q1")

        assert result.details["query_id"] == "Q1"

    def test_error_includes_query_id(self):

        df1 = pl.DataFrame({"a": [1, 2, 3]})
        df2 = pl.DataFrame({"a": [1, 2, 4]})

        result = compare_with_sql(df1, df2, query_id="Q1")

        assert result.is_valid is False
        assert "[Q1]" in result.errors[0]


class TestValidateQueryResult:
    def test_row_count_only(self):

        df = pl.DataFrame({"a": [1, 2, 3]})

        result = validate_query_result(df, expected_rows=3)

        assert result.is_valid is True

    def test_columns_only(self):

        df = pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})

        result = validate_query_result(df, expected_columns=["a", "b"])

        assert result.is_valid is True

    def test_both_validations(self):

        df = pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})

        result = validate_query_result(df, expected_rows=3, expected_columns=["a", "b"])

        assert result.is_valid is True

    def test_adds_query_id(self):

        df = pl.DataFrame({"a": [1, 2, 3]})

        result = validate_query_result(df, expected_rows=3, query_id="Q1")

        assert result.details["query_id"] == "Q1"


class TestDataFrameValidator:
    def test_validate_stores_results(self):
        validator = DataFrameValidator()
        df1 = pl.DataFrame({"a": [1, 2, 3]})
        df2 = pl.DataFrame({"a": [1, 2, 3]})

        validator.validate(df1, df2, query_id="Q1")
        validator.validate(df1, df2, query_id="Q2")

        assert len(validator.results) == 2

    def test_validate_row_count(self):

        validator = DataFrameValidator()
        df = pl.DataFrame({"a": [1, 2, 3]})

        result = validator.validate_row_count(df, 3, query_id="Q1")

        assert result.is_valid is True
        assert len(validator.results) == 1

    def test_summary(self):
        validator = DataFrameValidator()
        df1 = pl.DataFrame({"a": [1, 2, 3]})
        df2 = pl.DataFrame({"a": [1, 2, 3]})
        df3 = pl.DataFrame({"a": [1, 2, 4]})

        validator.validate(df1, df2, query_id="Q1")
        validator.validate(df1, df3, query_id="Q2")

        summary = validator.summary()

        assert summary["total"] == 2
        assert summary["passed"] == 1
        assert summary["failed"] == 1
        assert summary["pass_rate"] == 0.5
        assert summary["is_valid"] is False

    def test_reset(self):
        validator = DataFrameValidator()
        df = pl.DataFrame({"a": [1, 2, 3]})

        validator.validate(df, df, query_id="Q1")
        assert len(validator.results) == 1

        validator.reset()
        assert len(validator.results) == 0

    def test_custom_config(self):

        config = ValidationConfig(float_tolerance=1e-3)
        validator = DataFrameValidator(config=config)

        df1 = pl.DataFrame({"a": [1.0]})
        df2 = pl.DataFrame({"a": [1.0005]})

        result = validator.validate(df1, df2)

        assert result.is_valid is True


class TestValidationConfig:
    def test_default_values(self):

        config = ValidationConfig()

        assert config.level == ValidationLevel.STANDARD
        assert config.float_tolerance == 1e-6
        assert config.ignore_column_order is True
        assert config.ignore_row_order is True

    def test_custom_values(self):

        config = ValidationConfig(
            level=ValidationLevel.STRICT,
            float_tolerance=1e-10,
            ignore_column_order=False,
        )

        assert config.level == ValidationLevel.STRICT
        assert config.float_tolerance == 1e-10
        assert config.ignore_column_order is False


class TestValidationLevel:
    def test_levels(self):

        assert ValidationLevel.STRICT.value == "strict"
        assert ValidationLevel.STANDARD.value == "standard"
        assert ValidationLevel.LOOSE.value == "loose"


class TestComparisonStatus:
    def test_statuses(self):

        assert ComparisonStatus.MATCH.value == "match"
        assert ComparisonStatus.MISMATCH.value == "mismatch"
        assert ComparisonStatus.PARTIAL_MATCH.value == "partial_match"
        assert ComparisonStatus.ERROR.value == "error"


