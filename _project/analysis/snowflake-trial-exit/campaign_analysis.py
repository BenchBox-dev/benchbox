#!/usr/bin/env python3

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

BENEFIT_UPPER = 0.95
REGRESSION_LOWER = 1.05


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_means(payload: dict[str, Any]) -> dict[str, float]:
    per_query: dict[str, list[float]] = defaultdict(list)
    for query in payload["queries"]:
        if query.get("run_type") == "measurement" and query.get("status") == "SUCCESS":
            per_query[str(query["id"])].append(float(query["ms"]))
    return {query_id: statistics.mean(values) for query_id, values in per_query.items()}


def _checks(path: Path, payload: dict[str, Any]) -> list[str]:
    problems = []
    statuses = {query.get("status") for query in payload["queries"]}
    if statuses != {"SUCCESS"}:
        problems.append(f"{path.name}: non-success query statuses {sorted(map(str, statuses))}")
    compute = payload.get("platform", {}).get("compute", {})
    if compute.get("warehouse_size") != "X-Small":
        problems.append(f"{path.name}: warehouse_size {compute.get('warehouse_size')}")
    if not (compute.get("cache_control") or {}).get("cache_disabled"):
        problems.append(f"{path.name}: result cache not recorded as disabled")
    loading = payload.get("phases", {}).get("data_loading", {})
    if loading.get("status") not in (None, "NOT_RUN", "SKIPPED") and (loading.get("duration_ms") or 0) > 0:
        problems.append(f"{path.name}: data_loading ran for {loading.get('duration_ms')} ms (expected reuse)")
    return problems


def _geomean(values: list[float]) -> float:
    return math.exp(statistics.mean(math.log(value) for value in values))


def _arm(runs: list[dict[str, float]], query_ids: list[str]) -> dict[str, float]:
    return {query_id: statistics.median(run[query_id] for run in runs) for query_id in query_ids}


def _ratio(before: dict[str, float], after: dict[str, float], query_ids: list[str]) -> float:
    return _geomean([after[q] / before[q] for q in query_ids])


def analyse(untuned_paths: list[Path], tuned_paths: list[Path], seed: int, resamples: int) -> dict[str, Any]:
    payloads = {path: _load(path) for path in untuned_paths + tuned_paths}
    problems = [problem for path, payload in payloads.items() for problem in _checks(path, payload)]
    untuned_runs = [_run_means(payloads[path]) for path in untuned_paths]
    tuned_runs = [_run_means(payloads[path]) for path in tuned_paths]
    query_ids = sorted(set.intersection(*(set(run) for run in untuned_runs + tuned_runs)))
    all_ids = set().union(*(set(run) for run in untuned_runs + tuned_runs))
    if set(query_ids) != all_ids:
        problems.append(f"query sets differ across runs; analysed {len(query_ids)} of {len(all_ids)}")
    untuned = _arm(untuned_runs, query_ids)
    tuned = _arm(tuned_runs, query_ids)
    logs = [math.log(tuned[q] / untuned[q]) for q in query_ids]
    rng = random.Random(seed)
    draws = sorted(math.exp(statistics.mean(rng.choices(logs, k=len(logs)))) for _ in range(resamples))
    ratio = math.exp(statistics.mean(logs))
    low, high = draws[int(0.025 * resamples)], draws[int(0.975 * resamples)]
    if high < BENEFIT_UPPER:
        verdict = "benefit"
    elif low > REGRESSION_LOWER:
        verdict = "regression"
    else:
        verdict = "no detectable effect"
    untuned_noise = [_ratio(a, b, query_ids) for a, b in itertools.combinations(untuned_runs, 2)]
    tuned_noise = [_ratio(a, b, query_ids) for a, b in itertools.combinations(tuned_runs, 2)]
    noise_bound = max([abs(math.log(value)) for value in untuned_noise + tuned_noise] or [0.0])
    return {
        "artifact": "tuned-vs-untuned",
        "untuned_runs": [path.name for path in untuned_paths],
        "tuned_runs": [path.name for path in tuned_paths],
        "queries": len(query_ids),
        "method": (
            "Per run, the mean of measured iterations per query; per arm, the median of run means per query; "
            "ratio is the geometric mean of tuned/untuned across queries with a percentile bootstrap over queries "
            f"({resamples} resamples, seed {seed}). Verdict: benefit if the 95% upper bound < {BENEFIT_UPPER}, "
            f"regression if the lower bound > {REGRESSION_LOWER}, otherwise no detectable effect."
        ),
        "ratio_tuned_over_untuned": round(ratio, 3),
        "ci95_low": round(low, 3),
        "ci95_high": round(high, 3),
        "verdict": verdict,
        "exceeds_same_arm_noise": abs(math.log(ratio)) > noise_bound,
        "same_arm_ratios": {
            "untuned_pairs": [round(value, 3) for value in untuned_noise],
            "tuned_pairs": [round(value, 3) for value in tuned_noise],
        },
        "queries_faster": sum(1 for value in logs if value < 0),
        "per_query": {
            q: {
                "untuned_ms": round(untuned[q], 1),
                "tuned_ms": round(tuned[q], 1),
                "ratio": round(tuned[q] / untuned[q], 3),
            }
            for q in query_ids
        },
        "check_problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--untuned", type=Path, nargs="+", required=True)
    parser.add_argument("--tuned", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--resamples", type=int, default=4000)
    args = parser.parse_args()
    result = analyse(args.untuned, args.tuned, args.seed, args.resamples)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: result[k] for k in ("ratio_tuned_over_untuned", "ci95_low", "ci95_high", "verdict", "check_problems")}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
