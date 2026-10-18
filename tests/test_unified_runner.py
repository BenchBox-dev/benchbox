#!/usr/bin/env python3
# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.medium


sys.path.insert(0, str(Path(__file__).parent / "utilities"))

from unified_test_runner import (
    Benchmark,
    DatabaseType,
    ExecutionStrategy,
    UnifiedRunnerConfig,
    UnifiedRunnerMode,
    UnifiedTestRunner,
    parse_args,
)


class TestUnifiedTestRunner(unittest.TestCase):
    def setUp(self):
        self.runner = UnifiedTestRunner()

    def test_benchmark_enum_values(self):
        expected_benchmarks = {
            "tpch",
            "tpcds",
            "tpcdi",
            "ssb",
            "amplab",
            "clickbench",
            "h2odb",
            "merge",
            "read_primitives",
            "tpchavoc",
            "all",
        }
        actual_benchmarks = {b.value for b in Benchmark}
        self.assertEqual(expected_benchmarks, actual_benchmarks)

    def test_test_mode_enum_values(self):
        expected_modes = {"unit", "integration", "performance", "specialized", "all"}
        actual_modes = {m.value for m in UnifiedRunnerMode}
        self.assertEqual(expected_modes, actual_modes)

    def test_execution_strategy_enum_values(self):
        expected_strategies = {"development", "ci", "integration", "custom"}
        actual_strategies = {s.value for s in ExecutionStrategy}
        self.assertEqual(expected_strategies, actual_strategies)

    def test_database_type_enum_values(self):
        expected_databases = {"duckdb", "sqlite", "both"}
        actual_databases = {d.value for d in DatabaseType}
        self.assertEqual(expected_databases, actual_databases)

    def test_get_test_files_for_benchmark(self):
        tpch_files = self.runner.get_test_files_for_benchmark(Benchmark.TPCH)
        self.assertIsInstance(tpch_files, list)

        all_files = self.runner.get_test_files_for_benchmark(Benchmark.ALL)
        self.assertIsInstance(all_files, list)

        if tpch_files and all_files:
            self.assertGreaterEqual(len(all_files), len(tpch_files))

    def test_get_test_files_for_mode(self):
        unit_files = self.runner.get_test_files_for_mode(UnifiedRunnerMode.UNIT)
        self.assertIsInstance(unit_files, list)

        all_files = self.runner.get_test_files_for_mode(UnifiedRunnerMode.ALL)
        self.assertIsInstance(all_files, list)

        if unit_files and all_files:
            self.assertGreaterEqual(len(all_files), len(unit_files))

    def test_build_pytest_command(self):
        config = UnifiedRunnerConfig(
            benchmarks=[Benchmark.TPCH],
            modes=[UnifiedRunnerMode.UNIT],
            strategy=ExecutionStrategy.CUSTOM,
            database_type=DatabaseType.DUCKDB,
            coverage=True,
            parallel=True,
            workers=2,
            output_format="term",
            output_location=None,
            verbose=True,
            fail_fast=True,
            timeout=300,
            markers=["quick"],
            exclude_markers=["slow"],
            include_patterns=[],
            exclude_patterns=[],
            min_coverage=80.0,
            benchmark_only=False,
            collect_only=False,
            dry_run=False,
        )

        test_files = [Path("test_example.py")]
        cmd = self.runner.build_pytest_command(
            test_files, config, Benchmark.TPCH, UnifiedRunnerMode.UNIT, DatabaseType.DUCKDB
        )

        self.assertIn("python", cmd)
        self.assertIn("-m", cmd)
        self.assertIn("pytest", cmd)
        self.assertIn("test_example.py", cmd)

        self.assertIn("--cov=benchbox", cmd)
        self.assertIn("--cov-fail-under=80.0", cmd)

        self.assertIn("-n", cmd)
        self.assertIn("2", cmd)

        self.assertIn("-v", cmd)

        self.assertIn("-x", cmd)

        self.assertIn("--timeout", cmd)
        self.assertIn("300", cmd)

    def test_parse_pytest_output(self):
        stdout = """
        ============================= test session starts ==============================
        platform darwin -- Python 3.9.0, pytest-6.2.0, py-1.10.0, pluggy-0.13.1
        rootdir: /path/to/project
        collected 15 items

        test_example.py::test_func1 PASSED                                      [ 20%]
        test_example.py::test_func2 PASSED                                      [ 40%]
        test_example.py::test_func3 FAILED                                      [ 60%]
        test_example.py::test_func4 SKIPPED                                     [ 80%]
        test_example.py::test_func5 PASSED                                      [100%]

        ========================= 3 passed, 1 failed, 1 skipped in 5.2s =========================
        """

        stderr = ""

        result = self.runner.parse_pytest_output(stdout, stderr)

        self.assertEqual(result.total_tests, 5)
        self.assertEqual(result.passed, 3)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.errors, 0)

    def test_extract_coverage(self):
        output_with_coverage = """
        Name                    Stmts   Miss  Cover
        -------------------------------------------
        benchbox/__init__.py       10      2    80%
        benchbox/core.py           50      5    90%
        -------------------------------------------
        TOTAL                      60      7    88%
        """

        coverage = self.runner.extract_coverage(output_with_coverage)
        self.assertEqual(coverage, 88.0)

        output_no_coverage = "No coverage data available"
        coverage = self.runner.extract_coverage(output_no_coverage)
        self.assertIsNone(coverage)

    @patch("sys.argv", ["unified_test_runner.py", "--help"])
    def test_parse_args_help(self):
        pass

    def test_parse_args_basic(self):
        test_args = [
            "unified_test_runner.py",
            "--benchmark",
            "tpch",
            "--mode",
            "unit",
            "--coverage",
            "--parallel",
            "--workers",
            "4",
        ]

        with patch("sys.argv", test_args):
            args = parse_args()

            self.assertEqual(args.benchmark, ["tpch"])
            self.assertEqual(args.mode, ["unit"])
            self.assertTrue(args.coverage)
            self.assertTrue(args.parallel)
            self.assertEqual(args.workers, 4)

    def test_configuration_creation(self):
        config = UnifiedRunnerConfig(
            benchmarks=[Benchmark.TPCH],
            modes=[UnifiedRunnerMode.UNIT],
            strategy=ExecutionStrategy.DEVELOPMENT,
            database_type=DatabaseType.DUCKDB,
            coverage=True,
            parallel=True,
            workers=2,
            output_format="term",
            output_location=None,
            verbose=True,
            fail_fast=True,
            timeout=300,
            markers=[],
            exclude_markers=[],
            include_patterns=[],
            exclude_patterns=[],
            min_coverage=None,
            benchmark_only=False,
            collect_only=False,
            dry_run=False,
        )

        self.assertEqual(config.benchmarks, [Benchmark.TPCH])
        self.assertEqual(config.modes, [UnifiedRunnerMode.UNIT])
        self.assertEqual(config.strategy, ExecutionStrategy.DEVELOPMENT)
        self.assertEqual(config.database_type, DatabaseType.DUCKDB)
        self.assertTrue(config.coverage)
        self.assertTrue(config.parallel)
        self.assertEqual(config.workers, 2)


if __name__ == "__main__":
    unittest.main()
