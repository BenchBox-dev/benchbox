#!/usr/bin/env python3
# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytestmark = pytest.mark.medium


sys.path.insert(0, str(Path(__file__).parent.parent))

sys.path.insert(0, str(Path(__file__).parent))

from utilities.benchmark_validator import (
    BenchmarkValidationReport,
    BenchmarkValidator,
    ValidationError,
    ValidationResult,
)


class TestBenchmarkValidator(unittest.TestCase):
    def setUp(self):
        self.validator = BenchmarkValidator(verbose=False)

    def test_validator_initialization(self):
        self.assertIsInstance(self.validator, BenchmarkValidator)
        self.assertFalse(self.validator.verbose)
        self.assertIsNotNone(self.validator.logger)

    def test_supported_benchmarks(self):
        expected_benchmarks = {
            "tpch",
            "tpcds",
            "read_primitives",
            "ssb",
            "amplab",
            "clickbench",
            "h2odb",
            "merge",
            "tpcdi",
        }

        actual_benchmarks = set(self.validator.SUPPORTED_BENCHMARKS.keys())
        self.assertEqual(actual_benchmarks, expected_benchmarks)

    def test_validation_report_creation(self):
        report = BenchmarkValidationReport(benchmark_name="test")

        self.assertEqual(report.benchmark_name, "test")
        self.assertEqual(report.total_checks, 0)
        self.assertEqual(report.passed_checks, 0)
        self.assertEqual(report.failed_checks, 0)
        self.assertEqual(report.warnings, 0)
        self.assertEqual(report.skipped_checks, 0)
        self.assertEqual(report.success_rate, 0.0)
        self.assertTrue(report.is_valid)

    def test_validation_report_metrics(self):
        report = BenchmarkValidationReport(benchmark_name="test")
        report.passed_checks = 8
        report.failed_checks = 2
        report.warnings = 1
        report.skipped_checks = 0
        report.total_checks = 10

        self.assertEqual(report.success_rate, 80.0)
        self.assertFalse(report.is_valid)

    def test_validation_error_creation(self):
        error = ValidationError(
            category="test",
            message="Test error message",
            severity="error",
            details="Test details",
        )

        self.assertEqual(error.category, "test")
        self.assertEqual(error.message, "Test error message")
        self.assertEqual(error.severity, "error")
        self.assertEqual(error.details, "Test details")

    def test_load_benchmark_class_invalid(self):
        result = self.validator._load_benchmark_class("invalid_benchmark")
        self.assertIsNone(result)

    def test_report_generation_text(self):
        report = BenchmarkValidationReport(benchmark_name="test")
        report.passed_checks = 5
        report.failed_checks = 1
        report.total_checks = 6
        report.execution_time = 1.5

        results = {"test": report}
        text_report = self.validator.generate_report(results, "text")

        self.assertIn("BENCHBOX VALIDATION REPORT", text_report)
        self.assertIn("BENCHMARK: TEST", text_report)
        self.assertIn("Success Rate: 83.3%", text_report)

    def test_report_generation_json(self):
        report = BenchmarkValidationReport(benchmark_name="test")
        report.passed_checks = 5
        report.failed_checks = 1
        report.total_checks = 6
        report.execution_time = 1.5

        results = {"test": report}
        json_report = self.validator.generate_report(results, "json")

        self.assertIn('"summary"', json_report)
        self.assertIn('"benchmarks"', json_report)
        self.assertIn('"test"', json_report)

    def test_report_generation_markdown(self):
        report = BenchmarkValidationReport(benchmark_name="test")
        report.passed_checks = 5
        report.failed_checks = 1
        report.total_checks = 6
        report.execution_time = 1.5

        results = {"test": report}
        markdown_report = self.validator.generate_report(results, "markdown")

        self.assertIn("# BenchBox Validation Report", markdown_report)
        self.assertIn("## Summary", markdown_report)
        self.assertIn("### ❌ TEST", markdown_report)

    @patch("utilities.benchmark_validator.BENCHBOX_AVAILABLE", False)
    def test_benchmark_validation_no_benchbox(self):
        validator = BenchmarkValidator(verbose=False)
        report = validator.validate_benchmark("tpch")

        self.assertFalse(report.is_valid)
        self.assertEqual(len(report.errors), 1)
        self.assertEqual(report.errors[0].category, "import")

    def test_mock_benchmark_validation(self):
        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {"table1": "CREATE TABLE table1 (id INT)"}
        mock_benchmark.get_queries.return_value = {"q1": "SELECT * FROM table1"}
        mock_benchmark.generate_data.return_value = {"table1": "data"}
        mock_benchmark.name = "mock_benchmark"
        mock_benchmark.scale_factor = 0.01

        report = BenchmarkValidationReport(benchmark_name="mock")
        self.validator._validate_benchmark_schema(mock_benchmark, report)

        self.assertTrue(report.passed_checks > 0)
        self.assertEqual(report.failed_checks, 0)

    def test_mock_query_validation(self):
        mock_benchmark = Mock()
        mock_benchmark.get_queries.return_value = {
            "q1": "SELECT 1",
            "q2": "SELECT 2",
        }

        report = BenchmarkValidationReport(benchmark_name="mock")
        self.validator._validate_benchmark_queries(mock_benchmark, report, quick_check=True)

        self.assertTrue(report.passed_checks > 0)

    def test_interface_validation(self):
        mock_benchmark = Mock()
        mock_benchmark.name = "test"
        mock_benchmark.scale_factor = 1.0
        mock_benchmark.get_queries = Mock(return_value={})
        mock_benchmark.get_schema = Mock(return_value={})
        mock_benchmark.generate_data = Mock(return_value={})

        report = BenchmarkValidationReport(benchmark_name="mock")
        self.validator._validate_benchmark_interface(mock_benchmark, report)

        self.assertTrue(report.passed_checks > 0)
        self.assertEqual(report.failed_checks, 0)

    def test_validation_result_enum(self):
        self.assertEqual(ValidationResult.PASS.value, "PASS")
        self.assertEqual(ValidationResult.FAIL.value, "FAIL")
        self.assertEqual(ValidationResult.SKIP.value, "SKIP")
        self.assertEqual(ValidationResult.WARN.value, "WARN")


