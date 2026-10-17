from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

SUBSCALE_COMPLIANCE_CLASS = "unofficial_subscale"
NONSTANDARD_COMPLIANCE_CLASS = "unofficial_nonstandard"
RANKING_EXCLUDED_COMPLIANCE_CLASSES = frozenset({NONSTANDARD_COMPLIANCE_CLASS})
SUBSCALE_TPC_BENCHMARKS = frozenset({"tpch", "tpcds"})
POWER_SCORE_RECORDED = "recorded"
POWER_SCORE_DERIVED_FROM_TIMINGS = "derived_from_timings"

_NON_POWER_TEST_TYPES = frozenset({"throughput", "maintenance"})


def is_subscale_tpc(benchmark_id: Any, scale_factor: Any) -> bool:
    try:
        scale = float(scale_factor)
    except (TypeError, ValueError):
        return False
    return str(benchmark_id or "").lower() in SUBSCALE_TPC_BENCHMARKS and 0 < scale < 1.0


def effective_compliance_class(benchmark_id: Any, scale_factor: Any, recorded: Any) -> str | None:
    if recorded is not None:
        return str(getattr(recorded, "value", recorded))
    if is_subscale_tpc(benchmark_id, scale_factor):
        return SUBSCALE_COMPLIANCE_CLASS
    return None


def unofficial_metric_marker(compliance_class: str) -> dict[str, Any]:
    return {"unofficial": True, "reason": f"compliance_class={compliance_class}"}


def derive_power_at_size(queries: Iterable[Mapping[str, Any]], scale_factor: Any) -> float | None:
    try:
        scale = float(scale_factor)
    except (TypeError, ValueError):
        return None
    measurement = [
        query
        for query in queries
        if isinstance(query, Mapping)
        and query.get("run_type") == "measurement"
        and _iteration(query) > 0
        and str(query.get("test_type") or "power").lower() not in _NON_POWER_TEST_TYPES
    ]
    if not measurement or scale <= 0:
        return None
    final_iteration = max(_iteration(query) for query in measurement)
    final = [query for query in measurement if _iteration(query) == final_iteration]
    if any(str(query.get("status") or "").upper() != "SUCCESS" for query in final):
        return None
    seconds = [ms / 1000.0 for query in final if (ms := _duration_ms(query)) is not None and ms > 0]
    if not seconds:
        return None
    geometric_mean = math.exp(sum(math.log(value) for value in seconds) / len(seconds))
    return (scale * 3600) / geometric_mean


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
