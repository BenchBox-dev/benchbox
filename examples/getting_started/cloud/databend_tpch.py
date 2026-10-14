from __future__ import annotations

import argparse
import os
from pathlib import Path

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.config import BenchmarkConfig, DatabaseConfig
from benchbox.core.system import SystemProfiler
from benchbox.examples import execute_example_dry_run

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _PROJECT_ROOT / "benchmark_runs" / "getting_started" / "databend"


def _build_configs(scale_factor: float) -> tuple[BenchmarkConfig, DatabaseConfig]:
    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=scale_factor,
        test_execution_type="power",
        options={"enable_preflight_validation": False},
    )

    dsn = os.getenv("DATABEND_DSN")

    options: dict[str, object] = {}

    if dsn:
        options["dsn"] = dsn
    else:
        host = os.getenv("DATABEND_HOST")
        if not host:
            raise RuntimeError(
                "Missing Databend connection configuration. Set either:\n"
                "  DATABEND_DSN=databend+http://user:pass@host:port/db?sslmode=disable\n"
                "or:\n"
                "  DATABEND_HOST=<hostname>\n"
                "  DATABEND_USER=<username>\n"
                "  DATABEND_PASSWORD=<password>"
            )
        options["host"] = host
        options["username"] = os.getenv("DATABEND_USER", "benchbox")
        options["password"] = os.getenv("DATABEND_PASSWORD", "")

        port = os.getenv("DATABEND_PORT")
        if port:
            options["port"] = int(port)

    options["database"] = os.getenv("DATABEND_DATABASE", "benchbox")

    warehouse = os.getenv("DATABEND_WAREHOUSE")
    if warehouse:
        options["compute_resource"] = warehouse

    database_config = DatabaseConfig(
        type="databend",
        name="databend_tpch",
        options=options,
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
            filename_prefix="databend_tpch",
        )
        print()
        print("Dry-run complete! Review SQL files before running on Databend.")
        print()
        print("Deployment options:")
        print("- Databend Cloud: https://www.databend.com (managed, SSL on port 443)")
        print("- Self-hosted: docker run -p 8000:8000 datafuselabs/databend:latest")
        print()
        print("Before running:")
        print("1. Verify Databend is accessible at the configured host and port")
        print("2. Database will be created automatically if it doesn't exist")
        print("3. Review query translations (Snowflake dialect) in generated SQL files")
        print("4. For self-hosted: ensure object storage backend is configured")
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
    print("- Try larger scale factors for distributed performance testing")
    print("- Compare with other cloud OLAP platforms (Snowflake, ClickHouse Cloud)")
    print("- For Databend Cloud: check warehouse activity in the web console")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TPC-H on Databend")
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
