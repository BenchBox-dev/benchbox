from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping, Sequence

from tests.integration._cli_e2e_utils import run_cli_command

E2E_BENCHMARK_TIMEOUT = 600.0


@pytest.fixture(autouse=True)
def benchmark_runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "benchmark_runs"
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(root))
    return root


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    results = tmp_path / "benchmark_results"
    results.mkdir()
    return results


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "benchmark_data"
    data.mkdir()
    return data


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    output = tmp_path / "cli_output"
    output.mkdir()
    return output


@pytest.fixture
def dry_run_dir(tmp_path: Path) -> Path:
    dry_run = tmp_path / "dry_run"
    dry_run.mkdir()
    return dry_run


@pytest.fixture
def cleanup_results(results_dir: Path) -> Generator[Path, None, None]:
    yield results_dir
    if results_dir.exists():
        shutil.rmtree(results_dir)


def _platform_config(platform: str) -> dict[str, Any]:
    return {"platform": platform, "benchmark": "tpch", "scale": "0.01"}


def _dry_run_config(platform: str, dry_run_dir: Path) -> dict[str, Any]:
    return {**_platform_config(platform), "dry_run": str(dry_run_dir)}


@pytest.fixture
def duckdb_config() -> dict[str, Any]:
    return _platform_config("duckdb")


@pytest.fixture
def sqlite_config() -> dict[str, Any]:
    return _platform_config("sqlite")


@pytest.fixture
def datafusion_config() -> dict[str, Any]:
    return _platform_config("datafusion")


@pytest.fixture
def clickhouse_stub_dir(tmp_path: Path) -> Path:
    stub_root = tmp_path / "clickhouse_stubs"

    chdb_package_dir = stub_root / "chdb"
    session_dir = chdb_package_dir / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    (chdb_package_dir / "__init__.py").write_text(
        "chdb_version = (0, 10, 0)\n"
        "class _StubConnection:\n"
        "    def query(self, _sql, format='CSV'):\n"
        "        return ''\n"
        "    def close(self):\n"
        "        pass\n"
        "\n"
        "def connect():\n"
        "    return _StubConnection()\n"
        "\n"
        "from .session import Session\n",
        encoding="utf-8",
    )

    (session_dir / "__init__.py").write_text(
        "class Session:\n"
        "    def __init__(self, path=None):\n"
        "        self.path = path\n"
        "    def query(self, _sql, format='CSV'):\n"
        "        return ''\n"
        "    def close(self):\n"
        "        pass\n",
        encoding="utf-8",
    )

    driver_dir = stub_root / "clickhouse_driver"
    driver_dir.mkdir(parents=True, exist_ok=True)
    (driver_dir / "__init__.py").write_text(
        "__version__ = '0.0.0'\n"
        "class Client:\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        self.args = args\n"
        "        self.kwargs = kwargs\n"
        "    def execute(self, _sql):\n"
        "        return []\n"
        "\n"
        "from . import errors\n",
        encoding="utf-8",
    )
    (driver_dir / "errors.py").write_text(
        "class Error(Exception):\n    pass\n",
        encoding="utf-8",
    )

    return stub_root


@pytest.fixture
def clickhouse_env(clickhouse_stub_dir: Path) -> dict[str, str]:
    existing = os.environ.get("PYTHONPATH", "")
    return {"PYTHONPATH": f"{clickhouse_stub_dir}{os.pathsep}{existing}" if existing else str(clickhouse_stub_dir)}


@pytest.fixture
def pandas_df_config() -> dict[str, Any]:
    return _platform_config("pandas-df")


@pytest.fixture
def polars_df_config() -> dict[str, Any]:
    return _platform_config("polars-df")


@pytest.fixture
def dask_df_config() -> dict[str, Any]:
    return _platform_config("dask-df")


@pytest.fixture
def snowflake_dry_run_config(dry_run_dir: Path) -> dict[str, Any]:
    return _dry_run_config("snowflake", dry_run_dir)


@pytest.fixture
def bigquery_dry_run_config(dry_run_dir: Path) -> dict[str, Any]:
    return _dry_run_config("bigquery", dry_run_dir)


@pytest.fixture
def redshift_dry_run_config(dry_run_dir: Path) -> dict[str, Any]:
    return _dry_run_config("redshift", dry_run_dir)


@pytest.fixture
def athena_dry_run_config(dry_run_dir: Path) -> dict[str, Any]:
    return _dry_run_config("athena", dry_run_dir)


@pytest.fixture
def databricks_dry_run_config(dry_run_dir: Path) -> dict[str, Any]:
    return _dry_run_config("databricks", dry_run_dir)


def build_cli_args(
    config: dict[str, Any],
    *,
    extra_args: Sequence[str] | None = None,
) -> list[str]:
    args = ["run"]

    if "platform" in config:
        args.extend(["--platform", config["platform"]])
    if "benchmark" in config:
        args.extend(["--benchmark", config["benchmark"]])
    if "scale" in config:
        args.extend(["--scale", str(config["scale"])])
    if "phases" in config:
        args.extend(["--phases", config["phases"]])
    if "queries" in config:
        args.extend(["--queries", config["queries"]])
    if "dry_run" in config:
        args.extend(["--dry-run", config["dry_run"]])
    if "tuning" in config:
        args.extend(["--tuning", config["tuning"]])
    if "compression" in config:
        args.extend(["--compression", config["compression"]])
    if "validation" in config:
        args.extend(["--validation", config["validation"]])
    if "seed" in config:
        args.extend(["--seed", str(config["seed"])])
    if "force" in config:
        args.extend(["--force", config["force"]])
    if config.get("capture_plans"):
        args.append("--capture-plans")

    for key, value in config.get("platform_options", {}).items():
        args.extend(["--platform-option", f"{key}={value}"])

    if extra_args:
        args.extend(extra_args)

    return args


def run_benchmark(
    config: dict[str, Any],
    *,
    extra_args: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = E2E_BENCHMARK_TIMEOUT,
) -> subprocess.CompletedProcess[str]:

    args = build_cli_args(config, extra_args=extra_args)
    return run_cli_command(args, env=env, timeout=timeout)


def find_result_files(
    directory: Path,
    *,
    pattern: str = "*.json",
) -> list[Path]:
    files = list(directory.glob(pattern))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def find_latest_result(
    directory: Path,
    *,
    pattern: str = "*.json",
) -> Path | None:
    files = find_result_files(directory, pattern=pattern)
    return files[0] if files else None


import subprocess
