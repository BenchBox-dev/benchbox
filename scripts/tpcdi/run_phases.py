#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.utils.dialect_utils import sql_translation_context, summarize_sql_translation_outcomes


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run TPC-DI initialization, the historical load and the audit on DuckDB in one invocation."
    )
    parser.add_argument("--scale-factor", type=int, required=True)
    parser.add_argument("--staging", type=Path, required=True, help="DIGen output directory with its manifest")
    parser.add_argument("--database", type=Path, required=True, help="new DuckDB database file")
    parser.add_argument("--output", type=Path, required=True, help="result JSON file")
    parser.add_argument(
        "--allow-third-party-extensions",
        action="store_true",
        help="allow loading the community extension that reads CustomerMgmt.xml",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.database.exists():
        raise SystemExit(f"{args.database} exists; name a new database file")
    benchmark = TPCDIBenchmark(scale_factor=args.scale_factor, output_dir=args.database.parent / "tpcdi-output")
    benchmark.digen_staging_dir = args.staging
    adapter = DuckDBAdapter(
        database_path=str(args.database), allow_third_party_extensions=args.allow_third_party_extensions
    )
    with sql_translation_context(strict=True) as translation_outcomes:
        result = adapter.run_benchmark(benchmark, benchmark_name="tpcdi", test_execution_type="power")
    return {
        "benchmark": "tpcdi",
        "benchmark_name": result.benchmark_name,
        "platform": result.platform,
        "scale_factor": result.scale_factor,
        "staging": str(args.staging),
        "validation_status": result.validation_status,
        "validation_details": result.validation_details,
        "query_results": result.query_results,
        "translation": summarize_sql_translation_outcomes(translation_outcomes, strict_mode=True),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run(args)
    args.output.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    audit = summary["validation_details"]["tpcdi_audit"]
    print(
        f"status {summary['validation_status']}; failures {[t for t in audit['tests'] if t['evaluable'] and t['result'] != 'OK']}"
    )
    return 0 if summary["validation_status"] != "FAILED" else 1


if __name__ == "__main__":
    sys.exit(main())
