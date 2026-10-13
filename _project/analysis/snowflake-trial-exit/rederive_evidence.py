#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import json
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

STAGE3_RUNS = {
    "tpch_baseline": "tpch_sf1_snowflake_sql_20261003_113923_e545c1c5.json",
    "tpch_untuned_a": "tpch_sf1_snowflake_sql_20261012_171902_44735cac.json",
    "tpch_untuned_b": "tpch_sf1_snowflake_sql_20261012_172155_3067b960.json",
    "tpch_tuned": "tpch_sf1_snowflake_sql_20261012_173334_97f9cfe1.json",
    "ssb_baseline": "ssb_sf1_snowflake_sql_20260919_225814_ec4b3865.json",
    "ssb_untuned": "ssb_sf1_snowflake_sql_20261012_173619_910313da.json",
    "ssb_tuned": "ssb_sf1_snowflake_sql_20261012_173845_5dff7fb3.json",
}
STAGE3_PAIRS = (
    ("tpch_baseline", "tpch_untuned_a"),
    ("tpch_baseline", "tpch_untuned_b"),
    ("tpch_untuned_a", "tpch_untuned_b"),
    ("tpch_untuned_b", "tpch_tuned"),
    ("tpch_untuned_a", "tpch_tuned"),
    ("ssb_baseline", "ssb_untuned"),
    ("ssb_untuned", "ssb_tuned"),
)
STATISTICS: dict[str, Callable[[list[float]], float]] = {
    "mean": statistics.mean,
    "median": statistics.median,
    "min": min,
}
TPCH_SHAPE_CHANGED_REASON = (
    "TPC-H power iterations ran on streams 0..3 (distinct query order and, when seeded, distinct "
    "qgen parameters per iteration); since commit ff1dec15e every power iteration runs stream 0. "
    "Data generated before commit e23ba66c9 also carries non-canonical customer and supplier addresses."
)
SHARED_DATABASES = {
    "read_primitives_sf001": "TPCH_SF001_NOTUNING_NOCONSTRAINTS",
    "transaction_primitives_sf001": "TPCH_SF001_NOTUNING_NOCONSTRAINTS",
    "write_primitives_sf001": "TPCH_SF001_NOTUNING_NOCONSTRAINTS",
    "read_primitives_sf01": "TPCH_SF01_NOTUNING_NOCONSTRAINTS",
    "transaction_primitives_sf01": "TPCH_SF01_NOTUNING_NOCONSTRAINTS",
    "write_primitives_sf01": "TPCH_SF01_NOTUNING_NOCONSTRAINTS",
    "transaction_primitives_sf1": "TPCH_SF1_NOTUNING_NOCONSTRAINTS",
    "write_primitives_sf1": "TPCH_SF1_NOTUNING_NOCONSTRAINTS",
    "tpcds_obt_sf1": "TPCDS_SF1_NOTUNING_NOCONSTRAINTS",
}


