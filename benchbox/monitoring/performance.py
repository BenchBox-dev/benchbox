from __future__ import annotations

import json
import statistics
import tempfile
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

DEFAULT_REGRESSION_THRESHOLD = 0.15


@dataclass(frozen=True)
class TimingStats:
    count: int
    minimum: float
    maximum: float
    mean: float
    median: float
    p90: float
    p95: float
    p99: float
    total: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class PerformanceSnapshot:
    timestamp: str
    counters: dict[str, int]
    gauges: dict[str, float]
    timings: dict[str, TimingStats]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "timings": {name: stats.to_dict() for name, stats in self.timings.items()},
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PerformanceRegressionAlert:
    metric: str
    baseline: float
    current: float
    change_percent: float
    threshold_percent: float
    direction: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PerformanceMonitor:
    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._timings: dict[str, list[float]] = {}
        self._metadata: dict[str, Any] = {}

    def increment_counter(self, name: str, value: int = 1) -> None:

        self._counters[name] = self._counters.get(name, 0) + value

    def get_counter(self, name: str) -> int:

        return self._counters.get(name, 0)

    def set_gauge(self, name: str, value: float) -> None:

        self._gauges[name] = float(value)

    def record_timing(self, name: str, duration_seconds: float) -> None:

        self._timings.setdefault(name, []).append(float(duration_seconds))

    @contextmanager
    def time_operation(self, name: str):

        start = perf_counter()
        try:
            yield
        finally:
            elapsed = perf_counter() - start
            self.record_timing(name, elapsed)

    def set_metadata(self, key: str, value: Any) -> None:

        self._metadata[key] = value

    def update_metadata(self, items: dict[str, Any]) -> None:

        self._metadata.update(items)

    def snapshot(self) -> PerformanceSnapshot:

        timings_summary = {name: self._summarize_timings(samples) for name, samples in self._timings.items()}
        return PerformanceSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            counters=dict(self._counters),
            gauges=dict(self._gauges),
            timings=timings_summary,
            metadata=dict(self._metadata),
        )

    def summary(self) -> dict[str, Any]:

        snapshot = self.snapshot()
        return snapshot.to_dict()

    def reset(self) -> None:

        self._counters.clear()
        self._gauges.clear()
        self._timings.clear()
        self._metadata.clear()

    @staticmethod
    def _summarize_timings(samples: Iterable[float]) -> TimingStats:
        series = [float(value) for value in samples if value is not None]
        if not series:
            return TimingStats(
                count=0, minimum=0.0, maximum=0.0, mean=0.0, median=0.0, p90=0.0, p95=0.0, p99=0.0, total=0.0
            )

        sorted_series = sorted(series)
        count = len(sorted_series)
        total = sum(sorted_series)
        mean = statistics.fmean(sorted_series)
        median = statistics.median(sorted_series)

        def percentile(p: float) -> float:
            if count == 1:
                return sorted_series[0]
            rank = (p / 100) * (count - 1)
            lower = int(rank)
            upper = min(lower + 1, count - 1)
            weight = rank - lower
            return sorted_series[lower] + weight * (sorted_series[upper] - sorted_series[lower])

        return TimingStats(
            count=count,
            minimum=sorted_series[0],
            maximum=sorted_series[-1],
            mean=mean,
            median=median,
            p90=percentile(90),
            p95=percentile(95),
            p99=percentile(99),
            total=total,
        )


