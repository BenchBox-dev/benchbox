from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from benchbox.core.results.loader import load_result_file
from tests.e2e.conftest import (
    E2E_BENCHMARK_TIMEOUT,
    find_result_files,
    run_benchmark,
)
from tests.e2e.utils import is_platform_available
from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = pytest.mark.slow


if TYPE_CHECKING:
    from collections.abc import Mapping


class TestDuckDBE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.e2e_quick
    @pytest.mark.duckdb
    @pytest.mark.tpch
    def test_tpch_full_execution(self, benchmark_runs_root: Path) -> None:
        config = {
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale": "0.01",
        }

        result = run_benchmark(config, timeout=E2E_BENCHMARK_TIMEOUT)

        assert result.returncode == 0, f"CLI failed with:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        assert "Benchmark completed" in result.stdout or "benchmark completed" in result.stdout.lower()

        result_files = find_result_files(benchmark_runs_root / "results", pattern="tpch_*_duckdb_*.json")
        assert result_files, f"no DuckDB TPC-H result bundle under {benchmark_runs_root}"
        loaded, raw = load_result_file(result_files[0])
        assert raw["benchmark"]["id"] == "tpch"
        assert loaded.platform.lower() == "duckdb"
        assert loaded.scale_factor == 0.01
        assert loaded.successful_queries > 0
        assert loaded.failed_queries == 0

    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.e2e_quick
    @pytest.mark.duckdb
    @pytest.mark.tpch
    def test_tpch_query_subset(self, tmp_path: Path) -> None:
        config = {
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale": "0.01",
            "queries": "Q1,Q6,Q14",
        }

        result = run_benchmark(config, timeout=300)

        assert result.returncode == 0, f"CLI failed: {result.stdout}"
        assert "Q1" in result.stdout or "q1" in result.stdout.lower()

    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.duckdb
    @pytest.mark.tpch
    @pytest.mark.slow
    def test_tpch_with_capture_plans(self, tmp_path: Path) -> None:
        config = {
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale": "0.01",
            "queries": "Q1,Q6",
            "capture_plans": True,
        }

        result = run_benchmark(config, timeout=300)

        assert result.returncode == 0, f"CLI failed: {result.stdout}"

    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.duckdb
    @pytest.mark.ssb
    @pytest.mark.slow
    def test_ssb_execution(self, tmp_path: Path) -> None:
        config = {
            "platform": "duckdb",
            "benchmark": "ssb",
            "scale": "0.01",
        }

        result = run_benchmark(config, timeout=E2E_BENCHMARK_TIMEOUT)

        assert result.returncode == 0, f"CLI failed: {result.stdout}"


class TestSQLiteE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.slow
    @pytest.mark.stress
    @pytest.mark.sqlite
    @pytest.mark.tpch
    def test_tpch_full_execution(self, tmp_path: Path) -> None:
        config = {
            "platform": "sqlite",
            "benchmark": "tpch",
            "scale": "0.01",
            "force": "all",
            "platform_options": {"database_name": tmp_path.name},
        }

        result = run_benchmark(config, timeout=E2E_BENCHMARK_TIMEOUT)

        assert result.returncode == 0, f"CLI failed with:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.e2e_quick
    @pytest.mark.sqlite
    @pytest.mark.tpch
    def test_tpch_query_subset_smoke(self, tmp_path: Path) -> None:
        config = {
            "platform": "sqlite",
            "benchmark": "tpch",
            "scale": "0.01",
            "queries": "Q1",
            "force": "all",
            "platform_options": {"database_name": tmp_path.name},
        }

        result = run_benchmark(config, timeout=300)

        assert result.returncode == 0, f"CLI failed: {result.stdout}"

    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.slow
    @pytest.mark.sqlite
    @pytest.mark.tpch
    def test_tpch_phases_generate_load(self, tmp_path: Path) -> None:
        config = {
            "platform": "sqlite",
            "benchmark": "tpch",
            "scale": "0.01",
            "phases": "generate,load",
            "force": "all",
            "platform_options": {"database_name": tmp_path.name},
        }

        result = run_benchmark(config, timeout=300)

        assert result.returncode == 0, f"CLI failed: {result.stdout}"


