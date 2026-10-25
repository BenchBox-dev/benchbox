# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import hashlib
import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any, Optional

from benchbox.utils.printing import emit


class ValidationLevel(Enum):
    BASIC = "basic"
    STANDARD = "standard"
    CERTIFICATION = "certification"


class ValidationResult(Enum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


@dataclass
class ValidationIssue:
    level: str
    message: str
    details: Optional[dict[str, Any]] = None
    timestamp: datetime = field(default_factory=datetime.now)
    validator_name: str = ""


@dataclass
class ValidationReport:
    validation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)
    benchmark_name: str = ""
    scale_factor: float = 1.0
    validation_level: ValidationLevel = ValidationLevel.STANDARD
    overall_result: ValidationResult = ValidationResult.PASSED
    issues: list[ValidationIssue] = field(default_factory=list)
    validator_results: dict[str, ValidationResult] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    execution_summary: dict[str, Any] = field(default_factory=dict)
    audit_trail: list[dict[str, Any]] = field(default_factory=list)
    certification_status: Optional[str] = None

    def add_issue(
        self,
        level: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
        validator_name: str = "",
    ) -> None:
        issue = ValidationIssue(level=level, message=message, details=details, validator_name=validator_name)
        self.issues.append(issue)

        if level == "ERROR" and self.overall_result == ValidationResult.PASSED:
            self.overall_result = ValidationResult.FAILED
        elif level == "WARNING" and self.overall_result == ValidationResult.PASSED:
            self.overall_result = ValidationResult.WARNING

    def get_issues_by_level(self, level: str) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.level == level]

    def get_issues_by_validator(self, validator_name: str) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.validator_name == validator_name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "timestamp": self.timestamp.isoformat(),
            "benchmark_name": self.benchmark_name,
            "scale_factor": self.scale_factor,
            "validation_level": self.validation_level.value,
            "overall_result": self.overall_result.value,
            "issues": [
                {
                    "level": issue.level,
                    "message": issue.message,
                    "details": issue.details,
                    "timestamp": issue.timestamp.isoformat(),
                    "validator_name": issue.validator_name,
                }
                for issue in self.issues
            ],
            "validator_results": {k: v.value for k, v in self.validator_results.items()},
            "metrics": self.metrics,
            "execution_summary": self.execution_summary,
            "audit_trail": self.audit_trail,
            "certification_status": self.certification_status,
        }


