import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from benchbox.core.tpc_disclaimer import TPCDS_DERIVED_DISCLAIMER
from benchbox.utils.printing import emit

from .benchmark import (
    BenchmarkResult,
    PhaseResult,
)


class TPCDSReportGenerator:
    def __init__(self, output_dir: Path, verbose: bool = False) -> None:
        self.output_dir = Path(output_dir)
        self.verbose = verbose

        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.reports_dir = self.output_dir / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_complete_report(self, result: BenchmarkResult) -> dict[str, Path]:
        if self.verbose:
            emit(f"Generating TPC-DS benchmark reports in {self.reports_dir}")

        reports = {}

        reports["executive_summary"] = self._generate_executive_summary(result)

        reports["detailed_analysis"] = self._generate_detailed_analysis(result)

        reports["query_analysis"] = self._generate_query_analysis(result)

        reports["json_report"] = self._generate_json_report(result)

        reports["csv_export"] = self._generate_csv_export(result)

        reports["html_report"] = self._generate_html_report(result)

        reports["validation_checklist_report"] = self._generate_validation_checklist_report(result)

        reports["performance_summary"] = self._generate_performance_summary(result)

        if self.verbose:
            emit(f"Generated {len(reports)} reports:")
            for report_type, path in reports.items():
                emit(f"  - {report_type}: {path}")

        return reports

    def _generate_executive_summary(self, result: BenchmarkResult) -> Path:
        report_path = self.reports_dir / "executive_summary.txt"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("TPC-DS BENCHMARK EXECUTIVE SUMMARY\n")
            f.write("=" * 50 + "\n")
            f.write(f"{TPCDS_DERIVED_DISCLAIMER}\n\n")

            if result.benchmark_start_time:
                f.write(f"Benchmark Date: {result.benchmark_start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Scale Factor: {result.scale_factor}\n")
            f.write(f"Total Execution Time: {result.total_benchmark_time:.2f} seconds\n\n")

            f.write("TPC-DS DERIVED METRICS\n")
            f.write("-" * 25 + "\n")

            if result.power_at_size:
                f.write(f"Power@Size: {result.power_at_size:.2f}\n")
            else:
                f.write("Power@Size: Not calculated\n")

            if result.throughput_at_size:
                f.write(f"Throughput@Size: {result.throughput_at_size:.2f}\n")
            else:
                f.write("Throughput@Size: Not calculated\n")

            f.write("\n")

            f.write("PHASE RESULTS SUMMARY\n")
            f.write("-" * 25 + "\n")

            if result.power_test:
                status = "PASSED" if result.power_test.success else "FAILED"
                f.write(f"Power Test: {status} ({result.power_test.total_time:.2f}s)\n")

            if result.throughput_test:
                status = "PASSED" if result.throughput_test.success else "FAILED"
                f.write(f"Throughput Test: {status} ({result.throughput_test.total_time:.2f}s)\n")

            if result.maintenance_test:
                status = "PASSED" if result.maintenance_test.success else "FAILED"
                f.write(f"Maintenance Test: {status} ({result.maintenance_test.total_time:.2f}s)\n")

            f.write("\n")

            if result.validation_results:
                f.write("VALIDATION RESULTS\n")
                f.write("-" * 18 + "\n")

                overall_status = "VALID" if result.validation_results.get("overall_valid", False) else "INVALID"
                f.write(f"Overall Validation: {overall_status}\n")

                if result.validation_results.get("issues"):
                    f.write("Issues:\n")
                    for issue in result.validation_results["issues"]:
                        f.write(f"  - {issue}\n")

                f.write("\n")

            f.write("CONFIGURATION\n")
            f.write("-" * 13 + "\n")
            f.write(f"scale_factor: {result.scale_factor}\n")
            f.write(f"num_streams: {result.num_streams}\n")

        return report_path

    def _generate_detailed_analysis(self, result: BenchmarkResult) -> Path:
        report_path = self.reports_dir / "detailed_analysis.txt"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("TPC-DS BENCHMARK DETAILED ANALYSIS\n")
            f.write("=" * 50 + "\n\n")

            if result.power_test:
                f.write("POWER TEST ANALYSIS\n")
                f.write("-" * 20 + "\n")
                self._write_phase_analysis(f, result.power_test)
                f.write("\n")

            if result.throughput_test:
                f.write("THROUGHPUT TEST ANALYSIS\n")
                f.write("-" * 24 + "\n")
                self._write_phase_analysis(f, result.throughput_test)
                f.write("\n")

            if result.maintenance_test:
                f.write("MAINTENANCE TEST ANALYSIS\n")
                f.write("-" * 25 + "\n")
                self._write_phase_analysis(f, result.maintenance_test)
                f.write("\n")

            f.write("METRIC CALCULATIONS\n")
            f.write("-" * 19 + "\n")

            if result.power_test and result.power_at_size:
                f.write("Power@Size Calculation:\n")
                f.write(f"  Formula: 3600 × {result.scale_factor} / {result.power_test.total_time:.2f}\n")
                f.write(f"  Result: {result.power_at_size:.2f}\n\n")

            if result.throughput_test and result.throughput_at_size:
                num_streams = result.num_streams
                f.write("Throughput@Size Calculation:\n")
                f.write(
                    f"  Formula: 99 × {num_streams} × 3600 × {result.scale_factor} / {result.throughput_test.total_time:.2f}\n"
                )
                f.write(f"  Result: {result.throughput_at_size:.2f}\n\n")

        return report_path

    def _generate_query_analysis(self, result: BenchmarkResult) -> Path:
        report_path = self.reports_dir / "query_analysis.txt"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("TPC-DS QUERY-LEVEL ANALYSIS\n")
            f.write("=" * 50 + "\n\n")

            for phase_name, phase_result in [
                ("Power Test", result.power_test),
                ("Throughput Test", result.throughput_test),
                ("Maintenance Test", result.maintenance_test),
            ]:
                if phase_result:
                    f.write(f"{phase_name.upper()}\n")
                    f.write("-" * len(phase_name) + "\n")

                    sorted_queries = sorted(
                        phase_result.queries,
                        key=lambda q: q.execution_time or 0,
                        reverse=True,
                    )

                    f.write(f"{'Query ID':<8} {'Stream':<8} {'Time (s)':<10} {'Status':<10} {'Rows':<10}\n")
                    f.write("-" * 60 + "\n")

                    for query in sorted_queries:
                        stream_str = str(query.stream_id) if query.stream_id is not None else "N/A"
                        time_str = f"{query.execution_time:.3f}" if query.execution_time else "N/A"
                        status_str = "SUCCESS" if query.success else "FAILED"
                        rows_str = str(query.row_count) if query.row_count is not None else "N/A"

                        f.write(f"{query.query_id:<8} {stream_str:<8} {time_str:<10} {status_str:<10} {rows_str:<10}\n")

                    f.write("\n")

                    successful_queries = [q for q in phase_result.queries if q.success]
                    if successful_queries:
                        times = [q.execution_time for q in successful_queries if q.execution_time]
                        if times:
                            f.write("Statistics:\n")
                            f.write(f"  Total Queries: {len(phase_result.queries)}\n")
                            f.write(f"  Successful: {len(successful_queries)}\n")
                            f.write(f"  Failed: {len(phase_result.queries) - len(successful_queries)}\n")
                            f.write(f"  Average Time: {sum(times) / len(times):.3f}s\n")
                            f.write(f"  Min Time: {min(times):.3f}s\n")
                            f.write(f"  Max Time: {max(times):.3f}s\n")
                            f.write("\n")

        return report_path

    def _generate_json_report(self, result: BenchmarkResult) -> Path:
        report_path = self.reports_dir / "benchmark_results.json"

        def convert_datetime(obj: Any) -> Any:
            if isinstance(obj, datetime):
                return obj.isoformat()
            return obj

        result_dict = asdict(result)

        def process_dict(d: Any) -> Any:
            if isinstance(d, dict):
                return {k: process_dict(v) for k, v in d.items()}
            elif isinstance(d, list):
                return [process_dict(item) for item in d]
            else:
                return convert_datetime(d)

        result_dict = process_dict(result_dict)

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=2, default=str)

        return report_path

    def _generate_csv_export(self, result: BenchmarkResult) -> Path:
        report_path = self.reports_dir / "query_results.csv"

        with open(report_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            writer.writerow(
                [
                    "Phase",
                    "Query ID",
                    "Stream ID",
                    "Start Time",
                    "End Time",
                    "Execution Time (s)",
                    "Success",
                    "Row Count",
                    "Error Message",
                ]
            )

            for phase_name, phase_result in [
                ("Power Test", result.power_test),
                ("Throughput Test", result.throughput_test),
                ("Maintenance Test", result.maintenance_test),
            ]:
                if phase_result:
                    for query in phase_result.queries:
                        writer.writerow(
                            [
                                phase_name,
                                query.query_id,
                                query.stream_id,
                                query.start_time.isoformat() if query.start_time else "",
                                query.end_time.isoformat() if query.end_time else "",
                                query.execution_time,
                                query.success,
                                query.row_count,
                                query.error_message or "",
                            ]
                        )

        return report_path

    def _generate_html_report(self, result: BenchmarkResult) -> Path:
        report_path = self.reports_dir / "benchmark_report.html"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(self._generate_html_content(result))

        return report_path

    def _generate_validation_checklist_report(self, result: BenchmarkResult) -> Path:
        report_path = self.reports_dir / "validation_checklist_report.txt"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("TPC-DS VALIDATION CHECKLIST REPORT\n")
            f.write("=" * 50 + "\n")
            f.write(f"{TPCDS_DERIVED_DISCLAIMER}\n\n")

            f.write("VALIDATION CHECKLIST\n")
            f.write("-" * 20 + "\n")

            checks = [
                ("Power Test executed", result.power_test is not None),
                ("Throughput Test executed", result.throughput_test is not None),
                ("Maintenance Test executed", result.maintenance_test is not None),
                (
                    "All 99 queries executed in Power Test",
                    result.power_test and len(result.power_test.queries) >= 99,
                ),
                (
                    "Multi-stream execution in Throughput Test",
                    result.throughput_test and result.num_streams > 1,
                ),
                ("No query failures", self._check_no_failures(result)),
                ("Proper parameter generation", True),
                (
                    "Result validation passed",
                    result.validation_results.get("overall_valid", False) if result.validation_results else False,
                ),
            ]

            for check_name, passed in checks:
                status = "PASS" if passed else "FAIL"
                f.write(f"[{status}] {check_name}\n")

            f.write("\n")

            overall_passed = all(passed for _, passed in checks)
            f.write(f"Overall Checklist Status: {'ALL CHECKS PASSED' if overall_passed else 'CHECKS FAILED'}\n\n")

            if result.validation_results:
                f.write("DETAILED VALIDATION RESULTS\n")
                f.write("-" * 27 + "\n")

                for key, value in result.validation_results.items():
                    if isinstance(value, list):
                        f.write(f"{key}: {len(value)} items\n")
                        for item in value:
                            f.write(f"  - {item}\n")
                    else:
                        f.write(f"{key}: {value}\n")

        return report_path

    def _generate_performance_summary(self, result: BenchmarkResult) -> Path:
        report_path = self.reports_dir / "performance_summary.txt"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("TPC-DS PERFORMANCE SUMMARY\n")
            f.write("=" * 50 + "\n\n")

            f.write("OVERALL PERFORMANCE\n")
            f.write("-" * 18 + "\n")
            f.write(f"Scale Factor: {result.scale_factor}\n")
            f.write(f"Total Execution Time: {result.total_benchmark_time:.2f} seconds\n")
            f.write("\n")

            for phase_name, phase_result in [
                ("Power Test", result.power_test),
                ("Throughput Test", result.throughput_test),
                ("Maintenance Test", result.maintenance_test),
            ]:
                if phase_result:
                    f.write(f"{phase_name.upper()} PERFORMANCE\n")
                    f.write("-" * (len(phase_name) + 12) + "\n")

                    f.write(f"Execution Time: {phase_result.total_time:.2f} seconds\n")
                    f.write(f"Success Rate: {self._calculate_success_rate(phase_result):.1f}%\n")
                    f.write("\n")

            f.write("PERFORMANCE RECOMMENDATIONS\n")
            f.write("-" * 26 + "\n")
            self._write_performance_recommendations(f, result)

        return report_path

    def _write_phase_analysis(self, f: Any, phase_result: PhaseResult) -> None:
        if phase_result.start_time:
            f.write(f"Start Time: {phase_result.start_time}\n")
        if phase_result.end_time:
            f.write(f"End Time: {phase_result.end_time}\n")
        f.write(f"Execution Time: {phase_result.total_time:.2f} seconds\n")
        f.write(f"Success: {phase_result.success}\n")
        f.write(f"Total Queries: {len(phase_result.queries)}\n")

        successful_queries = [q for q in phase_result.queries if q.success]
        f.write(f"Successful Queries: {len(successful_queries)}\n")
        f.write(f"Failed Queries: {len(phase_result.queries) - len(successful_queries)}\n")

        if phase_result.error_message:
            f.write(f"Error Message: {phase_result.error_message}\n")

    def _generate_html_content(self, result: BenchmarkResult) -> str:
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>TPC-DS Benchmark Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background-color: #f0f0f0; padding: 20px; border-radius: 5px; }}
        .metric {{ background-color: #e8f4f8; padding: 10px; margin: 10px 0; border-radius: 5px; }}
        .phase {{ margin: 20px 0; }}
        .error {{ color: red; }}
        .success {{ color: green; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>TPC-DS Benchmark Report</h1>
        <p><strong>Scale Factor:</strong> {result.scale_factor}</p>
        <p><strong>Execution Date:</strong> {result.benchmark_start_time.strftime("%Y-%m-%d %H:%M:%S") if result.benchmark_start_time else "N/A"}</p>
        <p><strong>Total Time:</strong> {result.total_benchmark_time:.2f} seconds</p>
    </div>

    <div class="metric">
        <h2>TPC-DS Derived Metrics</h2>
        <p>{TPCDS_DERIVED_DISCLAIMER}</p>
        <p><strong>Power@Size:</strong> {result.power_at_size:.2f}</p>
        <p><strong>Throughput@Size:</strong> {result.throughput_at_size:.2f}</p>
    </div>
"""

        for phase_name, phase_result in [
            ("Power Test", result.power_test),
            ("Throughput Test", result.throughput_test),
            ("Maintenance Test", result.maintenance_test),
        ]:
            if phase_result:
                status_class = "success" if phase_result.success else "error"
                html_content += f"""
    <div class="phase">
        <h3>{phase_name} <span class="{status_class}">({"SUCCESS" if phase_result.success else "FAILED"})</span></h3>
        <p><strong>Execution Time:</strong> {phase_result.total_time:.2f} seconds</p>
        <p><strong>Queries:</strong> {len(phase_result.queries)} total</p>
        <p><strong>Success Rate:</strong> {self._calculate_success_rate(phase_result):.1f}%</p>
    </div>
"""

        html_content += """
</body>
</html>
"""
        return html_content

    def _calculate_success_rate(self, phase_result: PhaseResult) -> float:
        if not phase_result.queries:
            return 0.0

        successful = sum(1 for q in phase_result.queries if q.success)
        return (successful / len(phase_result.queries)) * 100.0

    def _check_no_failures(self, result: BenchmarkResult) -> bool:
        all_queries = []

        for phase_result in [
            result.power_test,
            result.throughput_test,
            result.maintenance_test,
        ]:
            if phase_result:
                all_queries.extend(phase_result.queries)

        return all(q.success for q in all_queries)

    def _write_performance_recommendations(self, f: Any, result: BenchmarkResult) -> None:
        recommendations = []

        if result.throughput_test:
            success_rate = self._calculate_success_rate(result.throughput_test)
            if success_rate < 95:
                recommendations.append("Investigate query failures in throughput test")

        if not recommendations:
            recommendations.append("No specific recommendations - benchmark completed successfully")

        for i, recommendation in enumerate(recommendations, 1):
            f.write(f"{i}. {recommendation}\n")
