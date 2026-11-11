# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
from datetime import datetime

import pytest

from benchbox.core.analysis.comparison import (
    ComparisonConfig,
    PlatformComparison,
    _extract_query_times,
    _get_query_times_for_query,
    _is_comparable,
    _normalize_execution_time_ms,
)
from benchbox.core.results.models import BenchmarkResults

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def create_mock_result(
    platform: str,
    query_times: dict[str, float],
    benchmark_name: str = "TPC-H",
    scale_factor: float = 10.0,
    cost_summary: dict | None = None,
) -> BenchmarkResults:
    query_results = [
        {"query_id": qid, "execution_time_ms": time, "status": "success"} for qid, time in query_times.items()
    ]

    return BenchmarkResults(
        benchmark_name=benchmark_name,
        platform=platform,
        scale_factor=scale_factor,
        execution_id=f"{platform}-test-001",
        timestamp=datetime.now(),
        duration_seconds=sum(query_times.values()) / 1000,
        total_queries=len(query_times),
        successful_queries=len(query_times),
        failed_queries=0,
        query_results=query_results,
        total_execution_time=sum(query_times.values()),
        average_query_time=sum(query_times.values()) / len(query_times) if query_times else 0,
        cost_summary=cost_summary,
    )


class TestComparisonConfig:
    def test_default_config(self):

        config = ComparisonConfig()

        assert config.significance_level == 0.05
        assert config.min_sample_size == 3
        assert config.outlier_method == "iqr"
        assert config.performance_ratio_threshold == 1.05
        assert config.apply_bonferroni is True

    def test_custom_config(self):

        config = ComparisonConfig(
            significance_level=0.01,
            outlier_method="zscore",
            apply_bonferroni=False,
        )

        assert config.significance_level == 0.01
        assert config.outlier_method == "zscore"
        assert config.apply_bonferroni is False


class TestExecutionTimeNormalization:
    @pytest.mark.parametrize(
        ("timing", "expected_ms"),
        [
            ({"execution_time_ms": 2000.0}, 2000.0),
            ({"execution_time_seconds": 2.0}, 2000.0),
            ({"execution_time": 2.0}, 2000.0),
            ({"execution_time_ms": 0, "execution_time_seconds": 0}, 0.0),
            (
                {
                    "execution_time_ms": 2000,
                    "execution_time_seconds": 2.0009,
                    "execution_time": 2.0009,
                },
                2000.9,
            ),
            ({"execution_time_ms": 0, "execution_time_seconds": 0.0009}, 0.9),
            ({"execution_time_ms": 1, "execution_time_seconds": 0.0019}, 1.9),
        ],
    )
    def test_normalizes_explicit_units(self, timing, expected_ms):
        assert _normalize_execution_time_ms(timing) == pytest.approx(expected_ms)

    def test_missing_duration_is_not_changed_to_zero(self):
        assert _normalize_execution_time_ms({}) is None

    def test_conflicting_representations_fail_closed(self):
        with pytest.raises(ValueError, match="Conflicting query duration representations"):
            _normalize_execution_time_ms(
                {
                    "execution_time_ms": 2.0,
                    "execution_time_seconds": 2.0,
                }
            )

    def test_seconds_are_normalized_in_both_comparison_extractors(self):
        result = create_mock_result("duckdb", {})
        result.query_results = [{"query_id": "Q1", "execution_time_seconds": 2.0}]
        result.per_query_timings = [{"query_id": "Q2", "execution_time": 3.0}]

        assert _extract_query_times(result) == {"Q1": 2000.0, "Q2": 3000.0}
        assert _get_query_times_for_query(result, "Q1") == [2000.0]
        assert _get_query_times_for_query(result, "Q2") == [3000.0]

    def test_zero_is_preserved_in_both_comparison_extractors(self):
        result = create_mock_result("duckdb", {})
        result.query_results = [{"query_id": "Q1", "execution_time_ms": 0}]
        result.per_query_timings = [{"query_id": "Q2", "execution_time_seconds": 0}]

        assert _extract_query_times(result) == {"Q1": 0.0, "Q2": 0.0}
        assert _get_query_times_for_query(result, "Q1") == [0.0]
        assert _get_query_times_for_query(result, "Q2") == [0.0]

    def test_failed_queries_are_excluded_from_comparison_samples(self):
        result = create_mock_result("duckdb", {})
        result.query_results = [
            {"query_id": "Q1", "execution_time_ms": 100.0, "status": "FAILED"},
            {"query_id": "Q2", "execution_time_ms": 0.0, "status": "SUCCESS"},
        ]
        result.per_query_timings = [{"query_id": "Q3", "execution_time_seconds": 0.5, "status": "failed"}]

        assert _extract_query_times(result) == {"Q2": 0.0}
        assert _get_query_times_for_query(result, "Q1") == []
        assert _get_query_times_for_query(result, "Q2") == [0.0]
        assert _get_query_times_for_query(result, "Q3") == []

    def test_repeated_query_samples_use_order_independent_arithmetic_mean(self):
        result = create_mock_result("duckdb", {})
        result.query_results = [{"query_id": "Q1", "execution_time_ms": 100.0}]
        result.per_query_timings = [
            {"query_id": "Q1", "execution_time_seconds": 0.2},
            {"query_id": "Q1", "execution_time": 0.3},
        ]

        assert _extract_query_times(result) == {"Q1": 200.0}

        result.per_query_timings.reverse()
        assert _extract_query_times(result) == {"Q1": 200.0}


