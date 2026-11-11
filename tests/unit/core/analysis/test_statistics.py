# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.analysis.models import SignificanceLevel
from benchbox.core.analysis.statistics import (
    apply_bonferroni_correction,
    calculate_coefficient_of_variation,
    calculate_cohens_d,
    calculate_confidence_interval,
    calculate_geometric_mean,
    calculate_mean,
    calculate_median,
    calculate_performance_metrics,
    calculate_statistical_power,
    calculate_std_dev,
    calculate_variance,
    detect_outliers_iqr,
    detect_outliers_zscore,
    interpret_p_value,
    mann_whitney_u_test,
    recommend_sample_size,
    welchs_t_test,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBasicStatistics:
    def test_calculate_mean(self):

        assert calculate_mean([1, 2, 3, 4, 5]) == 3.0
        assert calculate_mean([10]) == 10.0
        assert calculate_mean([]) == 0.0

    def test_calculate_median_odd(self):

        assert calculate_median([1, 2, 3, 4, 5]) == 3.0
        assert calculate_median([5, 1, 3]) == 3.0

    def test_calculate_median_even(self):

        assert calculate_median([1, 2, 3, 4]) == 2.5
        assert calculate_median([1, 2]) == 1.5

    def test_calculate_median_empty(self):

        assert calculate_median([]) == 0.0

    def test_calculate_geometric_mean(self):

        result = calculate_geometric_mean([2, 8])
        assert result == pytest.approx(4.0, rel=0.001)

        result = calculate_geometric_mean([1, 10, 100])
        assert result == pytest.approx(10.0, rel=0.001)

    def test_calculate_geometric_mean_empty(self):

        assert calculate_geometric_mean([]) == 0.0

    def test_calculate_geometric_mean_filters_non_positive(self):

        result = calculate_geometric_mean([2, -1, 8, 0])
        assert result == pytest.approx(4.0, rel=0.001)

    def test_calculate_std_dev(self):

        values = [2, 4, 4, 4, 5, 5, 7, 9]
        result = calculate_std_dev(values)
        expected = 2.138
        assert result == pytest.approx(expected, rel=0.01)

    def test_calculate_std_dev_insufficient_data(self):

        assert calculate_std_dev([]) == 0.0
        assert calculate_std_dev([1]) == 0.0

    def test_calculate_variance(self):

        values = [2, 4, 4, 4, 5, 5, 7, 9]
        result = calculate_variance(values)
        expected = 4.571
        assert result == pytest.approx(expected, rel=0.01)

    def test_calculate_coefficient_of_variation(self):

        values = [10, 12, 14, 16, 18]
        cv = calculate_coefficient_of_variation(values)
        assert cv == pytest.approx(0.226, rel=0.01)

    def test_calculate_cv_zero_mean(self):

        assert calculate_coefficient_of_variation([0, 0, 0]) == 0.0

    def test_calculate_cv_empty(self):

        assert calculate_coefficient_of_variation([]) == 0.0


class TestConfidenceInterval:
    def test_calculate_ci_basic(self):

        values = [100, 105, 95, 102, 98]
        ci = calculate_confidence_interval(values)

        assert ci.confidence_level == 0.95
        assert ci.point_estimate == pytest.approx(100.0, rel=0.01)
        assert ci.lower < ci.point_estimate
        assert ci.upper > ci.point_estimate

    def test_calculate_ci_contains(self):

        values = [100, 105, 95, 102, 98]
        ci = calculate_confidence_interval(values)

        assert ci.contains(ci.point_estimate) is True
        assert ci.contains(ci.lower - 1) is False
        assert ci.contains(ci.upper + 1) is False

    def test_calculate_ci_single_value(self):

        ci = calculate_confidence_interval([100])

        assert ci.point_estimate == 100.0
        assert ci.lower == 100.0
        assert ci.upper == 100.0


class TestPerformanceMetrics:
    def test_calculate_performance_metrics(self):

        values = [100, 120, 90, 110, 105]
        metrics = calculate_performance_metrics(values)

        assert metrics.mean == pytest.approx(105.0, rel=0.01)
        assert metrics.median == 105.0
        assert metrics.min_time == 90
        assert metrics.max_time == 120
        assert metrics.sample_count == 5
        assert metrics.std_dev > 0
        assert metrics.cv > 0
        assert metrics.confidence_interval is not None

    def test_calculate_performance_metrics_empty(self):

        metrics = calculate_performance_metrics([])

        assert metrics.mean == 0.0
        assert metrics.median == 0.0
        assert metrics.sample_count == 0

    def test_performance_metrics_to_dict(self):

        values = [100, 120, 90]
        metrics = calculate_performance_metrics(values)
        result = metrics.to_dict()

        assert "mean" in result
        assert "median" in result
        assert "std_dev" in result
        assert "cv" in result
        assert "sample_count" in result


class TestEffectSize:
    def test_cohens_d_large_effect(self):

        group_a = [100, 105, 95, 102, 98]
        group_b = [150, 155, 145, 152, 148]
        d = calculate_cohens_d(group_a, group_b)

        assert abs(d) > 0.8

    def test_cohens_d_small_effect(self):

        group_a = [100, 105, 95, 102, 98]
        group_b = [101, 106, 96, 103, 99]
        d = calculate_cohens_d(group_a, group_b)

        assert abs(d) < 0.5

    def test_cohens_d_insufficient_data(self):

        assert calculate_cohens_d([1], [2]) == 0.0
        assert calculate_cohens_d([], []) == 0.0


class TestStatisticalTests:
    def test_welchs_t_test_significant(self):

        group_a = [100, 102, 98, 101, 99] * 3
        group_b = [150, 152, 148, 151, 149] * 3

        result = welchs_t_test(group_a, group_b)

        assert result.test_name == "welch_t_test"
        assert result.p_value < 0.05
        assert result.significance != SignificanceLevel.NOT_SIGNIFICANT

    def test_welchs_t_test_not_significant(self):

        group_a = [100, 120, 80, 110, 90] * 2
        group_b = [102, 118, 82, 108, 92] * 2

        result = welchs_t_test(group_a, group_b)

        assert result.p_value > 0.05 or result.significance == SignificanceLevel.NOT_SIGNIFICANT

    def test_welchs_t_test_insufficient_samples(self):

        result = welchs_t_test([1, 2], [3, 4])

        assert result.p_value == 1.0
        assert result.significance == SignificanceLevel.NOT_SIGNIFICANT
        assert "Insufficient" in (result.notes or "")

    def test_mann_whitney_significant(self):

        group_a = [10, 12, 14, 16, 18, 20, 22, 24, 26, 28]
        group_b = [50, 52, 54, 56, 58, 60, 62, 64, 66, 68]

        result = mann_whitney_u_test(group_a, group_b)

        assert result.test_name == "mann_whitney_u"
        assert result.p_value < 0.05

    def test_mann_whitney_insufficient_samples(self):

        result = mann_whitney_u_test([1, 2], [3, 4])

        assert result.p_value == 1.0
        assert "Insufficient" in (result.notes or "")


class TestPValueInterpretation:
    def test_interpret_not_significant(self):

        assert interpret_p_value(0.10) == SignificanceLevel.NOT_SIGNIFICANT
        assert interpret_p_value(0.05) == SignificanceLevel.NOT_SIGNIFICANT

    def test_interpret_significant(self):

        assert interpret_p_value(0.049) == SignificanceLevel.SIGNIFICANT
        assert interpret_p_value(0.02) == SignificanceLevel.SIGNIFICANT

    def test_interpret_highly_significant(self):

        assert interpret_p_value(0.009) == SignificanceLevel.HIGHLY_SIGNIFICANT

    def test_interpret_very_highly_significant(self):

        assert interpret_p_value(0.0009) == SignificanceLevel.VERY_HIGHLY_SIGNIFICANT


class TestOutlierDetection:
    def test_detect_outliers_iqr(self):

        values = [10, 12, 14, 16, 18, 100]
        outliers = detect_outliers_iqr(values)

        assert len(outliers) == 1
        outlier_value = outliers[0][1]
        assert outlier_value == 100

    def test_detect_outliers_iqr_no_outliers(self):

        values = [10, 12, 14, 16, 18]
        outliers = detect_outliers_iqr(values)

        assert len(outliers) == 0

    def test_detect_outliers_iqr_insufficient_data(self):

        values = [1, 2, 3]
        outliers = detect_outliers_iqr(values)

        assert len(outliers) == 0

    def test_detect_outliers_zscore(self):

        values = [10, 12, 14, 16, 18, 100]
        outliers = detect_outliers_zscore(values, threshold=2.0)

        assert len(outliers) >= 1
        outlier_values = [o[1] for o in outliers]
        assert 100 in outlier_values

    def test_detect_outliers_zscore_no_outliers(self):

        values = [10, 11, 12, 13, 14]
        outliers = detect_outliers_zscore(values)

        assert len(outliers) == 0


class TestBonferroniCorrection:
    def test_bonferroni_basic(self):

        p_values = [0.01, 0.02, 0.03]
        corrected = apply_bonferroni_correction(p_values)

        assert corrected[0] == pytest.approx(0.03, rel=0.001)
        assert corrected[1] == pytest.approx(0.06, rel=0.001)
        assert corrected[2] == pytest.approx(0.09, rel=0.001)

    def test_bonferroni_capped_at_one(self):
        p_values = [0.5, 0.6]
        corrected = apply_bonferroni_correction(p_values)

        assert corrected[0] == 1.0
        assert corrected[1] == 1.0

    def test_bonferroni_empty(self):

        assert apply_bonferroni_correction([]) == []


class TestStatisticalPower:
    def test_calculate_power_large_effect(self):

        power = calculate_statistical_power(effect_size=0.8, sample_size=30)
        assert power > 0.7

    def test_calculate_power_small_effect(self):

        power = calculate_statistical_power(effect_size=0.2, sample_size=10)
        assert power < 0.5

    def test_calculate_power_zero_effect(self):

        power = calculate_statistical_power(effect_size=0.0, sample_size=100)
        assert power == 0.0

    def test_recommend_sample_size_large_effect(self):

        n = recommend_sample_size(effect_size=0.8)
        assert n < 30

    def test_recommend_sample_size_small_effect(self):

        n = recommend_sample_size(effect_size=0.2)
        assert n > 100

    def test_recommend_sample_size_zero_effect(self):

        n = recommend_sample_size(effect_size=0.0)
        assert n == 1000


