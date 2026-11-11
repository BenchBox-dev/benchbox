from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.conftest import E2E_BENCHMARK_TIMEOUT, run_benchmark
from tests.e2e.utils import (
    is_dataframe_available,
    is_gpu_available,
)
from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = pytest.mark.slow


class TestPandasDataFrameE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_dataframe
    @pytest.mark.e2e_quick
    def test_tpch_full_execution(self, tmp_path: Path) -> None:
        if not is_dataframe_available("pandas-df"):
            pytest.skip("Pandas not available")

        config = {
            "platform": "pandas-df",
            "benchmark": "tpch",
            "scale": "0.01",
        }

        result = run_benchmark(config, timeout=E2E_BENCHMARK_TIMEOUT)

        assert result.returncode == 0, f"CLI failed with:\nstdout: {result.stdout}\nstderr: {result.stderr}"


class TestPolarsDataFrameE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_dataframe
    @pytest.mark.e2e_quick
    def test_tpch_full_execution(self, tmp_path: Path) -> None:
        if not is_dataframe_available("polars-df"):
            pytest.skip("Polars not available")

        config = {
            "platform": "polars-df",
            "benchmark": "tpch",
            "scale": "0.01",
        }

        result = run_benchmark(config, timeout=E2E_BENCHMARK_TIMEOUT)

        assert result.returncode == 0, f"CLI failed with:\nstdout: {result.stdout}\nstderr: {result.stderr}"


class TestDaskDataFrameE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_dataframe
    @pytest.mark.slow
    @pytest.mark.stress
    def test_tpch_full_execution(self, tmp_path: Path) -> None:
        if not is_dataframe_available("dask-df"):
            pytest.skip("Dask not available")

        config = {
            "platform": "dask-df",
            "benchmark": "tpch",
            "scale": "0.01",
        }

        result = run_benchmark(config, timeout=E2E_BENCHMARK_TIMEOUT)

        assert result.returncode == 0, f"CLI failed with:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    @pytest.mark.e2e
    @pytest.mark.e2e_dataframe
    @pytest.mark.e2e_quick
    def test_dry_run_generates_artifacts(self, tmp_path: Path) -> None:
        if not is_dataframe_available("dask-df"):
            pytest.skip("Dask not available")

        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "dask-df",
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


class TestPySparkDataFrameE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_dataframe
    @pytest.mark.slow
    @pytest.mark.stress
    def test_tpch_full_execution(self, tmp_path: Path) -> None:
        if not is_dataframe_available("pyspark-df"):
            pytest.skip("PySpark not available")

        config = {
            "platform": "pyspark-df",
            "benchmark": "tpch",
            "scale": "0.01",
        }

        result = run_benchmark(config, timeout=E2E_BENCHMARK_TIMEOUT)

        assert result.returncode == 0, f"CLI failed with:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    @pytest.mark.e2e
    @pytest.mark.e2e_dataframe
    @pytest.mark.e2e_quick
    def test_dry_run_generates_artifacts(self, tmp_path: Path) -> None:
        if not is_dataframe_available("pyspark-df"):
            pytest.skip("PySpark not available")

        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "pyspark-df",
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


class TestCuDFDataFrameE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_dataframe
    @pytest.mark.slow
    @pytest.mark.stress
    def test_tpch_full_execution(self, tmp_path: Path) -> None:
        if not is_gpu_available():
            pytest.skip("NVIDIA GPU with CUDA not available")
        if not is_dataframe_available("cudf-df"):
            pytest.skip("cuDF not available")

        config = {
            "platform": "cudf-df",
            "benchmark": "tpch",
            "scale": "0.01",
        }

        result = run_benchmark(config, timeout=E2E_BENCHMARK_TIMEOUT)

        assert result.returncode == 0, f"CLI failed with:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    @pytest.mark.e2e
    @pytest.mark.e2e_dataframe
    @pytest.mark.e2e_quick
    def test_dry_run_generates_artifacts(self, tmp_path: Path) -> None:
        if not is_gpu_available():
            pytest.skip("NVIDIA GPU with CUDA not available")
        if not is_dataframe_available("cudf-df"):
            pytest.skip("cuDF not available")

        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "cudf-df",
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


class TestDataFusionDataFrameE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_dataframe
    @pytest.mark.slow
    def test_tpch_full_execution(self, tmp_path: Path) -> None:
        if not is_dataframe_available("datafusion-df"):
            pytest.skip("DataFusion not available")

        config = {
            "platform": "datafusion-df",
            "benchmark": "tpch",
            "scale": "0.01",
        }

        result = run_benchmark(config, timeout=E2E_BENCHMARK_TIMEOUT)

        assert result.returncode == 0, f"CLI failed with:\nstdout: {result.stdout}\nstderr: {result.stderr}"


@pytest.mark.e2e
@pytest.mark.e2e_dataframe
@pytest.mark.e2e_quick
@pytest.mark.parametrize(
    "platform",
    [
        "pandas-df",
        "polars-df",
    ],
)
def test_dataframe_platform_dry_run(tmp_path: Path, platform: str) -> None:
    if not is_dataframe_available(platform):
        pytest.skip(f"{platform} not available")

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
@pytest.mark.e2e_dataframe
@pytest.mark.e2e_quick
@pytest.mark.parametrize(
    "platform",
    [
        "pandas-df",
        "polars-df",
    ],
)
def test_dataframe_platform_query_subset(tmp_path: Path, platform: str) -> None:
    if not is_dataframe_available(platform):
        pytest.skip(f"{platform} not available")

    config = {
        "platform": platform,
        "benchmark": "tpch",
        "scale": "0.01",
        "queries": "Q1,Q6",
    }

    result = run_benchmark(config, timeout=300)

    assert result.returncode == 0, f"CLI failed for {platform}: {result.stdout}"


class TestCrossDataFrameE2E:
    @pytest.mark.e2e
    @pytest.mark.e2e_dataframe
    @pytest.mark.slow
    def test_pandas_polars_same_results(self, tmp_path: Path) -> None:
        if not is_dataframe_available("pandas-df"):
            pytest.skip("Pandas not available")
        if not is_dataframe_available("polars-df"):
            pytest.skip("Polars not available")

        pandas_config = {
            "platform": "pandas-df",
            "benchmark": "tpch",
            "scale": "0.01",
            "queries": "Q1",
        }
        pandas_result = run_benchmark(pandas_config, timeout=300)
        assert pandas_result.returncode == 0, f"Pandas failed: {pandas_result.stdout}"

        polars_config = {
            "platform": "polars-df",
            "benchmark": "tpch",
            "scale": "0.01",
            "queries": "Q1",
        }
        polars_result = run_benchmark(polars_config, timeout=300)
        assert polars_result.returncode == 0, f"Polars failed: {polars_result.stdout}"


