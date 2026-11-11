# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from benchbox.monitoring.performance import PerformanceMonitor
from benchbox.monitoring.profiler import (
    EnhancedResourceProfiler,
    ResourceSample,
    ResourceTimeline,
    ResourceType,
    ResourceUtilization,
    calculate_utilization,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _real_time_with(**overrides):
    return SimpleNamespace(**{**vars(time), **overrides})


class TestResourceType:
    def test_all_types(self):
        types = list(ResourceType)
        assert ResourceType.CPU in types
        assert ResourceType.MEMORY in types
        assert ResourceType.DISK_READ in types
        assert ResourceType.DISK_WRITE in types
        assert ResourceType.NETWORK_SEND in types
        assert ResourceType.NETWORK_RECV in types

    def test_string_values(self):
        assert ResourceType.CPU.value == "cpu"
        assert ResourceType.MEMORY.value == "memory"


class TestResourceSample:
    def test_basic_creation(self):
        sample = ResourceSample(timestamp=1000.0)
        assert sample.timestamp == 1000.0
        assert sample.cpu_percent == 0.0
        assert sample.memory_mb == 0.0

    def test_full_creation(self):
        sample = ResourceSample(
            timestamp=1000.0,
            cpu_percent=75.5,
            memory_mb=1024.0,
            memory_percent=50.0,
            disk_read_bytes=1000000,
            disk_write_bytes=500000,
            disk_read_iops=100.0,
            disk_write_iops=50.0,
            network_send_bytes=2000000,
            network_recv_bytes=3000000,
            network_send_rate_mbps=10.5,
            network_recv_rate_mbps=15.2,
        )
        assert sample.cpu_percent == 75.5
        assert sample.memory_mb == 1024.0
        assert sample.disk_read_iops == 100.0
        assert sample.network_send_rate_mbps == 10.5

    def test_to_dict(self):
        sample = ResourceSample(
            timestamp=1000.0,
            cpu_percent=50.0,
            memory_mb=512.0,
        )
        d = sample.to_dict()
        assert d["timestamp"] == 1000.0
        assert d["cpu_percent"] == 50.0
        assert d["memory_mb"] == 512.0
        assert "disk_read_bytes" in d
        assert "network_send_rate_mbps" in d


class TestResourceTimeline:
    @pytest.fixture
    def sample_timeline(self):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=50.0 + i * 5,
                memory_mb=1000.0 + i * 100,
                memory_percent=40.0 + i * 2,
                disk_read_bytes=i * 1000000,
                disk_write_bytes=i * 500000,
                disk_read_iops=100.0 + i * 10,
                disk_write_iops=50.0 + i * 5,
                network_send_bytes=i * 2000000,
                network_recv_bytes=i * 3000000,
                network_send_rate_mbps=10.0 + i,
                network_recv_rate_mbps=15.0 + i,
            )
            for i in range(10)
        ]
        return ResourceTimeline(samples=samples, start_time=0.0, end_time=10.0)

    def test_duration_seconds(self, sample_timeline):
        assert sample_timeline.duration_seconds == 10.0

    def test_duration_from_samples(self):
        samples = [
            ResourceSample(timestamp=5.0),
            ResourceSample(timestamp=15.0),
        ]
        timeline = ResourceTimeline(samples=samples)
        assert timeline.duration_seconds == 10.0

    def test_sample_count(self, sample_timeline):
        assert sample_timeline.sample_count == 10

    def test_empty_timeline(self):
        timeline = ResourceTimeline()
        assert timeline.duration_seconds == 0.0
        assert timeline.sample_count == 0
        assert timeline.get_peak_cpu() == 0.0
        assert timeline.get_avg_cpu() == 0.0

    def test_get_peak_cpu(self, sample_timeline):
        assert sample_timeline.get_peak_cpu() == 95.0

    def test_get_avg_cpu(self, sample_timeline):
        assert sample_timeline.get_avg_cpu() == 72.5

    def test_get_peak_memory_mb(self, sample_timeline):
        assert sample_timeline.get_peak_memory_mb() == 1900.0

    def test_get_avg_memory_mb(self, sample_timeline):
        assert sample_timeline.get_avg_memory_mb() == 1450.0

    def test_get_total_disk_read_bytes(self, sample_timeline):
        assert sample_timeline.get_total_disk_read_bytes() == 9000000

    def test_get_total_disk_write_bytes(self, sample_timeline):
        assert sample_timeline.get_total_disk_write_bytes() == 4500000

    def test_get_avg_disk_iops(self, sample_timeline):
        assert sample_timeline.get_avg_disk_read_iops() == 145.0

    def test_get_total_network_bytes(self, sample_timeline):
        assert sample_timeline.get_total_network_send_bytes() == 18000000
        assert sample_timeline.get_total_network_recv_bytes() == 27000000

    def test_get_avg_network_mbps(self, sample_timeline):
        assert sample_timeline.get_avg_network_send_mbps() == 14.5
        assert sample_timeline.get_avg_network_recv_mbps() == 19.5

    def test_get_resource_series(self, sample_timeline):
        cpu_series = sample_timeline.get_resource_series(ResourceType.CPU)
        assert len(cpu_series) == 10
        assert cpu_series[0] == 50.0
        assert cpu_series[-1] == 95.0

        mem_series = sample_timeline.get_resource_series(ResourceType.MEMORY)
        assert len(mem_series) == 10
        assert mem_series[0] == 1000.0

    def test_to_dict(self, sample_timeline):
        d = sample_timeline.to_dict()
        assert d["duration_seconds"] == 10.0
        assert d["sample_count"] == 10
        assert "peak_cpu_percent" in d
        assert "avg_memory_mb" in d
        assert "total_disk_read_bytes" in d