class PerformanceHistory:
    def __init__(self, storage_path: Path, max_entries: int = 50) -> None:
        self.storage_path = Path(storage_path)
        self.max_entries = max_entries
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._history: list[dict[str, Any]] = self._load_history()

    def _load_history(self) -> list[dict[str, Any]]:
        if not self.storage_path.exists():
            return []
        try:
            with self.storage_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
                if isinstance(payload, list):
                    return payload
                if isinstance(payload, dict) and "entries" in payload:
                    return list(payload["entries"])
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _write_history(self) -> None:
        payload = {"entries": self._history}
        with self.storage_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def record(
        self,
        snapshot: PerformanceSnapshot,
        regression_thresholds: dict[str, float] | None = None,
        prefer_lower_metrics: list[str] | None = None,
    ) -> list[PerformanceRegressionAlert]:

        prefer_lower_metrics = prefer_lower_metrics or []
        regression_thresholds = regression_thresholds or {}

        alerts: list[PerformanceRegressionAlert] = []
        if self._history:
            baseline = self._history[-1]
            alerts = self._detect_regressions(baseline, snapshot.to_dict(), regression_thresholds, prefer_lower_metrics)

        self._history.append(snapshot.to_dict())
        if len(self._history) > self.max_entries:
            self._history = self._history[-self.max_entries :]
        self._write_history()
        return alerts

    def trend(self, metric: str, window: int = 10) -> str:

        if window < 2:
            window = 2
        series = self.metric_history(metric)[-window:]
        if len(series) < 2:
            return "insufficient_data"

        midpoint = len(series) // 2
        first_half = series[:midpoint]
        second_half = series[midpoint:]

        if not first_half or not second_half:
            return "insufficient_data"

        first_avg = statistics.fmean(first_half)
        second_avg = statistics.fmean(second_half)

        if first_avg == 0 and second_avg == 0:
            return "stable"

        change = float("inf") if first_avg == 0 else (second_avg - first_avg) / abs(first_avg)

        if change > 0.1:
            return "degrading"
        if change < -0.1:
            return "improving"
        return "stable"

    def metric_history(self, metric: str) -> list[float]:
        values: list[float] = []
        for entry in self._history:
            if metric in entry.get("counters", {}):
                values.append(float(entry["counters"][metric]))
                continue
            timing = entry.get("timings", {}).get(metric)
            if timing:
                values.append(float(timing.get("mean", 0.0)))
                continue
            gauge = entry.get("gauges", {}).get(metric)
            if gauge is not None:
                values.append(float(gauge))
        return values

    @staticmethod
    def _detect_regressions(
        baseline: dict[str, Any],
        current: dict[str, Any],
        thresholds: dict[str, float],
        prefer_lower_metrics: list[str],
    ) -> list[PerformanceRegressionAlert]:
        alerts: list[PerformanceRegressionAlert] = []

        def extract_value(entry: dict[str, Any], key: str) -> float | None:
            if key in entry.get("counters", {}):
                return float(entry["counters"][key])
            if key in entry.get("gauges", {}):
                return float(entry["gauges"][key])
            timing = entry.get("timings", {}).get(key)
            if timing:
                return float(timing.get("mean", 0.0))
            return None

        for metric, threshold in thresholds.items():
            baseline_value = extract_value(baseline, metric)
            current_value = extract_value(current, metric)
            if baseline_value is None or current_value is None:
                continue
            if baseline_value == 0 and current_value == 0:
                continue

            change = 0.0 if baseline_value == 0 else (current_value - baseline_value) / abs(baseline_value)

            prefers_lower = metric in prefer_lower_metrics
            regression = change > threshold if prefers_lower else change < -threshold

            if regression:
                direction = "increase" if prefers_lower else "decrease"
                alerts.append(
                    PerformanceRegressionAlert(
                        metric=metric,
                        baseline=baseline_value,
                        current=current_value,
                        change_percent=change,
                        threshold_percent=threshold,
                        direction=direction,
                    )
                )
        return alerts


