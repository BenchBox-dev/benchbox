#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

CLI_DESCRIPTION = "Build the perf-smoke baseline from several CI result files."

RULE_SLOWEST_CPU_MODEL = "slowest_cpu_model_median"
RULE_PER_QUERY_MAXIMUM = "per_query_maximum_over_all_models"

MIN_SOURCE_RESULTS = 5
FLAT_FLOOR_MS = 5
FLAT_FLOOR_SPREAD_LIMIT_MS = 4.0
SPREAD_FLOOR_FACTOR = 1.25
FLOOR_CAP_MS = 15


class BaselineError(ValueError):
    pass


def _entry_key(entry: Mapping[str, Any]) -> tuple[str, str, int]:
    return str(entry["id"]), str(entry["run_type"]), int(entry["iter"])


def _entry_layout(result: Mapping[str, Any]) -> list[tuple[str, str, int]]:
    return sorted(_entry_key(entry) for entry in result["queries"])


def _require_comparable(results: Sequence[Mapping[str, Any]]) -> None:
    if len(results) < MIN_SOURCE_RESULTS:
        raise BaselineError(f"need at least {MIN_SOURCE_RESULTS} result files, got {len(results)}")
    layout = _entry_layout(results[0])
    for result in results[1:]:
        if _entry_layout(result) != layout:
            raise BaselineError("result files record different queries or iterations")
    effective_timing_method(results)
    for result in results:
        failed = [entry for entry in result["queries"] if entry.get("status") != "SUCCESS"]
        if failed:
            raise BaselineError("a source result contains a failed query")


def effective_timing_method(results: Sequence[Mapping[str, Any]]) -> int:
    methods = {result.get("run", {}).get("timing_method") for result in results}
    if None in methods:
        raise BaselineError("a source result records no run.timing_method")
    if len(methods) != 1:
        raise BaselineError(f"source results mix timing methods {sorted(methods)}")
    return int(methods.pop())


