from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

TPCH_MIN_STREAMS: Mapping[float, int] = {
    1.0: 2,
    10.0: 3,
    30.0: 4,
    100.0: 5,
    300.0: 6,
    1000.0: 7,
    3000.0: 8,
    10000.0: 9,
    30000.0: 10,
    100000.0: 11,
}

TPCDS_MIN_STREAMS: Mapping[float, int] = {
    100.0: 3,
    300.0: 5,
    1000.0: 7,
    3000.0: 8,
    10000.0: 9,
    30000.0: 10,
    100000.0: 11,
}

TPCDS_FLOOR_STREAMS = 4

FULL_SPEC_BENCHMARKS = frozenset({"tpch", "tpcds"})

_REQUIRED_PHASES: Mapping[str, tuple[str, ...]] = {
    "tpch": ("power", "throughput"),
    "tpcds": ("power", "throughput", "maintenance"),
}

_LABELS = {"tpch": "TPC-H", "tpcds": "TPC-DS"}


def min_streams_for_scale(benchmark: str, scale_factor: float) -> int | None:
    name = (benchmark or "").lower()
    scale = float(scale_factor)
    if name == "tpch":
        return TPCH_MIN_STREAMS.get(scale)
    if name == "tpcds":
        base = max(TPCDS_MIN_STREAMS.get(scale, 0), TPCDS_FLOOR_STREAMS)
        return base + base % 2
    return None


def full_spec_violations(
    benchmark: str | None,
    scale_factor: float,
    *,
    seed: int | None,
    phases: Iterable[str] | None,
    streams: int | None,
    query_subset: Sequence[str] | None,
    refresh_functions: bool | None = None,
) -> list[str]:
    name = (benchmark or "").lower()
    if name not in FULL_SPEC_BENCHMARKS:
        return []
    label = _LABELS[name]
    violations: list[str] = []

    if seed is None:
        violations.append("a --seed is required so the run is reproducible")

    if query_subset:
        violations.append(f"query subsets are not allowed; all {label} queries must run (remove --queries)")

    phase_set = {str(p).strip().lower() for p in phases or ()}
    missing = [p for p in _REQUIRED_PHASES[name] if p not in phase_set]
    if missing:
        violations.append(
            f"partial runs are not allowed; missing phases: {', '.join(missing)} "
            f"(required: {', '.join(_REQUIRED_PHASES[name])})"
        )

    if "throughput" in _REQUIRED_PHASES[name]:
        minimum = min_streams_for_scale(name, scale_factor)
        if streams is None:
            violations.append(
                f"--streams is required; {label} scale factor {scale_factor:g} needs at least {minimum} streams"
            )
        elif minimum is not None and streams < minimum:
            violations.append(
                f"{label} scale factor {scale_factor:g} requires at least {minimum} throughput streams; got {streams}"
            )
        elif name == "tpcds" and streams % 2:
            violations.append(f"{label} requires an even number of throughput streams; got {streams}")

    if name == "tpch" and refresh_functions is not True:
        violations.append(
            "TPC-H refresh functions RF1/RF2 must run in the power and throughput tests "
            "(--benchmark-option tpch_refresh_functions=true)"
        )

    return violations


def require_full_spec(
    benchmark: str | None,
    scale_factor: float,
    *,
    seed: int | None,
    phases: Iterable[str] | None,
    streams: int | None,
    query_subset: Sequence[str] | None,
    refresh_functions: bool | None = None,
) -> None:
    violations = full_spec_violations(
        benchmark,
        scale_factor,
        seed=seed,
        phases=phases,
        streams=streams,
        query_subset=query_subset,
        refresh_functions=refresh_functions,
    )
    if violations:
        label = _LABELS.get((benchmark or "").lower(), "TPC")
        details = "\n".join(f"  - {item}" for item in violations)
        raise ValueError(f"--official requires the full {label} specification; this run does not meet it:\n{details}")


def refresh_functions_enabled(config_options: Mapping[str, Any] | None, benchmark_instance: Any = None) -> bool:
    options = dict(config_options or {})
    benchmark_options = options.get("benchmark_options") or {}
    for key in ("tpch_refresh_functions", "tpch-refresh-functions"):
        if key in benchmark_options:
            return benchmark_options[key] is True
    return getattr(benchmark_instance, "tpch_refresh_functions", False) is True
