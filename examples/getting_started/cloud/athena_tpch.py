from __future__ import annotations

import argparse
import os
from pathlib import Path

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.config import BenchmarkConfig, DatabaseConfig
from benchbox.core.system import SystemProfiler
from benchbox.examples import execute_example_dry_run

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _PROJECT_ROOT / "benchmark_runs" / "getting_started" / "athena"


def _require_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable {var_name}. Set AWS/Athena configuration before running this example."
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

    staging_dir = _require_env("ATHENA_S3_STAGING_DIR")

    database_config = DatabaseConfig(
        type="athena",
        name="athena_tpch",
        options={
            "s3_staging_dir": staging_dir,
            "s3_output_location": os.getenv("ATHENA_OUTPUT_LOCATION") or f"{staging_dir.rstrip('/')}/results/",
            "region": os.getenv("AWS_REGION", "us-east-1"),
            "compute_resource": os.getenv("ATHENA_WORKGROUP", "primary"),
            "database": os.getenv("ATHENA_DATABASE", "benchbox"),
            "aws_profile": os.getenv("AWS_PROFILE"),
            "default_format": "PARQUET",
            "compression": "SNAPPY",
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
            filename_prefix="athena_tpch",
        )
        print()
        print("Dry-run complete! Review SQL files before running on Athena.")
        print()
        print("Cost estimation (Athena charges $5/TB scanned):")
        print(f"- TPC-H SF={scale_factor}:")
        data_size_mb = scale_factor * 1000
        cost_csv = data_size_mb / 1024 / 1024 * 5 * 22
        cost_parquet = cost_csv / 10
        print(f"  - CSV format: ~${cost_csv:.4f} for full benchmark")
        print(f"  - Parquet format: ~${cost_parquet:.4f} for full benchmark (recommended)")
        print()
        print("Before running:")
        print("1. Verify S3 bucket exists and is accessible")
        print("2. Check IAM permissions for Athena and S3")
        print("3. Review query translations in generated SQL files")
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
    print("- Check Athena console for query history and data scanned")
    print("- Compare with other platforms using result_analysis.py")
    print("- Clean up S3 data: aws s3 rm --recursive s3://bucket/benchbox/")
    print()
    print("Cost optimization tips:")
    print("- Use Parquet format (already configured) for ~10x cost reduction")
    print("- Partition large tables by date or category")
    print("- Use workgroup query limits to prevent runaway costs")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TPC-H on AWS Athena")
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
