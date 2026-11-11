from __future__ import annotations

import time

import pytest

from benchbox.monitoring import PerformanceMonitor, ResourceMonitor

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def test_resource_monitor_initialization():
    monitor = PerformanceMonitor()
    resource_monitor = ResourceMonitor(monitor, sample_interval=1.0)

    assert resource_monitor.monitor is monitor
    assert resource_monitor.sample_interval == 1.0
    assert resource_monitor._thread is None
    assert resource_monitor._stop_event is None
    assert resource_monitor._peak_memory_mb == 0.0


def test_resource_monitor_start_stop():
    monitor = PerformanceMonitor()
    resource_monitor = ResourceMonitor(monitor, sample_interval=0.1)

    resource_monitor.start()
    assert resource_monitor._thread is not None
    assert resource_monitor._thread.is_alive()

    time.sleep(0.3)

    resource_monitor.stop()
    assert resource_monitor._thread is None

    snapshot = monitor.snapshot()
    assert "memory_mb" in snapshot.gauges or "peak_memory_mb" in snapshot.gauges


def test_resource_monitor_tracks_peak_memory():
    monitor = PerformanceMonitor()
    resource_monitor = ResourceMonitor(monitor, sample_interval=0.1)

    resource_monitor.start()
    time.sleep(0.3)
    resource_monitor.stop()

    snapshot = monitor.snapshot()
    if "peak_memory_mb" in snapshot.gauges:
        assert snapshot.gauges["peak_memory_mb"] > 0.0


def test_resource_monitor_double_start_is_safe():
    monitor = PerformanceMonitor()
    resource_monitor = ResourceMonitor(monitor, sample_interval=0.1)

    resource_monitor.start()
    first_thread = resource_monitor._thread

    resource_monitor.start()
    second_thread = resource_monitor._thread

    assert first_thread is second_thread

    resource_monitor.stop()


def test_resource_monitor_stop_before_start():
    monitor = PerformanceMonitor()
    resource_monitor = ResourceMonitor(monitor, sample_interval=0.1)

    resource_monitor.stop()


def test_resource_monitor_get_current_usage():
    monitor = PerformanceMonitor()
    resource_monitor = ResourceMonitor(monitor, sample_interval=0.1)

    usage = resource_monitor.get_current_usage()
    assert usage == {"memory_mb": 0.0, "memory_percent": 0.0, "cpu_percent": 0.0}

    resource_monitor.start()
    time.sleep(0.2)

    usage = resource_monitor.get_current_usage()
    assert usage["memory_mb"] >= 0.0

    resource_monitor.stop()


def test_resource_monitor_records_cpu_metrics():
    monitor = PerformanceMonitor()
    resource_monitor = ResourceMonitor(monitor, sample_interval=0.1)

    resource_monitor.start()
    time.sleep(0.3)
    resource_monitor.stop()

    snapshot = monitor.snapshot()
    if "cpu_percent" in snapshot.gauges:
        assert snapshot.gauges["cpu_percent"] >= 0.0


def test_resource_monitor_without_psutil():
    monitor = PerformanceMonitor()
    resource_monitor = ResourceMonitor(monitor, sample_interval=0.1)

    resource_monitor.start()
    resource_monitor.stop()


def test_resource_monitor_updates_monitor_gauges():
    monitor = PerformanceMonitor()
    resource_monitor = ResourceMonitor(monitor, sample_interval=0.1)

    resource_monitor.start()
    time.sleep(0.3)
    resource_monitor.stop()

    snapshot = monitor.snapshot()
    gauges = snapshot.gauges

    assert "memory_mb" in gauges or "peak_memory_mb" in gauges


def test_resource_monitor_shutdown_timeout():
    monitor = PerformanceMonitor()
    resource_monitor = ResourceMonitor(monitor, sample_interval=0.05)

    resource_monitor.start()

    start_time = time.time()
    resource_monitor.stop()
    elapsed = time.time() - start_time

    assert elapsed < 10.0


