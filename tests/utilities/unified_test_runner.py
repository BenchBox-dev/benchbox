#!/usr/bin/env python3
# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import argparse
import contextlib
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class Benchmark(Enum):
    TPCH = "tpch"
    TPCDS = "tpcds"
    TPCDI = "tpcdi"
    SSB = "ssb"
    AMPLAB = "amplab"
    CLICKBENCH = "clickbench"
    H2ODB = "h2odb"
    MERGE = "merge"
    PRIMITIVES = "read_primitives"
    TPCHAVOC = "tpchavoc"
    ALL = "all"


class UnifiedRunnerMode(Enum):
    UNIT = "unit"
    INTEGRATION = "integration"
    PERFORMANCE = "performance"
    SPECIALIZED = "specialized"
    ALL = "all"


class ExecutionStrategy(Enum):
    DEVELOPMENT = "development"
    CI = "ci"
    INTEGRATION = "integration"
    CUSTOM = "custom"


class DatabaseType(Enum):
    DUCKDB = "duckdb"
    SQLITE = "sqlite"
    BOTH = "both"


@dataclass
class UnifiedRunnerConfig:
    benchmarks: list[Benchmark]
    modes: list[UnifiedRunnerMode]
    strategy: ExecutionStrategy
    database_type: DatabaseType
    coverage: bool
    parallel: bool
    workers: int
    output_format: str
    output_location: Optional[str]
    verbose: bool
    fail_fast: bool
    timeout: Optional[int]
    markers: list[str]
    exclude_markers: list[str]
    include_patterns: list[str]
    exclude_patterns: list[str]
    min_coverage: Optional[float]
    benchmark_only: bool
    collect_only: bool
    dry_run: bool


@dataclass
class TestResult:
    benchmark: str
    mode: str
    database: str
    total_tests: int
    passed: int
    failed: int
    skipped: int
    errors: int
    warnings: int
    duration: float
    coverage: Optional[float] = None
    exit_code: int = 0
    output: str = ""
    command: str = ""


