from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.utils import (
    ResultValidator,
    assert_benchmark_result_valid,
    assert_phase_timing_valid,
    assert_query_execution_valid,
    load_result_json,
    validate_execution_phases,
    validate_result_structure,
)

pytestmark = pytest.mark.medium


class TestResultStructure:
    def test_valid_result_passes_validation(self) -> None:
        valid_result = {
            "benchmark_name": "TPC-H",
            "platform": "duckdb",
            "scale_factor": 0.01,
            "execution_id": "test-execution-123",
            "timestamp": "2024-01-15T10:30:00Z",
            "duration_seconds": 45.5,
            "total_queries": 22,
            "successful_queries": 22,
            "failed_queries": 0,
            "query_results": [
                {"query_id": "Q1", "status": "SUCCESS", "execution_time_ms": 100},
                {"query_id": "Q2", "status": "SUCCESS", "execution_time_ms": 150},
            ],
        }

        is_valid, errors = validate_result_structure(valid_result)
        assert is_valid, f"Validation failed with errors: {errors}"
        assert len(errors) == 0

    def test_missing_required_field_fails(self) -> None:
        invalid_result = {
            "benchmark_name": "TPC-H",
            "platform": "duckdb",
        }

        is_valid, errors = validate_result_structure(invalid_result)
        assert not is_valid
        assert len(errors) > 0

        missing_fields = {e.field for e in errors}
        assert "scale_factor" in missing_fields or any("scale_factor" in e.message for e in errors)

    def test_null_required_field_fails(self) -> None:
        invalid_result = {
            "benchmark_name": "TPC-H",
            "platform": "duckdb",
            "scale_factor": None,
            "execution_id": "test-123",
            "timestamp": "2024-01-15T10:30:00Z",
            "duration_seconds": 45.5,
            "total_queries": 22,
            "successful_queries": 22,
            "failed_queries": 0,
        }

        is_valid, errors = validate_result_structure(invalid_result)
        assert not is_valid
        assert any("scale_factor" in e.field for e in errors)

    def test_invalid_timestamp_format_fails(self) -> None:
        invalid_result = {
            "benchmark_name": "TPC-H",
            "platform": "duckdb",
            "scale_factor": 0.01,
            "execution_id": "test-123",
            "timestamp": "invalid-timestamp-format",
            "duration_seconds": 45.5,
            "total_queries": 22,
            "successful_queries": 22,
            "failed_queries": 0,
        }

        is_valid, errors = validate_result_structure(invalid_result)
        assert not is_valid
        assert any("timestamp" in e.field for e in errors)

    def test_negative_duration_fails(self) -> None:
        invalid_result = {
            "benchmark_name": "TPC-H",
            "platform": "duckdb",
            "scale_factor": 0.01,
            "execution_id": "test-123",
            "timestamp": "2024-01-15T10:30:00Z",
            "duration_seconds": -10,
            "total_queries": 22,
            "successful_queries": 22,
            "failed_queries": 0,
        }

        is_valid, errors = validate_result_structure(invalid_result)
        assert not is_valid
        assert any("duration" in e.field for e in errors)

    def test_query_count_mismatch_fails(self) -> None:
        invalid_result = {
            "benchmark_name": "TPC-H",
            "platform": "duckdb",
            "scale_factor": 0.01,
            "execution_id": "test-123",
            "timestamp": "2024-01-15T10:30:00Z",
            "duration_seconds": 45.5,
            "total_queries": 22,
            "successful_queries": 20,
            "failed_queries": 1,
            "query_results": [],
        }

        is_valid, errors = validate_result_structure(invalid_result)
        assert not is_valid
        assert any("count" in e.field or "count" in e.message.lower() for e in errors)


