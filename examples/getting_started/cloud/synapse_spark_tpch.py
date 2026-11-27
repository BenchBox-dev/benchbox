from __future__ import annotations

import argparse
import os
from pathlib import Path

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.config import BenchmarkConfig, DatabaseConfig
from benchbox.core.system import SystemProfiler
from benchbox.examples import execute_example_dry_run

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _PROJECT_ROOT / "benchmark_runs" / "getting_started" / "synapse_spark"


def _require_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable {var_name}. Set Synapse configuration before running this example."
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

    workspace_name = _require_env("SYNAPSE_WORKSPACE_NAME")
    spark_pool = _require_env("SYNAPSE_SPARK_POOL")
    storage_account = _require_env("SYNAPSE_STORAGE_ACCOUNT")
    storage_container = _require_env("SYNAPSE_STORAGE_CONTAINER")

    database_config = DatabaseConfig(
        type="synapse-spark",
        name="synapse_spark_tpch",
        options={
            "workspace_name": workspace_name,
            "compute_resource": spark_pool,
            "storage_account": storage_account,
            "storage_container": storage_container,
            "storage_path": os.getenv("SYNAPSE_STORAGE_PATH", "benchbox"),
            "tenant_id": os.getenv("AZURE_TENANT_ID"),
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
            filename_prefix="synapse_spark_tpch",
        )
        print()
        print("Dry-run complete! Review SQL files before running on Synapse Spark.")
        print()
        print("Cost estimation (Synapse vCore-hour pricing):")
        print(f"- TPC-H SF={scale_factor}:")
        nodes = 3
        minutes_per_query = 2 if scale_factor >= 1.0 else 1
        cost_per_query = (minutes_per_query / 60) * 0.44 * nodes
        total_cost = cost_per_query * 22
        print(f"  - Estimated: ~${total_cost:.2f} for full benchmark (22 queries)")
        print()
        print("Before running:")
        print("1. Ensure Azure credentials are configured (az login)")
        print("2. Verify Spark pool is started (or auto-start enabled)")
        print("3. Check ADLS Gen2 storage is accessible")
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
    print("- Check Synapse Studio monitoring for pool metrics")
    print("- Compare with other platforms using result_analysis.py")
    print("- Query data via Synapse SQL serverless (if enabled)")
    print()
    print("Cost optimization tips:")
    print("- Enable auto-pause for idle pools")
    print("- Right-size node count based on workload")
    print("- Use smaller nodes for development")
    print("- Consider reserved capacity for production")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TPC-H on Azure Synapse Spark")
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
