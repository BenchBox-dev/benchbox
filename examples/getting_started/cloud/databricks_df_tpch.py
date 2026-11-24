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

_OUTPUT_DIR = _PROJECT_ROOT / "benchmark_runs" / "getting_started" / "databricks_df"


def _require_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable {var_name}. Set Databricks Connect credentials before running this example."
        )
    return value


def _get_env(var_name: str, default: str | None = None) -> str | None:
    return os.getenv(var_name, default)


def _build_configs(scale_factor: float) -> tuple[BenchmarkConfig, DatabaseConfig]:
    host = _require_env("DATABRICKS_HOST")
    http_path = _require_env("DATABRICKS_HTTP_PATH")
    token = _require_env("DATABRICKS_TOKEN")
    cluster_id = _get_env("DATABRICKS_CLUSTER_ID")

    catalog = _get_env("DATABRICKS_CATALOG", "workspace")
    schema = _get_env("DATABRICKS_SCHEMA")
    staging_root = _get_env("DATABRICKS_STAGING_ROOT")

    benchmark_config = BenchmarkConfig(
        name="tpch",
        scale_factor=scale_factor,
        phases=["generate", "load", "power"],
    )

    db_config = DatabaseConfig(
        type="databricks-df",
        name="Databricks DataFrame",
        options={
            "server_hostname": host,
            "http_path": http_path,
            "access_token": token,
            "catalog": catalog,
            "schema": schema,
            "staging_root": staging_root,
            "compute_resource": cluster_id,
            "execution_mode": "dataframe",
        },
    )

    return benchmark_config, db_config


def run_example(scale_factor: float = 0.01, dry_run: bool = False) -> None:
    print("=" * 70)
    print("Databricks DataFrame TPC-H Benchmark Example")
    print("=" * 70)
    print()
    print("This example runs TPC-H queries using PySpark DataFrame API")
    print("on a Databricks cluster via Databricks Connect.")
    print()

    if dry_run:
        print("[DRY RUN] Validating configuration without connecting...")
        print()

        try:
            benchmark_config, db_config = _build_configs(scale_factor)
            print(f"  Platform: {db_config.type}")
            print(f"  Benchmark: {benchmark_config.name}")
            print(f"  Scale Factor: {benchmark_config.scale_factor}")
            print(f"  Catalog: {db_config.options.get('catalog', 'workspace')}")
            print(f"  Cluster ID: {db_config.options.get('compute_resource', 'Not set')}")
            print()
            print("[OK] Configuration valid")
            return
        except RuntimeError as e:
            print(f"[ERROR] {e}")
            sys.exit(1)

    try:
        benchmark_config, db_config = _build_configs(scale_factor)
    except RuntimeError as e:
        print(f"Configuration error: {e}")
        print()
        print("Required environment variables:")
        print("  DATABRICKS_HOST         - Databricks workspace host")
        print("  DATABRICKS_HTTP_PATH    - SQL Warehouse HTTP path")
        print("  DATABRICKS_TOKEN        - Personal access token")
        print()
        print("Optional environment variables:")
        print("  DATABRICKS_CLUSTER_ID   - Interactive cluster ID for Connect")
        print("  DATABRICKS_CATALOG      - Unity Catalog catalog (default: workspace)")
        print("  DATABRICKS_STAGING_ROOT - UC Volume path for data staging")
        sys.exit(1)

    print("Configuration:")
    print(f"  Host: {db_config.options.get('server_hostname')}")
    print(f"  Catalog: {db_config.options.get('catalog')}")
    print(f"  Cluster ID: {db_config.options.get('compute_resource', 'Not set')}")
    print(f"  Execution Mode: {db_config.options.get('execution_mode')}")
    print(f"  Scale Factor: {scale_factor}")
    print()

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Starting benchmark run...")
    print()

    orchestrator = BenchmarkOrchestrator(
        benchmark_config=benchmark_config,
        database_config=db_config,
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


def main():
    parser = argparse.ArgumentParser(description="Run TPC-H benchmark on Databricks using DataFrame API")
    parser.add_argument(
        "--scale-factor",
        type=float,
        default=0.01,
        help="TPC-H scale factor (default: 0.01)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without connecting",
    )

    args = parser.parse_args()

    run_example(scale_factor=args.scale_factor, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
