# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock

import pytest

from benchbox.core.tpc_validation import (
    AuditTrail,
    CertificationChecker,
    CompletenessValidator,
    ComplianceChecker,
    DataIntegrityValidator,
    MetricsValidator,
    QueryResultValidator,
    TimingValidator,
    TPCResultValidator,
    ValidationLevel,
    ValidationReport,
    ValidationResult,
    create_sample_test_results,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestValidationReport:
    def test_validation_report_creation(self):
        report = ValidationReport(
            benchmark_name="TPC-H",
            scale_factor=1.0,
            validation_level=ValidationLevel.STANDARD,
        )

        assert report.benchmark_name == "TPC-H"
        assert report.scale_factor == 1.0
        assert report.validation_level == ValidationLevel.STANDARD
        assert report.overall_result == ValidationResult.PASSED
        assert len(report.issues) == 0
        assert len(report.validator_results) == 0

    def test_add_issue(self):
        report = ValidationReport()

        report.add_issue("ERROR", "Test error message", {"key": "value"}, "test_validator")

        assert len(report.issues) == 1
        assert report.issues[0].level == "ERROR"
        assert report.issues[0].message == "Test error message"
        assert report.issues[0].details == {"key": "value"}
        assert report.issues[0].validator_name == "test_validator"
        assert report.overall_result == ValidationResult.FAILED

    def test_get_issues_by_level(self):
        report = ValidationReport()

        report.add_issue("ERROR", "Error message", validator_name="test1")
        report.add_issue("WARNING", "Warning message", validator_name="test2")
        report.add_issue("ERROR", "Another error", validator_name="test3")

        error_issues = report.get_issues_by_level("ERROR")
        warning_issues = report.get_issues_by_level("WARNING")

        assert len(error_issues) == 2
        assert len(warning_issues) == 1
        assert all(issue.level == "ERROR" for issue in error_issues)
        assert all(issue.level == "WARNING" for issue in warning_issues)

    def test_get_issues_by_validator(self):
        report = ValidationReport()

        report.add_issue("ERROR", "Error 1", validator_name="validator1")
        report.add_issue("WARNING", "Warning 1", validator_name="validator1")
        report.add_issue("ERROR", "Error 2", validator_name="validator2")

        validator1_issues = report.get_issues_by_validator("validator1")
        validator2_issues = report.get_issues_by_validator("validator2")

        assert len(validator1_issues) == 2
        assert len(validator2_issues) == 1

    def test_to_dict(self):
        report = ValidationReport(benchmark_name="TPC-H", scale_factor=1.0)
        report.add_issue("ERROR", "Test error", {"key": "value"}, "test_validator")

        report_dict = report.to_dict()

        assert report_dict["benchmark_name"] == "TPC-H"
        assert report_dict["scale_factor"] == 1.0
        assert report_dict["overall_result"] == "failed"
        assert len(report_dict["issues"]) == 1
        assert report_dict["issues"][0]["level"] == "ERROR"
        assert report_dict["issues"][0]["message"] == "Test error"


class TestCompletenessValidator:
    def test_completeness_validator_success(self):
        config = {
            "required_queries": {"TPC-H": [1, 2, 3]},
            "required_tables": ["customer", "orders"],
            "required_maintenance_ops": [],
        }

        validator = CompletenessValidator(config)
        report = ValidationReport()

        test_results = {
            "benchmark_name": "TPC-H",
            "scale_factor": 1.0,
            "test_start_time": "2023-01-01T10:00:00Z",
            "test_end_time": "2023-01-01T11:00:00Z",
            "query_results": {
                "1": {"status": "success", "execution_time_seconds": 1.0, "row_count": 100},
                "2": {"status": "success", "execution_time_seconds": 2.0, "row_count": 200},
                "3": {"status": "success", "execution_time_seconds": 3.0, "row_count": 300},
            },
            "data_generation": {"generated_tables": ["customer", "orders", "lineitem"]},
            "metrics": {},
            "maintenance_operations": {},
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.PASSED
        assert len(report.issues) == 0

    def test_completeness_validator_missing_queries(self):
        config = {
            "required_queries": {"TPC-H": [1, 2, 3, 4]},
            "required_tables": [],
            "required_maintenance_ops": [],
        }

        validator = CompletenessValidator(config)
        report = ValidationReport()

        test_results = {
            "benchmark_name": "TPC-H",
            "scale_factor": 1.0,
            "test_start_time": "2023-01-01T10:00:00Z",
            "test_end_time": "2023-01-01T11:00:00Z",
            "query_results": {
                "1": {"status": "success", "execution_time_seconds": 1.0, "row_count": 100},
                "2": {"status": "success", "execution_time_seconds": 2.0, "row_count": 200},
            },
            "data_generation": {"generated_tables": []},
            "metrics": {},
            "maintenance_operations": {},
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.FAILED
        assert len(report.issues) > 0
        assert any("Missing required queries" in issue.message for issue in report.issues)

    def test_completeness_validator_missing_tables(self):
        config = {
            "required_queries": {},
            "required_tables": ["customer", "orders", "lineitem"],
            "required_maintenance_ops": [],
        }

        validator = CompletenessValidator(config)
        report = ValidationReport()

        test_results = {
            "benchmark_name": "TPC-H",
            "scale_factor": 1.0,
            "test_start_time": "2023-01-01T10:00:00Z",
            "test_end_time": "2023-01-01T11:00:00Z",
            "query_results": {},
            "data_generation": {"generated_tables": ["customer", "orders"]},
            "metrics": {},
            "maintenance_operations": {},
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.FAILED
        assert len(report.issues) > 0
        assert any("Missing required tables" in issue.message for issue in report.issues)


class TestQueryResultValidator:
    def test_query_result_validator_success(self):
        config = {
            "max_execution_time": 3600,
            "min_row_count": 0,
            "expected_schemas": {},
        }

        validator = QueryResultValidator(config)
        report = ValidationReport()

        test_results = {
            "query_results": {
                "1": {
                    "status": "success",
                    "execution_time_seconds": 5.2,
                    "row_count": 100,
                    "results": [{"col1": "value1", "col2": "value2"}],
                },
                "2": {
                    "status": "success",
                    "execution_time_seconds": 3.1,
                    "row_count": 50,
                    "results": [{"col1": "value3", "col2": "value4"}],
                },
            }
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.PASSED
        assert len(report.issues) == 0

    def test_query_result_validator_failed_query(self):
        validator = QueryResultValidator()
        report = ValidationReport()

        test_results = {
            "query_results": {
                "1": {
                    "status": "failed",
                    "execution_time_seconds": 5.2,
                    "row_count": 0,
                    "error": "SQL syntax error",
                }
            }
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.FAILED
        assert len(report.issues) > 0
        assert any("failed with status" in issue.message for issue in report.issues)

    def test_query_result_validator_timeout(self):
        config = {"max_execution_time": 10}

        validator = QueryResultValidator(config)
        report = ValidationReport()

        test_results = {
            "query_results": {
                "1": {
                    "status": "success",
                    "execution_time_seconds": 15.0,
                    "row_count": 100,
                    "results": [],
                }
            }
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.WARNING
        assert len(report.issues) > 0
        assert any("execution time" in issue.message and "exceeds maximum" in issue.message for issue in report.issues)


class TestTimingValidator:
    def test_timing_validator_success(self):
        validator = TimingValidator()
        report = ValidationReport()

        test_results = {
            "test_start_time": "2023-01-01T10:00:00Z",
            "test_end_time": "2023-01-01T11:00:00Z",
            "query_results": {
                "1": {"execution_time_seconds": 4.1},
                "2": {"execution_time_seconds": 4.2},
            },
            "data_generation": {
                "generation_time": 120.5,
                "generated_tables": ["customer", "orders"],
            },
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.PASSED
        assert len(report.issues) == 0

    def test_timing_validator_invalid_timestamps(self):
        validator = TimingValidator()
        report = ValidationReport()

        test_results = {
            "test_start_time": "2023-01-01T11:00:00Z",
            "test_end_time": "2023-01-01T10:00:00Z",
            "query_results": {},
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.FAILED
        assert len(report.issues) > 0
        assert any("end time before start time" in issue.message for issue in report.issues)

    def test_timing_validator_excessive_total_time(self):
        config = {"max_total_time": 3600}

        validator = TimingValidator(config)
        report = ValidationReport()

        test_results = {
            "test_start_time": "2023-01-01T10:00:00Z",
            "test_end_time": "2023-01-01T13:00:00Z",
            "query_results": {},
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.WARNING
        assert len(report.issues) > 0
        assert any("Total test time" in issue.message and "exceeds maximum" in issue.message for issue in report.issues)


class TestDataIntegrityValidator:
    def test_data_integrity_validator_success(self):
        validator = DataIntegrityValidator()
        report = ValidationReport()

        test_results = {
            "maintenance_operations": {
                "insert_operation": {
                    "status": "success",
                    "start_time": "2023-01-01T10:00:00Z",
                    "end_time": "2023-01-01T10:05:00Z",
                    "records_affected": 1000,
                }
            },
            "data_generation": {
                "generated_tables": ["customer", "orders", "lineitem"],
                "generation_time": 120.5,
            },
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.PASSED
        assert len(report.issues) == 0

    def test_data_integrity_validator_failed_operation(self):
        validator = DataIntegrityValidator()
        report = ValidationReport()

        test_results = {
            "maintenance_operations": {
                "insert_operation": {
                    "status": "failed",
                    "start_time": "2023-01-01T10:00:00Z",
                    "end_time": "2023-01-01T10:05:00Z",
                    "records_affected": 0,
                    "error": "Database connection failed",
                }
            }
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.FAILED
        assert len(report.issues) > 0
        assert any("failed integrity validation" in issue.message for issue in report.issues)

    def test_data_integrity_validator_no_operations(self):
        validator = DataIntegrityValidator()
        report = ValidationReport()

        test_results = {"maintenance_operations": {}}

        result = validator.validate(test_results, report)

        assert result == ValidationResult.PASSED


class TestMetricsValidator:
    def test_metrics_validator_success(self):
        config = {
            "required_metrics": ["avg_query_time", "total_query_time"],
            "metric_ranges": {"avg_query_time": {"min": 0.0, "max": 300.0}},
        }

        validator = MetricsValidator(config)
        report = ValidationReport()

        test_results = {
            "metrics": {
                "avg_query_time": 4.15,
                "total_query_time": 8.3,
                "queries_per_second": 0.0005555555555555556,
            },
            "query_results": {
                "1": {"execution_time_seconds": 4.15},
                "2": {"execution_time_seconds": 4.15},
            },
            "test_start_time": "2023-01-01T10:00:00Z",
            "test_end_time": "2023-01-01T11:00:00Z",
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.PASSED
        assert len(report.issues) == 0

    def test_metrics_validator_missing_metrics(self):
        config = {
            "required_metrics": [
                "avg_query_time",
                "total_query_time",
                "missing_metric",
            ],
            "metric_ranges": {},
        }

        validator = MetricsValidator(config)
        report = ValidationReport()

        test_results = {
            "metrics": {
                "avg_query_time": 4.15,
                "total_query_time": 8.3,
            },
            "query_results": {},
            "test_start_time": "2023-01-01T10:00:00Z",
            "test_end_time": "2023-01-01T11:00:00Z",
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.FAILED
        assert len(report.issues) > 0
        assert any("Required metric missing" in issue.message for issue in report.issues)

    def test_metrics_validator_out_of_range(self):
        config = {
            "required_metrics": [],
            "metric_ranges": {"avg_query_time": {"min": 0.0, "max": 5.0}},
        }

        validator = MetricsValidator(config)
        report = ValidationReport()

        test_results = {
            "metrics": {"avg_query_time": 10.0},
            "query_results": {},
            "test_start_time": "2023-01-01T10:00:00Z",
            "test_end_time": "2023-01-01T11:00:00Z",
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.WARNING
        assert len(report.issues) > 0
        assert any("above maximum" in issue.message for issue in report.issues)


class TestComplianceChecker:
    def test_compliance_checker_tpch_success(self):
        validator = ComplianceChecker()
        report = ValidationReport()

        test_results = {
            "benchmark_name": "TPC-H",
            "scale_factor": 1.0,
            "query_results": {str(i): {"status": "success"} for i in range(1, 23)},
            "test_isolation": {"isolated": True},
            "reproducibility": {
                "seed": 12345,
                "timestamp": "2023-01-01T10:00:00Z",
                "environment": "test_env",
            },
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.PASSED
        assert len(report.issues) == 0

    def test_compliance_checker_tpch_missing_queries(self):
        validator = ComplianceChecker()
        report = ValidationReport()

        test_results = {
            "benchmark_name": "TPC-H",
            "scale_factor": 1.0,
            "query_results": {str(i): {"status": "success"} for i in range(1, 20)},
            "test_isolation": {"isolated": True},
            "reproducibility": {
                "seed": 12345,
                "timestamp": "2023-01-01T10:00:00Z",
                "environment": "test_env",
            },
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.FAILED
        assert len(report.issues) > 0
        assert any("requires all 22 queries" in issue.message for issue in report.issues)

    def test_compliance_checker_tpcds_success(self):
        validator = ComplianceChecker()
        report = ValidationReport()

        test_results = {
            "benchmark_name": "TPC-DS",
            "scale_factor": 1.0,
            "query_results": {str(i): {"status": "success"} for i in range(1, 100)},
            "maintenance_operations": {"insert_op": {"status": "success"}},
            "test_isolation": {"isolated": True},
            "reproducibility": {
                "seed": 12345,
                "timestamp": "2023-01-01T10:00:00Z",
                "environment": "test_env",
            },
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.PASSED
        assert len(report.issues) == 0

    def test_compliance_checker_unknown_benchmark(self):
        validator = ComplianceChecker()
        report = ValidationReport()

        test_results = {
            "benchmark_name": "UNKNOWN-BENCHMARK",
            "scale_factor": 1.0,
            "query_results": {},
            "test_isolation": {"isolated": True},
            "reproducibility": {
                "seed": 12345,
                "timestamp": "2023-01-01T10:00:00Z",
                "environment": "test_env",
            },
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.WARNING
        assert len(report.issues) > 0
        assert any("No specific compliance rules" in issue.message for issue in report.issues)


class TestCertificationChecker:
    def test_certification_checker_ready(self):
        config = {
            "required_documentation": ["test_report", "environment_spec"],
            "performance_thresholds": {"avg_query_time": 100.0},
        }

        validator = CertificationChecker(config)
        report = ValidationReport()

        test_results = {
            "metrics": {"avg_query_time": 50.0},
            "documentation": {
                "test_report": "path/to/test_report.pdf",
                "environment_spec": "path/to/env_spec.json",
            },
            "query_results": {"1": {"status": "success"}, "2": {"status": "success"}},
            "reproducibility": {"seed": 12345, "environment": "test_env"},
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.PASSED
        assert report.certification_status == "READY"
        assert len(report.issues) == 0

    def test_certification_checker_not_ready(self):
        config = {
            "required_documentation": ["test_report", "environment_spec"],
            "performance_thresholds": {},
        }

        validator = CertificationChecker(config)
        report = ValidationReport()

        test_results = {
            "metrics": {},
            "documentation": {"test_report": "path/to/test_report.pdf"},
            "query_results": {"1": {"status": "failed"}},
            "reproducibility": {"seed": 12345, "environment": "test_env"},
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.FAILED
        assert report.certification_status == "NOT_READY"
        assert len(report.issues) > 0

    def test_certification_checker_conditional(self):
        config = {
            "required_documentation": ["test_report"],
            "performance_thresholds": {"avg_query_time": 10.0},
        }

        validator = CertificationChecker(config)
        report = ValidationReport()

        test_results = {
            "metrics": {"avg_query_time": 50.0},
            "documentation": {"test_report": "path/to/test_report.pdf"},
            "query_results": {"1": {"status": "success"}},
            "reproducibility": {"seed": 12345, "environment": "test_env"},
        }

        result = validator.validate(test_results, report)

        assert result == ValidationResult.WARNING
        assert report.certification_status == "CONDITIONAL"
        assert len(report.issues) > 0
        assert any("exceeds validation threshold" in issue.message for issue in report.issues)


class TestAuditTrail:
    def test_audit_trail_logging(self):
        audit_trail = AuditTrail()

        audit_trail.log_event("test_event", "Test event description", {"key": "value"})

        summary = audit_trail.get_audit_summary()

        assert summary["total_events"] == 1
        assert len(summary["events"]) == 1
        assert summary["events"][0]["event_type"] == "test_event"
        assert summary["events"][0]["description"] == "Test event description"
        assert summary["events"][0]["details"] == {"key": "value"}

    def test_audit_trail_reproducibility_hash(self):
        audit_trail = AuditTrail()

        test_results = {
            "benchmark_name": "TPC-H",
            "scale_factor": 1.0,
            "query_results": {"1": {}, "2": {}},
            "reproducibility": {"seed": 12345},
        }

        hash1 = audit_trail.generate_reproducibility_hash(test_results)
        hash2 = audit_trail.generate_reproducibility_hash(test_results)

        assert hash1 == hash2
        assert len(hash1) == 64

        test_results["scale_factor"] = 2.0
        hash3 = audit_trail.generate_reproducibility_hash(test_results)
        assert hash1 != hash3


class TestTPCResultValidator:
    def test_tpc_result_validator_creation(self):
        validator = TPCResultValidator()

        assert len(validator.validators) == 7
        assert validator.audit_trail is not None
        assert validator.logger is not None

    def test_tpc_result_validator_validation(self):
        validator = TPCResultValidator()
        test_results = create_sample_test_results()

        report = validator.validate(test_results, ValidationLevel.STANDARD)

        assert report.benchmark_name == "TPC-H"
        assert report.scale_factor == 1.0
        assert report.validation_level == ValidationLevel.STANDARD
        assert len(report.validator_results) == 7
        assert len(report.audit_trail) > 0
        assert "validation_score" in report.metrics
        assert "total_queries" in report.execution_summary

    def test_tpc_result_validator_save_load_report(self, tmp_path):
        validator = TPCResultValidator()
        test_results = create_sample_test_results()

        report = validator.validate(test_results, ValidationLevel.STANDARD)

        report_path = tmp_path / "test_report.json"
        validator.save_report(report, report_path)

        assert report_path.exists()

        loaded_report = validator.load_report(report_path)

        assert loaded_report.validation_id == report.validation_id
        assert loaded_report.benchmark_name == report.benchmark_name
        assert loaded_report.scale_factor == report.scale_factor
        assert loaded_report.validation_level == report.validation_level
        assert loaded_report.overall_result == report.overall_result
        assert len(loaded_report.issues) == len(report.issues)
        assert len(loaded_report.validator_results) == len(report.validator_results)

    def test_tpc_result_validator_default_config(self):
        validator = TPCResultValidator()
        config = validator.create_default_config()

        assert "validators" in config
        assert "completeness" in config["validators"]
        assert "query_result" in config["validators"]
        assert "timing" in config["validators"]
        assert "data_integrity" in config["validators"]
        assert "metrics" in config["validators"]
        assert "compliance" in config["validators"]
        assert "certification" in config["validators"]

    def test_tpc_result_validator_with_config(self):
        config = {
            "validators": {
                "completeness": {"required_queries": {"TPC-H": list(range(1, 23))}},
                "timing": {"max_total_time": 7200},
            }
        }

        validator = TPCResultValidator(config)
        test_results = create_sample_test_results()

        report = validator.validate(test_results, ValidationLevel.CERTIFICATION)

        assert report.validation_level == ValidationLevel.CERTIFICATION
        assert len(report.validator_results) == 7

    def test_tpc_result_validator_validator_exception(self):
        validator = TPCResultValidator()

        mock_validator = Mock()
        mock_validator.name = "mock_validator"
        mock_validator.validate.side_effect = Exception("Test exception")

        validator.validators[0] = mock_validator

        test_results = create_sample_test_results()
        report = validator.validate(test_results, ValidationLevel.STANDARD)

        assert "mock_validator" in report.validator_results
        assert report.validator_results["mock_validator"] == ValidationResult.FAILED
        assert any("failed with exception" in issue.message for issue in report.issues)


class TestCreateSampleTestResults:
    def test_create_sample_test_results(self):
        sample_results = create_sample_test_results()

        assert sample_results["benchmark_name"] == "TPC-H"
        assert sample_results["scale_factor"] == 1.0
        assert "test_start_time" in sample_results
        assert "test_end_time" in sample_results
        assert "query_results" in sample_results
        assert "data_generation" in sample_results
        assert "metrics" in sample_results
        assert "reproducibility" in sample_results

        query_results = sample_results["query_results"]
        assert len(query_results) == 2
        assert all("status" in qr for qr in query_results.values())
        assert all("execution_time_seconds" in qr for qr in query_results.values())
        assert all("row_count" in qr for qr in query_results.values())

        data_generation = sample_results["data_generation"]
        assert "generation_time" in data_generation
        assert "generated_tables" in data_generation
        assert len(data_generation["generated_tables"]) == 8

        metrics = sample_results["metrics"]
        assert "avg_query_time" in metrics
        assert "total_query_time" in metrics
        assert "queries_per_second" in metrics


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