class TestPlatformComparison:
    @pytest.fixture
    def duckdb_result(self) -> BenchmarkResults:
        return create_mock_result(
            platform="duckdb",
            query_times={
                "Q1": 100.0,
                "Q2": 150.0,
                "Q3": 200.0,
                "Q4": 120.0,
                "Q5": 180.0,
            },
        )

    @pytest.fixture
    def clickhouse_result(self) -> BenchmarkResults:
        return create_mock_result(
            platform="clickhouse",
            query_times={
                "Q1": 120.0,
                "Q2": 180.0,
                "Q3": 160.0,
                "Q4": 140.0,
                "Q5": 200.0,
            },
        )

    @pytest.fixture
    def snowflake_result(self) -> BenchmarkResults:
        return create_mock_result(
            platform="snowflake",
            query_times={
                "Q1": 200.0,
                "Q2": 250.0,
                "Q3": 300.0,
                "Q4": 220.0,
                "Q5": 280.0,
            },
        )

    def test_init_requires_results(self):

        with pytest.raises(ValueError, match="At least one"):
            PlatformComparison([])

    def test_init_with_results(self, duckdb_result, clickhouse_result):

        comparison = PlatformComparison([duckdb_result, clickhouse_result])

        assert len(comparison.results) == 2
        assert comparison.platforms == ["duckdb", "clickhouse"]

    def test_platforms_property(self, duckdb_result, clickhouse_result, snowflake_result):

        comparison = PlatformComparison([duckdb_result, clickhouse_result, snowflake_result])

        assert len(comparison.platforms) == 3
        assert "duckdb" in comparison.platforms
        assert "clickhouse" in comparison.platforms
        assert "snowflake" in comparison.platforms

    def test_benchmark_name_property(self, duckdb_result):

        comparison = PlatformComparison([duckdb_result])
        assert comparison.benchmark_name == "TPC-H"

    def test_scale_factor_property(self, duckdb_result):

        comparison = PlatformComparison([duckdb_result])
        assert comparison.scale_factor == 10.0

    def test_direct_constructor_converts_query_seconds_to_milliseconds(self):
        seconds_result = create_mock_result("duckdb", {})
        seconds_result.query_results = [{"query_id": "Q1", "execution_time_seconds": 2.0}]
        milliseconds_result = create_mock_result("sqlite", {"Q1": 2500.0})

        report = PlatformComparison([seconds_result, milliseconds_result]).compare()
        rankings = {ranking.platform: ranking for ranking in report.rankings}

        assert rankings["duckdb"].geometric_mean_time == pytest.approx(2000.0)
        assert report.query_comparisons["Q1"].metrics["duckdb"].mean == 2000.0

    def test_from_files_preserves_canonical_v2_milliseconds(self, tmp_path):

        def write_result(platform: str, duration_ms: float):
            path = tmp_path / f"{platform}.json"
            path.write_text(
                json.dumps(
                    {
                        "version": "2.0",
                        "run": {"id": f"{platform}-run", "timestamp": "2026-08-08T00:00:00"},
                        "benchmark": {"name": "TPC-H", "scale_factor": 1.0},
                        "platform": {"name": platform},
                        "summary": {"queries": {"total": 1, "passed": 1, "failed": 0}},
                        "queries": [{"id": "Q1", "ms": duration_ms}],
                    }
                ),
                encoding="utf-8",
            )
            return path

        comparison = PlatformComparison.from_files(
            [
                write_result("duckdb", 2000.0),
                write_result("sqlite", 2500.0),
            ]
        )
        report = comparison.compare()
        rankings = {ranking.platform: ranking for ranking in report.rankings}

        assert rankings["duckdb"].geometric_mean_time == pytest.approx(2000.0)
        assert report.query_comparisons["Q1"].metrics["duckdb"].mean == 2000.0


