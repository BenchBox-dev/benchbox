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
_OUTPUT_DIR = _PROJECT_ROOT / "benchmark_runs" / "getting_started" / "comparison"


def _build_benchmark_config(scale_factor: float) -> BenchmarkConfig:
    return BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=scale_factor,
        test_execution_type="power",
        options={"enable_preflight_validation": False},
    )


def _build_lakesail_config() -> DatabaseConfig:
    endpoint = os.getenv("SAIL_ENDPOINT", "sc://localhost:50051")
    return DatabaseConfig(
        type="lakesail:local",
        name="lakesail_comparison",
        options={
            "endpoint": endpoint,
            "app_name": "BenchBox-Comparison-LakeSail",
            "table_format": "parquet",
            "driver_memory": "4g",
            "shuffle_partitions": 200,
            "adaptive_enabled": True,
        },
    )


def _build_spark_config() -> DatabaseConfig:
    endpoint = os.getenv("SPARK_ENDPOINT", "sc://localhost:15002")
    return DatabaseConfig(
        type="spark",
        name="spark_comparison",
        options={
            "master": endpoint,
            "app_name": "BenchBox-Comparison-Spark",
            "driver_memory": "4g",
            "shuffle_partitions": 200,
            "adaptive_enabled": True,
        },
    )


def _dry_run(benchmark_config: BenchmarkConfig, output_dir: Path) -> None:
    for name, database_config in (("lakesail", _build_lakesail_config()), ("spark", _build_spark_config())):
        execute_example_dry_run(
            benchmark_config=benchmark_config,
            database_config=database_config,
            output_dir=output_dir / name,
            filename_prefix=f"{name}_comparison",
        )
    print("Dry-run complete. Review the generated SQL for both platforms before running the comparison.")


def run_comparison(scale_factor: float = 0.01, *, dry_run_output: Path | None = None) -> None:
    benchmark_config = _build_benchmark_config(scale_factor)
    if dry_run_output is not None:
        _dry_run(benchmark_config, Path(dry_run_output))
        return
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    profiler = SystemProfiler()
    system_profile = profiler.get_system_profile()
    phases = ["generate", "load", "power"]

    print("=" * 60)
    print("Running TPC-H on LakeSail Sail...")
    print("=" * 60)
    sail_orchestrator = BenchmarkOrchestrator(base_dir=str(_OUTPUT_DIR / "lakesail"))
    sail_result = sail_orchestrator.execute_benchmark(
        config=benchmark_config,
        system_profile=system_profile,
        database_config=_build_lakesail_config(),
        phases_to_run=phases,
    )

    print()
    print("=" * 60)
    print("Running TPC-H on Apache Spark...")
    print("=" * 60)
    spark_orchestrator = BenchmarkOrchestrator(base_dir=str(_OUTPUT_DIR / "spark"))
    spark_result = spark_orchestrator.execute_benchmark(
        config=benchmark_config,
        system_profile=system_profile,
        database_config=_build_spark_config(),
        phases_to_run=phases,
    )

    print()
    print("=" * 60)
    print(f"TPC-H SF={scale_factor} Comparison: LakeSail Sail vs Apache Spark")
    print("=" * 60)
    print()
    print(f"{'Metric':<30} {'LakeSail':>12} {'Spark':>12} {'Speedup':>10}")
    print("-" * 66)

    sail_time = sail_result.total_execution_time
    spark_time = spark_result.total_execution_time
    speedup = spark_time / sail_time if sail_time > 0 else float("inf")

    print(f"{'Total time (s)':<30} {sail_time:>12.2f} {spark_time:>12.2f} {speedup:>9.1f}x")
    print(f"{'Successful queries':<30} {sail_result.successful_queries:>12} {spark_result.successful_queries:>12}")
    print()
    print(f"Results saved to: {_OUTPUT_DIR}")
    print()
    print("For detailed analysis, use BenchBox's compare tools:")
    print("  benchbox compare <lakesail_result.json> <spark_result.json>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare TPC-H: LakeSail Sail vs Apache Spark")
    parser.add_argument("--scale", type=float, default=0.01, help="Benchmark scale factor")
    parser.add_argument(
        "--dry-run",
        type=str,
        metavar="OUTPUT_DIR",
        help="Preview both runs without executing; write artifacts to OUTPUT_DIR.",
    )
    args = parser.parse_args(argv)
    run_comparison(scale_factor=args.scale, dry_run_output=Path(args.dry_run) if args.dry_run else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
