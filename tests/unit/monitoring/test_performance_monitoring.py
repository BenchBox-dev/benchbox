from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from benchbox.monitoring import (
    PerformanceHistory,
    PerformanceMonitor,
    PerformanceTracker,
    attach_snapshot_to_result,
)
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def temp_history_path() -> Generator[Path, None, None]:
    fd, path = tempfile.mkstemp(suffix="_performance.json")
    os.close(fd)
    temp_path = Path(path)
    if temp_path.exists():
        temp_path.unlink()
    try:
        yield temp_path
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _make_result():
    return make_benchmark_results(
        benchmark_name="dummy",
        platform="test",
        scale_factor=1.0,
        execution_id="exec-1",
        duration_seconds=0.1,
        total_queries=1,
        successful_queries=1,
    )


def test_performance_monitor_summary_and_attachment() -> None:
    monitor = PerformanceMonitor()
    monitor.increment_counter("queries", 5)
    monitor.record_timing("execute", 0.10)
    monitor.record_timing("execute", 0.12)

    snapshot = monitor.snapshot()

    result = _make_result()
    summary = attach_snapshot_to_result(result, snapshot)

    assert summary["counters"]["queries"] == 5
    assert "execute" in summary["timings"]
    assert result.performance_summary["counters"]["queries"] == 5
    assert "performance_characteristics" in result.__dict__


def test_performance_history_detects_regressions(temp_history_path: Path) -> None:
    history = PerformanceHistory(temp_history_path)

    baseline_monitor = PerformanceMonitor()
    for _ in range(5):
        baseline_monitor.record_timing("execution_time", 0.10)
    baseline_snapshot = baseline_monitor.snapshot()

    alerts = history.record(
        baseline_snapshot,
        regression_thresholds={"execution_time": 0.25},
        prefer_lower_metrics=["execution_time"],
    )
    assert alerts == []

    degraded_monitor = PerformanceMonitor()
    for _ in range(5):
        degraded_monitor.record_timing("execution_time", 0.40)
    degraded_snapshot = degraded_monitor.snapshot()

    alerts = history.record(
        degraded_snapshot,
        regression_thresholds={"execution_time": 0.25},
        prefer_lower_metrics=["execution_time"],
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.metric == "execution_time"
    assert alert.change_percent > 0.25


def test_performance_tracker_trend_and_anomaly_detection() -> None:

    fd, path = tempfile.mkstemp(suffix="_tracker.json")
    os.close(fd)
    temp_file = Path(path)
    try:
        tracker = PerformanceTracker(temp_file)

        for value in [1.0, 0.95, 0.9, 0.85, 0.8, 0.78]:
            tracker.record_metric("improving", value)
        trend = tracker.get_trend("improving")
        assert trend["trend"] == "improving"

        for value in [1.0, 1.01, 0.99, 1.0, 1.02, 0.98]:
            tracker.record_metric("stable", value)
        trend = tracker.get_trend("stable")
        assert trend["trend"] == "stable"

        for value in [1.0] * 12:
            tracker.record_metric("anomaly", value)
        tracker.record_metric("anomaly", 3.5)
        tracker.record_metric("anomaly", 4.8)
        anomalies = tracker.detect_anomalies("anomaly", threshold_multiplier=1.5)
        assert len(anomalies) >= 2
    finally:
        try:
            if temp_file.exists():
                temp_file.unlink()
        except PermissionError:
            pass