class BaseValidator(ABC):
    def __init__(self, name: str, config: Optional[dict[str, Any]] = None) -> None:
        self.name = name
        self.config = config or {}
        self.logger = logging.getLogger(f"tpc_validation.{name}")

    @abstractmethod
    def validate(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        pass

    def _check_required_fields(self, data: dict[str, Any], required_fields: list[str]) -> list[str]:
        missing = []
        for field_name in required_fields:
            if field_name not in data:
                missing.append(field_name)
        return missing


class CompletenessValidator(BaseValidator):
    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        super().__init__("completeness", config)
        self.required_queries = self.config.get("required_queries", {})
        self.required_tables = self.config.get("required_tables", [])
        self.required_maintenance_ops = self.config.get("required_maintenance_ops", [])

    def validate(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED

        required_fields = [
            "benchmark_name",
            "scale_factor",
            "test_start_time",
            "test_end_time",
            "query_results",
            "data_generation",
            "metrics",
        ]

        missing_fields = self._check_required_fields(test_results, required_fields)
        if missing_fields:
            report.add_issue(
                "ERROR",
                f"Missing required top-level fields: {', '.join(missing_fields)}",
                {"missing_fields": missing_fields},
                self.name,
            )
            result = ValidationResult.FAILED

        query_results = test_results.get("query_results", {})
        if self.required_queries:
            benchmark_name = test_results.get("benchmark_name", "unknown")
            expected_queries = self.required_queries.get(benchmark_name, [])

            missing_queries = []
            for query_id in expected_queries:
                if str(query_id) not in query_results:
                    missing_queries.append(query_id)

            if missing_queries:
                report.add_issue(
                    "ERROR",
                    f"Missing required queries: {missing_queries}",
                    {
                        "missing_queries": missing_queries,
                        "expected_queries": expected_queries,
                    },
                    self.name,
                )
                result = ValidationResult.FAILED

        data_generation = test_results.get("data_generation", {})
        if self.required_tables:
            generated_tables = data_generation.get("generated_tables", [])
            missing_tables = [table for table in self.required_tables if table not in generated_tables]

            if missing_tables:
                report.add_issue(
                    "ERROR",
                    f"Missing required tables: {missing_tables}",
                    {
                        "missing_tables": missing_tables,
                        "required_tables": self.required_tables,
                    },
                    self.name,
                )
                result = ValidationResult.FAILED

        maintenance_ops = test_results.get("maintenance_operations", {})
        if self.required_maintenance_ops:
            completed_ops = list(maintenance_ops.keys())
            missing_ops = [op for op in self.required_maintenance_ops if op not in completed_ops]

            if missing_ops:
                report.add_issue(
                    "ERROR",
                    f"Missing required maintenance operations: {missing_ops}",
                    {
                        "missing_ops": missing_ops,
                        "required_ops": self.required_maintenance_ops,
                    },
                    self.name,
                )
                result = ValidationResult.FAILED

        for query_id, query_result in query_results.items():
            required_query_fields = ["execution_time_seconds", "status", "row_count"]
            missing_query_fields = self._check_required_fields(query_result, required_query_fields)

            if missing_query_fields:
                report.add_issue(
                    "WARNING",
                    f"Query {query_id} missing fields: {', '.join(missing_query_fields)}",
                    {"query_id": query_id, "missing_fields": missing_query_fields},
                    self.name,
                )
                if result == ValidationResult.PASSED:
                    result = ValidationResult.WARNING

        return result


class QueryResultValidator(BaseValidator):
    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        super().__init__("query_result", config)
        self.max_execution_time = self.config.get("max_execution_time", 3600)
        self.min_row_count = self.config.get("min_row_count", 0)
        self.expected_schemas = self.config.get("expected_schemas", {})

    def validate(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED
        query_results = test_results.get("query_results", {})

        for query_id, query_result in query_results.items():
            status = query_result.get("status", "unknown")
            if status != "success":
                report.add_issue(
                    "ERROR",
                    f"Query {query_id} failed with status: {status}",
                    {
                        "query_id": query_id,
                        "status": status,
                        "error": query_result.get("error"),
                    },
                    self.name,
                )
                result = ValidationResult.FAILED
                continue

            execution_time = query_result.get("execution_time_seconds", 0)
            if execution_time > self.max_execution_time:
                report.add_issue(
                    "WARNING",
                    f"Query {query_id} execution time ({execution_time}s) exceeds maximum ({self.max_execution_time}s)",
                    {
                        "query_id": query_id,
                        "execution_time_seconds": execution_time,
                        "max_time": self.max_execution_time,
                    },
                    self.name,
                )
                if result == ValidationResult.PASSED:
                    result = ValidationResult.WARNING

            row_count = query_result.get("row_count", 0)
            if row_count < self.min_row_count:
                report.add_issue(
                    "WARNING",
                    f"Query {query_id} returned {row_count} rows, minimum expected: {self.min_row_count}",
                    {
                        "query_id": query_id,
                        "row_count": row_count,
                        "min_count": self.min_row_count,
                    },
                    self.name,
                )
                if result == ValidationResult.PASSED:
                    result = ValidationResult.WARNING

            if "results" in query_result:
                results = query_result["results"]
                if not isinstance(results, list):
                    report.add_issue(
                        "ERROR",
                        f"Query {query_id} results are not in list format",
                        {"query_id": query_id, "result_type": type(results).__name__},
                        self.name,
                    )
                    result = ValidationResult.FAILED

                if row_count > 0 and not results:
                    report.add_issue(
                        "WARNING",
                        f"Query {query_id} reports {row_count} rows but results are empty",
                        {
                            "query_id": query_id,
                            "row_count": row_count,
                            "results_length": len(results),
                        },
                        self.name,
                    )
                    if result == ValidationResult.PASSED:
                        result = ValidationResult.WARNING

            if query_id in self.expected_schemas:
                schema_validation = self._validate_result_schema(
                    query_id, query_result, self.expected_schemas[query_id]
                )
                if not schema_validation["valid"]:
                    report.add_issue(
                        "ERROR",
                        f"Query {query_id} schema validation failed: {schema_validation['error']}",
                        {"query_id": query_id, "schema_error": schema_validation},
                        self.name,
                    )
                    result = ValidationResult.FAILED

        return result

    def _validate_result_schema(
        self,
        query_id: str,
        query_result: dict[str, Any],
        expected_schema: dict[str, Any],
    ) -> dict[str, Any]:
        results = query_result.get("results", [])
        if not results:
            return {"valid": True, "message": "No results to validate"}

        first_row = results[0]
        if not isinstance(first_row, dict):
            return {"valid": False, "error": "Results are not in dictionary format"}

        expected_columns = set(expected_schema.get("columns", []))
        actual_columns = set(first_row.keys())

        missing_columns = expected_columns - actual_columns
        extra_columns = actual_columns - expected_columns

        if missing_columns:
            return {"valid": False, "error": f"Missing columns: {missing_columns}"}

        if extra_columns and not expected_schema.get("allow_extra_columns", False):
            return {"valid": False, "error": f"Unexpected columns: {extra_columns}"}

        return {"valid": True, "message": "Schema validation passed"}


class TimingValidator(BaseValidator):
    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        super().__init__("timing", config)
        self.precision_threshold = self.config.get("precision_threshold", 0.001)
        self.max_total_time = self.config.get("max_total_time", 86400)
        self.min_query_time = self.config.get("min_query_time", 0.0)
        self.timing_consistency_threshold = self.config.get("timing_consistency_threshold", 0.1)

    def validate(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED

        result = self._validate_overall_timing(test_results, report, result)

        result = self._validate_query_timing(test_results, report, result)

        result = self._validate_data_generation_timing(test_results, report, result)

        return result

    def _validate_overall_timing(
        self, test_results: dict[str, Any], report: ValidationReport, result: ValidationResult
    ) -> ValidationResult:
        test_start = test_results.get("test_start_time")
        test_end = test_results.get("test_end_time")

        if not (test_start and test_end):
            return result

        try:
            start_time = datetime.fromisoformat(test_start.replace("Z", "+00:00"))
            end_time = datetime.fromisoformat(test_end.replace("Z", "+00:00"))
            total_time = (end_time - start_time).total_seconds()

            if total_time > self.max_total_time:
                report.add_issue(
                    "WARNING",
                    f"Total test time ({total_time}s) exceeds maximum ({self.max_total_time}s)",
                    {"total_time": total_time, "max_time": self.max_total_time},
                    self.name,
                )
                if result == ValidationResult.PASSED:
                    result = ValidationResult.WARNING

            if total_time < 0:
                report.add_issue(
                    "ERROR",
                    "Invalid test timing: end time before start time",
                    {
                        "start_time": test_start,
                        "end_time": test_end,
                        "total_time": total_time,
                    },
                    self.name,
                )
                result = ValidationResult.FAILED

        except ValueError as e:
            report.add_issue(
                "ERROR",
                f"Invalid timestamp format: {e}",
                {"start_time": test_start, "end_time": test_end},
                self.name,
            )
            result = ValidationResult.FAILED

        return result

    def _validate_query_timing(
        self, test_results: dict[str, Any], report: ValidationReport, result: ValidationResult
    ) -> ValidationResult:
        query_results = test_results.get("query_results", {})
        execution_times = []

        for query_id, query_result in query_results.items():
            execution_time = query_result.get("execution_time_seconds", 0)

            if execution_time < self.min_query_time:
                report.add_issue(
                    "WARNING",
                    f"Query {query_id} execution time ({execution_time}s) below minimum ({self.min_query_time}s)",
                    {
                        "query_id": query_id,
                        "execution_time_seconds": execution_time,
                        "min_time": self.min_query_time,
                    },
                    self.name,
                )
                if result == ValidationResult.PASSED:
                    result = ValidationResult.WARNING

            if execution_time > 0:
                execution_times.append(execution_time)

                if execution_time < self.precision_threshold:
                    report.add_issue(
                        "INFO",
                        f"Query {query_id} execution time ({execution_time}s) below precision threshold ({self.precision_threshold}s)",
                        {
                            "query_id": query_id,
                            "execution_time_seconds": execution_time,
                            "threshold": self.precision_threshold,
                        },
                        self.name,
                    )

        if len(execution_times) > 1:
            self._validate_timing_consistency(execution_times, report)

        return result

    def _validate_data_generation_timing(
        self, test_results: dict[str, Any], report: ValidationReport, result: ValidationResult
    ) -> ValidationResult:
        data_generation = test_results.get("data_generation", {})
        gen_time = data_generation.get("generation_time", 0)

        if gen_time > 0:
            tables_generated = len(data_generation.get("generated_tables", []))
            if tables_generated > 0:
                avg_time_per_table = gen_time / tables_generated
                if avg_time_per_table > 3600:
                    report.add_issue(
                        "WARNING",
                        f"Data generation time per table ({avg_time_per_table}s) seems excessive",
                        {
                            "generation_time": gen_time,
                            "tables_count": tables_generated,
                            "avg_per_table": avg_time_per_table,
                        },
                        self.name,
                    )
                    if result == ValidationResult.PASSED:
                        result = ValidationResult.WARNING

        return result

    def _validate_timing_consistency(self, execution_times: list[float], report: ValidationReport) -> None:
        if len(execution_times) < 2:
            return

        avg_time = mean(execution_times)
        if avg_time > 0:
            std_dev = stdev(execution_times)
            coefficient_of_variation = std_dev / avg_time

            if coefficient_of_variation > self.timing_consistency_threshold:
                report.add_issue(
                    "WARNING",
                    f"High timing variability detected (CV: {coefficient_of_variation:.3f})",
                    {
                        "execution_times": execution_times,
                        "average": avg_time,
                        "std_dev": std_dev,
                        "coefficient_of_variation": coefficient_of_variation,
                        "threshold": self.timing_consistency_threshold,
                    },
                    self.name,
                )


class DataIntegrityValidator(BaseValidator):
    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        super().__init__("data_integrity", config)
        self.integrity_checks = self.config.get("integrity_checks", [])
        self.referential_integrity_checks = self.config.get("referential_integrity", True)
        self.data_consistency_checks = self.config.get("data_consistency", True)

    def validate(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED

        maintenance_ops = test_results.get("maintenance_operations", {})
        if not maintenance_ops:
            return result

        for op_name, op_result in maintenance_ops.items():
            op_validation = self._validate_maintenance_operation(op_name, op_result)

            if not op_validation["valid"]:
                report.add_issue(
                    "ERROR",
                    f"Maintenance operation {op_name} failed integrity validation: {op_validation['error']}",
                    {"operation": op_name, "validation_result": op_validation},
                    self.name,
                )
                result = ValidationResult.FAILED

        if self.referential_integrity_checks:
            ref_integrity_result = self._validate_referential_integrity(test_results)
            if not ref_integrity_result["valid"]:
                report.add_issue(
                    "ERROR",
                    f"Referential integrity validation failed: {ref_integrity_result['error']}",
                    {"validation_result": ref_integrity_result},
                    self.name,
                )
                result = ValidationResult.FAILED

        if self.data_consistency_checks:
            consistency_result = self._validate_data_consistency(test_results)
            if not consistency_result["valid"]:
                report.add_issue(
                    "WARNING",
                    f"Data consistency validation failed: {consistency_result['error']}",
                    {"validation_result": consistency_result},
                    self.name,
                )
                if result == ValidationResult.PASSED:
                    result = ValidationResult.WARNING

        return result

    def _validate_maintenance_operation(self, op_name: str, op_result: dict[str, Any]) -> dict[str, Any]:
        status = op_result.get("status", "unknown")
        if status != "success":
            return {"valid": False, "error": f"Operation failed with status: {status}"}

        required_fields = ["start_time", "end_time", "records_affected"]
        for field_name in required_fields:
            if field_name not in op_result:
                return {"valid": False, "error": f"Missing required field: {field_name}"}

        try:
            start_time = datetime.fromisoformat(op_result["start_time"].replace("Z", "+00:00"))
            end_time = datetime.fromisoformat(op_result["end_time"].replace("Z", "+00:00"))

            if end_time <= start_time:
                return {"valid": False, "error": "End time must be after start time"}
        except ValueError as e:
            return {"valid": False, "error": f"Invalid timestamp format: {e}"}

        records_affected = op_result.get("records_affected", 0)
        if not isinstance(records_affected, int) or records_affected < 0:
            return {
                "valid": False,
                "error": "Records affected must be a non-negative integer",
            }

        return {"valid": True, "message": "Maintenance operation validation passed"}

    def _validate_referential_integrity(self, test_results: dict[str, Any]) -> dict[str, Any]:

        data_generation = test_results.get("data_generation", {})
        generated_tables = data_generation.get("generated_tables", [])

        if not generated_tables:
            return {"valid": False, "error": "No tables were generated"}

        return {"valid": True, "message": "Referential integrity validation passed"}

    def _validate_data_consistency(self, test_results: dict[str, Any]) -> dict[str, Any]:

        return {"valid": True, "message": "Data consistency validation passed"}


class MetricsValidator(BaseValidator):
    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        super().__init__("metrics", config)
        self.required_metrics = self.config.get("required_metrics", [])
        self.metric_ranges = self.config.get("metric_ranges", {})
        self.statistical_significance = self.config.get("statistical_significance", 0.05)

    def validate(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED
        metrics = test_results.get("metrics", {})

        for metric_name in self.required_metrics:
            if metric_name not in metrics:
                report.add_issue(
                    "ERROR",
                    f"Required metric missing: {metric_name}",
                    {
                        "metric_name": metric_name,
                        "available_metrics": list(metrics.keys()),
                    },
                    self.name,
                )
                result = ValidationResult.FAILED

        result = self._validate_metric_values(metrics, report, result)

        result = self._validate_derived_metrics(test_results, metrics, report, result)

        return result

    def _validate_metric_values(
        self, metrics: dict[str, Any], report: ValidationReport, result: ValidationResult
    ) -> ValidationResult:
        for metric_name, metric_value in metrics.items():
            if metric_name in self.metric_ranges:
                result = self._check_metric_range(metric_name, metric_value, report, result)

            if not isinstance(metric_value, (int, float)):
                report.add_issue(
                    "WARNING",
                    f"Metric {metric_name} has non-numeric value: {metric_value}",
                    {
                        "metric_name": metric_name,
                        "value": metric_value,
                        "type": type(metric_value).__name__,
                    },
                    self.name,
                )
                if result == ValidationResult.PASSED:
                    result = ValidationResult.WARNING
        return result

    def _check_metric_range(
        self, metric_name: str, metric_value: Any, report: ValidationReport, result: ValidationResult
    ) -> ValidationResult:
        range_config = self.metric_ranges[metric_name]
        min_val = range_config.get("min")
        max_val = range_config.get("max")

        if min_val is not None and metric_value < min_val:
            report.add_issue(
                "WARNING",
                f"Metric {metric_name} ({metric_value}) below minimum ({min_val})",
                {"metric_name": metric_name, "value": metric_value, "min": min_val},
                self.name,
            )
            if result == ValidationResult.PASSED:
                result = ValidationResult.WARNING

        if max_val is not None and metric_value > max_val:
            report.add_issue(
                "WARNING",
                f"Metric {metric_name} ({metric_value}) above maximum ({max_val})",
                {"metric_name": metric_name, "value": metric_value, "max": max_val},
                self.name,
            )
            if result == ValidationResult.PASSED:
                result = ValidationResult.WARNING

        return result

    def _validate_derived_metrics(
        self, test_results: dict[str, Any], metrics: dict[str, Any], report: ValidationReport, result: ValidationResult
    ) -> ValidationResult:
        calculated_metrics = self._calculate_derived_metrics(test_results)
        tolerance = 0.01
        for metric_name, calculated_value in calculated_metrics.items():
            reported_value = metrics.get(metric_name)
            if reported_value is not None:
                if abs(calculated_value - reported_value) > abs(calculated_value * tolerance):
                    report.add_issue(
                        "WARNING",
                        f"Metric {metric_name} calculation mismatch: reported={reported_value}, calculated={calculated_value}",
                        {
                            "metric_name": metric_name,
                            "reported_value": reported_value,
                            "calculated_value": calculated_value,
                            "tolerance": tolerance,
                        },
                        self.name,
                    )
                    if result == ValidationResult.PASSED:
                        result = ValidationResult.WARNING
        return result

    def _calculate_derived_metrics(self, test_results: dict[str, Any]) -> dict[str, float]:
        metrics = {}
        query_results = test_results.get("query_results", {})

        if query_results:
            execution_times = [qr.get("execution_time_seconds", 0) for qr in query_results.values()]
            execution_times = [t for t in execution_times if t > 0]

            if execution_times:
                metrics["avg_query_time"] = mean(execution_times)
                metrics["total_query_time"] = sum(execution_times)

                if len(execution_times) > 1:
                    metrics["query_time_std"] = stdev(execution_times)
                    metrics["query_time_median"] = median(execution_times)

        test_start = test_results.get("test_start_time")
        test_end = test_results.get("test_end_time")

        if test_start and test_end and query_results:
            try:
                start_time = datetime.fromisoformat(test_start.replace("Z", "+00:00"))
                end_time = datetime.fromisoformat(test_end.replace("Z", "+00:00"))
                total_time = (end_time - start_time).total_seconds()

                if total_time > 0:
                    metrics["queries_per_second"] = len(query_results) / total_time

                    total_rows = sum(qr.get("row_count", 0) for qr in query_results.values())
                    if total_rows > 0:
                        metrics["rows_per_second"] = total_rows / total_time

            except ValueError:
                pass

        return metrics


class ComplianceChecker(BaseValidator):
    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        super().__init__("compliance", config)
        self.benchmark_type = self.config.get("benchmark_type", "unknown")
        self.compliance_rules = self.config.get("compliance_rules", {})
        self.certification_level = self.config.get("certification_level", "standard")

    def validate(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED

        benchmark_name = test_results.get("benchmark_name", "unknown")

        if benchmark_name == "TPC-H":
            result = self._validate_tpch_compliance(test_results, report)
        elif benchmark_name == "TPC-DS":
            result = self._validate_tpcds_compliance(test_results, report)
        elif benchmark_name == "TPC-DI":
            result = self._validate_tpcdi_compliance(test_results, report)
        else:
            report.add_issue(
                "WARNING",
                f"No specific compliance rules for benchmark: {benchmark_name}",
                {"benchmark_name": benchmark_name},
                self.name,
            )
            if result == ValidationResult.PASSED:
                result = ValidationResult.WARNING

        general_compliance = self._validate_general_compliance(test_results, report)
        if general_compliance == ValidationResult.FAILED:
            result = ValidationResult.FAILED
        elif general_compliance == ValidationResult.WARNING and result == ValidationResult.PASSED:
            result = ValidationResult.WARNING

        return result

    def _validate_tpch_compliance(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED

        query_results = test_results.get("query_results", {})
        expected_queries = {str(i) for i in range(1, 23)}
        actual_queries = set(query_results.keys())

        missing_queries = expected_queries - actual_queries
        if missing_queries:
            report.add_issue(
                "ERROR",
                f"TPC-H requires all 22 queries. Missing: {sorted(missing_queries)}",
                {
                    "missing_queries": sorted(missing_queries),
                    "expected": 22,
                    "actual": len(actual_queries),
                },
                self.name,
            )
            result = ValidationResult.FAILED

        scale_factor = test_results.get("scale_factor", 1.0)
        valid_scale_factors = [1, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000]

        if scale_factor not in valid_scale_factors:
            report.add_issue(
                "WARNING",
                f"TPC-H scale factor {scale_factor} not in standard set: {valid_scale_factors}",
                {
                    "scale_factor": scale_factor,
                    "valid_scale_factors": valid_scale_factors,
                },
                self.name,
            )
            if result == ValidationResult.PASSED:
                result = ValidationResult.WARNING

        return result

    def _validate_tpcds_compliance(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED

        query_results = test_results.get("query_results", {})
        expected_queries = {str(i) for i in range(1, 100)}
        actual_queries = set(query_results.keys())

        missing_queries = expected_queries - actual_queries
        if missing_queries:
            report.add_issue(
                "ERROR",
                f"TPC-DS requires all 99 queries. Missing: {len(missing_queries)} queries",
                {
                    "missing_count": len(missing_queries),
                    "expected": 99,
                    "actual": len(actual_queries),
                },
                self.name,
            )
            result = ValidationResult.FAILED

        maintenance_ops = test_results.get("maintenance_operations", {})
        if not maintenance_ops:
            report.add_issue(
                "WARNING",
                "TPC-DS typically includes maintenance operations",
                {"maintenance_operations": list(maintenance_ops.keys())},
                self.name,
            )
            if result == ValidationResult.PASSED:
                result = ValidationResult.WARNING

        return result

    def _validate_tpcdi_compliance(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED

        etl_operations = test_results.get("etl_operations", {})
        if not etl_operations:
            report.add_issue(
                "ERROR",
                "TPC-DI requires ETL operations",
                {"etl_operations": list(etl_operations.keys())},
                self.name,
            )
            result = ValidationResult.FAILED

        data_quality = test_results.get("data_quality", {})
        if not data_quality:
            report.add_issue(
                "WARNING",
                "TPC-DI typically includes data quality validation",
                {"data_quality_operations": list(data_quality.keys())},
                self.name,
            )
            if result == ValidationResult.PASSED:
                result = ValidationResult.WARNING

        return result

    def _validate_general_compliance(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED

        test_isolation = test_results.get("test_isolation", {})
        if not test_isolation.get("isolated", False):
            report.add_issue(
                "WARNING",
                "TPC tests should be run in isolation",
                {"test_isolation": test_isolation},
                self.name,
            )
            if result == ValidationResult.PASSED:
                result = ValidationResult.WARNING

        reproducibility = test_results.get("reproducibility", {})
        required_repro_fields = ["seed", "timestamp", "environment"]
        missing_repro_fields = [field for field in required_repro_fields if field not in reproducibility]

        if missing_repro_fields:
            report.add_issue(
                "WARNING",
                f"Missing reproducibility information: {missing_repro_fields}",
                {
                    "missing_fields": missing_repro_fields,
                    "reproducibility": reproducibility,
                },
                self.name,
            )
            if result == ValidationResult.PASSED:
                result = ValidationResult.WARNING

        return result


class AuditTrail:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.start_time = datetime.now()
        self.environment_info = self._capture_environment()

    def log_event(
        self,
        event_type: str,
        description: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        event = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "description": description,
            "details": details or {},
        }
        self.events.append(event)

    def _capture_environment(self) -> dict[str, Any]:
        import platform
        import sys

        return {
            "platform": platform.platform(),
            "python_version": sys.version,
            "timestamp": datetime.now().isoformat(),
            "working_directory": str(Path.cwd()),
        }

    def get_audit_summary(self) -> dict[str, Any]:
        return {
            "start_time": self.start_time.isoformat(),
            "end_time": datetime.now().isoformat(),
            "total_events": len(self.events),
            "environment": self.environment_info,
            "events": self.events,
        }

    def generate_reproducibility_hash(self, test_results: dict[str, Any]) -> str:
        reproducible_data = {
            "benchmark_name": test_results.get("benchmark_name"),
            "scale_factor": test_results.get("scale_factor"),
            "queries_executed": sorted(test_results.get("query_results", {}).keys()),
            "environment": self.environment_info,
            "seed": test_results.get("reproducibility", {}).get("seed"),
        }

        data_str = json.dumps(reproducible_data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()


class CertificationChecker(BaseValidator):
    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        super().__init__("certification", config)
        self.certification_level = self.config.get("certification_level", "standard")
        self.required_documentation = self.config.get("required_documentation", [])
        self.performance_thresholds = self.config.get("performance_thresholds", {})

    def validate(self, test_results: dict[str, Any], report: ValidationReport) -> ValidationResult:
        result = ValidationResult.PASSED

        metrics = test_results.get("metrics", {})
        for metric_name, threshold in self.performance_thresholds.items():
            if metric_name in metrics:
                metric_value = metrics[metric_name]
                if metric_value > threshold:
                    report.add_issue(
                        "WARNING",
                        f"Performance metric {metric_name} ({metric_value}) exceeds validation threshold ({threshold})",
                        {
                            "metric_name": metric_name,
                            "value": metric_value,
                            "threshold": threshold,
                        },
                        self.name,
                    )
                    if result == ValidationResult.PASSED:
                        result = ValidationResult.WARNING

        documentation = test_results.get("documentation", {})
        for doc_type in self.required_documentation:
            if doc_type not in documentation or not documentation[doc_type]:
                report.add_issue(
                    "ERROR",
                    f"Required documentation missing: {doc_type}",
                    {
                        "doc_type": doc_type,
                        "available_docs": list(documentation.keys()),
                    },
                    self.name,
                )
                result = ValidationResult.FAILED

        certification_completeness = self._check_certification_completeness(test_results)
        if not certification_completeness["complete"]:
            report.add_issue(
                "ERROR",
                f"Completeness check failed: {certification_completeness['reason']}",
                {"completeness_check": certification_completeness},
                self.name,
            )
            result = ValidationResult.FAILED

        if result == ValidationResult.PASSED:
            report.certification_status = "READY"
        elif result == ValidationResult.WARNING:
            report.certification_status = "CONDITIONAL"
        else:
            report.certification_status = "NOT_READY"

        return result

    def _check_certification_completeness(self, test_results: dict[str, Any]) -> dict[str, Any]:
        checks = {
            "all_queries_passed": True,
            "timing_validation": True,
            "data_integrity": True,
            "reproducibility": True,
            "documentation": True,
        }

        query_results = test_results.get("query_results", {})
        for _query_id, query_result in query_results.items():
            if query_result.get("status") != "success":
                checks["all_queries_passed"] = False
                break

        reproducibility = test_results.get("reproducibility", {})
        if not reproducibility.get("seed") or not reproducibility.get("environment"):
            checks["reproducibility"] = False

        complete = all(checks.values())

        return {
            "complete": complete,
            "checks": checks,
            "reason": "All specification checks met" if complete else "Some specification checks not met",
        }


class TPCResultValidator:
    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.validators: list[BaseValidator] = []
        self.audit_trail = AuditTrail()
        self.logger = logging.getLogger("tpc_validation")

        self._initialize_validators()

    def _initialize_validators(self) -> None:
        validator_configs = self.config.get("validators", {})

        self.validators = [
            CompletenessValidator(validator_configs.get("completeness", {})),
            QueryResultValidator(validator_configs.get("query_result", {})),
            TimingValidator(validator_configs.get("timing", {})),
            DataIntegrityValidator(validator_configs.get("data_integrity", {})),
            MetricsValidator(validator_configs.get("metrics", {})),
            ComplianceChecker(validator_configs.get("compliance", {})),
            CertificationChecker(validator_configs.get("certification", {})),
        ]

    def validate(
        self,
        test_results: dict[str, Any],
        validation_level: ValidationLevel = ValidationLevel.STANDARD,
    ) -> ValidationReport:
        self.audit_trail.log_event("validation_start", "Starting TPC result validation")

        report = ValidationReport(
            benchmark_name=test_results.get("benchmark_name", "unknown"),
            scale_factor=test_results.get("scale_factor", 1.0),
            validation_level=validation_level,
        )

        for validator in self.validators:
            try:
                self.audit_trail.log_event("validator_start", f"Running validator: {validator.name}")

                validator_result = validator.validate(test_results, report)
                report.validator_results[validator.name] = validator_result

                self.audit_trail.log_event(
                    "validator_complete",
                    f"Validator {validator.name} completed with result: {validator_result.value}",
                )

            except Exception as e:
                self.logger.error(f"Validator {validator.name} failed: {e}")
                report.add_issue(
                    "ERROR",
                    f"Validator {validator.name} failed with exception: {str(e)}",
                    {"exception": str(e), "validator": validator.name},
                    validator.name,
                )
                report.validator_results[validator.name] = ValidationResult.FAILED

        self._generate_final_metrics(test_results, report)

        report.audit_trail = self.audit_trail.get_audit_summary()["events"]

        self.audit_trail.log_event("validation_complete", "TPC result validation completed")

        return report

    def _generate_final_metrics(self, test_results: dict[str, Any], report: ValidationReport) -> None:
        error_count = len(report.get_issues_by_level("ERROR"))
        warning_count = len(report.get_issues_by_level("WARNING"))
        info_count = len(report.get_issues_by_level("INFO"))

        passed_validators = sum(1 for result in report.validator_results.values() if result == ValidationResult.PASSED)
        total_validators = len(report.validator_results)

        validation_score = (passed_validators / total_validators) * 100 if total_validators > 0 else 0

        report.metrics = {
            "validation_score": validation_score,
            "error_count": error_count,
            "warning_count": warning_count,
            "info_count": info_count,
            "total_issues": len(report.issues),
            "passed_validators": passed_validators,
            "total_validators": total_validators,
            "reproducibility_hash": self.audit_trail.generate_reproducibility_hash(test_results),
        }

        query_results = test_results.get("query_results", {})
        successful_queries = sum(1 for qr in query_results.values() if qr.get("status") == "success")

        report.execution_summary = {
            "total_queries": len(query_results),
            "successful_queries": successful_queries,
            "failed_queries": len(query_results) - successful_queries,
            "success_rate": (successful_queries / len(query_results)) * 100 if query_results else 0,
            "benchmark_name": test_results.get("benchmark_name", "unknown"),
            "scale_factor": test_results.get("scale_factor", 1.0),
        }

    def save_report(self, report: ValidationReport, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2)

        self.logger.info(f"Validation report saved to: {output_path}")

    def load_report(self, input_path: Path) -> ValidationReport:
        with open(input_path, encoding="utf-8") as f:
            report_data = json.load(f)

        report = ValidationReport(
            validation_id=report_data["validation_id"],
            timestamp=datetime.fromisoformat(report_data["timestamp"]),
            benchmark_name=report_data["benchmark_name"],
            scale_factor=report_data["scale_factor"],
            validation_level=ValidationLevel(report_data["validation_level"]),
            overall_result=ValidationResult(report_data["overall_result"]),
            validator_results={k: ValidationResult(v) for k, v in report_data["validator_results"].items()},
            metrics=report_data["metrics"],
            execution_summary=report_data["execution_summary"],
            audit_trail=report_data["audit_trail"],
            certification_status=report_data.get("certification_status"),
        )

        for issue_data in report_data["issues"]:
            issue = ValidationIssue(
                level=issue_data["level"],
                message=issue_data["message"],
                details=issue_data.get("details"),
                timestamp=datetime.fromisoformat(issue_data["timestamp"]),
                validator_name=issue_data["validator_name"],
            )
            report.issues.append(issue)

        return report

    def create_default_config(self) -> dict[str, Any]:
        return {
            "validators": {
                "completeness": {
                    "required_queries": {
                        "TPC-H": list(range(1, 23)),
                        "TPC-DS": list(range(1, 100)),
                        "TPC-DI": ["etl_queries"],
                    },
                    "required_tables": [],
                    "required_maintenance_ops": [],
                },
                "query_result": {
                    "max_execution_time": 3600,
                    "min_row_count": 0,
                    "expected_schemas": {},
                },
                "timing": {
                    "precision_threshold": 0.001,
                    "max_total_time": 86400,
                    "min_query_time": 0.0,
                    "timing_consistency_threshold": 0.1,
                },
                "data_integrity": {
                    "integrity_checks": [],
                    "referential_integrity": True,
                    "data_consistency": True,
                },
                "metrics": {
                    "required_metrics": ["avg_query_time", "total_query_time"],
                    "metric_ranges": {"avg_query_time": {"min": 0.0, "max": 3600.0}},
                },
                "compliance": {
                    "benchmark_type": "TPC-H",
                    "certification_level": "standard",
                },
                "certification": {
                    "certification_level": "standard",
                    "required_documentation": ["test_report", "environment_spec"],
                    "performance_thresholds": {"avg_query_time": 300.0},
                },
            }
        }


def create_sample_test_results() -> dict[str, Any]:
    return {
        "benchmark_name": "TPC-H",
        "scale_factor": 1.0,
        "test_start_time": "2023-01-01T10:00:00Z",
        "test_end_time": "2023-01-01T11:00:00Z",
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
        },
        "data_generation": {
            "generation_time": 120.5,
            "generated_tables": [
                "customer",
                "orders",
                "lineitem",
                "part",
                "supplier",
                "partsupp",
                "nation",
                "region",
            ],
        },
        "metrics": {
            "avg_query_time": 4.15,
            "total_query_time": 8.3,
            "queries_per_second": 0.48,
        },
        "maintenance_operations": {},
        "reproducibility": {
            "seed": 12345,
            "environment": "test_env",
            "timestamp": "2023-01-01T10:00:00Z",
        },
        "test_isolation": {"isolated": True},
        "documentation": {
            "test_report": "path/to/test_report.pdf",
            "environment_spec": "path/to/env_spec.json",
        },
    }


if __name__ == "__main__":
    validator = TPCResultValidator()
    sample_results = create_sample_test_results()

    report = validator.validate(sample_results, ValidationLevel.CERTIFICATION)

    emit(f"Validation completed with result: {report.overall_result.value}")
    emit(f"Issues found: {len(report.issues)}")
    emit(f"Readiness status: {report.certification_status}")

    validator.save_report(report, Path("validation_report.json"))
