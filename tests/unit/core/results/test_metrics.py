from __future__ import annotations

import math

import pytest

from benchbox.core.results.metrics import (
    TimingStatsCalculator,
    TPCMetricsCalculator,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTPCMetricsCalculator:
    def test_calculate_power_at_size_basic(self) -> None:
        times = [1.0, 1.0, 1.0, 1.0]
        result = TPCMetricsCalculator.calculate_power_at_size(times, scale_factor=1.0)
        assert result == 3600.0

    def test_calculate_power_at_size_different_scale_factor(self) -> None:
        times = [1.0, 1.0, 1.0, 1.0]

        result = TPCMetricsCalculator.calculate_power_at_size(times, scale_factor=10.0)
        assert result == 36000.0

        result = TPCMetricsCalculator.calculate_power_at_size(times, scale_factor=0.01)
        assert result == 36.0

    def test_calculate_power_at_size_varied_times(self) -> None:
        times = [1.0, 2.0, 4.0, 8.0]
        result = TPCMetricsCalculator.calculate_power_at_size(times, scale_factor=1.0)
        expected_geom_mean = math.pow(64, 0.25)
        expected = 3600.0 / expected_geom_mean
        assert abs(result - expected) < 0.01

    def test_calculate_power_at_size_empty_list(self) -> None:
        result = TPCMetricsCalculator.calculate_power_at_size([], scale_factor=1.0)
        assert result == 0.0

    def test_calculate_power_at_size_with_zeros(self) -> None:
        times = [0.0, 1.0, 1.0, 0.0]
        result = TPCMetricsCalculator.calculate_power_at_size(times, scale_factor=1.0)
        assert result == 3600.0

    def test_calculate_power_at_size_all_zeros(self) -> None:
        times = [0.0, 0.0, 0.0]
        result = TPCMetricsCalculator.calculate_power_at_size(times, scale_factor=1.0)
        assert result == 0.0

    def test_calculate_power_at_size_matches_legacy_power_formula(self) -> None:
        times = [float(i + 1) for i in range(22)]

        result = TPCMetricsCalculator.calculate_power_at_size(times, scale_factor=10.0)

        assert result == pytest.approx((10.0 * 3600.0) / math.prod(times) ** (1 / len(times)))

    def test_calculate_throughput_at_size_basic(self) -> None:
        result = TPCMetricsCalculator.calculate_throughput_at_size(
            total_queries=100,
            total_time_seconds=100.0,
            scale_factor=1.0,
            num_streams=1,
        )
        assert result == 3600.0

    def test_calculate_throughput_at_size_with_streams(self) -> None:
        result = TPCMetricsCalculator.calculate_throughput_at_size(
            total_queries=100,
            total_time_seconds=100.0,
            scale_factor=1.0,
            num_streams=2,
        )
        assert result == 3600.0

    def test_calculate_throughput_at_size_zero_time(self) -> None:
        result = TPCMetricsCalculator.calculate_throughput_at_size(
            total_queries=100,
            total_time_seconds=0.0,
            scale_factor=1.0,
            num_streams=1,
        )
        assert result == 0.0

    def test_calculate_throughput_at_size_zero_streams(self) -> None:
        result = TPCMetricsCalculator.calculate_throughput_at_size(
            total_queries=100,
            total_time_seconds=100.0,
            scale_factor=1.0,
            num_streams=0,
        )
        assert result == 0.0

    def test_calculate_throughput_at_size_uses_total_query_count(self) -> None:
        result = TPCMetricsCalculator.calculate_throughput_at_size(
            total_queries=44,
            total_time_seconds=22.0,
            scale_factor=1.0,
            num_streams=2,
        )

        assert result == pytest.approx((44 * 3600.0) / 22.0)

    def test_calculate_geometric_mean_basic(self) -> None:
        times = [1.0, 1.0, 1.0, 1.0]
        result = TPCMetricsCalculator.calculate_geometric_mean(times)
        assert result == 1.0

    def test_calculate_geometric_mean_varied(self) -> None:
        times = [1.0, 2.0, 4.0, 8.0]
        result = TPCMetricsCalculator.calculate_geometric_mean(times)
        expected = math.pow(64, 0.25)
        assert abs(result - expected) < 0.0001

    def test_calculate_geometric_mean_empty(self) -> None:
        result = TPCMetricsCalculator.calculate_geometric_mean([])
        assert result == 0.0

    def test_calculate_geometric_mean_filters_zeros(self) -> None:
        times = [0.0, 1.0, 1.0, 0.0]
        result = TPCMetricsCalculator.calculate_geometric_mean(times)
        assert result == 1.0


class TestTimingStatsCalculator:
    def test_calculate_basic(self) -> None:
        times_ms = [100.0, 200.0, 300.0, 400.0, 500.0]
        result = TimingStatsCalculator.calculate(times_ms)

        assert result["total_ms"] == 1500.0
        assert result["avg_ms"] == 300.0
        assert result["min_ms"] == 100.0
        assert result["max_ms"] == 500.0
        assert "geometric_mean_ms" in result
        assert "stdev_ms" in result
        assert "p50_ms" in result
        assert "p90_ms" in result
        assert "p95_ms" in result
        assert "p99_ms" in result

    def test_calculate_empty(self) -> None:
        result = TimingStatsCalculator.calculate([])
        assert result == {}

    def test_calculate_single_value(self) -> None:
        times_ms = [100.0]
        result = TimingStatsCalculator.calculate(times_ms)

        assert result["total_ms"] == 100.0
        assert result["avg_ms"] == 100.0
        assert result["min_ms"] == 100.0
        assert result["max_ms"] == 100.0
        assert result["stdev_ms"] == 0.0

    def test_calculate_percentiles(self) -> None:
        times_ms = [float(i) for i in range(1, 101)]
        result = TimingStatsCalculator.calculate(times_ms)

        assert result["p50_ms"] == 50.0
        assert result["p90_ms"] == 90.0
        assert result["p95_ms"] == 95.0
        assert result["p99_ms"] == 99.0

    def test_calculate_seconds(self) -> None:
        times_seconds = [0.1, 0.2, 0.3, 0.4, 0.5]
        result = TimingStatsCalculator.calculate_seconds(times_seconds)

        assert result["total_s"] == 1.5
        assert result["avg_s"] == 0.3
        assert result["min_s"] == 0.1
        assert result["max_s"] == 0.5
        assert "geometric_mean_s" in result
        assert "stdev_s" in result

    def test_calculate_seconds_empty(self) -> None:
        result = TimingStatsCalculator.calculate_seconds([])
        assert result == {}


def _refresh_function(function: str, set_id: int) -> dict:
    return {"function": function, "update_set": set_id, "duration_ms": 10, "status": "SUCCESS"}


def _refresh_stream_evidence(first_set: int, count: int) -> dict:
    return {
        "first_set": first_set,
        "status": "COMPLETED",
        "start_time": "2026-01-01T00:00:00",
        "end_time": "2026-01-01T00:00:01",
        "pair_count": count,
        "pairs": [
            {
                "update_set": first_set + offset,
                "status": "SUCCESS",
                "functions": [
                    _refresh_function("RF1", first_set + offset),
                    _refresh_function("RF2", first_set + offset),
                ],
            }
            for offset in range(count)
        ],
    }


def _power_file(iteration_times: dict[int, float], *, failed: int = 0, tpc_metrics: dict | None = None) -> dict:
    rows = [
        {"id": str(q), "iter": iteration, "ms": seconds * 1000, "run_type": "measurement", "status": "SUCCESS"}
        for iteration, seconds in iteration_times.items()
        for q in range(1, 23)
    ]
    summary: dict = {"queries": {"total": len(rows), "passed": len(rows), "failed": failed}}
    if tpc_metrics is not None:
        summary["tpc_metrics"] = tpc_metrics
    return {
        "benchmark": {"id": "tpch", "name": "TPC-H", "compliance_class": "official"},
        "environment": {"scale_factor": 1.0},
        "summary": summary,
        "queries": rows,
        "phases": {
            "power_test": {
                "status": "COMPLETED",
                "query_count": 22,
                "refresh_first_set": 1,
                "refresh_functions": [_refresh_function("RF1", 1), _refresh_function("RF2", 1)],
            }
        },
    }


def _throughput_file(
    *,
    benchmark_id: str = "tpch",
    name: str = "TPC-H",
    streams: int = 2,
    duration_ms: int = 3_600_000,
    summed_query_ms: int = 7_200_000,
    phase_status: str = "COMPLETED",
    failed: int = 0,
    tpc_metrics: dict | None = None,
) -> dict:
    summary: dict = {
        "queries": {"total": 44, "passed": 44 - failed, "failed": failed},
        "timing": {"total_ms": summed_query_ms},
    }
    if tpc_metrics is not None:
        summary["tpc_metrics"] = tpc_metrics
    return {
        "benchmark": {"id": benchmark_id, "name": name, "compliance_class": "official"},
        "environment": {"scale_factor": 1.0},
        "run": {"streams": streams},
        "summary": summary,
        "phases": {
            "throughput_test": {
                "status": phase_status,
                "duration_ms": duration_ms,
                "stream_results": [{"stream_id": stream_id, "success": True} for stream_id in range(1, streams + 1)],
                "refresh_stream": _refresh_stream_evidence(2, streams),
            }
        },
    }


class TestDeriveFromResultFiles:
    def test_suppressed_metrics_are_refused(self) -> None:
        suppressed = {"suppressed": True, "reason": "compliance_class=unofficial_subscale"}
        with pytest.raises(ValueError, match="suppressed"):
            TPCMetricsCalculator.compute_qphh_result(
                _power_file({1: 1.0}), _throughput_file(tpc_metrics=suppressed), scale_factor=1.0
            )
        with pytest.raises(ValueError, match="suppressed"):
            TPCMetricsCalculator.compute_qphh_result(
                _power_file({1: 1.0}, tpc_metrics=suppressed), _throughput_file(), scale_factor=1.0
            )

    @pytest.mark.parametrize("which", ["power", "throughput"])
    def test_failed_queries_are_refused(self, which: str) -> None:
        power = _power_file({1: 1.0}, failed=5 if which == "power" else 0)
        throughput = _throughput_file(failed=5 if which == "throughput" else 0)
        with pytest.raises(ValueError, match="failed queries"):
            TPCMetricsCalculator.compute_qphh_result(power, throughput, scale_factor=1.0)

    def test_failed_throughput_phase_without_failed_rows_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Throughput phase did not complete"):
            TPCMetricsCalculator.compute_qphh_result(
                _power_file({1: 1.0}), _throughput_file(phase_status="FAILED"), scale_factor=1.0
            )

    def test_throughput_uses_phase_wall_duration_not_summed_query_time(self) -> None:
        result = TPCMetricsCalculator.compute_qphh_result(
            _power_file({1: 1.0}), _throughput_file(duration_ms=3_600_000, summed_query_ms=7_200_000), scale_factor=1.0
        )

        assert result["throughput_test_time"] == pytest.approx(3600.0)
        assert result["throughput_at_size"] == pytest.approx(22 * 2)

    def test_tpcds_throughput_scores_99_queries_per_stream(self) -> None:
        throughput = _throughput_file(benchmark_id="tpcds", name="TPC-DS", streams=3)
        _, throughput_at_size, _, _ = TPCMetricsCalculator.derive_tpc_metrics(_power_file({1: 1.0}), throughput, 1.0)

        assert throughput_at_size == pytest.approx(99 * 3)

    def test_power_uses_only_the_final_iteration(self) -> None:
        result = TPCMetricsCalculator.compute_qphh_result(
            _power_file({1: 100.0, 2: 10.0, 3: 1.0}), _throughput_file(), scale_factor=1.0
        )

        assert result["power_at_size"] == pytest.approx(3600.0 / (0.01 * 0.01) ** (1.0 / 24.0))
        assert result["power_test_time"] == pytest.approx(22.02)

    @pytest.mark.parametrize("compliance_class", ["unofficial_subscale", "unofficial_nonstandard"])
    def test_unofficial_bundles_without_a_suppressed_marker_are_refused(self, compliance_class: str) -> None:
        legacy = _throughput_file()
        legacy["benchmark"]["compliance_class"] = compliance_class
        with pytest.raises(ValueError, match=compliance_class):
            TPCMetricsCalculator.compute_qphh_result(_power_file({1: 1.0}), legacy, scale_factor=1.0)
        legacy_power = _power_file({1: 1.0})
        legacy_power["benchmark"]["compliance_class"] = compliance_class
        with pytest.raises(ValueError, match=compliance_class):
            TPCMetricsCalculator.compute_qphh_result(legacy_power, _throughput_file(), scale_factor=1.0)

    def test_official_bundles_are_accepted(self) -> None:
        power = _power_file({1: 1.0})
        power["benchmark"]["compliance_class"] = "official"

        result = TPCMetricsCalculator.compute_qphh_result(power, _throughput_file(), scale_factor=1.0)

        assert result["power_at_size"] == pytest.approx(3600.0 / (0.01 * 0.01) ** (1.0 / 24.0))

    def test_result_omits_composite_without_refresh_evidence(self) -> None:
        power = _power_file({1: 1.0})
        power["phases"]["power_test"].pop("refresh_functions")
        throughput = _throughput_file()

        reasons = TPCMetricsCalculator.qphh_unavailable_reasons(power, throughput)
        assert reasons
        assert any("Power phase" in reason for reason in reasons)
        with pytest.raises(ValueError, match="QphH@Size unavailable"):
            TPCMetricsCalculator.compute_qphh_result(power, throughput, scale_factor=1.0)


def test_scale_factor_is_detected_from_the_benchmark_block() -> None:
    power = _power_file({1: 1.0})
    throughput = _throughput_file()
    for data in (power, throughput):
        data.pop("environment")
        data["benchmark"]["scale_factor"] = 1.0

    result = TPCMetricsCalculator.compute_qphh_result(power, throughput)

    assert result["scale_factor"] == 1.0


def test_stream_count_comes_from_the_throughput_phase_not_distinct_row_streams() -> None:
    throughput = _throughput_file(streams=4)
    throughput["phases"]["throughput_test"]["stream_results"] = [{"stream_id": 0}, {"stream_id": 1}]
    throughput["phases"]["throughput_test"]["refresh_stream"] = _refresh_stream_evidence(2, 2)

    result = TPCMetricsCalculator.compute_qphh_result(_power_file({1: 1.0}), throughput, scale_factor=1.0)

    assert result["num_streams"] == 2
    assert result["throughput_at_size"] == pytest.approx(22 * 2)