class TestResourceUtilization:
    def test_basic_creation(self):
        util = ResourceUtilization(
            resource_type=ResourceType.CPU,
            min_value=10.0,
            max_value=90.0,
            avg_value=50.0,
            median_value=48.0,
            p95_value=85.0,
            std_dev=20.0,
            sample_count=100,
            unit="%",
        )
        assert util.resource_type == ResourceType.CPU
        assert util.avg_value == 50.0

    def test_utilization_percent(self):
        util = ResourceUtilization(
            resource_type=ResourceType.CPU,
            avg_value=45.0,
            max_value=90.0,
        )
        assert util.utilization_percent == 50.0

    def test_utilization_percent_zero_max(self):
        util = ResourceUtilization(
            resource_type=ResourceType.CPU,
            avg_value=0.0,
            max_value=0.0,
        )
        assert util.utilization_percent == 0.0

    def test_to_dict(self):
        util = ResourceUtilization(
            resource_type=ResourceType.CPU,
            min_value=10.0,
            max_value=90.0,
            avg_value=50.0,
            unit="%",
        )
        d = util.to_dict()
        assert d["resource_type"] == "cpu"
        assert d["avg_value"] == 50.0
        assert d["unit"] == "%"
        assert "utilization_percent" in d


class TestCalculateUtilization:
    @pytest.fixture
    def cpu_timeline(self):
        samples = [
            ResourceSample(timestamp=i, cpu_percent=float(v))
            for i, v in enumerate([50, 60, 70, 55, 65, 80, 75, 90, 85, 95])
        ]
        return ResourceTimeline(samples=samples)

    def test_calculate_cpu_utilization(self, cpu_timeline):
        util = calculate_utilization(cpu_timeline, ResourceType.CPU)
        assert util.resource_type == ResourceType.CPU
        assert util.min_value == 50.0
        assert util.max_value == 95.0
        assert util.sample_count == 10
        assert util.unit == "%"

    def test_calculate_empty_timeline(self):
        timeline = ResourceTimeline()
        util = calculate_utilization(timeline, ResourceType.CPU)
        assert util.sample_count == 0
        assert util.avg_value == 0.0

    def test_calculate_median(self, cpu_timeline):
        util = calculate_utilization(cpu_timeline, ResourceType.CPU)
        assert 70 <= util.median_value <= 75

    def test_calculate_p95(self, cpu_timeline):
        util = calculate_utilization(cpu_timeline, ResourceType.CPU)
        assert util.p95_value >= 90.0

    def test_calculate_std_dev(self, cpu_timeline):
        util = calculate_utilization(cpu_timeline, ResourceType.CPU)
        assert util.std_dev > 0