class UnifiedTestRunner:
    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or PROJECT_ROOT
        self.tests_dir = self.project_root / "tests"
        self.results: list[TestResult] = []
        self.start_time = time.time()

    def get_test_files_for_benchmark(self, benchmark: Benchmark) -> list[Path]:
        test_files = []

        if benchmark == Benchmark.ALL:
            test_files.extend(self.tests_dir.glob("test_*.py"))
            test_files.extend(self.tests_dir.glob("**/test_*.py"))
        else:
            benchmark_name = benchmark.value
            patterns = [
                f"test_{benchmark_name}.py",
                f"test_{benchmark_name}_*.py",
                f"**/test_{benchmark_name}.py",
                f"**/test_{benchmark_name}_*.py",
            ]

            for pattern in patterns:
                test_files.extend(self.tests_dir.glob(pattern))

        return sorted(set(test_files))

    def get_test_files_for_mode(self, mode: UnifiedRunnerMode) -> list[Path]:
        test_files = []

        if mode == UnifiedRunnerMode.ALL:
            test_files.extend(self.tests_dir.glob("**/test_*.py"))
        elif mode == UnifiedRunnerMode.UNIT:
            test_files.extend(self.tests_dir.glob("unit/**/test_*.py"))
            test_files.extend(self.tests_dir.glob("test_*.py"))
        elif mode == UnifiedRunnerMode.INTEGRATION:
            test_files.extend(self.tests_dir.glob("integration/**/test_*.py"))
        elif mode == UnifiedRunnerMode.PERFORMANCE:
            test_files.extend(self.tests_dir.glob("performance/**/test_*.py"))
        elif mode == UnifiedRunnerMode.SPECIALIZED:
            test_files.extend(self.tests_dir.glob("specialized/**/test_*.py"))

        return sorted(set(test_files))

    def build_pytest_command(
        self,
        test_files: list[Path],
        config: UnifiedRunnerConfig,
        benchmark: Benchmark,
        mode: UnifiedRunnerMode,
        database: DatabaseType,
    ) -> list[str]:
        cmd = ["python", "-m", "pytest"]

        if test_files:
            cmd.extend([str(f) for f in test_files])

        cmd.append("-v" if config.verbose else "-q")

        if config.fail_fast:
            cmd.append("-x")

        if config.parallel and config.workers > 1:
            cmd.extend(["-n", str(config.workers)])

        cmd.extend(self._coverage_args(config))
        cmd.extend(self._report_args(config))

        if config.timeout:
            cmd.extend(["--timeout", str(config.timeout)])

        cmd.extend(self._marker_args(config, benchmark, mode, database))

        if config.benchmark_only:
            cmd.append("--benchmark-only")

        if config.collect_only:
            cmd.append("--collect-only")

        cmd.extend(["--tb=short", "--strict-markers", "--disable-warnings"])

        return cmd

    @staticmethod
    def _coverage_args(config: UnifiedRunnerConfig) -> list[str]:
        if not config.coverage:
            return []
        args = [
            "--cov=benchbox",
            "--cov-report=term-missing",
            "--cov-report=html",
            "--cov-report=xml",
        ]
        if config.min_coverage:
            args.append(f"--cov-fail-under={config.min_coverage}")
        return args

    @staticmethod
    def _report_args(config: UnifiedRunnerConfig) -> list[str]:
        if config.output_format == "json":
            args = ["--json-report"]
            if config.output_location:
                args.append(f"--json-report-file={config.output_location}")
            return args
        if config.output_format == "junit":
            output_file = config.output_location or "test_results.xml"
            return [f"--junit-xml={output_file}"]
        return []

    @staticmethod
    def _marker_args(
        config: UnifiedRunnerConfig,
        benchmark: Benchmark,
        mode: UnifiedRunnerMode,
        database: DatabaseType,
    ) -> list[str]:
        markers = config.markers.copy()

        if benchmark != Benchmark.ALL:
            markers.append(benchmark.value)

        if mode != UnifiedRunnerMode.ALL:
            markers.append(mode.value)

        if database == DatabaseType.DUCKDB:
            markers.append("duckdb")
            config.exclude_markers.append("sqlite")
        elif database == DatabaseType.SQLITE:
            markers.append("sqlite")
            config.exclude_markers.append("duckdb")

        marker_expr = []
        if markers:
            marker_expr.append("(" + " or ".join(markers) + ")")

        if config.exclude_markers:
            exclude_expr = " and ".join(f"not {marker}" for marker in config.exclude_markers)
            marker_expr.append(f"({exclude_expr})")

        if marker_expr:
            return ["-m", " and ".join(marker_expr)]
        return []

    def execute_test_suite(
        self,
        config: UnifiedRunnerConfig,
        benchmark: Benchmark,
        mode: UnifiedRunnerMode,
        database: DatabaseType,
    ) -> TestResult:
        print(f"\n=== Running {benchmark.value.upper()} {mode.value} tests ({database.value}) ===")

        benchmark_files = self.get_test_files_for_benchmark(benchmark)
        mode_files = self.get_test_files_for_mode(mode)

        test_files = list(set(benchmark_files) & set(mode_files))

        if not test_files:
            print(f"No test files found for {benchmark.value} {mode.value}")
            return TestResult(
                benchmark=benchmark.value,
                mode=mode.value,
                database=database.value,
                total_tests=0,
                passed=0,
                failed=0,
                skipped=0,
                errors=0,
                warnings=0,
                duration=0.0,
            )

        print(f"Found {len(test_files)} test files")

        cmd = self.build_pytest_command(test_files, config, benchmark, mode, database)

        if config.dry_run:
            print(f"DRY RUN - Command: {' '.join(cmd)}")
            return TestResult(
                benchmark=benchmark.value,
                mode=mode.value,
                database=database.value,
                total_tests=0,
                passed=0,
                failed=0,
                skipped=0,
                errors=0,
                warnings=0,
                duration=0.0,
                command=" ".join(cmd),
            )

        start_time = time.time()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.project_root,
                timeout=config.timeout,
            )

            print(result.stdout)
            print(result.stderr)

            duration = time.time() - start_time

            test_result = self.parse_pytest_output(result.stdout, result.stderr)
            test_result.benchmark = benchmark.value
            test_result.mode = mode.value
            test_result.database = database.value
            test_result.duration = duration
            test_result.exit_code = result.returncode
            test_result.output = result.stdout + result.stderr
            test_result.command = " ".join(cmd)

            if config.coverage:
                test_result.coverage = self.extract_coverage(result.stdout)

            return test_result

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return TestResult(
                benchmark=benchmark.value,
                mode=mode.value,
                database=database.value,
                total_tests=0,
                passed=0,
                failed=0,
                skipped=0,
                errors=1,
                warnings=0,
                duration=duration,
                exit_code=124,
                output=f"Test execution timed out after {config.timeout} seconds",
            )

        except Exception as e:
            duration = time.time() - start_time
            return TestResult(
                benchmark=benchmark.value,
                mode=mode.value,
                database=database.value,
                total_tests=0,
                passed=0,
                failed=0,
                skipped=0,
                errors=1,
                warnings=0,
                duration=duration,
                exit_code=1,
                output=f"Test execution failed: {str(e)}",
            )

    def parse_pytest_output(self, stdout: str, stderr: str) -> TestResult:
        output = stdout + stderr

        total_tests = 0
        passed = 0
        failed = 0
        skipped = 0
        errors = 0
        warnings = 0

        lines = output.split("\n")
        for line in lines:
            if "passed" in line and ("failed" in line or "error" in line or "skipped" in line):
                parts = line.split()
                for i, part in enumerate(parts):
                    if part in ["passed", "passed,"] and i > 0:
                        with contextlib.suppress(ValueError):
                            passed = int(parts[i - 1])
                    elif part in ["failed", "failed,"] and i > 0:
                        with contextlib.suppress(ValueError):
                            failed = int(parts[i - 1])
                    elif part in ["skipped", "skipped,"] and i > 0:
                        with contextlib.suppress(ValueError):
                            skipped = int(parts[i - 1])
                    elif part in ["error", "error,", "errors", "errors,"] and i > 0:
                        with contextlib.suppress(ValueError):
                            errors = int(parts[i - 1])
                break

        warnings = output.count("warning")

        total_tests = passed + failed + skipped + errors

        return TestResult(
            benchmark="",
            mode="",
            database="",
            total_tests=total_tests,
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            warnings=warnings,
            duration=0.0,
        )

    def extract_coverage(self, output: str) -> Optional[float]:
        lines = output.split("\n")
        for line in lines:
            if "TOTAL" in line and "%" in line:
                try:
                    parts = line.split()
                    for part in parts:
                        if part.endswith("%"):
                            return float(part[:-1])
                except ValueError:
                    pass
        return None

    def run_development_strategy(self, config: UnifiedRunnerConfig) -> list[TestResult]:
        results = []

        for benchmark in config.benchmarks:
            if benchmark == Benchmark.ALL:
                continue

            result = self.execute_test_suite(config, benchmark, UnifiedRunnerMode.UNIT, config.database_type)
            results.append(result)

            if config.fail_fast and result.failed > 0:
                break

        return results

    def run_ci_strategy(self, config: UnifiedRunnerConfig) -> list[TestResult]:
        results = []

        print("=== CI Strategy: Running fast tests first ===")
        config.exclude_markers.append("slow")

        for benchmark in config.benchmarks:
            if benchmark == Benchmark.ALL:
                all_benchmarks = [b for b in Benchmark if b != Benchmark.ALL]
                for bench in all_benchmarks:
                    for mode in config.modes:
                        if mode == UnifiedRunnerMode.ALL:
                            continue
                        result = self.execute_test_suite(config, bench, mode, config.database_type)
                        results.append(result)
            else:
                for mode in config.modes:
                    if mode == UnifiedRunnerMode.ALL:
                        continue
                    result = self.execute_test_suite(config, benchmark, mode, config.database_type)
                    results.append(result)

        if all(r.failed == 0 for r in results):
            print("=== CI Strategy: Running slow tests ===")
            config.exclude_markers.remove("slow")
            config.markers.append("slow")

            for benchmark in config.benchmarks:
                if benchmark == Benchmark.ALL:
                    continue
                result = self.execute_test_suite(config, benchmark, UnifiedRunnerMode.PERFORMANCE, config.database_type)
                results.append(result)

        return results

    def run_integration_strategy(self, config: UnifiedRunnerConfig) -> list[TestResult]:
        results = []

        config.database_type = DatabaseType.DUCKDB
        config.markers.append("integration")

        for benchmark in config.benchmarks:
            if benchmark == Benchmark.ALL:
                continue
            result = self.execute_test_suite(config, benchmark, UnifiedRunnerMode.INTEGRATION, DatabaseType.DUCKDB)
            results.append(result)

        return results

    def run_custom_strategy(self, config: UnifiedRunnerConfig) -> list[TestResult]:
        results = []

        databases = []
        if config.database_type == DatabaseType.BOTH:
            databases = [DatabaseType.DUCKDB, DatabaseType.SQLITE]
        else:
            databases = [config.database_type]

        for database in databases:
            for benchmark in config.benchmarks:
                if benchmark == Benchmark.ALL:
                    all_benchmarks = [b for b in Benchmark if b != Benchmark.ALL]
                    for bench in all_benchmarks:
                        for mode in config.modes:
                            if mode == UnifiedRunnerMode.ALL:
                                all_modes = [m for m in UnifiedRunnerMode if m != UnifiedRunnerMode.ALL]
                                for test_mode in all_modes:
                                    result = self.execute_test_suite(config, bench, test_mode, database)
                                    results.append(result)
                            else:
                                result = self.execute_test_suite(config, bench, mode, database)
                                results.append(result)
                else:
                    for mode in config.modes:
                        if mode == UnifiedRunnerMode.ALL:
                            all_modes = [m for m in UnifiedRunnerMode if m != UnifiedRunnerMode.ALL]
                            for test_mode in all_modes:
                                result = self.execute_test_suite(config, benchmark, test_mode, database)
                                results.append(result)
                        else:
                            result = self.execute_test_suite(config, benchmark, mode, database)
                            results.append(result)

        return results

    def run(self, config: UnifiedRunnerConfig) -> list[TestResult]:
        print("BenchBox Unified Test Runner")
        print(f"Strategy: {config.strategy.value}")
        print(f"Benchmarks: {[b.value for b in config.benchmarks]}")
        print(f"Modes: {[m.value for m in config.modes]}")
        print(f"Database: {config.database_type.value}")
        print(f"Coverage: {config.coverage}")
        print(f"Parallel: {config.parallel} (workers: {config.workers})")
        print("=" * 60)

        if config.strategy == ExecutionStrategy.DEVELOPMENT:
            results = self.run_development_strategy(config)
        elif config.strategy == ExecutionStrategy.CI:
            results = self.run_ci_strategy(config)
        elif config.strategy == ExecutionStrategy.INTEGRATION:
            results = self.run_integration_strategy(config)
        else:
            results = self.run_custom_strategy(config)

        self.results = results
        return results

    def generate_report(self, results: list[TestResult]) -> str:
        total_duration = time.time() - self.start_time

        total_tests = sum(r.total_tests for r in results)
        total_passed = sum(r.passed for r in results)
        total_failed = sum(r.failed for r in results)
        total_skipped = sum(r.skipped for r in results)
        total_errors = sum(r.errors for r in results)
        total_warnings = sum(r.warnings for r in results)

        coverage_results = [r for r in results if r.coverage is not None]
        avg_coverage = sum(r.coverage for r in coverage_results) / len(coverage_results) if coverage_results else None

        report = []
        report.append("# BenchBox Test Execution Report")
        report.append(f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"Total execution time: {total_duration:.2f}s")
        report.append("")

        report.append("## Summary")
        report.append(f"- Total tests: {total_tests}")
        report.append(f"- Passed: {total_passed}")
        report.append(f"- Failed: {total_failed}")
        report.append(f"- Skipped: {total_skipped}")
        report.append(f"- Errors: {total_errors}")
        report.append(f"- Warnings: {total_warnings}")
        if avg_coverage:
            report.append(f"- Average coverage: {avg_coverage:.1f}%")

        success_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0
        report.append(f"- Success rate: {success_rate:.1f}%")
        report.append("")

        report.append("## Detailed Results")
        for result in results:
            status = "✅ PASS" if result.failed == 0 and result.errors == 0 else "❌ FAIL"
            report.append(f"### {result.benchmark.upper()} {result.mode} ({result.database}) - {status}")
            report.append(f"- Duration: {result.duration:.2f}s")
            report.append(
                f"- Tests: {result.total_tests} total, {result.passed} passed, {result.failed} failed, {result.skipped} skipped"
            )
            if result.errors > 0:
                report.append(f"- Errors: {result.errors}")
            if result.warnings > 0:
                report.append(f"- Warnings: {result.warnings}")
            if result.coverage:
                report.append(f"- Coverage: {result.coverage:.1f}%")
            if result.exit_code != 0:
                report.append(f"- Exit code: {result.exit_code}")
            report.append("")

        failed_results = [r for r in results if r.failed > 0 or r.errors > 0]
        if failed_results:
            report.append("## Failed Tests")
            for result in failed_results:
                report.append(f"### {result.benchmark.upper()} {result.mode} ({result.database})")
                report.append(f"Command: {result.command}")
                report.append("```")
                report.append(result.output[-1000:] if len(result.output) > 1000 else result.output)
                report.append("```")
                report.append("")

        return "\n".join(report)

    def save_report(self, report: str, filename: Optional[str] = None) -> Path:
        if not filename:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"test_report_{timestamp}.md"

        report_path = self.project_root / filename
        report_path.write_text(report)
        return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Test Runner for BenchBox",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all TPCH unit tests
  python unified_test_runner.py --benchmark tpch --mode unit

  # Run integration tests with coverage
  python unified_test_runner.py --mode integration --coverage

  # Run CI optimized tests
  python unified_test_runner.py --strategy ci --parallel

  # Run development tests for quick feedback
  python unified_test_runner.py --strategy development --fail-fast

  # Run specific benchmarks with DuckDB
  python unified_test_runner.py --benchmark tpch tpcds --duckdb --parallel
        """,
    )

    parser.add_argument(
        "--benchmark",
        nargs="+",
        choices=[b.value for b in Benchmark],
        default=["all"],
        help="Benchmarks to test",
    )

    parser.add_argument(
        "--mode",
        nargs="+",
        choices=[m.value for m in UnifiedRunnerMode],
        default=["unit"],
        help="Test modes to run",
    )

    parser.add_argument(
        "--strategy",
        choices=[s.value for s in ExecutionStrategy],
        default=ExecutionStrategy.CUSTOM.value,
        help="Test execution strategy",
    )

    db_group = parser.add_mutually_exclusive_group()
    db_group.add_argument("--duckdb", action="store_true", help="Run DuckDB tests only")
    db_group.add_argument("--sqlite", action="store_true", help="Run SQLite tests only")

    parser.add_argument("--coverage", action="store_true", help="Enable coverage reporting")
    parser.add_argument("--min-coverage", type=float, help="Minimum coverage percentage")

    parser.add_argument("--parallel", action="store_true", help="Enable parallel execution")
    parser.add_argument("--workers", type=int, default=2, help="Number of parallel workers")

    parser.add_argument(
        "--output",
        choices=["term", "json", "junit"],
        default="term",
        help="Output format",
    )
    parser.add_argument("--output-location", help="Output file location")

    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--fail-fast", "-x", action="store_true", help="Stop on first failure")
    parser.add_argument("--timeout", type=int, help="Test timeout in seconds")

    parser.add_argument("--markers", nargs="+", default=[], help="Include tests with these markers")
    parser.add_argument(
        "--exclude-markers",
        nargs="+",
        default=[],
        help="Exclude tests with these markers",
    )

    parser.add_argument("--benchmark-only", action="store_true", help="Run benchmark tests only")
    parser.add_argument("--collect-only", action="store_true", help="Collect tests only, don't run")
    parser.add_argument("--dry-run", action="store_true", help="Show commands without executing")

    parser.add_argument("--report", action="store_true", help="Generate detailed report")
    parser.add_argument("--report-file", help="Report file name")

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    benchmarks = [Benchmark(b) for b in args.benchmark]
    modes = [UnifiedRunnerMode(m) for m in args.mode]
    strategy = ExecutionStrategy(args.strategy)

    if args.duckdb:
        database_type = DatabaseType.DUCKDB
    elif args.sqlite:
        database_type = DatabaseType.SQLITE
    else:
        database_type = DatabaseType.BOTH

    config = UnifiedRunnerConfig(
        benchmarks=benchmarks,
        modes=modes,
        strategy=strategy,
        database_type=database_type,
        coverage=args.coverage,
        parallel=args.parallel,
        workers=args.workers,
        output_format=args.output,
        output_location=args.output_location,
        verbose=args.verbose,
        fail_fast=args.fail_fast,
        timeout=args.timeout,
        markers=args.markers,
        exclude_markers=args.exclude_markers,
        include_patterns=[],
        exclude_patterns=[],
        min_coverage=args.min_coverage,
        benchmark_only=args.benchmark_only,
        collect_only=args.collect_only,
        dry_run=args.dry_run,
    )

    runner = UnifiedTestRunner()
    results = runner.run(config)

    if args.report or args.report_file:
        report = runner.generate_report(results)
        if args.report_file:
            report_path = runner.save_report(report, args.report_file)
            print(f"\nReport saved to: {report_path}")
        else:
            print("\n" + report)

    total_failed = sum(r.failed + r.errors for r in results)
    total_tests = sum(r.total_tests for r in results)
    total_passed = sum(r.passed for r in results)

    print(f"\n{'=' * 60}")
    print("TEST SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total tests: {total_tests}")
    print(f"Passed: {total_passed}")
    print(f"Failed: {total_failed}")

    if total_failed == 0:
        print("All tests passed!")
        return 0
    else:
        print(f"❌ {total_failed} tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
