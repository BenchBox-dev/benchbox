#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from benchbox.core.expected_results.query_generation import current_query_generation_hash
from benchbox.core.expected_results.row_count_references import QUERY_IDS
from benchbox.core.expected_results.structural_bounds import (
    STRUCTURAL_BOUNDS_PATH,
    StructuralBound,
    derive_structural_bound,
    validate_structural_table,
)

SEEDS = (None, 1001, 1002, 7, 42, 2024, 1125143012)
_QUERY_ID = re.compile(r"(\d+)([ab]?)")


def _bound_for(benchmark: str, query_id: str, scale_factor: float, instance) -> StructuralBound:
    bounds = set()
    for seed in SEEDS:
        if benchmark == "tpch":
            sql = instance.get_query(int(query_id), seed=seed, scale_factor=scale_factor, dialect="duckdb")
        else:
            match = _QUERY_ID.fullmatch(query_id)
            kwargs = {"variant": match.group(2)} if match.group(2) else {}
            sql = instance.get_query(
                int(match.group(1)),
                seed=1001 if seed is None else seed,
                scale_factor=scale_factor,
                dialect="duckdb",
                **kwargs,
            )
        bounds.add(derive_structural_bound(sql))
    if len(bounds) != 1:
        raise SystemExit(f"{benchmark} query {query_id}: the structural bound differs between seeds: {bounds}")
    return bounds.pop()


def build_table(scale_factor: float = 1.0) -> dict:
    from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    table: dict = {"schema_version": 1}
    with tempfile.TemporaryDirectory() as work_dir:
        instances = {
            "tpch": TPCHBenchmark(scale_factor=scale_factor, output_dir=work_dir),
            "tpcds": TPCDSBenchmark(scale_factor=scale_factor, output_dir=work_dir),
        }
        for benchmark, instance in instances.items():
            limits: dict[str, int] = {}
            one_row: list[str] = []
            for query_id in QUERY_IDS[benchmark]:
                bound = _bound_for(benchmark, query_id, scale_factor, instance)
                if bound.one_row:
                    one_row.append(query_id)
                elif bound.limit is not None:
                    limits[query_id] = bound.limit
            table[benchmark] = {"limit": limits, "one_row": one_row}
    table["query_generation"] = {name: current_query_generation_hash(name) for name in ("tpch", "tpcds")}
    validate_structural_table(table)
    return table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rendered = json.dumps(build_table(), indent=2) + "\n"
    if args.check:
        return 0 if Path(STRUCTURAL_BOUNDS_PATH).read_text(encoding="utf-8") == rendered else 1
    Path(STRUCTURAL_BOUNDS_PATH).write_text(rendered, encoding="utf-8")
    print(f"Wrote {STRUCTURAL_BOUNDS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
