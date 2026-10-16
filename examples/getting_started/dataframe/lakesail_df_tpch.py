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
from benchbox.core.system import SystemProfiler
from benchbox.examples import execute_example_dry_run

_OUTPUT_DIR = _PROJECT_ROOT / "benchmark_runs" / "getting_started" / "lakesail_df"
_DEFAULT_DRY_RUN_DIR = _OUTPUT_DIR / "dry_run"


def _sail_mode() -> str:
    return os.getenv("SAIL_MODE", "local")


def _build_configs(scale_factor: float) -> tuple[BenchmarkConfig, DatabaseConfig]:
    endpoint = os.getenv("SAIL_ENDPOINT", "sc://localhost:50051")

    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=scale_factor,
        test_execution_type="power",
        options={"enable_preflight_validation": False},
    )

    database_config = DatabaseConfig(
        type=f"lakesail-df:{_sail_mode()}",
        name="lakesail_df_tpch",
        options={"endpoint": endpoint},
    )

    return benchmark_config, database_config


def run_example(scale_factor: float = 0.01, *, dry_run_output: Path | None = None) -> None:
    print("=" * 70)
    print("LakeSail Sail DataFrame TPC-H Benchmark")
    print("=" * 70)
    print()
    print("This example runs TPC-H queries using PySpark DataFrame API")
    print("on LakeSail Sail via Spark Connect protocol.")
    print()

    benchmark_config, database_config = _build_configs(scale_factor)

    print("Configuration:")
    print(f"  Endpoint: {database_config.options.get('endpoint')}")
    print(f"  Sail Mode: {_sail_mode()}")
    print(f"  Execution Mode: {database_config.execution_mode}")
    print(f"  Scale Factor: {scale_factor}")
    print()

    if dry_run_output is not None:
        execute_example_dry_run(
            benchmark_config=benchmark_config,
            database_config=database_config,
            output_dir=Path(dry_run_output),
            filename_prefix="lakesail_df_tpch",
        )
        print()
        print("To compare SQL vs DataFrame performance:")
        print("  1. Run SQL mode:       python examples/getting_started/sql/lakesail_tpch.py")
        print("  2. Run DataFrame mode: python examples/getting_started/dataframe/lakesail_df_tpch.py")
        print("  3. Compare results in benchmark_runs/getting_started/")
        return

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Starting benchmark run...")
    print()

    orchestrator = BenchmarkOrchestrator(base_dir=str(_OUTPUT_DIR))
    result = orchestrator.execute_benchmark(
        config=benchmark_config,
        system_profile=SystemProfiler().get_system_profile(),
        database_config=database_config,
        phases_to_run=["generate", "load", "power"],
    )

    print()
    print("=" * 70)
    print("Results Summary")
    print("=" * 70)
    print(f"  Successful queries: {result.successful_queries}")
    print("  Execution mode: DataFrame")
    print(f"  Total time: {result.total_execution_time:.2f}s")
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
        type=str,
        nargs="?",
        const=str(_DEFAULT_DRY_RUN_DIR),
        metavar="OUTPUT_DIR",
        help=f"Preview without connecting to a Sail server; write artifacts to OUTPUT_DIR "
        f"(default: {_DEFAULT_DRY_RUN_DIR})",
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
