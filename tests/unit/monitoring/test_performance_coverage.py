from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from benchbox.monitoring import PerformanceHistory, PerformanceMonitor

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_performance_monitor_getters_and_setters() -> None:
    monitor = PerformanceMonitor()

    assert monitor.get_counter("nonexistent") == 0

    monitor.set_gauge("memory_mb", 128.5)

    monitor.set_metadata("platform", "duckdb")
    monitor.set_metadata("version", "1.0.0")

    monitor.update_metadata({"scale_factor": 1.0, "threads": 4})

    snapshot = monitor.snapshot()
    assert snapshot.gauges["memory_mb"] == 128.5
    assert snapshot.metadata["platform"] == "duckdb"
    assert snapshot.metadata["version"] == "1.0.0"
    assert snapshot.metadata["scale_factor"] == 1.0
    assert snapshot.metadata["threads"] == 4


def test_performance_monitor_time_operation_context_manager() -> None:
    monitor = PerformanceMonitor()

    with monitor.time_operation("test_op"):
        pass

    snapshot = monitor.snapshot()
    assert "test_op" in snapshot.timings
    assert snapshot.timings["test_op"].count == 1


def test_performance_monitor_summary_method() -> None:
    monitor = PerformanceMonitor()
    monitor.increment_counter("ops", 10)
    monitor.set_gauge("cpu_percent", 45.2)

    summary = monitor.summary()

    assert isinstance(summary, dict)
    assert summary["counters"]["ops"] == 10
    assert summary["gauges"]["cpu_percent"] == 45.2


def test_performance_monitor_reset() -> None:
    monitor = PerformanceMonitor()
    monitor.increment_counter("ops", 5)
    monitor.set_gauge("memory", 100.0)
    monitor.record_timing("query", 0.5)
    monitor.set_metadata("key", "value")

    monitor.reset()

    snapshot = monitor.snapshot()
    assert len(snapshot.counters) == 0
    assert len(snapshot.gauges) == 0
    assert len(snapshot.timings) == 0
    assert len(snapshot.metadata) == 0


def test_performance_monitor_empty_timings() -> None:
    monitor = PerformanceMonitor()

    snapshot = monitor.snapshot()

    assert len(snapshot.timings) == 0


def test_performance_monitor_single_timing() -> None:
    monitor = PerformanceMonitor()
    monitor.record_timing("single", 1.5)

    snapshot = monitor.snapshot()
    stats = snapshot.timings["single"]

    assert stats.count == 1
    assert stats.minimum == 1.5
    assert stats.maximum == 1.5
    assert stats.mean == 1.5
    assert stats.median == 1.5
    assert stats.p90 == 1.5
    assert stats.p95 == 1.5
    assert stats.p99 == 1.5


def test_performance_snapshot_to_dict() -> None:
    monitor = PerformanceMonitor()
    monitor.increment_counter("queries", 10)

    snapshot = monitor.snapshot()
    snapshot_dict = snapshot.to_dict()

    assert isinstance(snapshot_dict, dict)
    assert "counters" in snapshot_dict
    assert snapshot_dict["counters"]["queries"] == 10


def test_performance_history_load_corrupted_json() -> None:
    fd, path = tempfile.mkstemp(suffix="_corrupted.json")
    os.close(fd)
    temp_path = Path(path)

    try:
        temp_path.write_text("{ invalid json {", encoding="utf-8")

        history = PerformanceHistory(temp_path)
        assert history._history == []
    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_performance_history_load_dict_with_entries() -> None:
    import json

    fd, path = tempfile.mkstemp(suffix="_entries.json")
    os.close(fd)
    temp_path = Path(path)

    try:
        data = {"entries": [{"timestamp": "2025-01-01T00:00:00", "counters": {}}]}
        temp_path.write_text(json.dumps(data), encoding="utf-8")

        history = PerformanceHistory(temp_path)
        assert len(history._history) == 1
    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_performance_history_metric_history() -> None:
    fd, path = tempfile.mkstemp(suffix="_metric_history.json")
    os.close(fd)
    temp_path = Path(path)

    try:
        history = PerformanceHistory(temp_path)

        for value in [1.0, 2.0, 3.0]:
            monitor = PerformanceMonitor()
            monitor.record_timing("query", value)
            snapshot = monitor.snapshot()
            history.record(snapshot, regression_thresholds={}, prefer_lower_metrics=[])

        values = history.metric_history("query")
        assert len(values) == 3
        assert values[0] == 1.0
        assert values[1] == 2.0
        assert values[2] == 3.0
    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_performance_history_max_entries_rotation() -> None:
    fd, path = tempfile.mkstemp(suffix="_rotation.json")
    os.close(fd)
    temp_path = Path(path)

    try:
        history = PerformanceHistory(temp_path, max_entries=3)

        for i in range(5):
            monitor = PerformanceMonitor()
            monitor.increment_counter("iteration", i)
            snapshot = monitor.snapshot()
            history.record(snapshot, regression_thresholds={}, prefer_lower_metrics=[])

        assert len(history._history) == 3
        assert history._history[0]["counters"]["iteration"] == 2
        assert history._history[-1]["counters"]["iteration"] == 4
    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_regression_alert_to_dict() -> None:
    from benchbox.monitoring import PerformanceRegressionAlert

    alert = PerformanceRegressionAlert(
        metric="query_time",
        baseline=1.0,
        current=2.0,
        change_percent=100.0,
        threshold_percent=50.0,
        direction="increase",
    )

    alert_dict = alert.to_dict()

    assert isinstance(alert_dict, dict)
    assert alert_dict["metric"] == "query_time"
    assert alert_dict["baseline"] == 1.0
    assert alert_dict["current"] == 2.0
    assert alert_dict["change_percent"] == 100.0