class TestDataFusionE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.slow
    def test_tpch_full_execution(self, tmp_path: Path) -> None:
        if not is_platform_available("datafusion"):
            pytest.skip("DataFusion not available")

        config = {
            "platform": "datafusion",
            "benchmark": "tpch",
            "scale": "0.01",
        }

        result = run_benchmark(config, timeout=E2E_BENCHMARK_TIMEOUT)

        assert result.returncode == 0, f"CLI failed with:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.e2e_quick
    def test_dry_run_generates_artifacts(self, tmp_path: Path) -> None:
        if not is_platform_available("datafusion"):
            pytest.skip("DataFusion not available")

        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "datafusion",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode == 0, f"Dry run failed: {result.stdout}"
        assert "Dry run completed" in result.stdout


class TestClickHouseLocalE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.e2e_quick
    def test_dry_run_generates_artifacts(
        self,
        tmp_path: Path,
        clickhouse_stub_dir: Path,
        clickhouse_env: Mapping[str, str],
    ) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()
        chdb_path = tmp_path / "chdb_store"
        chdb_path.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "clickhouse",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--platform-option",
                "mode=local",
                "--platform-option",
                f"data_path={chdb_path / 'benchbox.chdb'}",
                "--platform-option",
                "driver_auto_install=false",
                "--dry-run",
                str(output_dir),
            ],
            env=dict(clickhouse_env),
        )

        assert result.returncode == 0, f"Dry run failed: {result.stdout}"
        assert "Dry run completed" in result.stdout


class TestCrossPlatformE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_local
    @pytest.mark.slow
    def test_duckdb_sqlite_same_results(self, tmp_path: Path) -> None:
        duckdb_config = {
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale": "0.01",
            "queries": "Q1",
        }
        duckdb_result = run_benchmark(duckdb_config, timeout=300)
        assert duckdb_result.returncode == 0, f"DuckDB failed: {duckdb_result.stdout}"

        sqlite_config = {
            "platform": "sqlite",
            "benchmark": "tpch",
            "scale": "0.01",
            "queries": "Q1",
        }
        sqlite_result = run_benchmark(sqlite_config, timeout=300)
        assert sqlite_result.returncode == 0, f"SQLite failed: {sqlite_result.stdout}"


@pytest.mark.e2e
@pytest.mark.e2e_local
@pytest.mark.e2e_quick
@pytest.mark.parametrize("platform", ["duckdb", "sqlite"])
def test_help_command_per_platform(platform: str) -> None:
    result = run_cli_command(["run", "--help"])
    assert result.returncode == 0
    assert platform in result.stdout.lower() or "platform" in result.stdout.lower()


@pytest.mark.e2e
@pytest.mark.e2e_local
@pytest.mark.e2e_quick
@pytest.mark.parametrize(
    "platform,benchmark_name",
    [
        ("duckdb", "tpch"),
        ("sqlite", "tpch"),
    ],
)
def test_dry_run_parametrized(tmp_path: Path, platform: str, benchmark_name: str) -> None:
    output_dir = tmp_path / f"dry_run_{platform}_{benchmark_name}"
    output_dir.mkdir()

    result = run_cli_command(
        [
            "run",
            "--platform",
            platform,
            "--benchmark",
            benchmark_name,
            "--scale",
            "0.01",
            "--dry-run",
            str(output_dir),
        ]
    )

    assert result.returncode == 0, f"Dry run failed for {platform}/{benchmark_name}: {result.stdout}"
    assert "Dry run completed" in result.stdout

    artifacts = list(output_dir.glob("*"))
    assert artifacts, f"No artifacts generated for {platform}/{benchmark_name}"
    assert list(output_dir.glob("*.json")) or list(output_dir.glob("*.yaml"))