class PerformanceTracker:
    def __init__(self, storage_path: Path | None = None) -> None:
        default_path = Path(tempfile.gettempdir()) / "benchbox_performance_history.json"
        self.storage_path = Path(storage_path) if storage_path else default_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.metrics_history = self._load_history()

    def _load_history(self) -> dict[str, list[dict[str, Any]]]:
        if not self.storage_path.exists():
            return {}
        try:
            with self.storage_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
                if isinstance(payload, dict):
                    return {key: list(value) for key, value in payload.items()}
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _save_history(self) -> None:
        with self.storage_path.open("w", encoding="utf-8") as handle:
            json.dump(self.metrics_history, handle, indent=2)

    def record_metric(self, metric_name: str, value: float, timestamp: datetime | None = None) -> None:

        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        entry = {"timestamp": timestamp.isoformat(), "value": float(value)}
        self.metrics_history.setdefault(metric_name, []).append(entry)
        self._save_history()

    def get_trend(self, metric_name: str, days: int = 30) -> dict[str, Any]:

        history = self.metrics_history.get(metric_name, [])
        if not history:
            return {"trend": "unknown", "recent_values": [], "average": 0}

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent_entries = [entry for entry in history if datetime.fromisoformat(entry["timestamp"]) >= cutoff]

        if len(recent_entries) < 2:
            return {"trend": "insufficient_data", "recent_values": recent_entries, "average": 0}

        values = [entry["value"] for entry in recent_entries]
        if not values:
            return {"trend": "unknown", "recent_values": [], "average": 0}

        half = len(values) // 2
        first_half = values[:half] or values
        second_half = values[half:] or values

        first_avg = statistics.fmean(first_half)
        second_avg = statistics.fmean(second_half)

        change = (float("inf") if second_avg else 0.0) if first_avg == 0 else (second_avg - first_avg) / abs(first_avg)

        if change > 0.1:
            trend = "degrading"
        elif change < -0.1:
            trend = "improving"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "recent_values": values,
            "average": statistics.fmean(values),
            "min": min(values),
            "max": max(values),
            "std_dev": statistics.stdev(values) if len(values) > 1 else 0.0,
        }

    def detect_anomalies(self, metric_name: str, threshold_multiplier: float = 2.0) -> list[dict[str, Any]]:

        history = self.metrics_history.get(metric_name, [])
        if len(history) < 10:
            return []

        values = [entry["value"] for entry in history]
        mean_value = statistics.fmean(values)
        std_dev = statistics.stdev(values)

        threshold = std_dev * threshold_multiplier
        anomalies = []
        for entry in history:
            deviation = abs(entry["value"] - mean_value)
            if deviation > threshold:
                anomalies.append(
                    {
                        "timestamp": entry["timestamp"],
                        "value": entry["value"],
                        "deviation": deviation,
                        "threshold": threshold,
                    }
                )

        return anomalies


class ResourceMonitor:
    def __init__(self, monitor: PerformanceMonitor, sample_interval: float = 2.0):
        self.monitor = monitor
        self.sample_interval = sample_interval
        self._stop_event: Any = None
        self._thread: Any = None
        self._peak_memory_mb: float = 0.0
        self._process: Any = None

    def start(self) -> None:
        try:
            import threading

            import psutil
        except ImportError:
            return

        if self._thread is not None and self._thread.is_alive():
            return

        self._process = psutil.Process()
        self._stop_event = threading.Event()
        self._peak_memory_mb = 0.0

        def _sample_loop():
            while not self._stop_event.is_set():
                try:
                    mem_info = self._process.memory_info()
                    memory_mb = mem_info.rss / (1024 * 1024)
                    memory_percent = self._process.memory_percent()

                    cpu_percent = self._process.cpu_percent(interval=0.1)

                    self.monitor.set_gauge("memory_mb", memory_mb)
                    self.monitor.set_gauge("memory_percent", memory_percent)
                    self.monitor.set_gauge("cpu_percent", cpu_percent)

                    if memory_mb > self._peak_memory_mb:
                        self._peak_memory_mb = memory_mb
                        self.monitor.set_gauge("peak_memory_mb", memory_mb)

                except Exception:
                    pass

                self._stop_event.wait(self.sample_interval)

        self._thread = threading.Thread(target=_sample_loop, daemon=True, name="ResourceMonitor")
        self._thread.start()

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

        if self._peak_memory_mb > 0:
            self.monitor.set_gauge("peak_memory_mb", self._peak_memory_mb)

    def get_current_usage(self) -> dict[str, float]:
        if self._process is None:
            return {"memory_mb": 0.0, "memory_percent": 0.0, "cpu_percent": 0.0}

        try:
            mem_info = self._process.memory_info()
            return {
                "memory_mb": mem_info.rss / (1024 * 1024),
                "memory_percent": self._process.memory_percent(),
                "cpu_percent": self._process.cpu_percent(interval=0.1),
            }
        except Exception:
            return {"memory_mb": 0.0, "memory_percent": 0.0, "cpu_percent": 0.0}


def attach_snapshot_to_result(result: Any, snapshot: PerformanceSnapshot) -> dict[str, Any]:

    summary = snapshot.to_dict()
    result.performance_summary = summary

    existing = getattr(result, "performance_characteristics", None)
    if not existing:
        result.performance_characteristics = summary
    else:
        merged = dict(existing)
        merged.setdefault("monitoring", summary)
        result.performance_characteristics = merged

    return summary
