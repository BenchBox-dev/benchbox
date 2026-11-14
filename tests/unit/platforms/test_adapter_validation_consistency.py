# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock

import pytest

from benchbox.core.expected_results.models import ValidationMode, ValidationResult

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAdapterValidationConsistency:
    @pytest.fixture
    def mock_validation_passed(self):
        return ValidationResult(
            is_valid=True,
            query_id="1",
            expected_row_count=100,
            actual_row_count=100,
            validation_mode=ValidationMode.EXACT,
        )

    @pytest.fixture
    def mock_validation_failed(self):
        return ValidationResult(
            is_valid=False,
            query_id="1",
            expected_row_count=100,
            actual_row_count=50,
            validation_mode=ValidationMode.EXACT,
            error_message="Query validation FAILED: expected 100 rows, got 50 rows",
            difference=-50,
            difference_percent=-50.0,
        )

    @pytest.fixture
    def mock_validation_skipped(self):
        return ValidationResult(
            is_valid=True,
            query_id="1",
            expected_row_count=None,
            actual_row_count=100,
            validation_mode=ValidationMode.SKIP,
            warning_message="No expected row count defined for query '1'. Validation skipped.",
        )

    def test_base_adapter_validation_passed(self, mock_validation_passed):

        from benchbox.platforms.base.adapter import PlatformAdapter

        adapter = Mock(spec=PlatformAdapter)
        adapter._build_query_result_with_validation = PlatformAdapter._build_query_result_with_validation.__get__(
            adapter, PlatformAdapter
        )
        result = adapter._build_query_result_with_validation(
            query_id="1",
            execution_time=1.0,
            actual_row_count=100,
            validation_result=mock_validation_passed,
        )

        assert result["query_id"] == "1"
        assert result["status"] == "SUCCESS"
        assert result["execution_time_seconds"] == 1.0
        assert result["rows_returned"] == 100

        assert "row_count_validation" in result
        assert result["row_count_validation"]["expected"] == 100
        assert result["row_count_validation"]["actual"] == 100
        assert result["row_count_validation"]["status"] == "PASSED"
        assert "error" not in result["row_count_validation"]
        assert "warning" not in result["row_count_validation"]

    def test_base_adapter_validation_failed(self, mock_validation_failed):

        from benchbox.platforms.base.adapter import PlatformAdapter

        adapter = Mock(spec=PlatformAdapter)
        adapter._build_query_result_with_validation = PlatformAdapter._build_query_result_with_validation.__get__(
            adapter, PlatformAdapter
        )
        result = adapter._build_query_result_with_validation(
            query_id="1",
            execution_time=1.0,
            actual_row_count=50,
            validation_result=mock_validation_failed,
        )

        assert result["status"] == "FAILED"
        assert result["error"] == mock_validation_failed.error_message

        assert "row_count_validation" in result
        assert result["row_count_validation"]["expected"] == 100
        assert result["row_count_validation"]["actual"] == 50
        assert result["row_count_validation"]["status"] == "FAILED"
        assert result["row_count_validation"]["error"] == mock_validation_failed.error_message
        assert "warning" not in result["row_count_validation"]

    def test_base_adapter_loose_tolerance_match_is_not_recorded_as_passed(self):
        from benchbox.platforms.base.adapter import PlatformAdapter

        adapter = Mock(spec=PlatformAdapter)
        adapter._build_query_result_with_validation = PlatformAdapter._build_query_result_with_validation.__get__(
            adapter, PlatformAdapter
        )
        validation = ValidationResult(
            is_valid=True,
            query_id="16",
            expected_row_count=18314,
            actual_row_count=17900,
            validation_mode=ValidationMode.LOOSE,
        )
        result = adapter._build_query_result_with_validation(
            query_id="16",
            execution_time=1.0,
            actual_row_count=17900,
            validation_result=validation,
        )

        evidence = result["row_count_validation"]
        assert result["status"] == "SUCCESS"
        assert evidence["status"] == "SKIPPED"
        assert (evidence["expected"], evidence["actual"]) == (18314, 17900)
        assert "not an exact match" in evidence["warning"]

    def test_base_adapter_validation_skipped(self, mock_validation_skipped):

        from benchbox.platforms.base.adapter import PlatformAdapter

        adapter = Mock(spec=PlatformAdapter)
        adapter._build_query_result_with_validation = PlatformAdapter._build_query_result_with_validation.__get__(
            adapter, PlatformAdapter
        )
        result = adapter._build_query_result_with_validation(
            query_id="1",
            execution_time=1.0,
            actual_row_count=100,
            validation_result=mock_validation_skipped,
        )

        assert result["status"] == "SUCCESS"
        assert "error" not in result

        assert "row_count_validation" in result
        assert result["row_count_validation"]["expected"] is None
        assert result["row_count_validation"]["actual"] == 100
        assert result["row_count_validation"]["status"] == "SKIPPED"
        assert result["row_count_validation"]["warning"] == mock_validation_skipped.warning_message
        assert "error" not in result["row_count_validation"]

    def test_base_adapter_invalid_skip_serializes_as_failed(self):
        from benchbox.platforms.base.adapter import PlatformAdapter

        invalid_skip = ValidationResult(
            is_valid=False,
            query_id="1",
            expected_row_count=None,
            actual_row_count=5,
            validation_mode=ValidationMode.SKIP,
            error_message="Expected-results provider failed for query '1'.",
        )
        adapter = Mock(spec=PlatformAdapter)
        adapter._build_query_result_with_validation = PlatformAdapter._build_query_result_with_validation.__get__(
            adapter, PlatformAdapter
        )
        result = adapter._build_query_result_with_validation(
            query_id="1",
            execution_time=1.0,
            actual_row_count=5,
            validation_result=invalid_skip,
        )

        assert result["status"] == "FAILED"
        assert result["error"] == invalid_skip.error_message
        assert result["row_count_validation"]["status"] == "FAILED"
        assert result["row_count_validation"]["error"] == invalid_skip.error_message

    def test_base_adapter_no_validation(self):

        from benchbox.platforms.base.adapter import PlatformAdapter

        adapter = Mock(spec=PlatformAdapter)
        adapter._build_query_result_with_validation = PlatformAdapter._build_query_result_with_validation.__get__(
            adapter, PlatformAdapter
        )
        result = adapter._build_query_result_with_validation(
            query_id="1",
            execution_time=1.0,
            actual_row_count=100,
            validation_result=None,
        )

        assert result["query_id"] == "1"
        assert result["status"] == "SUCCESS"
        assert result["execution_time_seconds"] == 1.0
        assert result["rows_returned"] == 100

        assert "row_count_validation" not in result