class TestPlatformComparisonValidation:
    def test_validate_single_result(self):

        result = create_mock_result("duckdb", {"Q1": 100.0})
        comparison = PlatformComparison([result])

        validation = comparison.validate()

        assert validation.is_valid is False
        assert any("two results" in e for e in validation.errors)

    def test_validate_mismatched_benchmarks(self):

        result1 = create_mock_result("duckdb", {"Q1": 100.0}, benchmark_name="TPC-H")
        result2 = create_mock_result("clickhouse", {"Q1": 120.0}, benchmark_name="TPC-DS")
        comparison = PlatformComparison([result1, result2])

        validation = comparison.validate()

        assert validation.is_valid is False
        assert any("different benchmarks" in e for e in validation.errors)

    def test_validate_different_scale_factors(self):

        result1 = create_mock_result("duckdb", {"Q1": 100.0}, scale_factor=10.0)
        result2 = create_mock_result("clickhouse", {"Q1": 120.0}, scale_factor=100.0)
        comparison = PlatformComparison([result1, result2])

        validation = comparison.validate()

        assert any("scale factors" in w for w in validation.warnings)

    def test_validate_no_common_queries(self):

        result1 = create_mock_result("duckdb", {"Q1": 100.0, "Q2": 150.0})
        result2 = create_mock_result("clickhouse", {"Q3": 120.0, "Q4": 180.0})
        comparison = PlatformComparison([result1, result2])

        validation = comparison.validate()

        assert validation.is_valid is False
        assert any("No common queries" in e for e in validation.errors)

    def test_validate_partial_query_overlap(self):

        result1 = create_mock_result("duckdb", {"Q1": 100.0, "Q2": 150.0, "Q3": 200.0})
        result2 = create_mock_result("clickhouse", {"Q1": 120.0, "Q2": 180.0})
        comparison = PlatformComparison([result1, result2])

        validation = comparison.validate()

        assert validation.is_valid is True
        assert any("missing" in w.lower() for w in validation.warnings)
        assert "Q1" in validation.common_queries
        assert "Q2" in validation.common_queries


class TestPlatformComparisonResults:
    @pytest.fixture
    def two_platform_comparison(self) -> PlatformComparison:
        duckdb = create_mock_result(
            "duckdb",
            {"Q1": 100.0, "Q2": 150.0, "Q3": 200.0, "Q4": 120.0, "Q5": 180.0},
        )
        clickhouse = create_mock_result(
            "clickhouse",
            {"Q1": 150.0, "Q2": 200.0, "Q3": 250.0, "Q4": 170.0, "Q5": 220.0},
        )
        return PlatformComparison([duckdb, clickhouse])

    @pytest.fixture
    def three_platform_comparison(self) -> PlatformComparison:
        duckdb = create_mock_result(
            "duckdb",
            {"Q1": 100.0, "Q2": 150.0, "Q3": 200.0},
        )
        clickhouse = create_mock_result(
            "clickhouse",
            {"Q1": 150.0, "Q2": 200.0, "Q3": 250.0},
        )
        snowflake = create_mock_result(
            "snowflake",
            {"Q1": 200.0, "Q2": 250.0, "Q3": 300.0},
        )
        return PlatformComparison([duckdb, clickhouse, snowflake])

    def test_compare_produces_report(self, two_platform_comparison):
        report = two_platform_comparison.compare()

        assert report is not None
        assert report.benchmark_name == "TPC-H"
        assert report.scale_factor == 10.0
        assert len(report.platforms) == 2

    def test_compare_identifies_winner(self, two_platform_comparison):
        report = two_platform_comparison.compare()

        assert report.winner == "duckdb"

    def test_compare_rankings(self, three_platform_comparison):
        report = three_platform_comparison.compare()

        assert len(report.rankings) == 3
        assert report.rankings[0].platform == "duckdb"
        assert report.rankings[0].rank == 1
        assert report.rankings[1].platform == "clickhouse"
        assert report.rankings[1].rank == 2
        assert report.rankings[2].platform == "snowflake"
        assert report.rankings[2].rank == 3

    def test_compare_query_comparisons(self, two_platform_comparison):
        report = two_platform_comparison.compare()

        assert len(report.query_comparisons) == 5
        assert "Q1" in report.query_comparisons
        assert "Q2" in report.query_comparisons

        q1_comp = report.query_comparisons["Q1"]
        assert q1_comp.winner == "duckdb"
        assert "duckdb" in q1_comp.metrics
        assert "clickhouse" in q1_comp.metrics

    def test_compare_win_loss_matrix(self, two_platform_comparison):
        report = two_platform_comparison.compare()

        assert "duckdb" in report.win_loss_matrix
        assert "clickhouse" in report.win_loss_matrix

        duckdb_record = report.win_loss_matrix["duckdb"]
        assert duckdb_record.total == 5
        assert duckdb_record.wins >= 0

    def test_compare_head_to_head(self, two_platform_comparison):
        report = two_platform_comparison.compare()

        assert len(report.head_to_head) >= 1
        h2h = report.head_to_head[0]
        assert h2h.platform_a in ["duckdb", "clickhouse"]
        assert h2h.platform_b in ["duckdb", "clickhouse"]

    def test_compare_generates_insights(self, two_platform_comparison):
        report = two_platform_comparison.compare()

        assert len(report.insights) > 0

    def test_compare_by_query(self, two_platform_comparison):

        query_comparisons = two_platform_comparison.compare_by_query()

        assert isinstance(query_comparisons, dict)
        assert len(query_comparisons) == 5

    def test_get_head_to_head(self, two_platform_comparison):

        h2h = two_platform_comparison.get_head_to_head("duckdb", "clickhouse")

        assert h2h is not None
        assert h2h.platform_a in ["duckdb", "clickhouse"]
        assert h2h.platform_b in ["duckdb", "clickhouse"]

    def test_get_head_to_head_not_found(self, two_platform_comparison):

        h2h = two_platform_comparison.get_head_to_head("duckdb", "nonexistent")

        assert h2h is None


