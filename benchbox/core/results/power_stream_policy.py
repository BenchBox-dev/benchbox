from __future__ import annotations

from datetime import datetime
from typing import Any

POWER_STREAM_POLICY_FIXED_STREAM_0 = "fixed-stream-0"
POWER_STREAM_POLICY_PER_ITERATION = "per-iteration-stream"

POWER_STREAM_BENCHMARK_POLICIES = {
    "tpch": POWER_STREAM_POLICY_FIXED_STREAM_0,
    "tpcds": POWER_STREAM_POLICY_PER_ITERATION,
}

TPCH_FIXED_STREAM_CUTOFF = datetime(2026, 10, 7)


def current_power_stream_policy(benchmark_id: str | None) -> str | None:
    return POWER_STREAM_BENCHMARK_POLICIES.get(str(benchmark_id or "").lower())


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def effective_power_stream_policy(data: dict[str, Any]) -> tuple[str | None, bool]:
    benchmark = data.get("benchmark") if isinstance(data, dict) else None
    if not isinstance(benchmark, dict):
        return None, False
    recorded = benchmark.get("power_stream_policy")
    if isinstance(recorded, str) and recorded.strip():
        return recorded.strip(), False
    if str(benchmark.get("id") or "").lower() != "tpch":
        return None, False
    run = data.get("run")
    started = _parse_timestamp(run.get("timestamp") if isinstance(run, dict) else None)
    if started is None:
        return None, False
    if started < TPCH_FIXED_STREAM_CUTOFF:
        return POWER_STREAM_POLICY_PER_ITERATION, True
    return POWER_STREAM_POLICY_FIXED_STREAM_0, True


def check_power_stream_compatibility(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    baseline_policy, baseline_inferred = effective_power_stream_policy(baseline)
    current_policy, current_inferred = effective_power_stream_policy(current)
    outcome: dict[str, Any] = {
        "status": "compatible",
        "compatible": True,
        "warning": None,
        "baseline": {"power_stream_policy": baseline_policy, "inferred": baseline_inferred},
        "current": {"power_stream_policy": current_policy, "inferred": current_inferred},
    }
    if baseline_policy == current_policy:
        return outcome
    if baseline_policy is None or current_policy is None:
        outcome["status"] = "unknown"
        outcome["compatible"] = None
        outcome["warning"] = (
            "One result does not record its power-stream policy; stream order and "
            "query parameters may differ between the compared runs."
        )
        return outcome
    outcome["status"] = "incompatible"
    outcome["compatible"] = False
    outcome["warning"] = (
        "Results used different power-stream policies "
        f"(baseline={baseline_policy}{' (inferred from run date)' if baseline_inferred else ''}, "
        f"current={current_policy}{' (inferred from run date)' if current_inferred else ''}); "
        "query order and seeded parameters differ, so timing deltas may not be comparable."
    )
    return outcome