class TestQueryExecutionValidation:
    def test_valid_query_execution_passes(self) -> None:
        query_exec = {
            "query_id": "Q1",
            "status": "SUCCESS",
            "execution_time_ms": 150,
            "rows_returned": 100,
        }

        assert_query_execution_valid(query_exec)

    def test_missing_query_id_fails(self) -> None:
        query_exec = {
            "status": "SUCCESS",
            "execution_time_ms": 150,
        }

        with pytest.raises(AssertionError, match="query_id"):
            assert_query_execution_valid(query_exec)

    def test_empty_query_id_fails(self) -> None:
        query_exec = {
            "query_id": "",
            "status": "SUCCESS",
            "execution_time_ms": 150,
        }

        with pytest.raises(AssertionError, match="non-empty"):
            assert_query_execution_valid(query_exec)

    def test_invalid_status_fails(self) -> None:
        query_exec = {
            "query_id": "Q1",
            "status": "INVALID_STATUS",
            "execution_time_ms": 150,
        }

        with pytest.raises(AssertionError, match="status"):
            assert_query_execution_valid(query_exec)

    def test_negative_execution_time_fails(self) -> None:
        query_exec = {
            "query_id": "Q1",
            "status": "SUCCESS",
            "execution_time_ms": -100,
        }

        with pytest.raises(AssertionError):
            assert_query_execution_valid(query_exec)

    def test_failed_query_no_time_required(self) -> None:
        query_exec = {
            "query_id": "Q1",
            "status": "FAILED",
            "error_message": "Query failed",
        }

        assert_query_execution_valid(query_exec, require_positive_time=False)

    @pytest.mark.parametrize(
        "status",
        ["SUCCESS", "FAILED", "ERROR", "TIMEOUT", "SKIPPED"],
    )
    def test_valid_statuses_accepted(self, status: str) -> None:
        query_exec = {
            "query_id": "Q1",
            "status": status,
            "execution_time_ms": 100 if status == "SUCCESS" else 0,
        }

        assert_query_execution_valid(query_exec, require_positive_time=False)


class TestPhaseTimingValidation:
    def test_valid_phase_timing_passes(self) -> None:
        phase = {
            "duration_ms": 5000,
            "status": "completed",
        }

        assert_phase_timing_valid(phase, "test_phase")

    def test_missing_duration_fails(self) -> None:
        phase = {
            "status": "completed",
        }

        with pytest.raises(AssertionError, match="duration_ms"):
            assert_phase_timing_valid(phase, "test_phase")

    def test_negative_duration_fails(self) -> None:
        phase = {
            "duration_ms": -100,
        }

        with pytest.raises(AssertionError):
            assert_phase_timing_valid(phase, "test_phase")

    def test_zero_duration_allowed_when_not_required_positive(self) -> None:
        phase = {
            "duration_ms": 0,
        }

        assert_phase_timing_valid(phase, "test_phase", require_positive_duration=False)


class TestExecutionPhasesValidation:
    def test_valid_execution_phases_passes(self) -> None:
        result = {
            "benchmark_name": "TPC-H",
            "platform": "duckdb",
            "scale_factor": 0.01,
            "execution_id": "test-123",
            "timestamp": "2024-01-15T10:30:00Z",
            "duration_seconds": 45.5,
            "total_queries": 22,
            "successful_queries": 22,
            "failed_queries": 0,
            "execution_phases": {
                "setup": {
                    "data_generation": {
                        "duration_ms": 1000,
                        "status": "completed",
                    },
                    "data_loading": {
                        "duration_ms": 2000,
                        "status": "completed",
                    },
                },
                "power_test": {
                    "duration_ms": 10000,
                    "start_time": "2024-01-15T10:30:00Z",
                    "end_time": "2024-01-15T10:30:10Z",
                    "query_executions": [
                        {"query_id": "Q1", "status": "SUCCESS", "execution_time_ms": 100},
                    ],
                },
            },
        }

        is_valid, errors = validate_execution_phases(result)
        assert is_valid, f"Validation failed: {errors}"

    def test_invalid_power_test_duration_fails(self) -> None:
        result = {
            "execution_phases": {
                "setup": {},
                "power_test": {
                    "duration_ms": -100,
                    "query_executions": [],
                },
            },
        }

        is_valid, errors = validate_execution_phases(result)
        assert not is_valid
        assert any("power_test" in e.field for e in errors)

    def test_invalid_throughput_streams_fails(self) -> None:
        result = {
            "execution_phases": {
                "setup": {},
                "throughput_test": {
                    "duration_ms": 10000,
                    "num_streams": -1,
                    "streams": [],
                },
            },
        }

        is_valid, errors = validate_execution_phases(result)
        assert not is_valid
        assert any("num_streams" in e.field for e in errors)


