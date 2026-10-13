from __future__ import annotations

import pytest

from benchbox.core.results.regression_policy import (
    DEFAULT_REGRESSION_THRESHOLD_PERCENT,
    SEVERITIES,
    TREND_DIRECTIONS,
    TREND_SIGNIFICANCE_PERCENT,
    classify_change,
    classify_severity,
    classify_trend,
    is_meaningful_improvement,
    is_regression,
    percent_change,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SHARED_DELTAS = [-150.0, -100.0, -50.0, -10.1, -10.0, -5.1, -5.0, 0.0, 5.0, 5.1, 10.0, 10.1, 25.0, 50.0, 100.0, 250.0]


class TestPolicyIsSelfConsistent:
    @pytest.mark.parametrize("delta", SHARED_DELTAS)
    def test_classification_and_predicate_agree(self, delta: float):
        assert is_regression(delta) is (classify_change(delta) == "regression")

    @pytest.mark.parametrize("delta", SHARED_DELTAS)
    def test_severity_is_always_a_declared_band(self, delta: float):
        assert classify_severity(delta) in SEVERITIES

    def test_thresholds_are_exclusive_at_the_boundary(self):
        assert classify_change(DEFAULT_REGRESSION_THRESHOLD_PERCENT) == "stable"
        assert classify_change(DEFAULT_REGRESSION_THRESHOLD_PERCENT + 0.1) == "regression"
        assert classify_change(-DEFAULT_REGRESSION_THRESHOLD_PERCENT) == "stable"
        assert classify_change(-DEFAULT_REGRESSION_THRESHOLD_PERCENT - 0.1) == "improvement"

    def test_severity_ladder_boundaries(self):
        assert classify_severity(100.0) == "critical"
        assert classify_severity(99.9) == "high"
        assert classify_severity(50.0) == "high"
        assert classify_severity(49.9) == "medium"
        assert classify_severity(25.0) == "medium"
        assert classify_severity(24.9) == "low"

    def test_unusable_baseline_is_not_a_regression(self):
        assert percent_change(0.0, 100.0) is None
        assert percent_change(-1.0, 100.0) is None
        assert is_regression(None) is False

    @pytest.mark.parametrize(
        ("values", "direction"),
        [
            ([], "insufficient_data"),
            ([100.0], "insufficient_data"),
            ([0.0, 100.0], "unknown"),
            ([100.0, 90.0], "improving"),
            ([100.0, 110.0], "degrading"),
            ([100.0, 103.0], "stable"),
            ([100.0, 105.0], "stable"),
            ([100.0, 95.0], "stable"),
        ],
    )
    def test_trend_directions(self, values: list[float], direction: str):
        assert classify_trend(values)[0] == direction
        assert classify_trend(values)[0] in TREND_DIRECTIONS

    def test_trend_reports_zero_rather_than_a_fabricated_percentage(self):
        assert classify_trend([])[1] == 0.0
        assert classify_trend([0.0, 100.0])[1] == 0.0

    def test_improvement_label_uses_the_same_significance_cutoff(self):
        assert is_meaningful_improvement(-TREND_SIGNIFICANCE_PERCENT) is False
        assert is_meaningful_improvement(-TREND_SIGNIFICANCE_PERCENT - 0.1) is True
        assert is_meaningful_improvement(None) is False
        assert classify_trend([100.0, 100.0 - TREND_SIGNIFICANCE_PERCENT - 0.1])[0] == "improving"


class TestThreeSurfaceRegressionParity:
    @pytest.mark.parametrize("delta", SHARED_DELTAS)
    def test_cli_compare_and_core_agree(self, delta: float):
        from benchbox.cli.commands.compare import _check_regression

        comparison = {"query_comparisons": [{"change_percent": delta}], "performance_changes": {}}
        cli_verdict = _check_regression(comparison, DEFAULT_REGRESSION_THRESHOLD_PERCENT / 100)

        assert cli_verdict is is_regression(delta)

    @pytest.mark.parametrize("delta", SHARED_DELTAS)
    def test_mcp_analytics_and_core_agree(self, delta: float):
        from benchbox.mcp.tools.analytics import _classify_query_changes

        baseline = 100.0
        current = baseline * (1 + delta / 100)
        regressions, improvements, stable = _classify_query_changes(
            {"1": baseline}, {"1": current}, DEFAULT_REGRESSION_THRESHOLD_PERCENT
        )

        actual_delta = percent_change(baseline, current)
        assert actual_delta is not None
        expected = classify_change(actual_delta)
        assert bool(regressions) is (expected == "regression")
        assert bool(improvements) is (expected == "improvement")
        assert bool(stable) is (expected == "stable")

    @pytest.mark.parametrize("delta", [d for d in SHARED_DELTAS if d > 0])
    def test_mcp_severity_matches_core(self, delta: float):
        from benchbox.mcp.tools.analytics import _classify_query_changes

        baseline = 100.0
        current = baseline * (1 + delta / 100)
        regressions, _, _ = _classify_query_changes(
            {"1": baseline}, {"1": current}, DEFAULT_REGRESSION_THRESHOLD_PERCENT
        )

        if regressions:
            actual_delta = percent_change(baseline, current)
            assert actual_delta is not None
            assert regressions[0]["severity"] == classify_severity(actual_delta)

    @pytest.mark.parametrize("delta", SHARED_DELTAS)
    def test_results_database_and_core_agree(self, delta: float):
        from benchbox.core.results import database as database_module

        assert database_module.is_regression(delta) is is_regression(delta)

    def test_all_three_surfaces_agree_on_one_shared_delta(self):
        from benchbox.cli.commands.compare import _check_regression
        from benchbox.mcp.tools.analytics import _classify_query_changes

        delta = 12.0
        cli = _check_regression({"query_comparisons": [{"change_percent": delta}]}, 0.10)
        regressions, _, _ = _classify_query_changes({"1": 100.0}, {"1": 112.0}, 10.0)

        assert cli is True
        assert len(regressions) == 1
        assert is_regression(delta) is True


class TestNoSurfaceKeepsALocalPolicy:
    def test_surface_modules_do_not_redefine_policy_helpers(self):
        import benchbox.cli.commands.compare as compare_module
        import benchbox.cli.commands.report as report_module
        import benchbox.mcp.tools.analytics as analytics_module

        for module in (compare_module, report_module, analytics_module):
            for banned in ("_classify_regression_severity", "_compute_trend_direction"):
                assert not hasattr(module, banned), f"{module.__name__}.{banned}"


class TestReportThresholdIsNoLongerInert:
    @staticmethod
    def _trend(change_pct: float | None, *, is_regression_flag: bool):
        from datetime import datetime, timezone

        from benchbox.core.results.database import PerformanceTrend

        moment = datetime(2026, 8, 4, tzinfo=timezone.utc)
        return PerformanceTrend(
            platform="duckdb",
            benchmark="tpch",
            scale_factor=1.0,
            period_start=moment,
            period_end=moment,
            avg_geometric_mean_ms=100.0,
            min_geometric_mean_ms=90.0,
            max_geometric_mean_ms=110.0,
            sample_count=3,
            change_pct=change_pct,
            is_regression=is_regression_flag,
        )

    def _detect(self, tmp_path, trend, threshold: float):
        from unittest.mock import patch

        from benchbox.core.results.database import ResultDatabase

        db = ResultDatabase(tmp_path / "results.db")
        with (
            patch.object(ResultDatabase, "get_platforms", return_value=["duckdb"]),
            patch.object(ResultDatabase, "get_benchmarks", return_value=["tpch"]),
            patch.object(ResultDatabase, "_distinct_variants", return_value=[None]),
            patch.object(ResultDatabase, "get_performance_trends", return_value=[trend]),
            patch.object(db, "_connection") as connection,
        ):
            cursor = connection.return_value.__enter__.return_value.cursor.return_value
            cursor.fetchall.return_value = [(1.0,)]
            return db.detect_regressions(threshold_pct=threshold)

    def test_a_sub_default_threshold_now_selects_regressions(self, tmp_path):
        trend = self._trend(7.0, is_regression_flag=False)

        found = self._detect(tmp_path, trend, threshold=5.0)

        assert len(found) == 1
        assert found[0].change_pct == 7.0

    def test_the_returned_record_agrees_with_the_filter(self, tmp_path):
        trend = self._trend(7.0, is_regression_flag=False)

        found = self._detect(tmp_path, trend, threshold=5.0)

        assert found[0].is_regression is True

    def test_a_change_under_the_threshold_is_still_excluded(self, tmp_path):
        trend = self._trend(3.0, is_regression_flag=False)

        assert self._detect(tmp_path, trend, threshold=5.0) == []

    def test_the_default_threshold_is_unchanged(self, tmp_path):
        assert self._detect(tmp_path, self._trend(12.0, is_regression_flag=True), threshold=10.0)
        assert self._detect(tmp_path, self._trend(7.0, is_regression_flag=False), threshold=10.0) == []

    def test_a_missing_change_is_not_a_regression(self, tmp_path):
        trend = self._trend(None, is_regression_flag=False)

        assert self._detect(tmp_path, trend, threshold=5.0) == []

    def test_thresholds_at_or_above_the_default_are_unchanged(self, tmp_path):
        superseded = lambda change, threshold: (change > 10) and (change > threshold)

        for change in (0.0, 5.0, 7.0, 11.0, 25.0, 120.0):
            for threshold in (10.0, 15.0, 20.0):
                trend = self._trend(change, is_regression_flag=change > 10)
                selected = bool(self._detect(tmp_path, trend, threshold=threshold))
                assert selected is superseded(change, threshold), (change, threshold)
