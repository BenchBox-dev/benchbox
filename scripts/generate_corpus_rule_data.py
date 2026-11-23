#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

from benchbox.core.benchmark_registry import get_scale_factor_rule, list_benchmark_ids
from benchbox.core.tpc_spec import min_streams_for_scale

DATA_PATH = CHECKOUT_ROOT / "benchbox" / "core" / "results" / "corpus_rule_data.json"
REGENERATE_COMMAND = "uv run -- python scripts/generate_corpus_rule_data.py --write"
SCHEMA_VERSION = 1
TPC_BENCHMARKS = ("tpch", "tpcds")
TPC_DERIVED_BENCHMARKS = {"datavault": "tpch", "tpch_skew": "tpch", "tpchavoc": "tpch", "tpcds_obt": "tpcds"}


def _scale_key(scale: float) -> str:
    return repr(float(scale))


def build_data() -> dict[str, Any]:
    scales: dict[str, dict[str, Any]] = {}
    for benchmark_id in sorted(list_benchmark_ids()):
        rule = get_scale_factor_rule(benchmark_id)
        scales[benchmark_id] = {"minimum": rule.minimum, "standard": sorted(rule.standard)}
    stream_minimum: dict[str, dict[str, int]] = {}
    for benchmark_id in TPC_BENCHMARKS:
        minima: dict[str, int] = {}
        for scale in scales[benchmark_id]["standard"]:
            if scale < 1.0:
                continue
            minimum = min_streams_for_scale(benchmark_id, scale)
            if minimum is not None:
                minima[_scale_key(scale)] = minimum
        stream_minimum[benchmark_id] = minima
    return {
        "schema_version": SCHEMA_VERSION,
        "scales": scales,
        "stream_minimum": stream_minimum,
        "tpc_benchmarks": list(TPC_BENCHMARKS),
        "tpc_derived_benchmarks": dict(sorted(TPC_DERIVED_BENCHMARKS.items())),
    }


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def drift(path: Path = DATA_PATH) -> str | None:
    expected = render(build_data())
    if not path.exists():
        return f"{path} does not exist. Run: {REGENERATE_COMMAND}"
    if path.read_text(encoding="utf-8") != expected:
        return f"{path} differs from the scale registry and tpc_spec.py. Run: {REGENERATE_COMMAND}"
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate or check the stdlib-only data file the corpus outcome rule reads."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail if the data file differs from its sources.")
    mode.add_argument("--write", action="store_true", help="Write the regenerated data file.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write:
        DATA_PATH.write_text(render(build_data()), encoding="utf-8")
        print(f"Wrote {DATA_PATH}")
        return 0
    problem = drift()
    if problem:
        print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    print(f"OK: {DATA_PATH.name} matches the scale registry and tpc_spec.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