class TestResultValidatorClass:
    def test_validator_collects_all_errors(self) -> None:
        invalid_result = {
            "benchmark_name": "",
            "platform": "",
            "scale_factor": -1,
            "execution_id": "",
            "timestamp": "invalid",
            "duration_seconds": -10,
            "total_queries": 10,
            "successful_queries": 5,
            "failed_queries": 3,
        }

        validator = ResultValidator(invalid_result)
        is_valid = validator.validate_all()

        assert not is_valid
        assert len(validator.errors) > 1

    def test_validator_reusable(self) -> None:
        valid_result = {
            "benchmark_name": "TPC-H",
            "platform": "duckdb",
            "scale_factor": 0.01,
            "execution_id": "test-123",
            "timestamp": "2024-01-15T10:30:00Z",
            "duration_seconds": 45.5,
            "total_queries": 22,
            "successful_queries": 22,
            "failed_queries": 0,
        }

        validator = ResultValidator(valid_result)
        assert validator.validate_all()
        assert len(validator.errors) == 0

        assert validator.validate_all()
        assert len(validator.errors) == 0


class TestLoadResultFile:
    def test_load_valid_json_file(self, tmp_path: Path) -> None:
        result_data = {
            "benchmark_name": "TPC-H",
            "platform": "duckdb",
            "scale_factor": 0.01,
            "execution_id": "test-123",
            "timestamp": "2024-01-15T10:30:00Z",
            "duration_seconds": 45.5,
            "total_queries": 22,
            "successful_queries": 22,
            "failed_queries": 0,
        }

        result_file = tmp_path / "result.json"
        result_file.write_text(json.dumps(result_data))

        loaded = load_result_json(result_file)
        assert loaded == result_data

    def test_load_nonexistent_file_raises(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nonexistent.json"

        with pytest.raises(FileNotFoundError):
            load_result_json(nonexistent)

    def test_load_invalid_json_raises(self, tmp_path: Path) -> None:
        invalid_file = tmp_path / "invalid.json"
        invalid_file.write_text("not valid json {{{")

        with pytest.raises(json.JSONDecodeError):
            load_result_json(invalid_file)

    def test_load_and_validate_file(self, tmp_path: Path) -> None:
        result_data = {
            "benchmark_name": "TPC-H",
            "platform": "duckdb",
            "scale_factor": 0.01,
            "execution_id": "test-123",
            "timestamp": "2024-01-15T10:30:00Z",
            "duration_seconds": 45.5,
            "total_queries": 22,
            "successful_queries": 22,
            "failed_queries": 0,
            "query_results": [
                {"query_id": "Q1", "status": "SUCCESS", "execution_time_ms": 100},
            ],
        }

        result_file = tmp_path / "result.json"
        result_file.write_text(json.dumps(result_data))

        loaded = load_result_json(result_file)
        assert_benchmark_result_valid(loaded)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2024-01-15T10:30:00Z",
        "2024-01-15T10:30:00+00:00",
        "2024-01-15T10:30:00.000Z",
        "2024-01-15T10:30:00.123456Z",
    ],
)
def test_valid_timestamp_formats(timestamp: str) -> None:
    result = {
        "benchmark_name": "TPC-H",
        "platform": "duckdb",
        "scale_factor": 0.01,
        "execution_id": "test-123",
        "timestamp": timestamp,
        "duration_seconds": 45.5,
        "total_queries": 22,
        "successful_queries": 22,
        "failed_queries": 0,
    }

    is_valid, errors = validate_result_structure(result)
    assert is_valid, f"Timestamp {timestamp} should be valid: {errors}"


@pytest.mark.parametrize(
    "scale_factor",
    [0.01, 0.1, 1, 10, 100, 1000],
)
def test_valid_scale_factors(scale_factor: float) -> None:
    result = {
        "benchmark_name": "TPC-H",
        "platform": "duckdb",
        "scale_factor": scale_factor,
        "execution_id": "test-123",
        "timestamp": "2024-01-15T10:30:00Z",
        "duration_seconds": 45.5,
        "total_queries": 22,
        "successful_queries": 22,
        "failed_queries": 0,
    }

    is_valid, errors = validate_result_structure(result)
    assert is_valid, f"Scale factor {scale_factor} should be valid: {errors}"
