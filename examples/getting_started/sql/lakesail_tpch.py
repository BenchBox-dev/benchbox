# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import argparse
import os
from pathlib import Path

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.config import BenchmarkConfig, DatabaseConfig
from benchbox.core.system import SystemProfiler
from benchbox.examples import execute_example_dry_run

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _PROJECT_ROOT / "benchmark_runs" / "getting_started" / "lakesail"


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
        type=f"lakesail:{sail_mode}",
        name="lakesail_tpch",
        options={
            "endpoint": endpoint,
            "app_name": "BenchBox-LakeSail-Example",
            "sail_workers": int(sail_workers) if sail_workers else None,
            "table_format": "parquet",
            "driver_memory": "4g",
            "shuffle_partitions": 200,
            "adaptive_enabled": True,
        },
    )

    return benchmark_config, database_config


def run_example(scale_factor: float = 0.01, *, dry_run_output: Path | None = None) -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    benchmark_config, database_config = _build_configs(scale_factor)

    if dry_run_output is not None:
        execute_example_dry_run(
            benchmark_config=benchmark_config,
            database_config=database_config,
            output_dir=Path(dry_run_output),
            filename_prefix="lakesail_tpch",
        )
        print()
        print("Dry-run complete! Review SQL files before running on LakeSail Sail.")
        print()
        print("Before running:")
        print("1. Start a Sail server: sail-server start (or via Docker)")
        print(f"2. Verify server is reachable at {database_config.options.get('endpoint')}")
        print("3. Review generated SQL for Spark dialect compatibility")
        return

    profiler = SystemProfiler()
    system_profile = profiler.get_system_profile()

    orchestrator = BenchmarkOrchestrator(base_dir=str(_OUTPUT_DIR))

    result = orchestrator.execute_benchmark(
        config=benchmark_config,
        system_profile=system_profile,
        database_config=database_config,
        phases_to_run=["generate", "load", "power"],
    )

    print(f"Benchmark completed at scale factor {scale_factor} with {result.successful_queries} successful queries")
    print(f"Total runtime (s): {result.total_execution_time:.2f}")
    print()
    print("Results saved to:", _OUTPUT_DIR)
    print()
    print("Next steps:")
    print("- Review results.json for detailed query timings")
    print("- Compare with Apache Spark to measure Sail's performance advantage")
    print("- Try distributed mode for larger scale factors (SF=10+)")
    print("- Run the DataFrame variant: lakesail_df_tpch.py")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TPC-H on LakeSail Sail (SQL mode)")
    parser.add_argument("--scale", type=float, default=0.01, help="Benchmark scale factor")
    parser.add_argument(
        "--dry-run",
        type=str,
        metavar="OUTPUT_DIR",
        help="Preview without executing; write artifacts to OUTPUT_DIR.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_example(
        scale_factor=args.scale,
        dry_run_output=Path(args.dry_run) if args.dry_run else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