def _final_values(result: Mapping[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    for entry in result["queries"]:
        values[str(entry["id"])] = float(entry["ms"])
    return values


def largest_spread_ms(results: Sequence[Mapping[str, Any]]) -> tuple[float, str]:
    _require_comparable(results)
    per_result = [_final_values(result) for result in results]
    worst_query, worst_spread = "", 0.0
    for query_id in per_result[0]:
        values = [values_by_query[query_id] for values_by_query in per_result]
        spread = max(values) - min(values)
        if spread > worst_spread:
            worst_query, worst_spread = query_id, spread
    return worst_spread, worst_query


def regression_floor_ms(spread_ms: float) -> int:
    if spread_ms <= FLAT_FLOOR_SPREAD_LIMIT_MS:
        return FLAT_FLOOR_MS
    return min(FLOOR_CAP_MS, math.ceil(SPREAD_FLOOR_FACTOR * spread_ms))


def _nearest_rank(sorted_values: Sequence[float], percentile: int) -> float:
    return sorted_values[min(len(sorted_values) - 1, math.ceil(percentile / 100 * len(sorted_values)) - 1)]


def runner_class(result: Mapping[str, Any]) -> str:
    return " ".join(str(result["environment"].get("cpu_model", "unknown")).lower().split())


def _total_ms(result: Mapping[str, Any]) -> float:
    return float(result["summary"]["timing"]["total_ms"])


def select_runner_class(results: Sequence[Mapping[str, Any]], *, cpu_model: str | None = None) -> str:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for result in results:
        groups.setdefault(runner_class(result), []).append(result)
    if cpu_model is not None:
        chosen = " ".join(cpu_model.lower().split())
        if len(groups.get(chosen, [])) < MIN_SOURCE_RESULTS:
            raise BaselineError(f"fewer than {MIN_SOURCE_RESULTS} source results ran on {cpu_model!r}")
        return chosen
    eligible = {name: group for name, group in groups.items() if len(group) >= MIN_SOURCE_RESULTS}
    if not eligible:
        counts = ", ".join(f"{name}: {len(group)}" for name, group in sorted(groups.items()))
        raise BaselineError(f"no CPU model has {MIN_SOURCE_RESULTS} source results ({counts})")
    return max(eligible, key=lambda name: statistics.median(_total_ms(result) for result in eligible[name]))


def select_sources(
    results: Sequence[Mapping[str, Any]], *, cpu_model: str | None = None
) -> tuple[list[Mapping[str, Any]], str, str | None]:
    try:
        selected_class = select_runner_class(results, cpu_model=cpu_model)
    except BaselineError:
        if cpu_model is not None or len(results) < MIN_SOURCE_RESULTS:
            raise
        return list(results), RULE_PER_QUERY_MAXIMUM, None
    chosen = [result for result in results if runner_class(result) == selected_class]
    return chosen, RULE_SLOWEST_CPU_MODEL, selected_class


def median_result(results: Sequence[Mapping[str, Any]], *, reducer: Any = statistics.median) -> dict[str, Any]:
    _require_comparable(results)
    latest = max(results, key=lambda result: result["execution"]["timestamp"])
    merged = copy.deepcopy(dict(latest))
    samples: dict[tuple[str, str, int], list[float]] = {}
    for result in results:
        for entry in result["queries"]:
            samples.setdefault(_entry_key(entry), []).append(float(entry["ms"]))
    for entry in merged["queries"]:
        entry["ms"] = round(reducer(samples[_entry_key(entry)]), 1)
    measured = sorted(entry["ms"] for entry in merged["queries"] if entry["run_type"] != "warmup")
    timing = merged["summary"]["timing"]
    timing.update(
        total_ms=round(sum(measured), 1),
        avg_ms=round(statistics.mean(measured), 1),
        min_ms=measured[0],
        max_ms=measured[-1],
        geometric_mean_ms=round(math.exp(sum(math.log(value) for value in measured) / len(measured)), 1),
        stdev_ms=round(statistics.stdev(measured), 1),
        p90_ms=_nearest_rank(measured, 90),
        p95_ms=_nearest_rank(measured, 95),
        p99_ms=_nearest_rank(measured, 99),
    )
    merged["run"]["query_time_ms"] = round(sum(measured))
    merged["run"]["timing_method"] = effective_timing_method(results)
    return merged


_METHODS = {
    RULE_SLOWEST_CPU_MODEL: (
        "Hosted runners come from several CPU models whose totals differ by up to 25%. The baseline uses "
        "only the source results from the slowest CPU model that has enough of them, so a slower night "
        "cannot fail the gate for hardware alone. Each query entry (warmup and every measured iteration) is "
        "the median of the same entry across those results; summary timings are recomputed from the measured "
        "entries; all other fields come from the newest of them. The floor is derived from every source "
        "result, because a night on any CPU model is compared with this baseline."
    ),
    RULE_PER_QUERY_MAXIMUM: (
        "No CPU model has enough source results, so the baseline takes every source result of the same "
        "timing method regardless of CPU model. Each query entry (warmup and every measured iteration) is "
        "the maximum of the same entry across those results, so a faster night cannot fail the gate; summary "
        "timings are recomputed from the measured entries; all other fields come from the newest of them. "
        "The floor is derived from every source result."
    ),
}


def sources_record(
    sources: Mapping[str, Mapping[str, Any]],
    baseline: Mapping[str, Any],
    spread: float,
    worst: str,
    selected_class: str | None,
    rule: str,
) -> dict[str, Any]:
    ordered = sorted(sources.items(), key=lambda item: item[1]["execution"]["timestamp"])
    environment = baseline["environment"]
    return {
        "method": _METHODS[rule],
        "selection_rule": rule,
        "timing_method": baseline["run"]["timing_method"],
        "baseline_cpu_model": environment.get("cpu_model") if selected_class is not None else None,
        "regression_floor": {
            "largest_spread_ms": round(spread, 2),
            "largest_spread_query": worst,
            "floor_ms": regression_floor_ms(spread),
            "rule": "5 ms, or 1.25 x the largest spread rounded up when that spread exceeds 4 ms, capped at 15 ms",
        },
        "duckdb": baseline["platform"].get("version"),
        "source_runs": [
            {
                "workflow_run_id": run_id,
                "benchbox_timestamp": result["execution"]["timestamp"],
                "cpu_model": result["environment"].get("cpu_model"),
                "total_ms": _total_ms(result),
                "in_baseline": selected_class is None or runner_class(result) == selected_class,
            }
            for run_id, result in ordered
        ],
    }


def parse_sources(arguments: Sequence[str]) -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for argument in arguments:
        run_id, separator, path = argument.partition("=")
        if not separator or not run_id or not path:
            raise BaselineError(f"expected RUN_ID=PATH, got {argument!r}")
        if run_id in sources:
            raise BaselineError(f"duplicate run id {run_id!r}")
        sources[run_id] = json.loads(Path(path).read_text(encoding="utf-8"))
    return sources


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("sources", nargs="+", metavar="RUN_ID=RESULT.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sources-output", type=Path, required=True)
    parser.add_argument("--cpu-model", help="build from this CPU model instead of the slowest eligible one")
    args = parser.parse_args(argv)
    try:
        sources = parse_sources(args.sources)
        results = list(sources.values())
        spread, worst = largest_spread_ms(results)
        _require_comparable(results)
        chosen, rule, selected_class = select_sources(results, cpu_model=args.cpu_model)
        reducer = max if rule == RULE_PER_QUERY_MAXIMUM else statistics.median
        baseline = median_result(chosen, reducer=reducer)
    except (BaselineError, OSError, KeyError, ValueError) as error:
        print(f"perf_smoke_baseline: error: {error}", file=sys.stderr)
        return 2
    args.output.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    record = sources_record(sources, baseline, spread, worst, selected_class, rule)
    args.sources_output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    floor = record["regression_floor"]
    print(
        f"wrote {args.output} from {len(results)} results; largest spread {spread:.2f} ms ({worst}); floor {floor['floor_ms']} ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
