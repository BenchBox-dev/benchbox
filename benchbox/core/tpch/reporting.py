import csv
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional, Union

from benchbox.core.tpc_disclaimer import TPCH_DERIVED_DISCLAIMER
from benchbox.core.tpch.official_benchmark import TPCHOfficialBenchmarkResult


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return dict(vars(value)) if value is not None else {}


def _query_times(power: dict[str, Any]) -> dict[int, float]:
    times: dict[int, float] = {}
    for query in power.get("query_results") or []:
        match = re.search(r"\d+", str(query.get("query_id", "")))
        seconds = query.get("execution_time_seconds")
        if match and query.get("success", True) and isinstance(seconds, (int, float)):
            times[int(match.group())] = float(seconds)
    return times


def _report_view(result: Any) -> Any:
    if hasattr(result, "power_test"):
        return result
    power = _as_mapping(result.power_test_result)
    throughput = _as_mapping(result.throughput_test_result)
    return SimpleNamespace(
        success=result.success,
        scale_factor=result.config.scale_factor,
        total_benchmark_time=result.total_time,
        power_test=SimpleNamespace(
            total_time=power.get("total_time", 0.0),
            power_at_size=result.power_at_size,
            success=bool(power.get("success", result.power_at_size > 0)),
            query_times=_query_times(power),
        ),
        throughput_test=SimpleNamespace(
            total_time=throughput.get("total_time", 0.0),
            throughput_at_size=result.throughput_at_size,
            num_streams=throughput.get("streams_executed") or result.config.num_streams,
            success=bool(throughput.get("success", result.throughput_at_size > 0)),
        ),
    )


@dataclass
class PerformanceMetrics:
    power_at_size: float
    throughput_at_size: float
    total_execution_time: float
    average_query_time: float
    median_query_time: float
    query_time_std_dev: float
    scale_factor: float


@dataclass
class ValidationResult:
    checks_passed: bool
    no_warnings: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@dataclass
class ComparisonResult:
    baseline_power_at_size: float
    current_power_at_size: float
    performance_change: float
    relative_change: float
    significant_change: bool
    query_level_changes: dict[int, float] = field(default_factory=dict)


