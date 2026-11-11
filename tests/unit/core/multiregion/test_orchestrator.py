# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.experimental.multiregion.config import (
    MultiRegionConfig,
)
from benchbox.experimental.multiregion.orchestrator import (
    MultiRegionBenchmark,
    MultiRegionResult,
    RegionBenchmarkResult,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestRegionBenchmarkResult:
    @pytest.fixture
    def sample_result(self, us_east_region):
        return RegionBenchmarkResult(
            region=us_east_region,
            start_time=1000.0,
            end_time=1010.0,
            duration_seconds=10.0,
            queries_executed=100,
            queries_succeeded=95,
            queries_failed=5,
            throughput_qps=10.0,
            avg_latency_ms=50.0,
            p95_latency_ms=100.0,
            total_bytes_transferred=1024 * 1024,
        )

    def test_basic_creation(self, sample_result):
        assert sample_result.queries_executed == 100
        assert sample_result.duration_seconds == 10.0

    def test_success_rate(self, sample_result):
        assert sample_result.success_rate == 95.0

    def test_success_rate_zero_queries(self, us_east_region):
        result = RegionBenchmarkResult(
            region=us_east_region,
            start_time=0,
            end_time=1,
            duration_seconds=1,
            queries_executed=0,
            queries_succeeded=0,
            queries_failed=0,
            throughput_qps=0,
            avg_latency_ms=0,
            p95_latency_ms=0,
            total_bytes_transferred=0,
        )
        assert result.success_rate == 0.0


class TestMultiRegionResult:
    @pytest.fixture
    def multi_region_result(self, multi_region_config, us_east_region, eu_west_region):
        us_result = RegionBenchmarkResult(
            region=us_east_region,
            start_time=0,
            end_time=10,
            duration_seconds=10,
            queries_executed=100,
            queries_succeeded=95,
            queries_failed=5,
            throughput_qps=10.0,
            avg_latency_ms=50.0,
            p95_latency_ms=100.0,
            total_bytes_transferred=1024 * 1024,
        )
        eu_result = RegionBenchmarkResult(
            region=eu_west_region,
            start_time=0,
            end_time=10,
            duration_seconds=10,
            queries_executed=100,
            queries_succeeded=90,
            queries_failed=10,
            throughput_qps=8.0,
            avg_latency_ms=75.0,
            p95_latency_ms=150.0,
            total_bytes_transferred=1024 * 1024,
        )
        return MultiRegionResult(
            config=multi_region_config,
            start_time=0,
            end_time=20,
            total_duration_seconds=20,
            region_results={
                "us-east-1": us_result,
                "eu-west-1": eu_result,
            },
        )

    def test_get_best_region_throughput(self, multi_region_result):
        best = multi_region_result.get_best_region("throughput")
        assert best == "us-east-1"

    def test_get_best_region_latency(self, multi_region_result):
        best = multi_region_result.get_best_region("latency")
        assert best == "us-east-1"

    def test_get_best_region_success_rate(self, multi_region_result):
        best = multi_region_result.get_best_region("success_rate")
        assert best == "us-east-1"

    def test_get_best_region_empty(self, multi_region_config):
        result = MultiRegionResult(
            config=multi_region_config,
            start_time=0,
            end_time=0,
            total_duration_seconds=0,
        )
        assert result.get_best_region() is None

    def test_to_dict(self, multi_region_result):
        d = multi_region_result.to_dict()
        assert "total_duration_seconds" in d
        assert "regions_tested" in d
        assert "region_results" in d
        assert len(d["regions_tested"]) == 2


class TestMultiRegionBenchmark:
    @pytest.fixture
    def mock_benchmark_factory(self):

        class MockBenchmark:
            def __init__(self, region_config):
                self.region_config = region_config

            def run(self):
                class Result:
                    queries_executed = 10
                    queries_succeeded = 10
                    queries_failed = 0
                    latencies = [50.0, 60.0, 70.0]
                    total_bytes_transferred = 1000

                return Result()

        def factory(region_config):
            return MockBenchmark(region_config)

        return factory

    def test_run_sequential(self, multi_region_config, mock_benchmark_factory):
        benchmark = MultiRegionBenchmark(
            config=multi_region_config,
            benchmark_factory=mock_benchmark_factory,
        )
        result = benchmark.run(parallel=False, measure_latency=False)

        assert result.total_duration_seconds >= 0
        assert len(result.region_results) == 2

    def test_run_parallel(self, multi_region_config, mock_benchmark_factory):
        benchmark = MultiRegionBenchmark(
            config=multi_region_config,
            benchmark_factory=mock_benchmark_factory,
        )
        result = benchmark.run(parallel=True, measure_latency=False)

        assert result.total_duration_seconds >= 0
        assert len(result.region_results) == 2

    def test_handles_benchmark_error(self, multi_region_config):

        def failing_factory(region_config):
            class FailingBenchmark:
                def run(self):
                    raise RuntimeError("Benchmark failed")

            return FailingBenchmark()

        benchmark = MultiRegionBenchmark(
            config=multi_region_config,
            benchmark_factory=failing_factory,
        )
        result = benchmark.run(parallel=False, measure_latency=False)

        assert len(result.region_results) == 2
        for region_result in result.region_results.values():
            assert len(region_result.errors) > 0

    def test_region_comparison_generated(self, multi_region_config, mock_benchmark_factory):
        benchmark = MultiRegionBenchmark(
            config=multi_region_config,
            benchmark_factory=mock_benchmark_factory,
        )
        result = benchmark.run(parallel=False, measure_latency=False)

        assert "throughput_comparison" in result.region_comparison
        assert "latency_comparison" in result.region_comparison
        assert "rankings" in result.region_comparison

    def test_transfer_tracking(self, multi_region_config, mock_benchmark_factory):
        benchmark = MultiRegionBenchmark(
            config=multi_region_config,
            benchmark_factory=mock_benchmark_factory,
        )
        result = benchmark.run(parallel=False, measure_latency=False)

        assert isinstance(result.transfer_summary.total_transfers, int)


class TestMultiRegionBenchmarkEdgeCases:
    def test_single_region(self, us_east_config, us_east_region):
        config = MultiRegionConfig(
            primary_region=us_east_config,
            secondary_regions=[],
            client_region=us_east_region,
        )

        def simple_factory(region_config):
            class SimpleBenchmark:
                def run(self):
                    return type("Result", (), {"queries_executed": 1, "queries_succeeded": 1, "queries_failed": 0})()

            return SimpleBenchmark()

        benchmark = MultiRegionBenchmark(
            config=config,
            benchmark_factory=simple_factory,
        )
        result = benchmark.run(measure_latency=False)

        assert len(result.region_results) == 1

    def test_no_client_region_skips_latency(self, us_east_config):
        config = MultiRegionConfig(
            primary_region=us_east_config,
            client_region=None,
        )

        def simple_factory(region_config):
            class SimpleBenchmark:
                def run(self):
                    return type("Result", (), {"queries_executed": 1, "queries_succeeded": 1, "queries_failed": 0})()

            return SimpleBenchmark()

        benchmark = MultiRegionBenchmark(
            config=config,
            benchmark_factory=simple_factory,
        )
        result = benchmark.run(measure_latency=True)

        assert len(result.latency_profiles) == 0

    def test_benchmark_without_run_method(self, multi_region_config):

        def simple_factory(region_config):
            return object()

        benchmark = MultiRegionBenchmark(
            config=multi_region_config,
            benchmark_factory=simple_factory,
        )
        result = benchmark.run(measure_latency=False)

        for region_result in result.region_results.values():
            assert region_result.queries_executed >= 0


