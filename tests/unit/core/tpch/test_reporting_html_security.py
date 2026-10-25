from html import escape as html_escape
from types import SimpleNamespace

import pytest

from benchbox.core.tpch.reporting import ComparisonResult, PerformanceMetrics, TPCHReportGenerator, ValidationResult

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


UNTRUSTED_TITLE = '"><script>alert("title")</script>'
UNTRUSTED_QUERY_ID = '"><script>alert("query")</script>'
UNTRUSTED_ISSUE = '"><script>alert("issue")</script>'
UNTRUSTED_WARNING = '"><script>alert("warning")</script>'


def _benchmark_result(query_id: str = UNTRUSTED_QUERY_ID):
    return SimpleNamespace(
        scale_factor=1.0,
        power_test=SimpleNamespace(
            power_at_size=10.0,
            total_time=1.0,
            success=True,
            query_times={query_id: 1.0},
        ),
        throughput_test=SimpleNamespace(
            throughput_at_size=10.0,
            total_time=1.0,
            num_streams=2,
            success=True,
        ),
    )


def _metrics() -> PerformanceMetrics:
    return PerformanceMetrics(
        power_at_size=10.0,
        throughput_at_size=10.0,
        total_execution_time=1.0,
        average_query_time=1.0,
        median_query_time=1.0,
        query_time_std_dev=0.0,
        scale_factor=1.0,
    )


def test_detailed_report_escapes_title_query_id_issues_and_warnings(tmp_path):
    generator = TPCHReportGenerator(tmp_path)
    validation = ValidationResult(
        checks_passed=False,
        no_warnings=False,
        issues=[UNTRUSTED_ISSUE],
        warnings=[UNTRUSTED_WARNING],
    )

    html = generator._generate_html_report(
        _benchmark_result(),
        _metrics(),
        validation,
        UNTRUSTED_TITLE,
        include_detailed_analysis=True,
        include_validation_info=True,
    )

    for raw_value in (UNTRUSTED_TITLE, UNTRUSTED_QUERY_ID, UNTRUSTED_ISSUE, UNTRUSTED_WARNING):
        assert raw_value not in html
        assert html_escape(raw_value, quote=True) in html


def test_comparison_report_escapes_title(tmp_path):
    generator = TPCHReportGenerator(tmp_path)
    comparison = ComparisonResult(
        baseline_power_at_size=10.0,
        current_power_at_size=11.0,
        performance_change=1.0,
        relative_change=0.1,
        significant_change=False,
        query_level_changes={1: -0.1},
    )

    html = generator._generate_comparison_html(
        _benchmark_result(query_id="1"),
        _benchmark_result(query_id="1"),
        comparison,
        UNTRUSTED_TITLE,
    )

    assert UNTRUSTED_TITLE not in html
    assert html_escape(UNTRUSTED_TITLE, quote=True) in html
