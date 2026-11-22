from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

POWER_QUERY_COUNTS = {"tpch": 22, "tpcds": 99}
SCORE_DERIVED_FROM_TIMINGS = "derived_from_timings"

_NON_POWER_TEST_TYPES = frozenset({"throughput", "maintenance"})
_PASSING_STATUSES = frozenset({"SUCCESS", "PASS"})


def tpc_benchmark_id(data: Mapping[str, Any]) -> str | None:
    benchmark = data.get("benchmark")
    raw = benchmark.get("id") if isinstance(benchmark, Mapping) else None
    squashed = "".join(character for character in str(raw or "").lower() if character.isalnum())
    return squashed if squashed in POWER_QUERY_COUNTS else None


def _scale_factor(data: Mapping[str, Any]) -> float | None:
    benchmark = data.get("benchmark")
    value = benchmark.get("scale_factor") if isinstance(benchmark, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    scale = float(value)
    return scale if math.isfinite(scale) and scale > 0 else None


def _iteration(query: Mapping[str, Any]) -> int:
    value = query.get("iter", query.get("iteration"))
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _duration_ms(query: Mapping[str, Any]) -> float | None:
    value = query.get("ms", query.get("execution_time_ms"))
    if value is None or isinstance(value, bool):
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if math.isfinite(duration) else None


def _queries(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    queries = data.get("queries")
    return [query for query in queries if isinstance(query, Mapping)] if isinstance(queries, list) else []


def power_at_size(data: Mapping[str, Any]) -> float | None:
    benchmark_id = tpc_benchmark_id(data)
    scale = _scale_factor(data)
    if benchmark_id is None or scale is None:
        return None
    measurement = [
        query
        for query in _queries(data)
        if query.get("run_type") == "measurement"
        and _iteration(query) > 0
        and str(query.get("test_type") or "power").lower() not in _NON_POWER_TEST_TYPES
    ]
    if not measurement:
        return None
    final_iteration = max(_iteration(query) for query in measurement)
    final = [query for query in measurement if _iteration(query) == final_iteration]
    if any(str(query.get("status") or "").upper() not in _PASSING_STATUSES for query in final):
        return None
    if len({str(query.get("id", query.get("query_id"))) for query in final}) < POWER_QUERY_COUNTS[benchmark_id]:
        return None
    durations = [_duration_ms(query) for query in final]
    if any(ms is None or ms <= 0 for ms in durations):
        return None
    seconds = [ms / 1000.0 for ms in durations if ms is not None]
    geometric_mean = math.exp(sum(math.log(value) for value in seconds) / len(seconds))
    return (scale * 3600) / geometric_mean


def throughput_at_size(data: Mapping[str, Any]) -> float | None:
    scale = _scale_factor(data)
    phases = data.get("phases")
    block = phases.get("throughput_test") if isinstance(phases, Mapping) else None
    duration_ms = block.get("duration_ms") if isinstance(block, Mapping) else None
    if scale is None or isinstance(duration_ms, bool) or not isinstance(duration_ms, int | float) or duration_ms <= 0:
        return None
    executed = sum(
        1
        for query in _queries(data)
        if query.get("test_type") == "throughput" and query.get("run_type") == "measurement"
    )
    return executed * scale * 3600 / (duration_ms / 1000) if executed else None
