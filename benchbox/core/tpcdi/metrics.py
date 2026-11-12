# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from benchbox.core.tpc_disclaimer import derived_results_disclaimer
from benchbox.utils.printing import emit

from .etl import ETLResult
from .validation import DataQualityResult

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkMetrics:
    etl_throughput: float = 0.0
    data_quality_score: float = 0.0
    overall_performance: float = 0.0

    total_execution_time: float = 0.0
    total_records_processed: int = 0
    total_records_loaded: int = 0

    historical_load_time: float = 0.0
    historical_load_records: int = 0
    incremental_load_time: float = 0.0
    incremental_load_records: int = 0
    validation_time: float = 0.0

    validations_passed: int = 0
    validations_total: int = 0
    data_integrity_score: float = 0.0

    dimension_load_time: float = 0.0
    fact_load_time: float = 0.0
    index_creation_time: float = 0.0
    scd_processing_time: float = 0.0

    tpc_di_compliant: bool = False
    scale_factor: float = 1.0
    benchmark_date: datetime = field(default_factory=datetime.now)


@dataclass
class BenchmarkReport:
    metrics: BenchmarkMetrics
    summary: dict[str, Any] = field(default_factory=dict)
    phase_details: list[dict[str, Any]] = field(default_factory=list)
    validation_details: dict[str, Any] = field(default_factory=dict)
    performance_breakdown: dict[str, float] = field(default_factory=dict)


