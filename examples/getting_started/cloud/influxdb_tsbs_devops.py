from __future__ import annotations

import argparse
import os
from pathlib import Path

from benchbox.core.config import BenchmarkConfig, DatabaseConfig

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


def _build_configs(scale_factor: float, mode: str, *, ssl: bool) -> tuple[BenchmarkConfig, DatabaseConfig]:
    benchmark_config = BenchmarkConfig(
        name="tsbs_devops",
        display_name="TSBS DevOps",
        scale_factor=scale_factor,
        test_execution_type="power",
        options={"enable_preflight_validation": False},
    )
    options: dict[str, object] = {
        "token": _require_env("INFLUXDB_TOKEN"),
        "host": _get_env("INFLUXDB_HOST", "localhost"),
        "port": int(_get_env("INFLUXDB_PORT", "8086") or "8086"),
        "database": _get_env("INFLUXDB_DATABASE", "benchbox"),
        "ssl": ssl,
    }
    org = _get_env("INFLUXDB_ORG")
    if org:
        options["org"] = org
    database_config = DatabaseConfig(
        type=f"influxdb:{mode}",
        name="influxdb_tsbs_devops",
        options=options,
    )
    return benchmark_config, database_config


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

    benchmark_config, database_config = _build_configs(args.scale, args.mode, ssl=not args.no_ssl)

    if args.dry_run:
        from benchbox.examples import execute_example_dry_run

        execute_example_dry_run(
            benchmark_config=benchmark_config,
            database_config=database_config,
            output_dir=Path(args.dry_run),
            filename_prefix="influxdb_tsbs_devops",
        )
        return

    from benchbox.cli.orchestrator import BenchmarkOrchestrator
    from benchbox.core.system import SystemProfiler

    options = database_config.options
    print(f"Running TSBS DevOps benchmark on InfluxDB ({args.mode} mode) at scale {args.scale}...")
    print(f"  Host: {options['host']}:{options['port']}")
    print(f"  Database: {options['database']}")
    print(f"  SSL: {options['ssl']}")
    print()

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    orchestrator = BenchmarkOrchestrator(base_dir=str(_OUTPUT_DIR))
    result = orchestrator.execute_benchmark(
        config=benchmark_config,
        system_profile=SystemProfiler().get_system_profile(),
        database_config=database_config,
        phases_to_run=["generate", "load", "power"],
    )

    print("\n" + "=" * 60)
    print("Benchmark Complete!")
    print("=" * 60)
    print(f"Successful queries: {result.successful_queries}")
    print(f"Output directory: {_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
