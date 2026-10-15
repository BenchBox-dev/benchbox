from __future__ import annotations

import argparse
import os
from pathlib import Path

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.config import BenchmarkConfig, DatabaseConfig
from benchbox.core.system import SystemProfiler
from benchbox.examples import execute_example_dry_run

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _PROJECT_ROOT / "benchmark_runs" / "getting_started" / "snowpark_connect"


def _require_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable {var_name}. Set Snowflake configuration before running this example."
        )
    return value


def _build_configs(scale_factor: float) -> tuple[BenchmarkConfig, DatabaseConfig]:
    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=scale_factor,
        test_execution_type="power",
        options={"enable_preflight_validation": False},
    )

    account = _require_env("SNOWFLAKE_ACCOUNT")
    user = _require_env("SNOWFLAKE_USER")
    password = os.getenv("SNOWFLAKE_PASSWORD")

    database_config = DatabaseConfig(
        type="snowpark-connect",
        name="snowpark_connect_tpch",
        options={
            "account": account,
            "user": user,
            "password": password,
            "compute_resource": os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
            "database": os.getenv("SNOWFLAKE_DATABASE", "BENCHBOX"),
            "role": os.getenv("SNOWFLAKE_ROLE"),
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
            filename_prefix="snowpark_connect_tpch",
        )
        print()
        print("Dry-run complete! Review SQL files before running on Snowpark Connect.")
        print()
        print("Cost estimation (Snowflake credit pricing):")
        print(f"- TPC-H SF={scale_factor}:")
        credits_per_hour = 4.0 if scale_factor >= 1.0 else 1.0
        minutes = 30 if scale_factor >= 1.0 else 5
        credits_used = (minutes / 60) * credits_per_hour
        cost = credits_used * 3.0
        print(f"  - Estimated: ~${cost:.2f} for full benchmark (22 queries)")
        print()
        print("Before running:")
        print("1. Ensure Snowflake credentials are configured")
        print("2. Verify warehouse exists and is appropriately sized")
        print("3. Check database permissions")
        print("4. Review query translations in generated SQL files")
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
    print("- Review results.json for query timings")
    print("- Check Snowflake query history for execution details")
    print("- Compare with other platforms using result_analysis.py")
    print()
    print("Key differences from Apache Spark:")
    print("- No RDD APIs (DataFrame only)")
    print("- hint() and repartition() are no-ops")
    print("- Native Snowflake query optimization applies")
    print("- Credit-based billing (not cluster-based)")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TPC-H on Snowpark Connect")
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