class TPCHReportGenerator:
    def __init__(self, output_dir: Optional[Union[str, Path]] = None) -> None:
        self.output_dir = Path(output_dir) if output_dir else Path.cwd()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_detailed_report(
        self,
        result: TPCHOfficialBenchmarkResult,
        report_title: str = "TPC-H Benchmark Report",
        include_detailed_analysis: bool = True,
        include_validation_info: bool = True,
    ) -> Path:
        result = _report_view(result)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = self.output_dir / f"tpch_comprehensive_report_{timestamp}.html"

        metrics = self._calculate_performance_metrics(result)

        validation = self._validate_result(result)

        html_content = self._generate_html_report(
            result,
            metrics,
            validation,
            report_title,
            include_detailed_analysis,
            include_validation_info,
        )

        with open(report_file, "w", encoding="utf-8") as f:
            f.write(html_content)

        return report_file

    def generate_validation_report(self, result: TPCHOfficialBenchmarkResult) -> Path:
        result = _report_view(result)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = self.output_dir / f"tpch_validation_report_{timestamp}.txt"

        validation = self._validate_result(result)
        metrics = self._calculate_performance_metrics(result)

        with open(report_file, "w", encoding="utf-8") as f:
            f.write("TPC-H DERIVED RESULTS VALIDATION REPORT\n")
            f.write("=" * 60 + "\n")
            f.write(f"{TPCH_DERIVED_DISCLAIMER}\n\n")

            f.write("EXECUTIVE SUMMARY\n")
            f.write("-" * 20 + "\n")
            f.write(f"Scale Factor: {result.scale_factor}\n")
            f.write(f"Validation Checks Passed: {'YES' if validation.checks_passed else 'NO'}\n")
            f.write(f"No Validation Warnings: {'YES' if validation.no_warnings else 'NO'}\n\n")

            f.write("TEST RESULTS\n")
            f.write("-" * 15 + "\n")
            f.write("Power Test:\n")
            f.write(f"  Execution Time: {result.power_test.total_time:.2f} seconds\n")
            f.write(f"  Power@Size: {result.power_test.power_at_size:.2f}\n")
            f.write(f"  Success: {'YES' if result.power_test.success else 'NO'}\n\n")

            f.write("Throughput Test:\n")
            f.write(f"  Execution Time: {result.throughput_test.total_time:.2f} seconds\n")
            f.write(f"  Throughput@Size: {result.throughput_test.throughput_at_size:.2f}\n")
            f.write(f"  Number of Streams: {result.throughput_test.num_streams}\n")
            f.write(f"  Success: {'YES' if result.throughput_test.success else 'NO'}\n\n")

            f.write("DETAILED METRICS\n")
            f.write("-" * 18 + "\n")
            f.write(f"Average Query Time: {metrics.average_query_time:.3f} seconds\n")
            f.write(f"Median Query Time: {metrics.median_query_time:.3f} seconds\n")
            f.write(f"Query Time Std Dev: {metrics.query_time_std_dev:.3f} seconds\n")
            f.write(f"Total Execution Time: {metrics.total_execution_time:.2f} seconds\n\n")

            f.write("VALIDATION RESULTS\n")
            f.write("-" * 20 + "\n")
            if validation.issues:
                f.write("Issues Found:\n")
                for issue in validation.issues:
                    f.write(f"  - {issue}\n")
                f.write("\n")

            if validation.warnings:
                f.write("Warnings:\n")
                for warning in validation.warnings:
                    f.write(f"  - {warning}\n")
                f.write("\n")

            if validation.recommendations:
                f.write("Recommendations:\n")
                for rec in validation.recommendations:
                    f.write(f"  - {rec}\n")
                f.write("\n")

            f.write("QUERY-LEVEL PERFORMANCE\n")
            f.write("-" * 25 + "\n")
            f.write("Query ID | Execution Time (s) | Relative Performance\n")
            f.write("-" * 55 + "\n")

            for query_id, query_time in sorted(result.power_test.query_times.items()):
                relative_perf = query_time / metrics.average_query_time
                f.write(f"{query_id:8} | {query_time:16.3f} | {relative_perf:16.2f}\n")

            f.write("\nDERIVED RESULTS NOTICE\n")
            f.write("-" * 25 + "\n")
            f.write(f"{TPCH_DERIVED_DISCLAIMER}\n")
            if not validation.checks_passed:
                f.write("Validation checks did not pass. Please address the issues listed above.\n")

        return report_file

    def generate_performance_csv(self, result: TPCHOfficialBenchmarkResult) -> Path:
        result = _report_view(result)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_file = self.output_dir / f"tpch_performance_data_{timestamp}.csv"

        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            writer.writerow(
                [
                    "Query_ID",
                    "Execution_Time_Seconds",
                    "Relative_Performance",
                    "Query_Type",
                    "Complexity_Score",
                ]
            )

            avg_time = statistics.mean(result.power_test.query_times.values())

            for query_id, query_time in sorted(result.power_test.query_times.items()):
                relative_perf = query_time / avg_time
                query_type = self._classify_query_type(query_id)
                complexity = self._calculate_query_complexity(query_id)

                writer.writerow([query_id, query_time, relative_perf, query_type, complexity])

            writer.writerow([])
            writer.writerow(["SUMMARY"])
            writer.writerow(["Metric", "Value"])
            writer.writerow(["Power@Size", result.power_test.power_at_size])
            writer.writerow(["Throughput@Size", result.throughput_test.throughput_at_size])
            writer.writerow(["Scale_Factor", result.scale_factor])
            writer.writerow(["Total_Time", result.total_benchmark_time])
            writer.writerow(["Notice", TPCH_DERIVED_DISCLAIMER])

        return csv_file

    def compare_results(
        self,
        baseline_result: TPCHOfficialBenchmarkResult,
        current_result: TPCHOfficialBenchmarkResult,
        significance_threshold: float = 0.05,
    ) -> ComparisonResult:
        baseline_result = _report_view(baseline_result)
        current_result = _report_view(current_result)
        baseline_power = baseline_result.power_test.power_at_size
        current_power = current_result.power_test.power_at_size

        if baseline_power > 0:
            performance_change = current_power - baseline_power
            relative_change = performance_change / baseline_power
            significant_change = abs(relative_change) > significance_threshold
        else:
            performance_change = 0.0
            relative_change = 0.0
            significant_change = False

        query_changes = {}
        for query_id in range(1, 23):
            baseline_time = baseline_result.power_test.query_times.get(query_id, 0)
            current_time = current_result.power_test.query_times.get(query_id, 0)

            if baseline_time > 0:
                change = (current_time - baseline_time) / baseline_time
                query_changes[query_id] = change

        return ComparisonResult(
            baseline_power_at_size=baseline_power,
            current_power_at_size=current_power,
            performance_change=performance_change,
            relative_change=relative_change,
            significant_change=significant_change,
            query_level_changes=query_changes,
        )

    def generate_comparison_report(
        self,
        baseline_result: TPCHOfficialBenchmarkResult,
        current_result: TPCHOfficialBenchmarkResult,
        report_title: str = "TPC-H Performance Comparison",
    ) -> Path:
        baseline_result = _report_view(baseline_result)
        current_result = _report_view(current_result)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = self.output_dir / f"tpch_comparison_report_{timestamp}.html"

        comparison = self.compare_results(baseline_result, current_result)

        html_content = self._generate_comparison_html(baseline_result, current_result, comparison, report_title)

        with open(report_file, "w", encoding="utf-8") as f:
            f.write(html_content)

        return report_file

    def _calculate_performance_metrics(self, result: TPCHOfficialBenchmarkResult) -> PerformanceMetrics:
        query_times = list(result.power_test.query_times.values())

        if not query_times:
            return PerformanceMetrics(
                power_at_size=result.power_test.power_at_size,
                throughput_at_size=result.throughput_test.throughput_at_size,
                total_execution_time=result.total_benchmark_time,
                average_query_time=0.0,
                median_query_time=0.0,
                query_time_std_dev=0.0,
                scale_factor=result.scale_factor,
            )

        avg_query_time = statistics.mean(query_times)
        median_query_time = statistics.median(query_times)
        std_dev = statistics.stdev(query_times) if len(query_times) > 1 else 0.0

        return PerformanceMetrics(
            power_at_size=result.power_test.power_at_size,
            throughput_at_size=result.throughput_test.throughput_at_size,
            total_execution_time=result.total_benchmark_time,
            average_query_time=avg_query_time,
            median_query_time=median_query_time,
            query_time_std_dev=std_dev,
            scale_factor=result.scale_factor,
        )

    def _validate_result(self, result: TPCHOfficialBenchmarkResult) -> ValidationResult:
        issues = self._check_basic_requirements(result)
        warnings = self._check_execution_warnings(result)
        recommendations = self._generate_recommendations(result)

        checks_passed = len(issues) == 0
        no_warnings = checks_passed and len(warnings) == 0

        return ValidationResult(
            checks_passed=checks_passed,
            no_warnings=no_warnings,
            issues=issues,
            warnings=warnings,
            recommendations=recommendations,
        )

    def _check_basic_requirements(self, result: TPCHOfficialBenchmarkResult) -> list[str]:
        issues = []
        if not result.success:
            issues.append("Benchmark did not complete successfully")
        if not result.power_test.success:
            issues.append("Power Test failed")
        elif len(result.power_test.query_times) != 22:
            issues.append(f"Power Test must execute all 22 queries, got {len(result.power_test.query_times)}")
        if not result.throughput_test.success:
            issues.append("Throughput Test failed")
        elif result.throughput_test.num_streams < 1:
            issues.append("Throughput Test must have at least 1 stream")
        return issues

    def _check_execution_warnings(self, result: TPCHOfficialBenchmarkResult) -> list[str]:
        warnings = []
        if result.power_test.total_time < 10:
            warnings.append("Power Test execution time seems unusually fast")
        if result.throughput_test.total_time < 10:
            warnings.append("Throughput Test execution time seems unusually fast")

        if result.power_test.query_times:
            query_times = list(result.power_test.query_times.values())
            avg_time = statistics.mean(query_times)
            for query_id, query_time in result.power_test.query_times.items():
                if query_time > avg_time * 10:
                    warnings.append(f"Query {query_id} took unusually long: {query_time:.2f}s")
                elif query_time < avg_time * 0.01:
                    warnings.append(f"Query {query_id} completed unusually fast: {query_time:.2f}s")
        return warnings

    def _generate_recommendations(self, result: TPCHOfficialBenchmarkResult) -> list[str]:
        recommendations = []
        if result.throughput_test.num_streams < 2:
            recommendations.append("Consider using more streams in Throughput Test for better performance measurement")
        if result.scale_factor < 1:
            recommendations.append("Consider using scale factor >= 1 for meaningful results")
        return recommendations

    def _classify_query_type(self, query_id: int) -> str:
        if query_id in [1, 6, 10, 14, 15, 19]:
            return "Pricing_Summary"
        elif query_id in [2, 4, 17, 20]:
            return "Minimum_Cost"
        elif query_id in [3, 5, 7, 8, 16, 21]:
            return "Shipping_Priority"
        elif query_id in [9, 11, 22]:
            return "Product_Type"
        elif query_id in [12, 13, 18]:
            return "Order_Priority"
        else:
            return "Mixed"

    def _calculate_query_complexity(self, query_id: int) -> int:
        complexity_map = {
            1: 3,
            2: 7,
            3: 4,
            4: 5,
            5: 6,
            6: 2,
            7: 4,
            8: 5,
            9: 8,
            10: 4,
            11: 3,
            12: 3,
            13: 4,
            14: 3,
            15: 5,
            16: 4,
            17: 6,
            18: 5,
            19: 4,
            20: 7,
            21: 8,
            22: 9,
        }
        return complexity_map.get(query_id, 5)

    def _generate_html_report(
        self,
        result: TPCHOfficialBenchmarkResult,
        metrics: PerformanceMetrics,
        validation: ValidationResult,
        title: str,
        include_detailed_analysis: bool,
        include_validation_info: bool,
    ) -> str:
        safe_title = html_escape(str(title), quote=True)
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>{safe_title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background-color: #f0f0f0; padding: 20px; border-radius: 5px; }}
        .section {{ margin: 20px 0; }}
        .metrics {{ display: flex; justify-content: space-around; }}
        .metric {{ text-align: center; background-color: #e8f4f8; padding: 15px; border-radius: 5px; }}
        .success {{ color: green; }}
        .warning {{ color: orange; }}
        .error {{ color: red; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{safe_title}</h1>
        <p>Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        <p>{html_escape(TPCH_DERIVED_DISCLAIMER, quote=True)}</p>
    </div>

    <div class="section">
        <h2>Executive Summary</h2>
        <div class="metrics">
            <div class="metric">
                <h3>Power@Size</h3>
                <p><strong>{result.power_test.power_at_size:.2f}</strong></p>
            </div>
            <div class="metric">
                <h3>Throughput@Size</h3>
                <p><strong>{result.throughput_test.throughput_at_size:.2f}</strong></p>
            </div>
            <div class="metric">
                <h3>Scale Factor</h3>
                <p><strong>{result.scale_factor}</strong></p>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Test Results</h2>
        <h3>Power Test</h3>
        <p>Execution Time: {result.power_test.total_time:.2f} seconds</p>
        <p>Status: <span class="{"success" if result.power_test.success else "error"}">
            {"SUCCESS" if result.power_test.success else "FAILED"}</span></p>

        <h3>Throughput Test</h3>
        <p>Execution Time: {result.throughput_test.total_time:.2f} seconds</p>
        <p>Number of Streams: {result.throughput_test.num_streams}</p>
        <p>Status: <span class="{"success" if result.throughput_test.success else "error"}">
            {"SUCCESS" if result.throughput_test.success else "FAILED"}</span></p>
    </div>
"""

        if include_detailed_analysis:
            html += f"""
    <div class="section">
        <h2>Detailed Analysis</h2>
        <p>Average Query Time: {metrics.average_query_time:.3f} seconds</p>
        <p>Median Query Time: {metrics.median_query_time:.3f} seconds</p>
        <p>Query Time Standard Deviation: {metrics.query_time_std_dev:.3f} seconds</p>

        <h3>Query Performance</h3>
        <table>
            <tr><th>Query ID</th><th>Execution Time (s)</th><th>Relative Performance</th></tr>
"""

            for query_id, query_time in sorted(result.power_test.query_times.items()):
                relative_perf = query_time / metrics.average_query_time if metrics.average_query_time > 0 else 0
                html += (
                    f"<tr><td>{html_escape(str(query_id), quote=True)}</td>"
                    f"<td>{query_time:.3f}</td><td>{relative_perf:.2f}</td></tr>"
                )

            html += "</table></div>"

        if include_validation_info:
            html += f"""
    <div class="section">
        <h2>Validation Information</h2>
        <p>{html_escape(TPCH_DERIVED_DISCLAIMER, quote=True)}</p>
        <p>Validation Checks Passed: <span class="{"success" if validation.checks_passed else "error"}">
            {"YES" if validation.checks_passed else "NO"}</span></p>
        <p>No Validation Warnings: <span class="{"success" if validation.no_warnings else "warning"}">
            {"YES" if validation.no_warnings else "NO"}</span></p>
"""

            if validation.issues:
                html += "<h3>Issues</h3><ul>"
                for issue in validation.issues:
                    html += f"<li class='error'>{html_escape(str(issue), quote=True)}</li>"
                html += "</ul>"

            if validation.warnings:
                html += "<h3>Warnings</h3><ul>"
                for warning in validation.warnings:
                    html += f"<li class='warning'>{html_escape(str(warning), quote=True)}</li>"
                html += "</ul>"

            html += "</div>"

        html += """
</body>
</html>
"""
        return html

    def _generate_comparison_html(
        self,
        baseline_result: TPCHOfficialBenchmarkResult,
        current_result: TPCHOfficialBenchmarkResult,
        comparison: ComparisonResult,
        title: str,
    ) -> str:
        safe_title = html_escape(str(title), quote=True)
        change_class = "success" if comparison.relative_change > 0 else "error"

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>{safe_title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background-color: #f0f0f0; padding: 20px; border-radius: 5px; }}
        .section {{ margin: 20px 0; }}
        .comparison {{ display: flex; justify-content: space-around; }}
        .result {{ text-align: center; background-color: #e8f4f8; padding: 15px; border-radius: 5px; }}
        .improvement {{ color: green; }}
        .regression {{ color: red; }}
        .neutral {{ color: gray; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{safe_title}</h1>
        <p>Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        <p>{html_escape(TPCH_DERIVED_DISCLAIMER, quote=True)}</p>
    </div>

    <div class="section">
        <h2>Performance Comparison</h2>
        <div class="comparison">
            <div class="result">
                <h3>Baseline</h3>
                <p>Power@Size: <strong>{comparison.baseline_power_at_size:.2f}</strong></p>
            </div>
            <div class="result">
                <h3>Current</h3>
                <p>Power@Size: <strong>{comparison.current_power_at_size:.2f}</strong></p>
            </div>
            <div class="result">
                <h3>Change</h3>
                <p class="{change_class}">
                    {comparison.performance_change:+.2f} ({comparison.relative_change:+.1%})
                </p>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Query-Level Changes</h2>
        <table>
            <tr><th>Query ID</th><th>Baseline Time (s)</th><th>Current Time (s)</th><th>Change (%)</th></tr>
"""

        for query_id in range(1, 23):
            baseline_time = baseline_result.power_test.query_times.get(query_id, 0)
            current_time = current_result.power_test.query_times.get(query_id, 0)
            change = comparison.query_level_changes.get(query_id, 0)

            change_class = "improvement" if change < 0 else "regression" if change > 0 else "neutral"

            html += f"""
            <tr>
                <td>{query_id}</td>
                <td>{baseline_time:.3f}</td>
                <td>{current_time:.3f}</td>
                <td class="{change_class}">{change:+.1%}</td>
            </tr>
"""

        html += """
        </table>
    </div>
</body>
</html>
"""
        return html
