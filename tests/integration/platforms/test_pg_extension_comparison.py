# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os
import time
from pathlib import Path

import pytest

from benchbox import TPCH

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_pg_duckdb,
    pytest.mark.live_pg_mooncake,
    pytest.mark.slow,
]


PG_EXTENSIONS = {
    "pg-duckdb": {
        "host_env": "PG_DUCKDB_HOST",
        "port_env": "PG_DUCKDB_PORT",
        "default_port": 5432,
        "user_env": "PG_DUCKDB_USER",
        "password_env": "PG_DUCKDB_PASSWORD",
        "database_env": "PG_DUCKDB_DATABASE",
        "adapter_module": "benchbox.platforms.pg_duckdb",
        "adapter_class": "PgDuckDBAdapter",
    },
    "pg-mooncake": {
        "host_env": "PG_MOONCAKE_HOST",
        "port_env": "PG_MOONCAKE_PORT",
        "default_port": 5433,
        "user_env": "PG_MOONCAKE_USER",
        "password_env": "PG_MOONCAKE_PASSWORD",
        "database_env": "PG_MOONCAKE_DATABASE",
        "adapter_module": "benchbox.platforms.pg_mooncake",
        "adapter_class": "PgMooncakeAdapter",
    },
}


def _check_extension_available(platform_config: dict) -> bool:
    import socket

    host = os.getenv(platform_config["host_env"], "localhost")
    port = int(os.getenv(platform_config["port_env"], str(platform_config["default_port"])))
    try:
        sock = socket.create_connection((host, port), timeout=2.0)
        sock.close()
        return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def _run_benchmark_via_adapter(
    platform: str,
    data_dir: Path,
    *,
    benchmark: str = "tpch",
    scale: float = 0.01,
    queries: list[str] | None = None,
) -> dict | None:
    if queries is None:
        queries = ["Q1", "Q6"]

    config = PG_EXTENSIONS[platform]
    host = os.getenv(config["host_env"], "localhost")
    port = int(os.getenv(config["port_env"], str(config["default_port"])))
    skip_unless_docker_service(host, port, platform=platform)

    import importlib

    module = importlib.import_module(config["adapter_module"])
    AdapterClass = getattr(module, config["adapter_class"])

    adapter = AdapterClass(
        host=host,
        port=port,
        username=os.getenv(config["user_env"], "benchbox"),
        password=os.getenv(config["password_env"], "benchbox"),
        database=os.getenv(config["database_env"], "benchbox_test"),
    )

    tpch = TPCH(scale_factor=scale, output_dir=data_dir, verbose=False)
    data_files = tpch.generate_data()
    if not data_files:
        return None

    query_executions = []
    run_start = time.perf_counter()
    connection = adapter.create_connection()
    try:
        adapter.create_schema(tpch, connection)
        adapter.load_data(tpch, connection, data_dir)

        for qid in queries:
            q_num = int(qid.lstrip("Q"))
            query_sql = tpch.get_query(q_num, seed=42)
            q_start = time.perf_counter()
            try:
                result = adapter.execute_query(connection, query_sql, qid, benchmark_type=benchmark, scale_factor=scale)
                raw_status = str(result.get("status", "UNKNOWN")).lower()
                duration = result.get("execution_time_seconds", time.perf_counter() - q_start)
            except Exception as exc:
                raw_status = "failed"
                duration = time.perf_counter() - q_start
                result = {"error": str(exc)}

            query_executions.append(
                {
                    "query_id": qid,
                    "status": raw_status,
                    "duration_seconds": duration,
                }
            )
    finally:
        try:
            cursor = connection.cursor()
            for table in ["lineitem", "orders", "customer", "part", "partsupp", "supplier", "nation", "region"]:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
            cursor.close()
        except Exception:
            pass
        adapter.close_connection(connection)

    total_duration = time.perf_counter() - run_start
    successful = sum(1 for q in query_executions if q["status"] == "success")

    return {
        "benchmark_name": benchmark,
        "scale_factor": scale,
        "platform": platform,
        "successful_queries": successful,
        "duration_seconds": total_duration,
        "execution_phases": [
            {
                "phase": "power",
                "query_executions": query_executions,
            }
        ],
    }


@pytest.fixture(scope="module")
def available_extensions() -> list[str]:
    available = []
    for platform, config in PG_EXTENSIONS.items():
        if _check_extension_available(config):
            available.append(platform)
    if len(available) < 2:
        pytest.skip(
            f"Comparison requires at least 2 extensions running, found {len(available)}. "
            "Run: make test-docker-up-pg-extensions"
        )
    return available


@pytest.fixture(scope="module")
def comparison_results(available_extensions, tmp_path_factory) -> dict[str, dict]:
    data_dir = tmp_path_factory.mktemp("pg_extension_comparison")
    results = {}

    for platform in available_extensions:
        result = _run_benchmark_via_adapter(platform, data_dir)
        if result is not None:
            results[platform] = result

    if len(results) < 2:
        pytest.skip(f"Need results from at least 2 extensions, got {len(results)}")
    return results


class TestPgExtensionComparisonOrchestration:
    def test_all_extensions_produce_results(self, comparison_results, available_extensions):

        for platform in available_extensions:
            assert platform in comparison_results, f"Missing results for {platform}"
            result = comparison_results[platform]
            assert "benchmark_name" in result
            assert result.get("successful_queries", 0) > 0

    def test_results_are_comparable(self, comparison_results):

        platforms = list(comparison_results.keys())
        first = comparison_results[platforms[0]]

        for platform in platforms[1:]:
            other = comparison_results[platform]
            assert first["benchmark_name"] == other["benchmark_name"]
            assert first.get("scale_factor") == other.get("scale_factor")


class TestPgExtensionComparisonReport:
    def test_generate_comparison_table(self, comparison_results):
        rows = []
        for platform, result in comparison_results.items():
            duration = result.get("duration_seconds", 0)
            queries = result.get("successful_queries", 0)
            rows.append(
                {
                    "platform": platform,
                    "duration_s": round(duration, 3),
                    "queries": queries,
                    "avg_query_s": round(duration / queries, 3) if queries > 0 else None,
                }
            )

        assert len(rows) >= 2
        for row in rows:
            assert row["platform"]
            assert row["queries"] > 0

    def test_query_level_comparison(self, comparison_results):
        query_timings: dict[str, dict[str, float]] = {}

        for platform, result in comparison_results.items():
            for phase in result.get("execution_phases", []):
                for query in phase.get("query_executions", []):
                    qid = query.get("query_id", "")
                    if qid and query.get("status") == "success":
                        if qid not in query_timings:
                            query_timings[qid] = {}
                        query_timings[qid][platform] = query.get("duration_seconds", 0)

        multi_platform_queries = {qid: timings for qid, timings in query_timings.items() if len(timings) >= 2}
        assert len(multi_platform_queries) > 0, "Expected at least one query with results from multiple platforms"


