# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import statistics
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.monitoring import PerformanceMonitor, PerformanceTracker

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


@pytest.mark.slow
@pytest.mark.performance
@pytest.mark.monitoring
class TestPerformanceMonitoring:
    @pytest.fixture
    def benchmark_instance(self):
        return TPCDSBenchmark(
            scale_factor=1.0,
            verbose=False,
        )

    @pytest.fixture
    def performance_tracker(self):
        temp_file = Path(tempfile.mktemp(suffix=".json"))
        tracker = PerformanceTracker(temp_file)
        yield tracker
        if temp_file.exists():
            temp_file.unlink()

    @pytest.fixture
    def performance_monitor(self):
        return PerformanceMonitor()

    def test_metric_collection(self, benchmark_instance, performance_tracker) -> None:
        query_times = []
        for i in range(10):
            start_time = time.time()
            benchmark_instance.get_query((i % 20) + 1)
            query_time = time.time() - start_time
            query_times.append(query_time)

            performance_tracker.record_metric("query_generation_time", query_time)

        trend = performance_tracker.get_trend("query_generation_time")
        assert len(trend["recent_values"]) == 10
        assert trend["average"] > 0
        assert trend["min"] >= 0
        assert trend["max"] >= trend["min"]

    def test_trend_analysis(self, performance_tracker) -> None:
        base_time = datetime.now(timezone.utc)
        for i in range(20):
            value = 1.0 - (i * 0.02)
            timestamp = base_time + timedelta(hours=i)
            performance_tracker.record_metric("improving_metric", value, timestamp)

        trend = performance_tracker.get_trend("improving_metric")
        assert trend["trend"] == "improving"

        for i in range(20):
            value = 1.0 + (i * 0.03)
            timestamp = base_time + timedelta(hours=i)
            performance_tracker.record_metric("degrading_metric", value, timestamp)

        trend = performance_tracker.get_trend("degrading_metric")
        assert trend["trend"] == "degrading"

        for i in range(20):
            value = 1.0 + (i % 2) * 0.01
            timestamp = base_time + timedelta(hours=i)
            performance_tracker.record_metric("stable_metric", value, timestamp)

        trend = performance_tracker.get_trend("stable_metric")
        assert trend["trend"] == "stable"

    def test_anomaly_detection(self, performance_tracker) -> None:
        base_time = datetime.now(timezone.utc)
        normal_values = [1.0, 0.9, 1.1, 1.0, 0.95, 1.05, 1.0, 0.98, 1.02, 1.0]

        for i, value in enumerate(normal_values):
            timestamp = base_time + timedelta(hours=i)
            performance_tracker.record_metric("normal_metric", value, timestamp)

        anomaly_values = [3.0, 5.0]
        for i, value in enumerate(anomaly_values):
            timestamp = base_time + timedelta(hours=len(normal_values) + i)
            performance_tracker.record_metric("normal_metric", value, timestamp)

        anomalies = performance_tracker.detect_anomalies("normal_metric", threshold_multiplier=1.5)

        assert len(anomalies) >= 1
        anomaly_values_detected = [a["value"] for a in anomalies]
        assert 3.0 in anomaly_values_detected or 5.0 in anomaly_values_detected

    def test_performance_alerting(self, benchmark_instance, performance_tracker) -> None:
        thresholds = {
            "query_generation_time": {"warning": 0.01, "critical": 0.05},
            "memory_usage": {"warning": 50, "critical": 100},
            "error_rate": {"warning": 0.01, "critical": 0.05},
        }

        alerts = []

        def alert_callback(metric_name: str, level: str, value: float, threshold: float) -> None:
            alerts.append(
                {
                    "metric": metric_name,
                    "level": level,
                    "value": value,
                    "threshold": threshold,
                    "timestamp": datetime.now(timezone.utc),
                }
            )

        performance_tracker.record_metric("query_generation_time", 0.02)
        performance_tracker.record_metric("query_generation_time", 0.08)
        performance_tracker.record_metric("memory_usage", 75)
        performance_tracker.record_metric("memory_usage", 150)

        for metric_name, metric_thresholds in thresholds.items():
            trend = performance_tracker.get_trend(metric_name)
            if trend["recent_values"]:
                for value in trend["recent_values"]:
                    if value > metric_thresholds["critical"]:
                        alert_callback(
                            metric_name,
                            "critical",
                            value,
                            metric_thresholds["critical"],
                        )
                    elif value > metric_thresholds["warning"]:
                        alert_callback(
                            metric_name,
                            "warning",
                            value,
                            metric_thresholds["warning"],
                        )

        assert len(alerts) >= 2

        critical_alerts = [a for a in alerts if a["level"] == "critical"]
        assert len(critical_alerts) >= 1

        warning_alerts = [a for a in alerts if a["level"] == "warning"]
        assert len(warning_alerts) >= 1

    def test_historical_performance_tracking(self, benchmark_instance, performance_tracker) -> None:
        base_time = datetime.now(timezone.utc) - timedelta(days=30)

        for day in range(30):
            day_time = base_time + timedelta(days=day)

            for hour in range(0, 24, 4):
                timestamp = day_time + timedelta(hours=hour)

                query_time = 0.01 + (day * 0.0001)
                performance_tracker.record_metric("daily_query_time", query_time, timestamp)

                memory_usage = 20 + (day * 0.5)
                performance_tracker.record_metric("daily_memory_usage", memory_usage, timestamp)

        weekly_trend = performance_tracker.get_trend("daily_query_time", days=7)
        monthly_trend = performance_tracker.get_trend("daily_query_time", days=30)

        assert len(monthly_trend["recent_values"]) > len(weekly_trend["recent_values"])

        assert monthly_trend["trend"] == "degrading"

        memory_trend = performance_tracker.get_trend("daily_memory_usage", days=30)
        assert memory_trend["trend"] == "degrading"

    def test_benchmark_comparison(self, benchmark_instance, performance_tracker) -> None:
        current_results = {}

        query_times = []
        for i in range(10):
            start_time = time.time()
            benchmark_instance.get_query((i % 20) + 1)
            query_time = time.time() - start_time
            query_times.append(query_time)

        current_results["avg_query_time"] = statistics.mean(query_times)
        current_results["max_query_time"] = max(query_times)

        param_times = []
        for i in range(10):
            start_time = time.time()
            benchmark_instance.get_query((i % 20) + 1)
            param_time = time.time() - start_time
            param_times.append(param_time)

        current_results["avg_param_time"] = statistics.mean(param_times)
        current_results["max_param_time"] = max(param_times)

        for metric, value in current_results.items():
            performance_tracker.record_metric(f"benchmark_{metric}", value)

        historical_baseline = {
            "avg_query_time": 0.1,
            "max_query_time": 0.2,
            "avg_param_time": 0.1,
            "max_param_time": 0.2,
        }

        comparison_results = {}
        for metric, current_value in current_results.items():
            baseline_value = historical_baseline[metric]
            performance_ratio = current_value / baseline_value

            if performance_ratio > 1.2:
                comparison_results[metric] = "degraded"
            elif performance_ratio < 0.8:
                comparison_results[metric] = "improved"
            else:
                comparison_results[metric] = "stable"

        degraded_metrics = [k for k, v in comparison_results.items() if v == "degraded"]
        assert len(degraded_metrics) <= 1, f"Too many degraded metrics: {degraded_metrics}"

    def test_resource_utilization_monitoring(self, benchmark_instance, performance_tracker) -> None:
        import os

        import psutil

        process = psutil.Process(os.getpid())

        initial_memory = process.memory_info().rss / 1024 / 1024
        process.cpu_percent()

        resource_measurements = []

        for i in range(20):
            start_time = time.time()

            benchmark_instance.get_query((i % 30) + 1)

            current_memory = process.memory_info().rss / 1024 / 1024
            current_cpu = process.cpu_percent()
            query_time = time.time() - start_time

            measurement = {
                "iteration": i,
                "memory_mb": current_memory,
                "memory_increase": current_memory - initial_memory,
                "cpu_percent": current_cpu,
                "query_time": query_time,
            }

            resource_measurements.append(measurement)

            performance_tracker.record_metric("resource_memory_usage", current_memory)
            performance_tracker.record_metric("resource_cpu_usage", current_cpu)
            performance_tracker.record_metric("resource_query_time", query_time)

        memory_increases = [m["memory_increase"] for m in resource_measurements]
        cpu_usages = [m["cpu_percent"] for m in resource_measurements if m["cpu_percent"] > 0]
        query_times = [m["query_time"] for m in resource_measurements]

        max_memory_increase = max(memory_increases)
        assert max_memory_increase < 100, f"Memory usage too high: {max_memory_increase:.2f}MB"

        if cpu_usages:
            avg_cpu = statistics.mean(cpu_usages)
            assert avg_cpu < 100, f"CPU usage too high: {avg_cpu:.2f}%"

        if len(query_times) > 1:
            query_time_std = statistics.stdev(query_times)
            query_time_mean = statistics.mean(query_times)
            cv = query_time_std / query_time_mean if query_time_mean > 0 else 0
            assert cv < 2.0, f"Query time too inconsistent: CV={cv:.2f}"

    def test_performance_regression_alerting(self, benchmark_instance, performance_tracker) -> None:
        baseline_measurements = []
        for i in range(10):
            start_time = time.time()
            benchmark_instance.get_query((i % 10) + 1)
            query_time = time.time() - start_time
            baseline_measurements.append(query_time)

            performance_tracker.record_metric("regression_baseline", query_time)

        baseline_avg = statistics.mean(baseline_measurements)

        regression_measurements = []
        for i in range(10):
            degraded_time = baseline_avg * 1.5
            regression_measurements.append(degraded_time)

            performance_tracker.record_metric("regression_current", degraded_time)

        baseline_trend = performance_tracker.get_trend("regression_baseline")
        current_trend = performance_tracker.get_trend("regression_current")

        regression_ratio = current_trend["average"] / baseline_trend["average"]

        assert regression_ratio > 1.3, f"Failed to detect regression: {regression_ratio:.2f}"

        regression_alert = {
            "type": "performance_regression",
            "metric": "query_generation_time",
            "baseline_avg": baseline_trend["average"],
            "current_avg": current_trend["average"],
            "regression_ratio": regression_ratio,
            "severity": "high" if regression_ratio > 2.0 else "medium",
        }

        assert regression_alert["severity"] in ["medium", "high"]
        assert regression_alert["regression_ratio"] > 1.0
        assert regression_alert["current_avg"] > regression_alert["baseline_avg"]

    def test_performance_dashboard_data(self, benchmark_instance, performance_tracker) -> None:
        dashboard_data = {
            "summary": {},
            "trends": {},
            "alerts": [],
            "resource_usage": {},
            "historical_data": {},
        }

        metrics = ["query_time", "param_time", "memory_usage", "cpu_usage"]

        for metric in metrics:
            values = []
            for i in range(24):
                if metric == "query_time":
                    value = 0.01 + (i * 0.0001)
                elif metric == "param_time":
                    value = 0.02 + (i * 0.0002)
                elif metric == "memory_usage":
                    value = 25 + (i * 0.5)
                else:
                    value = 10 + (i * 0.3)

                values.append(value)
                timestamp = datetime.now(timezone.utc) - timedelta(hours=24 - i)
                performance_tracker.record_metric(metric, value, timestamp)

            dashboard_data["summary"][metric] = {
                "current": values[-1],
                "average": statistics.mean(values),
                "min": min(values),
                "max": max(values),
                "std_dev": statistics.stdev(values),
            }

            trend = performance_tracker.get_trend(metric, days=1)
            dashboard_data["trends"][metric] = trend

            if metric == "query_time" and values[-1] > 0.02:
                dashboard_data["alerts"].append(
                    {
                        "metric": metric,
                        "level": "warning",
                        "message": f"Query time elevated: {values[-1]:.6f}s",
                    }
                )
            elif metric == "memory_usage" and values[-1] > 35:
                dashboard_data["alerts"].append(
                    {
                        "metric": metric,
                        "level": "warning",
                        "message": f"Memory usage elevated: {values[-1]:.2f}MB",
                    }
                )

        assert "summary" in dashboard_data
        assert "trends" in dashboard_data
        assert "alerts" in dashboard_data

        for metric in metrics:
            assert metric in dashboard_data["summary"]
            assert "current" in dashboard_data["summary"][metric]
            assert "average" in dashboard_data["summary"][metric]
            assert dashboard_data["summary"][metric]["current"] > 0
            assert dashboard_data["summary"][metric]["average"] > 0

        for metric in metrics:
            assert metric in dashboard_data["trends"]
            assert "trend" in dashboard_data["trends"][metric]
            assert dashboard_data["trends"][metric]["trend"] in [
                "improving",
                "stable",
                "degrading",
                "unknown",
            ]

        assert len(dashboard_data["alerts"]) > 0

        print(
            f"Dashboard data collected: {len(dashboard_data['summary'])} metrics, {len(dashboard_data['alerts'])} alerts"
        )