class TestComparisonWithCost:
    def test_compare_with_cost_analysis(self):

        duckdb = create_mock_result(
            "duckdb",
            {"Q1": 100.0, "Q2": 150.0, "Q3": 200.0},
            cost_summary={"total_cost": 0.50},
        )
        snowflake = create_mock_result(
            "snowflake",
            {"Q1": 200.0, "Q2": 250.0, "Q3": 300.0},
            cost_summary={"total_cost": 2.00},
        )
        comparison = PlatformComparison([duckdb, snowflake])

        report = comparison.compare()

        assert report.cost_analysis is not None
        assert "duckdb" in report.cost_analysis.platforms
        assert "snowflake" in report.cost_analysis.platforms
        assert report.cost_analysis.best_value == "duckdb"

    def test_compare_cost_performance_method(self):

        duckdb = create_mock_result(
            "duckdb",
            {"Q1": 100.0},
            cost_summary={"total_cost": 0.50},
        )
        snowflake = create_mock_result(
            "snowflake",
            {"Q1": 200.0},
            cost_summary={"total_cost": 2.00},
        )
        duckdb.total_execution_time = 2.0
        snowflake.total_execution_time = 4.0
        comparison = PlatformComparison([duckdb, snowflake])

        cost_analysis = comparison.compare_cost_performance()

        assert cost_analysis is not None
        assert cost_analysis.best_value == "duckdb"
        assert cost_analysis.performance_per_dollar == {
            "duckdb": 1.0,
            "snowflake": 0.125,
        }

    def test_cost_per_query_uses_the_cost_platforms_query_count(self):
        no_cost = create_mock_result("duckdb", {"Q1": 100.0})
        with_cost = create_mock_result(
            "snowflake",
            {"Q1": 200.0, "Q2": 250.0},
            cost_summary={"total_cost": 2.0},
        )
        with_cost.total_execution_time = 4.0

        cost_analysis = PlatformComparison([no_cost, with_cost]).compare_cost_performance()

        assert cost_analysis is not None
        assert cost_analysis.cost_per_query == {"snowflake": 1.0}


class TestIsComparableHelper:
    def test_success_and_unknown_are_comparable(self):
        from types import SimpleNamespace

        assert _is_comparable(SimpleNamespace(status="SUCCESS")) is True
        assert _is_comparable(SimpleNamespace(status="UNKNOWN")) is True

    def test_failed_and_skipped_are_not_comparable(self):
        from types import SimpleNamespace

        assert _is_comparable(SimpleNamespace(status="FAILED")) is False
        assert _is_comparable(SimpleNamespace(status="SKIPPED")) is False


class TestComparisonReportSerialization:
    def test_report_to_dict(self):

        duckdb = create_mock_result("duckdb", {"Q1": 100.0, "Q2": 150.0})
        clickhouse = create_mock_result("clickhouse", {"Q1": 150.0, "Q2": 200.0})
        comparison = PlatformComparison([duckdb, clickhouse])
        report = comparison.compare()

        result = report.to_dict()

        assert isinstance(result, dict)
        assert "benchmark_name" in result
        assert "platforms" in result
        assert "winner" in result
        assert "rankings" in result
        assert "query_comparisons" in result


