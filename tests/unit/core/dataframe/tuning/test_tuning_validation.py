# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.dataframe.tuning import (
    DataFrameTuningConfiguration,
    ExecutionConfiguration,
    GPUConfiguration,
    MemoryConfiguration,
    ParallelismConfiguration,
    ValidationIssue,
    ValidationLevel,
    format_issues,
    has_errors,
    has_warnings,
    validate_dataframe_tuning,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestValidationIssue:
    def test_str_representation(self):

        issue = ValidationIssue(
            level=ValidationLevel.ERROR,
            message="Test error message",
            setting="test_setting",
            suggestion="Fix this",
        )
        result = str(issue)
        assert "[ERROR]" in result
        assert "test_setting" in result
        assert "Test error message" in result
        assert "Fix this" in result

    def test_str_without_setting(self):

        issue = ValidationIssue(
            level=ValidationLevel.WARNING,
            message="Test warning",
        )
        result = str(issue)
        assert "[WARNING]" in result
        assert "Test warning" in result


class TestValidationHelpers:
    def test_has_errors_true(self):
        issues = [
            ValidationIssue(ValidationLevel.ERROR, "Error"),
            ValidationIssue(ValidationLevel.WARNING, "Warning"),
        ]
        assert has_errors(issues) is True

    def test_has_errors_false(self):
        issues = [
            ValidationIssue(ValidationLevel.WARNING, "Warning"),
            ValidationIssue(ValidationLevel.INFO, "Info"),
        ]
        assert has_errors(issues) is False

    def test_has_warnings_true(self):
        issues = [
            ValidationIssue(ValidationLevel.WARNING, "Warning"),
        ]
        assert has_warnings(issues) is True

    def test_has_warnings_false(self):
        issues = [
            ValidationIssue(ValidationLevel.INFO, "Info"),
        ]
        assert has_warnings(issues) is False

    def test_format_issues_empty(self):
        result = format_issues([])
        assert "No validation issues found" in result

    def test_format_issues_with_content(self):
        issues = [
            ValidationIssue(ValidationLevel.WARNING, "Test warning"),
        ]
        result = format_issues(issues)
        assert "WARNING" in result
        assert "Test warning" in result


class TestValidateDataFrameTuning:
    def test_default_config_no_issues(self):

        config = DataFrameTuningConfiguration()
        issues = validate_dataframe_tuning(config, "polars")
        assert not any(i.level == ValidationLevel.ERROR for i in issues)

    def test_incompatible_setting_warning(self):

        config = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(worker_count=4),
        )
        issues = validate_dataframe_tuning(config, "polars")
        assert any(i.level == ValidationLevel.WARNING and "worker_count" in str(i) for i in issues)


class TestPolarsValidation:
    def test_streaming_without_chunk_size_info(self):

        config = DataFrameTuningConfiguration(
            execution=ExecutionConfiguration(streaming_mode=True),
        )
        issues = validate_dataframe_tuning(config, "polars")
        assert any(i.level == ValidationLevel.INFO for i in issues)

    def test_invalid_engine_affinity_error(self):

        config = DataFrameTuningConfiguration(
            execution=ExecutionConfiguration(engine_affinity="invalid"),
        )
        issues = validate_dataframe_tuning(config, "polars")
        assert any(i.level == ValidationLevel.ERROR and "engine_affinity" in str(i) for i in issues)

    def test_conflicting_streaming_settings_warning(self):

        config = DataFrameTuningConfiguration(
            execution=ExecutionConfiguration(
                streaming_mode=True,
                engine_affinity="in-memory",
            ),
        )
        issues = validate_dataframe_tuning(config, "polars")
        assert any(i.level == ValidationLevel.WARNING for i in issues)


class TestDaskValidation:
    def test_high_threads_per_worker_warning(self):

        config = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(threads_per_worker=16),
        )
        issues = validate_dataframe_tuning(config, "dask")
        assert any(i.level == ValidationLevel.WARNING and "threads_per_worker" in str(i) for i in issues)

    def test_spill_without_memory_limit_info(self):

        config = DataFrameTuningConfiguration(
            memory=MemoryConfiguration(spill_to_disk=True),
        )
        issues = validate_dataframe_tuning(config, "dask")
        assert any(i.level == ValidationLevel.INFO for i in issues)


class TestDataFusionValidation:
    def test_session_settings_are_supported(self):
        config = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(thread_count=4),
            memory=MemoryConfiguration(chunk_size=4096),
        )
        issues = validate_dataframe_tuning(config, "datafusion-df")

        assert not any(i.level == ValidationLevel.WARNING for i in issues)


class TestCuDFValidation:
    def test_gpu_disabled_warning(self):

        config = DataFrameTuningConfiguration(
            gpu=GPUConfiguration(enabled=False),
        )
        issues = validate_dataframe_tuning(config, "cudf")
        assert any(i.level == ValidationLevel.WARNING and "GPU is disabled" in str(i) for i in issues)

    def test_invalid_pool_type_error(self):

        config = DataFrameTuningConfiguration(
            gpu=GPUConfiguration(enabled=True),
        )
        issues = validate_dataframe_tuning(config, "cudf")
        assert not any(i.level == ValidationLevel.ERROR and "pool_type" in str(i) for i in issues)

    def test_spill_disabled_info(self):

        config = DataFrameTuningConfiguration(
            gpu=GPUConfiguration(enabled=True, spill_to_host=False),
        )
        issues = validate_dataframe_tuning(config, "cudf")
        assert any(i.level == ValidationLevel.INFO for i in issues)


class TestPlatformNormalization:
    def test_validates_with_df_suffix(self):

        config = DataFrameTuningConfiguration()
        issues = validate_dataframe_tuning(config, "polars-df")
        assert not any(i.level == ValidationLevel.ERROR for i in issues)

    def test_validates_case_insensitive(self):

        config = DataFrameTuningConfiguration()
        issues = validate_dataframe_tuning(config, "POLARS")
        assert not any(i.level == ValidationLevel.ERROR for i in issues)


