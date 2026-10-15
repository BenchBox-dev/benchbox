from __future__ import annotations

import argparse
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _PROJECT_ROOT / "benchmark_runs" / "getting_started" / "influxdb"


CLI_EPILOG = (
    "Run TSBS DevOps benchmark on InfluxDB 3.x.\n"
    "\n"
    "InfluxDB 3.x is a time series database built on the FDAP stack (Apache Arrow,\n"
    "DataFusion, Parquet) with native SQL support via FlightSQL protocol.\n"
    "\n"
    "This example demonstrates running the TSBS (Time Series Benchmark Suite)\n"
    "DevOps workload against InfluxDB for time series query performance testing.\n"
    "\n"
    "Deployment Modes:\n"
    "    - Cloud: InfluxDB Cloud managed service (default)\n"
    "    - Core: Self-hosted InfluxDB Core (OSS)\n"
    "\n"
    "Prerequisites:\n"
    "    1. InfluxDB 3.x instance (Cloud or Core)\n"
    "    2. Authentication token with read/write permissions\n"
    "    3. Database (bucket) created\n"
    "\n"
    "Required environment variables:\n"
    "    INFLUXDB_TOKEN    Authentication token\n"
    "\n"
    "Optional environment variables (with defaults):\n"
    "    INFLUXDB_HOST     Server hostname (default: localhost)\n"
    "    INFLUXDB_PORT     Server port (default: 8086)\n"
    "    INFLUXDB_ORG      Organization name\n"
    "    INFLUXDB_DATABASE Database name (default: benchbox)\n"
    "\n"
    "Installation:\n"
    "    uv add benchbox --extra influxdb\n"
    "\n"
    "Usage - InfluxDB Cloud:\n"
    "    export INFLUXDB_TOKEN=your_token\n"
    "    export INFLUXDB_HOST=us-east-1-1.aws.cloud2.influxdata.com\n"
    "    export INFLUXDB_ORG=your-org\n"
    "    export INFLUXDB_DATABASE=benchmarks\n"
    "\n"
    "    python examples/getting_started/cloud/influxdb_tsbs_devops.py\n"
    "\n"
    "Usage - InfluxDB Core (local Docker):\n"
    "    # Start InfluxDB Core\n"
    "    docker run -d --name influxdb -p 8086:8086 \\\n"
    "      -e DOCKER_INFLUXDB_INIT_MODE=setup \\\n"
    "      -e DOCKER_INFLUXDB_INIT_USERNAME=admin \\\n"
    "      -e DOCKER_INFLUXDB_INIT_PASSWORD=password123 \\\n"
    "      -e DOCKER_INFLUXDB_INIT_ORG=benchbox \\\n"
    "      -e DOCKER_INFLUXDB_INIT_BUCKET=benchmarks \\\n"
    "      -e DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=my-token \\\n"
    "      influxdb:3.0\n"
    "\n"
    "    export INFLUXDB_TOKEN=my-token\n"
    "    python examples/getting_started/cloud/influxdb_tsbs_devops.py --mode core --no-ssl\n"
    "\n"
    "Preview without execution:\n"
    "    python examples/getting_started/cloud/influxdb_tsbs_devops.py --dry-run ./preview\n"
)


def _get_env(var_name: str, default: str | None = None) -> str | None:
    return os.getenv(var_name, default)


def _require_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise ValueError(
            f"Missing required environment variable: {var_name}\nSet it with: export {var_name}=your_value"
        )
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run TSBS DevOps benchmark on InfluxDB 3.x",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CLI_EPILOG,
    )
    parser.add_argument(
        "--mode",
        choices=["cloud", "core"],
        default="cloud",
        help="InfluxDB deployment mode (default: cloud)",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Scale factor for TSBS data generation (default: 1.0)",
    )
    parser.add_argument(
        "--no-ssl",
        action="store_true",
        help="Disable SSL/TLS (for local InfluxDB Core)",
    )
    parser.add_argument(
        "--dry-run",
        type=str,
        metavar="PATH",
        help="Preview benchmark without execution, output to PATH",
    )
    args = parser.parse_args()

    token = _require_env("INFLUXDB_TOKEN")
    host = _get_env("INFLUXDB_HOST", "localhost")
    port = int(_get_env("INFLUXDB_PORT", "8086"))
    org = _get_env("INFLUXDB_ORG")
    database = _get_env("INFLUXDB_DATABASE", "benchbox")
    ssl = not args.no_ssl

    if args.dry_run:
        from benchbox.examples import execute_example_dry_run

        execute_example_dry_run(
            dry_run_path=args.dry_run,
            platform_name="influxdb",
            benchmark_name="tsbs-devops",
            scale_factor=args.scale,
            platform_config={
                "host": host,
                "port": port,
                "token": "***",
                "org": org or "(not set)",
                "database": database,
                "deployment_mode": args.mode,
                "ssl": ssl,
            },
        )
        return

    from benchbox.cli.orchestrator import BenchmarkOrchestrator
    from benchbox.core.config import BenchmarkConfig, DatabaseConfig
    from benchbox.core.system import SystemProfiler
    from benchbox.platforms.influxdb import INFLUXDB_AVAILABLE, InfluxDBAdapter

    if not INFLUXDB_AVAILABLE:
        raise ImportError("InfluxDB client not installed. Run: uv add influxdb3-python")

    print(f"Connecting to InfluxDB ({args.mode} mode)...")
    adapter = InfluxDBAdapter(
        host=host,
        port=port,
        token=token,
        org=org,
        database=database,
        deployment_mode=args.mode,
        ssl=ssl,
    )

    db_config = DatabaseConfig(
        platform=adapter,
        output_dir=_OUTPUT_DIR,
    )

    bench_config = BenchmarkConfig(
        benchmark_type="tsbs_devops",
        scale_factor=args.scale,
        phases=["power"],
    )

    orchestrator = BenchmarkOrchestrator(
        database_config=db_config,
        benchmark_config=bench_config,
        system_profiler=SystemProfiler(),
    )

    print(f"Running TSBS DevOps benchmark at scale {args.scale}...")
    print(f"  Host: {host}:{port}")
    print(f"  Database: {database}")
    print(f"  Mode: {args.mode}")
    print(f"  SSL: {ssl}")
    print()

    results = orchestrator.run()

    print("\n" + "=" * 60)
    print("Benchmark Complete!")
    print("=" * 60)
    print(f"Output directory: {_OUTPUT_DIR}")
    if hasattr(results, "manifest_path"):
        print(f"Results manifest: {results.manifest_path}")


if __name__ == "__main__":
    main()
