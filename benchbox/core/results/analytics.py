# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from benchbox.core.results.metrics import calculate_named_metric, percentile_ms, sample_stdev_ms
from benchbox.core.results.regression_policy import (
    classify_change,
    classify_severity,
    classify_trend,
    percent_change,
)
from benchbox.validation.bundle import COMPANION_SUFFIXES

logger = logging.getLogger(__name__)


def _list_result_files(results_dir: Path) -> list[Path]:
    result_files = [path for path in results_dir.glob("*.json") if not path.name.endswith(COMPANION_SUFFIXES)]
    return sorted(result_files, key=lambda p: p.stat().st_mtime, reverse=True)


def _extract_measurement_timings(data: dict[str, Any]) -> list[float]:
    timings: list[float] = []
    for query in data.get("queries", []):
        if query.get("run_type") != "measurement":
            continue
        runtime = query.get("ms")
        if runtime is not None and runtime > 0:
            timings.append(float(runtime))
    return timings


def _matches_filters(
    run_platform: str,
    run_benchmark: str,
    platform: str | None,
    benchmark: str | None,
) -> bool:
    if platform and platform.lower() not in run_platform.lower():
        return False
    if benchmark and benchmark.lower() not in run_benchmark.lower():
        return False
    return True


def _extract_run_identity(data: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    run_platform = data.get("platform", {}).get("name", "unknown")
    benchmark_block = data.get("benchmark", {}) if isinstance(data.get("benchmark"), dict) else {}
    run_benchmark = benchmark_block.get("id", "unknown")
    return run_platform, benchmark_block, run_benchmark


def _extract_keyed_timings(run_data: dict) -> dict[str, float]:
    timings: dict[str, float] = {}
    for query in run_data.get("queries", []):
        if query.get("run_type") != "measurement":
            continue
        qid = str(query.get("id", ""))
        runtime = query.get("ms")
        if qid and runtime is not None:
            timings[qid] = float(runtime)
    return timings


def _classify_query_changes(
    older_timings: dict[str, float],
    newer_timings: dict[str, float],
    threshold_percent: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    stable: list[str] = []

    all_queries = set(older_timings.keys()) | set(newer_timings.keys())
    for qid in sorted(all_queries):
        old_time = older_timings.get(qid)
        new_time = newer_timings.get(qid)

        if old_time is None or new_time is None or old_time <= 0:
            continue

        delta_ms = new_time - old_time
        delta_pct = percent_change(old_time, new_time)
        if delta_pct is None:
            continue

        change_class = classify_change(delta_pct, threshold_percent)
        if change_class == "regression":
            regressions.append(
                {
                    "query_id": qid,
                    "baseline_ms": round(old_time, 2),
                    "current_ms": round(new_time, 2),
                    "delta_ms": round(delta_ms, 2),
                    "delta_percent": round(delta_pct, 1),
                    "severity": classify_severity(delta_pct),
                }
            )
        elif change_class == "improvement":
            improvements.append(
                {
                    "query_id": qid,
                    "baseline_ms": round(old_time, 2),
                    "current_ms": round(new_time, 2),
                    "delta_ms": round(delta_ms, 2),
                    "delta_percent": round(delta_pct, 1),
                }
            )
        else:
            stable.append(qid)

    regressions.sort(key=lambda r: r["delta_percent"], reverse=True)
    return regressions, improvements, stable


def _load_regression_runs(
    result_files: list[Path],
    platform: str | None,
    benchmark: str | None,
    lookback_runs: int,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for file_path in result_files[: lookback_runs * 2]:
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)

            run_platform, benchmark_block, run_benchmark = _extract_run_identity(data)

            if not _matches_filters(run_platform, run_benchmark, platform, benchmark):
                continue

            from benchbox.core.gateway import variants_comparable

            if runs and not variants_comparable(runs[0]["data"], data):
                continue

            runs.append(
                {
                    "file": file_path.name,
                    "path": str(file_path),
                    "platform": run_platform,
                    "benchmark": run_benchmark,
                    "scale_factor": benchmark_block.get("scale_factor"),
                    "timestamp": data.get("run", {}).get("timestamp", file_path.stat().st_mtime),
                    "data": data,
                }
            )

            if len(runs) >= lookback_runs:
                break

        except Exception as e:
            logger.warning("Could not parse result file %s (%s)", file_path.name, type(e).__name__)
            continue
    return runs


def _resolve_timestamp_str(timestamp: Any, file_path: Path) -> str:
    if timestamp:
        try:
            if isinstance(timestamp, str):
                ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                ts = datetime.fromtimestamp(timestamp)
            return ts.isoformat()
        except Exception:
            return str(timestamp)
    return datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()


def _load_trend_data_point(
    file_path: Path,
    platform: str | None,
    benchmark: str | None,
    metric_lower: str,
) -> dict[str, Any] | None:
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("Could not parse result file %s (%s)", file_path.name, type(e).__name__)
        return None

    run_platform = data.get("platform", {}).get("name", "unknown")
    benchmark_block = data.get("benchmark", {}) if isinstance(data.get("benchmark"), dict) else {}
    run_benchmark = benchmark_block.get("id", "unknown")

    if platform and platform.lower() not in run_platform.lower():
        return None
    if benchmark and benchmark.lower() not in run_benchmark.lower():
        return None

    timings = _extract_measurement_timings(data)
    if not timings:
        return None

    metric_value = calculate_named_metric(timings, metric_lower)
    timestamp_str = _resolve_timestamp_str(data.get("run", {}).get("timestamp"), file_path)

    return {
        "file": file_path.name,
        "platform": run_platform,
        "benchmark": run_benchmark,
        "scale_factor": benchmark_block.get("scale_factor"),
        "timestamp": timestamp_str,
        "query_count": len(timings),
        "metric": metric_lower,
        "value": round(metric_value, 2),
    }


def _resolve_date_group_key(data: dict[str, Any], file_path: Path) -> str:
    timestamp = data.get("run", {}).get("timestamp", file_path.stat().st_mtime)
    if isinstance(timestamp, str):
        try:
            ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            return ts.strftime("%Y-%m-%d")
        except Exception:
            return timestamp[:10] if len(timestamp) >= 10 else "unknown"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def _resolve_group_key(
    group_by_lower: str,
    run_platform: str,
    run_benchmark: str,
    data: dict[str, Any],
    file_path: Path,
) -> str:
    if group_by_lower == "platform":
        return run_platform
    elif group_by_lower == "benchmark":
        return run_benchmark
    return _resolve_date_group_key(data, file_path)


def _compute_group_stats(runs: list[dict[str, Any]]) -> dict[str, Any]:
    all_timings = [t for run in runs for t in run["timings"]]
    total_times = [run["total_time"] for run in runs]

    return {
        "run_count": len(runs),
        "total_queries": len(all_timings),
        "query_stats": {
            "mean_ms": round(sum(all_timings) / len(all_timings), 2) if all_timings else 0,
            "std_ms": round(sample_stdev_ms(all_timings), 2) if len(all_timings) > 1 else 0,
            "min_ms": round(min(all_timings), 2) if all_timings else 0,
            "max_ms": round(max(all_timings), 2) if all_timings else 0,
            "p50_ms": round(percentile_ms(all_timings, 0.50), 2) if all_timings else 0,
            "p95_ms": round(percentile_ms(all_timings, 0.95), 2) if all_timings else 0,
        },
        "run_stats": {
            "mean_total_ms": round(sum(total_times) / len(total_times), 2) if total_times else 0,
            "std_total_ms": round(sample_stdev_ms(total_times), 2) if len(total_times) > 1 else 0,
            "min_total_ms": round(min(total_times), 2) if total_times else 0,
            "max_total_ms": round(max(total_times), 2) if total_times else 0,
        },
        "files": [run["file"] for run in runs],
    }


def compare_results(
    file1: Path,
    file2: Path,
    threshold_percent: float = 10.0,
    *,
    anonymize: bool = False,
) -> dict[str, Any]:
    from benchbox.core.results.exporter import ResultExporter
    from benchbox.utils.printing import get_quiet_console

    exporter = ResultExporter(anonymize=anonymize, console=get_quiet_console())
    comparison = exporter.compare_results(file1, file2)
    if "error" in comparison:
        return comparison

    regressions: list[str] = []
    improvements: list[str] = []
    stable: list[str] = []

    for entry in comparison.get("query_comparisons", []):
        change_pct = entry.get("change_percent", 0)
        if change_pct > threshold_percent:
            regressions.append(entry.get("query_id"))
        elif change_pct < -threshold_percent:
            improvements.append(entry.get("query_id"))
        else:
            stable.append(entry.get("query_id"))

    comparison["summary"] = {
        "total_queries_compared": len(comparison.get("query_comparisons", [])),
        "regressions": len(regressions),
        "improvements": len(improvements),
        "stable": len(stable),
        "threshold_percent": threshold_percent,
    }
    comparison["regressions"] = [q for q in regressions if q]
    comparison["improvements"] = [q for q in improvements if q]

    return comparison


def detect_regressions(
    results_dir: Path,
    platform: str | None = None,
    benchmark: str | None = None,
    threshold_percent: float = 10.0,
    lookback_runs: int = 10,
) -> dict[str, Any]:
    if not results_dir.exists():
        return {"status": "no_data", "message": f"No results directory found at {results_dir}", "regressions": []}

    result_files = _list_result_files(results_dir)
    if len(result_files) < 2:
        return {
            "status": "insufficient_data",
            "message": f"Need at least 2 benchmark runs for comparison, found {len(result_files)}",
            "regressions": [],
        }

    runs = _load_regression_runs(result_files, platform, benchmark, lookback_runs)

    if len(runs) < 2:
        return {
            "status": "insufficient_data",
            "message": f"Need at least 2 comparable runs, found {len(runs)} matching filters",
            "filters_applied": {"platform": platform, "benchmark": benchmark},
            "regressions": [],
        }

    newer_run = runs[0]
    older_run = runs[1]

    older_timings = _extract_keyed_timings(older_run["data"])
    newer_timings = _extract_keyed_timings(newer_run["data"])

    regressions, improvements, stable = _classify_query_changes(older_timings, newer_timings, threshold_percent)

    all_queries = set(older_timings.keys()) | set(newer_timings.keys())
    total_old = sum(older_timings.values())
    total_new = sum(newer_timings.values())
    total_delta_pct = ((total_new - total_old) / total_old * 100) if total_old > 0 else 0

    return {
        "status": "completed",
        "comparison": {
            "baseline": {
                "file": older_run["file"],
                "platform": older_run["platform"],
                "benchmark": older_run["benchmark"],
                "timestamp": older_run["timestamp"],
            },
            "current": {
                "file": newer_run["file"],
                "platform": newer_run["platform"],
                "benchmark": newer_run["benchmark"],
                "timestamp": newer_run["timestamp"],
            },
        },
        "summary": {
            "total_queries": len(all_queries),
            "regressions": len(regressions),
            "improvements": len(improvements),
            "stable": len(stable),
            "total_runtime_delta_percent": round(total_delta_pct, 1),
            "threshold_percent": threshold_percent,
        },
        "regressions": regressions,
        "improvements": improvements[:5],
    }


def get_performance_trends(
    results_dir: Path,
    platform: str | None = None,
    benchmark: str | None = None,
    metric: str = "geometric_mean",
    limit: int = 10,
) -> dict[str, Any]:
    valid_metrics = ["geometric_mean", "p50", "p95", "p99", "total_time"]
    metric_lower = metric.lower()
    if metric_lower not in valid_metrics:
        return {
            "error": f"Invalid metric: {metric}",
            "error_code": "VALIDATION_ERROR",
            "details": {"valid_metrics": valid_metrics},
        }

    if not results_dir.exists():
        return {"status": "no_data", "message": f"No results directory found at {results_dir}", "trends": []}

    result_files = _list_result_files(results_dir)

    runs: list[dict[str, Any]] = []
    for file_path in result_files:
        if len(runs) >= limit:
            break
        data_point = _load_trend_data_point(file_path, platform, benchmark, metric_lower)
        if data_point is not None:
            runs.append(data_point)

    if not runs:
        return {
            "status": "no_matching_data",
            "message": "No benchmark runs match the specified filters",
            "filters_applied": {"platform": platform, "benchmark": benchmark},
            "trends": [],
        }

    runs.reverse()
    trend_direction, trend_pct = classify_trend([run["value"] for run in runs])

    return {
        "status": "success",
        "metric": metric_lower,
        "filters_applied": {"platform": platform, "benchmark": benchmark, "limit": limit},
        "summary": {
            "run_count": len(runs),
            "first_run": runs[0]["timestamp"] if runs else None,
            "last_run": runs[-1]["timestamp"] if runs else None,
            "trend_direction": trend_direction,
            "trend_percent": round(trend_pct, 1),
        },
        "data_points": runs,
    }


def aggregate_results(
    results_dir: Path,
    platform: str | None = None,
    benchmark: str | None = None,
    group_by: str = "platform",
) -> dict[str, Any]:
    valid_group_by = ["platform", "benchmark", "date"]
    group_by_lower = group_by.lower()
    if group_by_lower not in valid_group_by:
        return {
            "error": f"Invalid group_by: {group_by}",
            "error_code": "VALIDATION_ERROR",
            "details": {"valid_options": valid_group_by},
        }

    if not results_dir.exists():
        return {"status": "no_data", "message": f"No results directory found at {results_dir}", "aggregates": {}}

    result_files = _list_result_files(results_dir)
    groups: dict[str, list[dict[str, Any]]] = {}

    for file_path in result_files:
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)

            run_platform, benchmark_block, run_benchmark = _extract_run_identity(data)

            if not _matches_filters(run_platform, run_benchmark, platform, benchmark):
                continue

            timings = _extract_measurement_timings(data)
            if not timings:
                continue

            group_key = _resolve_group_key(group_by_lower, run_platform, run_benchmark, data, file_path)
            groups.setdefault(group_key, []).append(
                {
                    "file": file_path.name,
                    "timings": timings,
                    "total_time": sum(timings),
                    "query_count": len(timings),
                    "scale_factor": benchmark_block.get("scale_factor"),
                }
            )

        except Exception as e:
            logger.warning("Could not parse result file %s (%s)", file_path.name, type(e).__name__)
            continue

    if not groups:
        return {
            "status": "no_matching_data",
            "message": "No benchmark runs match the specified filters",
            "filters_applied": {"platform": platform, "benchmark": benchmark},
            "aggregates": {},
        }

    aggregates = {key: _compute_group_stats(runs) for key, runs in sorted(groups.items())}

    return {
        "status": "success",
        "group_by": group_by_lower,
        "filters_applied": {"platform": platform, "benchmark": benchmark},
        "summary": {
            "total_groups": len(aggregates),
            "total_runs": sum(a["run_count"] for a in aggregates.values()),
        },
        "aggregates": aggregates,
    }
