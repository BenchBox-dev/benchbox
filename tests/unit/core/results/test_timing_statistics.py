from __future__ import annotations

import math
import statistics

import pytest

from benchbox.core.results.metrics import (
    NAMED_METRICS,
    TimingStatsCalculator,
    calculate_named_metric,
    geometric_mean_ms,
    percentile_ms,
    sample_stdev_ms,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


FIXTURES: dict[str, list[float]] = {
    "tpch22": [12, 45, 8, 120, 33, 5, 67, 89, 15, 200, 42, 7, 310, 55, 23, 98, 140, 11, 76, 29, 64, 180],
    "even4": [10.0, 20.0, 30.0, 40.0],
    "odd5": [10.0, 20.0, 30.0, 40.0, 50.0],
    "single": [42.0],
    "uniform100": [float(i) for i in range(1, 101)],
}


CANONICAL_PERCENTILES: dict[str, dict[str, float]] = {
    "tpch22": {"p50": 45.0, "p95": 200.0, "p99": 310.0},
    "even4": {"p50": 20.0, "p95": 40.0, "p99": 40.0},
    "odd5": {"p50": 30.0, "p95": 50.0, "p99": 50.0},
    "single": {"p50": 42.0, "p95": 42.0, "p99": 42.0},
    "uniform100": {"p50": 50.0, "p95": 95.0, "p99": 99.0},
}


SUPERSEDED_PERCENTILES: dict[str, dict[str, dict[str, float]]] = {
    "tpch22": {
        "mcp": {"p50": 50.0, "p95": 199.0, "p99": 286.9},
        "cli": {"p50": 50.0, "p95": 293.5, "p99": 310.0},
    },
    "even4": {
        "mcp": {"p50": 25.0, "p95": 38.5, "p99": 39.7},
        "cli": {"p50": 25.0, "p95": 40.0, "p99": 40.0},
    },
    "odd5": {
        "mcp": {"p50": 30.0, "p95": 48.0, "p99": 49.6},
        "cli": {"p50": 30.0, "p95": 50.0, "p99": 50.0},
    },
    "single": {
        "mcp": {"p50": 42.0, "p95": 42.0, "p99": 42.0},
        "cli": {"p50": 42.0, "p95": 42.0, "p99": 42.0},
    },
    "uniform100": {
        "mcp": {"p50": 50.5, "p95": 95.05, "p99": 99.01},
        "cli": {"p50": 50.5, "p95": 95.95, "p99": 99.99},
    },
}


def _superseded_mcp_percentile(data: list[float], p: float) -> float:
    if not data:
        return 0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100)
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    if f == c:
        return sorted_data[f]
    return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


def _superseded_cli_percentiles(times: list[float]) -> dict[str, float]:
    return {
        "p50": statistics.median(times),
        "p95": statistics.quantiles(times, n=20)[18] if len(times) >= 20 else max(times),
        "p99": statistics.quantiles(times, n=100)[98] if len(times) >= 100 else max(times),
    }


class TestSupersededImplementationsAreCharacterized:
    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_recorded_mcp_values_match_the_deleted_implementation(self, name: str):
        times = FIXTURES[name]
        recorded = SUPERSEDED_PERCENTILES[name]["mcp"]

        for key, percentile in (("p50", 50), ("p95", 95), ("p99", 99)):
            assert _superseded_mcp_percentile(times, percentile) == pytest.approx(recorded[key])

    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_recorded_cli_values_match_the_deleted_implementation(self, name: str):
        times = FIXTURES[name]
        recorded = SUPERSEDED_PERCENTILES[name]["cli"]

        assert _superseded_cli_percentiles(times) == pytest.approx(recorded)

    def test_the_migration_actually_changed_something(self):
        changed = [
            (name, surface, key)
            for name, surfaces in SUPERSEDED_PERCENTILES.items()
            for surface, values in surfaces.items()
            for key, old in values.items()
            if old != pytest.approx(CANONICAL_PERCENTILES[name][key])
        ]
        assert changed, "no recorded percentile changed; the delta table is stale"


