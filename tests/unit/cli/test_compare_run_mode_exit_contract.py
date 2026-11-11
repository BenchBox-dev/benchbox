from __future__ import annotations

import pytest

from benchbox.cli.commands.compare import _exit_on_comparison_failure
from benchbox.core.comparison.suite import UnifiedBenchmarkSuite
from benchbox.core.comparison.types import (
    PlatformType,
    UnifiedPlatformResult,
    UnifiedQueryResult,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _failed(platform: str, reason: str = "no driver") -> UnifiedPlatformResult:
    return UnifiedBenchmarkSuite()._build_platform_result(
        platform,
        PlatformType.SQL,
        [
            UnifiedQueryResult(
                query_id="ALL",
                platform=platform,
                platform_type=PlatformType.SQL,
                status="ERROR",
                error_message=reason,
            )
        ],
    )


def _succeeded(platform: str, geomean: float) -> UnifiedPlatformResult:
    return UnifiedBenchmarkSuite()._build_platform_result(
        platform,
        PlatformType.SQL,
        [
            UnifiedQueryResult(
                query_id="Q1",
                platform=platform,
                platform_type=PlatformType.SQL,
                iterations=1,
                execution_times_ms=[geomean],
                mean_time_ms=geomean,
                min_time_ms=geomean,
                max_time_ms=geomean,
                status="SUCCESS",
            )
        ],
    )


class TestFailedPlatformIsNeverReportedSuccessful:
    def test_a_platform_that_raised_has_zero_success_rate(self):
        assert _failed("duckdb").success_rate == 0.0

    def test_bare_construction_defaults_to_zero_not_one_hundred(self):
        bare = UnifiedPlatformResult(platform="duckdb", platform_type=PlatformType.SQL)
        assert bare.success_rate == 0.0


class TestSummaryDoesNotFabricateAComparison:
    def test_total_failure_yields_no_ranking(self):
        summary = UnifiedBenchmarkSuite().get_summary([_failed("duckdb"), _failed("sqlite")])
        assert summary.fastest_platform is None
        assert summary.slowest_platform is None
        assert summary.speedup_ratio is None
        assert summary.is_comparable is False

    def test_the_all_sentinel_is_not_counted_as_a_query(self):
        summary = UnifiedBenchmarkSuite().get_summary([_succeeded("duckdb", 10.0), _failed("snowflake")])
        assert summary.total_queries == 1

    def test_one_surviving_platform_is_not_a_speedup(self):
        summary = UnifiedBenchmarkSuite().get_summary([_succeeded("duckdb", 10.0), _failed("snowflake")])
        assert summary.fastest_platform == "duckdb"
        assert summary.slowest_platform is None
        assert summary.speedup_ratio is None

    def test_two_surviving_platforms_do_produce_a_ratio(self):
        summary = UnifiedBenchmarkSuite().get_summary([_succeeded("duckdb", 10.0), _succeeded("sqlite", 40.0)])
        assert summary.fastest_platform == "duckdb"
        assert summary.slowest_platform == "sqlite"
        assert summary.speedup_ratio == pytest.approx(4.0)


class TestExitCode:
    def test_total_failure_exits_nonzero(self):
        results = [_failed("duckdb"), _failed("sqlite")]
        summary = UnifiedBenchmarkSuite().get_summary(results)
        with pytest.raises(SystemExit) as exit_info:
            _exit_on_comparison_failure(results, summary)
        assert exit_info.value.code == 1

    def test_partial_failure_exits_nonzero(self):
        results = [_succeeded("duckdb", 10.0), _failed("snowflake")]
        summary = UnifiedBenchmarkSuite().get_summary(results)
        with pytest.raises(SystemExit) as exit_info:
            _exit_on_comparison_failure(results, summary)
        assert exit_info.value.code == 1

    def test_full_success_does_not_exit(self):
        results = [_succeeded("duckdb", 10.0), _succeeded("sqlite", 40.0)]
        summary = UnifiedBenchmarkSuite().get_summary(results)
        _exit_on_comparison_failure(results, summary)


class TestTextReportNeverClaimsAnUnearnedSpeedup:
    def test_total_failure_report_says_n_a(self):
        report = UnifiedBenchmarkSuite()._generate_text_report([_failed("duckdb"), _failed("sqlite")])
        assert "1.00x" not in report
        assert "no platform produced a usable timing" in report
        assert "100%" not in report

    def test_markdown_report_says_n_a(self):
        report = UnifiedBenchmarkSuite()._generate_markdown_report([_failed("duckdb"), _failed("sqlite")])
        assert "1.00x" not in report
        assert "n/a" in report