class TestBenchmarkValidatorIntegration(unittest.TestCase):
    def setUp(self):
        self.validator = BenchmarkValidator(verbose=False)

    @patch("utilities.benchmark_validator.BENCHBOX_AVAILABLE", True)
    @patch("utilities.benchmark_validator.benchbox")
    def test_validate_all_benchmarks_mock(self, mock_benchbox):
        mock_tpch = Mock()
        mock_tpch.return_value.get_schema.return_value = {"table1": "schema"}
        mock_tpch.return_value.get_queries.return_value = {"q1": "SELECT 1"}
        mock_tpch.return_value.generate_data.return_value = {"table1": "data"}
        mock_tpch.return_value.name = "tpch"
        mock_tpch.return_value.scale_factor = 0.01

        mock_benchbox.TPCH = mock_tpch

        results = self.validator.validate_all_benchmarks(benchmarks=["tpch"], quick_check=True)

        self.assertEqual(len(results), 1)
        self.assertIn("tpch", results)
        self.assertIsInstance(results["tpch"], BenchmarkValidationReport)

    def test_generate_comprehensive_report(self):
        report1 = BenchmarkValidationReport(benchmark_name="tpch")
        report1.passed_checks = 10
        report1.failed_checks = 0
        report1.total_checks = 10
        report1.execution_time = 2.5

        report2 = BenchmarkValidationReport(benchmark_name="tpcds")
        report2.passed_checks = 8
        report2.failed_checks = 2
        report2.total_checks = 10
        report2.execution_time = 3.2

        results = {"tpch": report1, "tpcds": report2}

        text_report = self.validator.generate_report(results, "text")
        json_report = self.validator.generate_report(results, "json")
        markdown_report = self.validator.generate_report(results, "markdown")

        self.assertIn("Total Benchmarks: 2", text_report)
        self.assertIn("Valid Benchmarks: 1", text_report)

        self.assertIn('"total_benchmarks": 2', json_report)
        self.assertIn('"valid_benchmarks": 1', json_report)

        self.assertIn("**Total Benchmarks**: 2", markdown_report)
        self.assertIn("**Valid Benchmarks**: 1", markdown_report)


if __name__ == "__main__":
    unittest.main()