class TestCanonicalPercentile:
    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_matches_the_pinned_canonical_values(self, name: str):
        times = FIXTURES[name]
        expected = CANONICAL_PERCENTILES[name]

        assert percentile_ms(times, 0.50) == pytest.approx(expected["p50"])
        assert percentile_ms(times, 0.95) == pytest.approx(expected["p95"])
        assert percentile_ms(times, 0.99) == pytest.approx(expected["p99"])

    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_always_returns_an_observed_measurement(self, name: str):
        times = FIXTURES[name]

        for p in (0.0, 0.5, 0.9, 0.95, 0.99, 1.0):
            assert percentile_ms(times, p) in times

    def test_empty_input_is_zero_not_an_error(self):
        assert percentile_ms([], 0.95) == 0.0

    def test_rank_is_clamped_at_both_ends(self):
        times = [10.0, 20.0, 30.0]

        assert percentile_ms(times, 0.0) == 10.0
        assert percentile_ms(times, 1.0) == 30.0

    @pytest.mark.parametrize("p", [95, 50, 99, 100, 1.01, -0.01, -1])
    def test_out_of_range_percentile_is_rejected_not_clamped(self, p: float):
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            percentile_ms([10.0, 20.0, 30.0], p)

    def test_the_range_guard_runs_before_the_empty_shortcut(self):
        with pytest.raises(ValueError):
            percentile_ms([], 95)

    def test_input_order_does_not_matter(self):
        assert percentile_ms([30.0, 10.0, 20.0], 0.95) == percentile_ms([10.0, 20.0, 30.0], 0.95)

    def test_caller_sequence_is_not_mutated(self):
        times = [30.0, 10.0, 20.0]
        percentile_ms(times, 0.95)

        assert times == [30.0, 10.0, 20.0]


class TestCanonicalGeometricMean:
    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_agrees_with_stdlib_over_positive_inputs(self, name: str):
        times = FIXTURES[name]

        assert geometric_mean_ms(times) == pytest.approx(statistics.geometric_mean(times))

    def test_non_positive_values_are_excluded_not_fatal(self):
        times = [0.0, 10.0, 40.0]
        superseded_mcp = math.exp(sum(math.log(t) for t in times if t > 0) / len(times))

        assert geometric_mean_ms(times) == pytest.approx(20.0)
        assert superseded_mcp == pytest.approx(7.368063)

    def test_all_non_positive_is_zero(self):
        assert geometric_mean_ms([0.0, -1.0]) == 0.0

    def test_empty_is_zero(self):
        assert geometric_mean_ms([]) == 0.0


class TestCanonicalStandardDeviation:
    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_is_the_sample_standard_deviation(self, name: str):
        times = FIXTURES[name]
        expected = statistics.stdev(times) if len(times) > 1 else 0.0

        assert sample_stdev_ms(times) == pytest.approx(expected)

    def test_single_value_is_zero_not_an_error(self):
        assert sample_stdev_ms([42.0]) == 0.0

    def test_empty_is_zero(self):
        assert sample_stdev_ms([]) == 0.0


class TestNamedMetricVocabulary:
    @pytest.mark.parametrize("metric", NAMED_METRICS)
    def test_every_declared_name_resolves(self, metric: str):
        assert calculate_named_metric(FIXTURES["tpch22"], metric) > 0

    def test_named_metrics_agree_with_the_direct_functions(self):
        times = FIXTURES["tpch22"]

        assert calculate_named_metric(times, "geometric_mean") == geometric_mean_ms(times)
        assert calculate_named_metric(times, "p50") == percentile_ms(times, 0.50)
        assert calculate_named_metric(times, "p95") == percentile_ms(times, 0.95)
        assert calculate_named_metric(times, "p99") == percentile_ms(times, 0.99)
        assert calculate_named_metric(times, "total_time") == sum(times)

    def test_unknown_metric_falls_back_to_the_arithmetic_mean(self):
        times = FIXTURES["tpch22"]

        assert calculate_named_metric(times, "not_a_metric") == pytest.approx(statistics.mean(times))

    def test_empty_input_is_zero(self):
        assert calculate_named_metric([], "p95") == 0.0


class TestResultBundleStatisticsAreUnchanged:
    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_bundle_percentiles_still_use_the_canonical_definition(self, name: str):
        times = FIXTURES[name]
        stats = TimingStatsCalculator.calculate(times)

        assert stats["p50_ms"] == percentile_ms(times, 0.50)
        assert stats["p90_ms"] == percentile_ms(times, 0.90)
        assert stats["p95_ms"] == percentile_ms(times, 0.95)
        assert stats["p99_ms"] == percentile_ms(times, 0.99)
        assert stats["geometric_mean_ms"] == geometric_mean_ms(times)
        assert stats["stdev_ms"] == sample_stdev_ms(times)

    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_bundle_percentiles_match_the_pinned_canonical_values(self, name: str):
        stats = TimingStatsCalculator.calculate(FIXTURES[name])
        expected = CANONICAL_PERCENTILES[name]

        assert stats["p50_ms"] == pytest.approx(expected["p50"])
        assert stats["p95_ms"] == pytest.approx(expected["p95"])
        assert stats["p99_ms"] == pytest.approx(expected["p99"])


