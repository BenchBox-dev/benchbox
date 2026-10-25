from __future__ import annotations

import pytest

from benchbox.core.tpch.official_benchmark import TPCHOfficialBenchmarkConfig, TPCHOfficialBenchmarkResult
from benchbox.core.tpch.power_test import TPCHPowerTestConfig, TPCHPowerTestResult
from benchbox.core.tpch.reporting import TPCHReportGenerator

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _current_result(power_at_size: float = 3600.0) -> TPCHOfficialBenchmarkResult:
    query_results = [
        {"query_id": query_id, "execution_time_seconds": 0.5 + query_id / 100, "success": True}
        for query_id in range(1, 23)
    ]
    power = TPCHPowerTestResult(
        config=TPCHPowerTestConfig(scale_factor=1.0),
        start_time="2026-10-04T10:00:00",
        end_time="2026-10-04T10:00:20",
        total_time=20.0,
        power_at_size=power_at_size,
        queries_executed=22,
        queries_successful=22,
        query_results=query_results,
        success=True,
        errors=[],
    )
    return TPCHOfficialBenchmarkResult(
        config=TPCHOfficialBenchmarkConfig(scale_factor=1.0, num_streams=2),
        start_time="2026-10-04T10:00:00",
        end_time="2026-10-04T10:01:00",
        total_time=60.0,
        power_test_result=power,
        throughput_test_result={"total_time": 30.0, "streams_executed": 2, "success": True},
        maintenance_test_result=None,
        power_at_size=power_at_size,
        throughput_at_size=7200.0,
        success=True,
        errors=[],
    )


def test_every_report_runs_on_a_current_result_without_a_composite(tmp_path):
    generator = TPCHReportGenerator(tmp_path)
    result = _current_result()

    validation_report = generator.generate_validation_report(result).read_text(encoding="utf-8")
    detailed = generator.generate_detailed_report(result).read_text(encoding="utf-8")
    csv_text = generator.generate_performance_csv(result).read_text(encoding="utf-8")
    comparison = generator.generate_comparison_report(_current_result(1800.0), result).read_text(encoding="utf-8")

    assert "Power@Size: 3600.00" in validation_report
    assert "Throughput@Size: 7200.00" in validation_report
    for text in (validation_report, detailed, csv_text, comparison):
        assert "QphH" not in text
        assert "certif" not in text.lower()
    for text in (validation_report, detailed, csv_text):
        assert "do not comply with the TPC-H Specification" in text


def test_comparison_uses_power_at_size(tmp_path):
    comparison = TPCHReportGenerator(tmp_path).compare_results(_current_result(1800.0), _current_result(3600.0))

    assert comparison.baseline_power_at_size == 1800.0
    assert comparison.current_power_at_size == 3600.0
    assert comparison.relative_change == pytest.approx(1.0)
