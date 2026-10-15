# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.config import BenchmarkConfig, DatabaseConfig

_OUTPUT_DIR = _PROJECT_ROOT / "benchmark_runs" / "getting_started" / "lakesail_df"


def _build_configs(scale_factor: float) -> tuple[BenchmarkConfig, DatabaseConfig]:
    endpoint = os.getenv("SAIL_ENDPOINT", "sc://localhost:50051")
    sail_mode = os.getenv("SAIL_MODE", "local")
    sail_workers = os.getenv("SAIL_WORKERS")

    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=scale_factor,
        test_execution_type="power",
        options={"enable_preflight_validation": False},
    )

    database_config = DatabaseConfig(
        type="lakesail-df",
        name="lakesail_df_tpch",
        options={
            "endpoint": endpoint,
            "app_name": "BenchBox-LakeSail-DF-Example",
            "deployment_mode": sail_mode,
            "sail_workers": int(sail_workers) if sail_workers else None,
            "execution_mode": "dataframe",
            "table_format": "parquet",
            "driver_memory": "4g",
            "shuffle_partitions": 200,
            "adaptive_enabled": True,
        },
    )

    return benchmark_config, database_config


def run_example(scale_factor: float = 0.01, *, dry_run: bool = False) -> None:
    print("=" * 70)
    print("LakeSail Sail DataFrame TPC-H Benchmark")
    print("=" * 70)
    print()
    print("This example runs TPC-H queries using PySpark DataFrame API")
    print("on LakeSail Sail via Spark Connect protocol.")
    print()

    if dry_run:
        print("[DRY RUN] Validating configuration without connecting...")
        print()

        benchmark_config, database_config = _build_configs(scale_factor)
        print(f"  Platform: {database_config.type} (DataFrame mode)")
        print(f"  Benchmark: {benchmark_config.name}")
        print(f"  Scale Factor: {benchmark_config.scale_factor}")
        print(f"  Endpoint: {database_config.options.get('endpoint')}")
        print(f"  Sail Mode: {database_config.options.get('deployment_mode')}")
        print()
        print("[OK] Configuration valid")
        print()
        print("To compare SQL vs DataFrame performance:")
        print("  1. Run SQL mode:       python examples/getting_started/sql/lakesail_tpch.py")
        print("  2. Run DataFrame mode: python examples/getting_started/dataframe/lakesail_df_tpch.py")
        print("  3. Compare results in benchmark_runs/getting_started/")
        return

    benchmark_config, database_config = _build_configs(scale_factor)

    print("Configuration:")
    print(f"  Endpoint: {database_config.options.get('endpoint')}")
    print(f"  Sail Mode: {database_config.options.get('deployment_mode')}")
    print("  Execution Mode: DataFrame")
    print(f"  Scale Factor: {scale_factor}")
    print()

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Starting benchmark run...")
    print()

    orchestrator = BenchmarkOrchestrator(
        benchmark_config=benchmark_config,
        database_config=database_config,
        output_dir=_OUTPUT_DIR,
    )

    results = orchestrator.run()

    print()
    print("=" * 70)
    print("Results Summary")
    print("=" * 70)
    if results:
        print(f"  Total queries: {len(results.get('query_results', []))}")
        print("  Execution mode: DataFrame")
        if "total_time" in results:
            print(f"  Total time: {results['total_time']:.2f}s")
    print()
    print("Results saved to:", _OUTPUT_DIR)
    print()
    print("Next steps:")
    print("- Compare with SQL mode: run lakesail_tpch.py at the same scale factor")
    print("- Try larger scale factors to see how DataFrame overhead scales")
    print("- Review per-query timings in results.json")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TPC-H on LakeSail Sail (DataFrame mode)")
    parser.add_argument(
        "--scale",
        type=float,
        default=0.01,
        help="TPC-H scale factor (default: 0.01)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without connecting to Sail server",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_example(
        scale_factor=args.scale,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