class TPCDIMetrics:
    def __init__(self, scale_factor: float = 1.0):
        self.scale_factor = scale_factor

    def calculate_etl_throughput(self, etl_result: ETLResult) -> float:
        total_records = 0
        total_time = 0.0

        if etl_result.historical_load and etl_result.historical_load.success:
            total_records += etl_result.historical_load.total_records_processed
            total_time += etl_result.historical_load.total_execution_time

        for incremental in etl_result.incremental_loads:
            if incremental.success:
                total_records += incremental.total_records_processed
                total_time += incremental.total_execution_time

        if total_time > 0:
            throughput = total_records / total_time
            logger.info(f"ETL Throughput: {throughput:,.2f} records/second")
            return throughput

        return 0.0

    def calculate_data_quality_score(self, validation_result: DataQualityResult) -> float:
        if validation_result.total_validations == 0:
            return 0.0

        score = validation_result.passed_validations / validation_result.total_validations
        logger.info(f"Data Quality Score: {score:.1%}")
        return score

    def calculate_overall_performance(self, throughput: float, quality_score: float) -> float:
        if throughput <= 0 or quality_score <= 0:
            return 0.0

        normalized_throughput = min(throughput / 1000.0, 1.0)

        composite_score = math.sqrt(normalized_throughput * quality_score) * 1000

        logger.info(f"Overall Performance Score: {composite_score:.2f}")
        return composite_score

    def calculate_detailed_metrics(
        self,
        etl_result: ETLResult,
        validation_result: DataQualityResult,
        start_time: datetime,
        end_time: datetime,
    ) -> BenchmarkMetrics:
        metrics = BenchmarkMetrics(scale_factor=self.scale_factor)

        metrics.etl_throughput = self.calculate_etl_throughput(etl_result)
        metrics.data_quality_score = self.calculate_data_quality_score(validation_result)
        metrics.overall_performance = self.calculate_overall_performance(
            metrics.etl_throughput, metrics.data_quality_score
        )

        metrics.total_execution_time = (end_time - start_time).total_seconds()

        if etl_result.historical_load:
            metrics.historical_load_time = etl_result.historical_load.total_execution_time
            metrics.historical_load_records = etl_result.historical_load.total_records_processed
            metrics.total_records_processed += metrics.historical_load_records

        for incremental in etl_result.incremental_loads:
            metrics.incremental_load_time += incremental.total_execution_time
            metrics.incremental_load_records += incremental.total_records_processed
            metrics.total_records_processed += incremental.total_records_processed

        metrics.validations_passed = validation_result.passed_validations
        metrics.validations_total = validation_result.total_validations
        metrics.data_integrity_score = metrics.data_quality_score

        metrics.tpc_di_compliant = self._check_tpc_di_compliance(metrics, validation_result)

        logger.info(
            f"Calculated TPC-DI metrics: "
            f"Throughput={metrics.etl_throughput:.2f} rec/s, "
            f"Quality={metrics.data_quality_score:.1%}, "
            f"Performance={metrics.overall_performance:.2f}"
        )

        return metrics

    def generate_official_report(self, metrics: BenchmarkMetrics) -> BenchmarkReport:
        report = BenchmarkReport(metrics=metrics)

        report.summary = {
            "scale_factor": metrics.scale_factor,
            "total_execution_time": self._format_duration(metrics.total_execution_time),
            "total_records_processed": f"{metrics.total_records_processed:,}",
            "etl_throughput": f"{metrics.etl_throughput:,.2f} rec/sec",
            "data_quality_score": f"{metrics.data_quality_score:.1%}",
            "overall_performance": f"{metrics.overall_performance:.2f}",
            "internal_validation_checks_passed": "YES" if metrics.tpc_di_compliant else "NO",
            "disclaimer": derived_results_disclaimer("TPC-DI"),
            "benchmark_date": metrics.benchmark_date.strftime("%Y-%m-%d %H:%M:%S"),
        }

        if metrics.historical_load_records > 0:
            report.phase_details.append(
                {
                    "phase": "Historical Load",
                    "records": f"{metrics.historical_load_records:,}",
                    "time": self._format_duration(metrics.historical_load_time),
                    "throughput": f"{metrics.historical_load_records / metrics.historical_load_time:.2f} rec/sec"
                    if metrics.historical_load_time > 0
                    else "N/A",
                }
            )

        if metrics.incremental_load_records > 0:
            report.phase_details.append(
                {
                    "phase": "Incremental Loads",
                    "records": f"{metrics.incremental_load_records:,}",
                    "time": self._format_duration(metrics.incremental_load_time),
                    "throughput": f"{metrics.incremental_load_records / metrics.incremental_load_time:.2f} rec/sec"
                    if metrics.incremental_load_time > 0
                    else "N/A",
                }
            )

        report.validation_details = {
            "total_validations": metrics.validations_total,
            "passed_validations": metrics.validations_passed,
            "failed_validations": metrics.validations_total - metrics.validations_passed,
            "quality_score": f"{metrics.data_quality_score:.1%}",
            "integrity_score": f"{metrics.data_integrity_score:.1%}",
        }

        report.performance_breakdown = {
            "Historical Load Time": metrics.historical_load_time,
            "Incremental Load Time": metrics.incremental_load_time,
            "Validation Time": metrics.validation_time,
            "Total ETL Time": metrics.historical_load_time + metrics.incremental_load_time,
        }

        return report

    def print_official_results(self, metrics: BenchmarkMetrics) -> None:
        emit("\n" + "=" * 80)
        emit("TPC-DI DERIVED BENCHMARK RESULTS")
        emit("=" * 80)
        emit(derived_results_disclaimer("TPC-DI"))

        emit(f"Scale Factor: {metrics.scale_factor}")
        emit(f"Total Execution Time: {self._format_duration(metrics.total_execution_time)}")
        emit(f"Internal Validation Checks Passed: {'YES' if metrics.tpc_di_compliant else 'NO'}")
        emit(f"Benchmark Date: {metrics.benchmark_date.strftime('%Y-%m-%d %H:%M:%S')}")

        emit("\nDERIVED TPC-DI METRICS:")
        emit("-" * 40)
        emit(f"ETL Throughput: {metrics.etl_throughput:,.2f} records/second")
        emit(f"Data Quality Score: {metrics.data_quality_score:.1%}")
        emit(f"Overall Performance: {metrics.overall_performance:.2f}")

        emit("\nEXECUTION BREAKDOWN:")
        emit("-" * 40)
        emit(f"Total Records Processed: {metrics.total_records_processed:,}")

        if metrics.historical_load_records > 0:
            emit(
                f"Historical Load: {metrics.historical_load_records:,} records in {self._format_duration(metrics.historical_load_time)}"
            )
            if metrics.historical_load_time > 0:
                hist_throughput = metrics.historical_load_records / metrics.historical_load_time
                emit(f"  Throughput: {hist_throughput:,.2f} records/second")

        if metrics.incremental_load_records > 0:
            emit(
                f"Incremental Loads: {metrics.incremental_load_records:,} records in {self._format_duration(metrics.incremental_load_time)}"
            )
            if metrics.incremental_load_time > 0:
                inc_throughput = metrics.incremental_load_records / metrics.incremental_load_time
                emit(f"  Throughput: {inc_throughput:,.2f} records/second")

        emit("\nDATA QUALITY ASSESSMENT:")
        emit("-" * 40)
        emit(f"Validations Passed: {metrics.validations_passed}/{metrics.validations_total}")
        emit(f"Data Integrity Score: {metrics.data_integrity_score:.1%}")

        if not metrics.tpc_di_compliant:
            emit("\nVALIDATION ISSUES:")
            emit("-" * 40)
            emit("⚠️️  Benchmark did not pass all internal validation checks")
            if metrics.data_quality_score < 1.0:
                emit(f"   - Data quality issues: {(1.0 - metrics.data_quality_score) * 100:.1f}% validation failures")

        emit("=" * 80)

    def export_metrics_json(self, metrics: BenchmarkMetrics) -> dict[str, Any]:
        return {
            "benchmark_info": {
                "type": "TPC-DI",
                "scale_factor": metrics.scale_factor,
                "benchmark_date": metrics.benchmark_date.isoformat(),
                "internal_validation_checks_passed": metrics.tpc_di_compliant,
                "disclaimer": derived_results_disclaimer("TPC-DI"),
            },
            "primary_metrics": {
                "etl_throughput": metrics.etl_throughput,
                "data_quality_score": metrics.data_quality_score,
                "overall_performance": metrics.overall_performance,
            },
            "execution_stats": {
                "total_execution_time": metrics.total_execution_time,
                "total_records_processed": metrics.total_records_processed,
                "historical_load_time": metrics.historical_load_time,
                "historical_load_records": metrics.historical_load_records,
                "incremental_load_time": metrics.incremental_load_time,
                "incremental_load_records": metrics.incremental_load_records,
            },
            "quality_metrics": {
                "validations_passed": metrics.validations_passed,
                "validations_total": metrics.validations_total,
                "data_integrity_score": metrics.data_integrity_score,
            },
        }

    def _check_tpc_di_compliance(self, metrics: BenchmarkMetrics, validation_result: DataQualityResult) -> bool:

        compliance_checks = [
            metrics.data_quality_score >= 1.0,
            metrics.etl_throughput > 0,
            metrics.total_records_processed > 0,
            validation_result.error_count == 0,
        ]

        compliant = all(compliance_checks)

        if not compliant:
            logger.warning("Benchmark does not meet TPC-DI compliance requirements")
            if metrics.data_quality_score < 1.0:
                logger.warning(
                    f"Data quality issues: {(1.0 - metrics.data_quality_score) * 100:.1f}% validation failures"
                )
            if validation_result.error_count > 0:
                logger.warning(f"Validation errors: {validation_result.error_count} critical issues")

        return compliant

    def _format_duration(self, seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.2f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        else:
            hours = seconds / 3600
            return f"{hours:.2f}h"

    def compare_benchmarks(
        self, baseline_metrics: BenchmarkMetrics, current_metrics: BenchmarkMetrics
    ) -> dict[str, Any]:
        comparison = {
            "throughput_change": self._calculate_percentage_change(
                baseline_metrics.etl_throughput, current_metrics.etl_throughput
            ),
            "quality_change": self._calculate_percentage_change(
                baseline_metrics.data_quality_score, current_metrics.data_quality_score
            ),
            "performance_change": self._calculate_percentage_change(
                baseline_metrics.overall_performance,
                current_metrics.overall_performance,
            ),
            "execution_time_change": self._calculate_percentage_change(
                baseline_metrics.total_execution_time,
                current_metrics.total_execution_time,
            ),
            "improvement": current_metrics.overall_performance > baseline_metrics.overall_performance,
        }

        return comparison

    def _calculate_percentage_change(self, baseline: float, current: float) -> float:
        if baseline == 0:
            return 0.0 if current == 0 else float("inf")

        return ((current - baseline) / baseline) * 100.0