def _measurements(path: Path, include_warmup: bool) -> dict[str, list[float]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    per_query: dict[str, list[float]] = defaultdict(list)
    for query in payload["queries"]:
        if query.get("status") != "SUCCESS":
            continue
        if not include_warmup and query.get("run_type") != "measurement":
            continue
        per_query[str(query["id"])].append(float(query["ms"]))
    return dict(per_query)


def _geomean(values: list[float]) -> float:
    return math.exp(statistics.mean(math.log(value) for value in values))


def _pooled_ratio(before: dict[str, list[float]], after: dict[str, list[float]]) -> float:
    shared = sorted(set(before) & set(after))
    return _geomean([v for q in shared for v in after[q]]) / _geomean([v for q in shared for v in before[q]])


def _paired_ratio(
    before: dict[str, list[float]],
    after: dict[str, list[float]],
    stat: Callable[[list[float]], float],
    rng: random.Random,
    resamples: int,
) -> dict[str, Any]:
    shared = sorted(set(before) & set(after))
    logs = [math.log(stat(after[q]) / stat(before[q])) for q in shared]
    draws = sorted(math.exp(statistics.mean(rng.choices(logs, k=len(logs)))) for _ in range(resamples))
    return {
        "ratio": round(math.exp(statistics.mean(logs)), 3),
        "ci95_low": round(draws[int(0.025 * resamples)], 3),
        "ci95_high": round(draws[int(0.975 * resamples)], 3),
        "queries_slower": sum(1 for value in logs if value > 0),
        "queries": len(logs),
        "per_query_min": round(math.exp(min(logs)), 2),
        "per_query_max": round(math.exp(max(logs)), 2),
    }


def stage3_recompute(results_dir: Path, seed: int, resamples: int) -> dict[str, Any]:
    rng = random.Random(seed)
    measured = {name: _measurements(results_dir / file, False) for name, file in STAGE3_RUNS.items()}
    with_warmup = {name: _measurements(results_dir / file, True) for name, file in STAGE3_RUNS.items()}
    comparisons = []
    for before, after in STAGE3_PAIRS:
        comparisons.append(
            {
                "after_over_before": f"{after}/{before}",
                "pooled_geomean_ratio_measured_only": round(_pooled_ratio(measured[before], measured[after]), 3),
                "pooled_geomean_ratio_including_warmup": round(
                    _pooled_ratio(with_warmup[before], with_warmup[after]), 3
                ),
                "paired_per_query": {
                    name: _paired_ratio(measured[before], measured[after], stat, rng, resamples)
                    for name, stat in STATISTICS.items()
                },
            }
        )
    return {
        "artifact": "stage3-recompute",
        "inputs": STAGE3_RUNS,
        "method": (
            "Measured iterations only (run_type == measurement) unless labelled otherwise. Paired ratios take one "
            "statistic per query per run, then the geometric mean of after/before across queries; the 95% interval "
            f"is a percentile bootstrap over queries ({resamples} resamples, seed {seed})."
        ),
        "comparisons": comparisons,
    }


def _distinct_query_orders(results_dir: Path, file: str) -> int:
    payload = json.loads((results_dir / file).read_text(encoding="utf-8"))
    orders: dict[tuple[Any, Any], list[str]] = defaultdict(list)
    for query in payload["queries"]:
        orders[(query.get("stream"), query.get("iter"))].append(str(query["id"]))
    return len({tuple(order) for order in orders.values()})


def gate1_corrected(gate1: dict[str, Any], results_dir: Path, generated_at: str) -> dict[str, Any]:
    corrected = copy.deepcopy(gate1)
    corrected["generated_at"] = generated_at
    corrected["supersedes"] = f"gate1-rerun-matrix {gate1['generated_at']}"
    changed = []
    for cell in corrected["cells"]:
        payload = json.loads((results_dir / cell["file"]).read_text(encoding="utf-8"))
        benchmark_id = payload["benchmark"].get("id")
        if benchmark_id == "tpch" and _distinct_query_orders(results_dir, cell["file"]) > 1:
            cell["pin"] = "shape-changed"
            cell["noncomparable_reasons"] = [TPCH_SHAPE_CHANGED_REASON]
            changed.append(cell["file"])
    counts = corrected["counts"]
    counts["reproducible_exact"] = sum(1 for cell in corrected["cells"] if cell["pin"] == "reproducible-exact")
    counts["shape_changed"] = len(changed)
    counts["non_comparable"] = len(changed)
    corrected["tier3_noncomparable"] = changed
    return corrected


def gate2_corrected(gate2: dict[str, Any], generated_at: str) -> dict[str, Any]:
    corrected = copy.deepcopy(gate2)
    corrected["generated_at"] = generated_at
    corrected["supersedes"] = f"gate2-reuse-reload-map {gate2['generated_at']}"
    for row in corrected["rows"]:
        if row["verdict"] != "reload":
            continue
        key = row["file"].split("_snowflake_sql_")[0]
        row["verdict"] = "reuse-shared-database"
        row["actual_database"] = SHARED_DATABASES[key]
        row["evidence"] = (
            "Baseline bundle platform.config.database pseudonym equals the pseudonym of "
            f"{SHARED_DATABASES[key]} (sha256 of '|database|<name>' with an empty salt)."
        )
    corrected["reload_cells"] = []
    corrected["counts"] = {
        "reuse": sum(1 for row in corrected["rows"] if row["verdict"] == "reuse"),
        "reuse_shared_database": sum(1 for row in corrected["rows"] if row["verdict"] == "reuse-shared-database"),
        "reload": 0,
    }
    corrected["reuse_caveat"] = (
        "On Snowflake the existing-database check never authenticated (admin connection passed username= instead "
        "of user=), so every run took the fresh-database path: CREATE DATABASE IF NOT EXISTS, then TRUNCATE and "
        "COPY INTO each benchmark table. Reuse classifications predict real reuse only once that defect is fixed."
    )
    return corrected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--gate1", type=Path, required=True)
    parser.add_argument("--gate2", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--resamples", type=int, default=4000)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "stage3-recompute.json": stage3_recompute(args.results_dir, args.seed, args.resamples),
        "gate1-matrix-corrected.json": gate1_corrected(
            json.loads(args.gate1.read_text(encoding="utf-8")), args.results_dir, args.generated_at
        ),
        "gate2-reuse-map-corrected.json": gate2_corrected(
            json.loads(args.gate2.read_text(encoding="utf-8")), args.generated_at
        ),
    }
    for name, payload in outputs.items():
        (args.out_dir / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(args.out_dir / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