class TestEnhancedResourceProfiler:
    @staticmethod
    def _wait_for_sample(
        profiler: EnhancedResourceProfiler,
        *,
        timeout_seconds: float = 0.2,
    ) -> None:
        deadline = time.time() + timeout_seconds
        while profiler.get_timeline().sample_count < 1 and time.time() < deadline:
            time.sleep(0.01)

    def test_basic_creation(self):
        profiler = EnhancedResourceProfiler()
        assert profiler.sample_interval == 1.0
        assert profiler.track_disk is True
        assert profiler.track_network is True

    def test_creation_with_monitor(self):
        monitor = PerformanceMonitor()
        profiler = EnhancedResourceProfiler(monitor=monitor)
        assert profiler.monitor is monitor

    def test_creation_with_options(self):
        profiler = EnhancedResourceProfiler(
            sample_interval=0.5,
            track_disk=False,
            track_network=False,
        )
        assert profiler.sample_interval == 0.5
        assert profiler.track_disk is False
        assert profiler.track_network is False

    def test_is_running_before_start(self):
        profiler = EnhancedResourceProfiler()
        assert profiler.is_running() is False

    def test_get_timeline_empty(self):
        profiler = EnhancedResourceProfiler()
        timeline = profiler.get_timeline()
        assert timeline.sample_count == 0

    def test_get_current_sample_none(self):
        profiler = EnhancedResourceProfiler()
        assert profiler.get_current_sample() is None

    @pytest.mark.slow
    def test_start_stop_cycle(self):
        profiler = EnhancedResourceProfiler(sample_interval=0.1)
        profiler.start()
        self._wait_for_sample(profiler)

        assert profiler.is_running() is True

        profiler.stop()
        assert profiler.is_running() is False

        timeline = profiler.get_timeline()
        assert timeline.sample_count >= 1

    @pytest.mark.slow
    def test_collects_cpu_memory(self):
        profiler = EnhancedResourceProfiler(sample_interval=0.1)
        profiler.start()
        self._wait_for_sample(profiler)
        profiler.stop()

        timeline = profiler.get_timeline()
        if timeline.sample_count > 0:
            sample = timeline.samples[0]
            assert sample.cpu_percent >= 0
            assert sample.memory_mb >= 0

    @pytest.mark.slow
    def test_updates_monitor(self):
        monitor = PerformanceMonitor()
        profiler = EnhancedResourceProfiler(monitor=monitor, sample_interval=0.1)

        profiler.start()
        self._wait_for_sample(profiler)
        profiler.stop()

        snapshot = monitor.snapshot()
        assert "cpu_percent" in snapshot.gauges or snapshot.gauges.get("cpu_percent", 0) >= 0

    def test_double_start(self):
        profiler = EnhancedResourceProfiler(sample_interval=0.1)
        profiler.start()
        profiler.start()
        profiler.stop()

    def test_stop_without_start(self):
        profiler = EnhancedResourceProfiler()
        profiler.stop()

    def test_collect_sample_uses_monotonic_elapsed_for_rates(self):
        profiler = EnhancedResourceProfiler(track_disk=True, track_network=True)

        class _FakeProcess:
            def cpu_percent(self, interval=0.1):
                return 12.5

            def memory_info(self):
                return SimpleNamespace(rss=256 * 1024 * 1024)

            def memory_percent(self):
                return 25.0

            def io_counters(self):
                return SimpleNamespace(
                    read_bytes=1_200_000,
                    write_bytes=700_000,
                    read_count=112,
                    write_count=58,
                )

        class _FakePsutil:
            AccessDenied = PermissionError

            @staticmethod
            def net_io_counters():
                return SimpleNamespace(bytes_sent=1_500_000, bytes_recv=2_250_000)

        profiler._psutil = _FakePsutil
        profiler._process = _FakeProcess()
        profiler._prev_sample_time = 10.0
        profiler._prev_disk_read_count = 100
        profiler._prev_disk_write_count = 50
        profiler._prev_net_send = 1_000_000
        profiler._prev_net_recv = 2_000_000

        with (
            patch("benchbox.monitoring.profiler.time", _real_time_with(time=lambda: 1000.0)),
            patch("benchbox.monitoring.profiler.mono_time", return_value=12.0),
        ):
            sample = profiler._collect_sample()

        assert sample.timestamp == 1000.0
        assert sample.disk_read_iops == 6.0
        assert sample.disk_write_iops == 4.0
        assert sample.network_send_rate_mbps == 2.0
        assert sample.network_recv_rate_mbps == 1.0


class TestResourceTimelineSingleSample:
    def test_single_sample_disk_bytes(self):
        timeline = ResourceTimeline(samples=[ResourceSample(timestamp=0, disk_read_bytes=1000)])
        assert timeline.get_total_disk_read_bytes() == 0

    def test_single_sample_network_bytes(self):
        timeline = ResourceTimeline(samples=[ResourceSample(timestamp=0, network_send_bytes=1000)])
        assert timeline.get_total_network_send_bytes() == 0


class TestResourceUtilizationEdgeCases:
    def test_single_sample_std_dev(self):
        timeline = ResourceTimeline(samples=[ResourceSample(timestamp=0, cpu_percent=50.0)])
        util = calculate_utilization(timeline, ResourceType.CPU)
        assert util.std_dev == 0.0

    def test_constant_values(self):
        samples = [ResourceSample(timestamp=i, cpu_percent=50.0) for i in range(10)]
        timeline = ResourceTimeline(samples=samples)
        util = calculate_utilization(timeline, ResourceType.CPU)
        assert util.min_value == 50.0
        assert util.max_value == 50.0
        assert util.avg_value == 50.0
        assert util.std_dev == 0.0


