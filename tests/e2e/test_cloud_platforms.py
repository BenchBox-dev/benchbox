from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.utils import (
    has_cloud_credentials,
)
from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = pytest.mark.slow


class TestSnowflakeE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_cloud
    @pytest.mark.live_integration
    @pytest.mark.live_snowflake
    def test_full_execution_with_credentials(self, tmp_path: Path) -> None:
        if not has_cloud_credentials("snowflake"):
            pytest.skip("Snowflake credentials not configured")

        pytest.skip("Full Snowflake execution test requires manual setup")


class TestBigQueryE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_cloud
    @pytest.mark.live_integration
    @pytest.mark.live_bigquery
    def test_full_execution_with_credentials(self, tmp_path: Path) -> None:
        if not has_cloud_credentials("bigquery"):
            pytest.skip("BigQuery credentials not configured")
        pytest.skip("Full BigQuery execution test requires manual setup")


class TestRedshiftE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_cloud
    @pytest.mark.live_integration
    @pytest.mark.live_redshift
    def test_full_execution_with_credentials(self, tmp_path: Path) -> None:
        if not has_cloud_credentials("redshift"):
            pytest.skip("Redshift credentials not configured")

        results_dir = tmp_path / "results"
        results_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "redshift",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--phases",
                "power",
                "--queries",
                "Q1,Q6",
            ],
            env={"BENCHBOX_RESULTS_DIR": str(results_dir)},
            timeout=600.0,
        )

        assert result.returncode == 0, f"Redshift benchmark failed:\n{result.stdout}"
        result_files = list(results_dir.glob("*.json"))
        assert result_files, "No result file produced"


class TestDatabricksE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_cloud
    @pytest.mark.live_integration
    @pytest.mark.live_databricks
    def test_full_execution_with_credentials(self, tmp_path: Path) -> None:
        if not has_cloud_credentials("databricks"):
            pytest.skip("Databricks credentials not configured")
        pytest.skip("Full Databricks execution test requires manual setup")


class TestClickHouseCloudE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_cloud
    @pytest.mark.e2e_quick
    def test_dry_run_server_mode(
        self,
        tmp_path: Path,
        clickhouse_stub_dir: Path,
        clickhouse_env: dict[str, str],
    ) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

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
                "mode=server",
                "--platform-option",
                "driver_auto_install=false",
                "--dry-run",
                str(output_dir),
            ],
            env=dict(clickhouse_env),
        )

        assert result.returncode == 0, f"Dry run failed: {result.stdout}"
        assert "Dry run completed" in result.stdout


@pytest.mark.e2e
@pytest.mark.e2e_cloud
@pytest.mark.e2e_quick
@pytest.mark.parametrize(
    "platform",
    [
        "snowflake",
        "bigquery",
        "redshift",
        "athena",
        "databricks",
        "firebolt",
        "trino",
        "presto",
    ],
)
def test_cloud_platform_dry_run(tmp_path: Path, platform: str) -> None:
    output_dir = tmp_path / f"dry_run_{platform}"
    output_dir.mkdir()

    result = run_cli_command(
        [
            "run",
            "--platform",
            platform,
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--dry-run",
            str(output_dir),
        ]
    )

    assert result.returncode == 0, f"Dry run failed for {platform}: {result.stdout}"
    assert "Dry run completed" in result.stdout

    artifacts = list(output_dir.glob("*"))
    assert artifacts, f"No artifacts generated for {platform}"


@pytest.mark.e2e
@pytest.mark.e2e_cloud
@pytest.mark.e2e_quick
@pytest.mark.parametrize(
    "platform,benchmark_name",
    [
        ("snowflake", "tpch"),
        ("bigquery", "tpch"),
        ("databricks", "tpch"),
        ("snowflake", "tpcds"),
        ("bigquery", "tpcds"),
    ],
)
def test_cloud_platform_benchmark_dry_run(tmp_path: Path, platform: str, benchmark_name: str) -> None:
    output_dir = tmp_path / f"dry_run_{platform}_{benchmark_name}"
    output_dir.mkdir()

    scale = "1" if benchmark_name == "tpcds" else "0.01"

    result = run_cli_command(
        [
            "run",
            "--platform",
            platform,
            "--benchmark",
            benchmark_name,
            "--scale",
            scale,
            "--dry-run",
            str(output_dir),
        ]
    )

    assert result.returncode == 0, f"Dry run failed for {platform}/{benchmark_name}: {result.stdout}"
    assert "Dry run completed" in result.stdout


