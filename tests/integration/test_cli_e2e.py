from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


try:
    import clickhouse_driver

    CLICKHOUSE_DRIVER_AVAILABLE = True
except ImportError:
    CLICKHOUSE_DRIVER_AVAILABLE = False


@pytest.mark.integration
def test_cli_help_command_runs_successfully():
    result = run_cli_command(["--help"])

    assert result.returncode == 0
    assert "Usage: python -m benchbox.cli.main" in result.stdout
    assert "Interactive database benchmark runner." in result.stdout


@pytest.mark.integration
def test_cli_version_command_reports_versions():
    result = run_cli_command(["--version"])

    assert result.returncode == 0
    assert "BenchBox Version:" in result.stdout
    assert "Version Consistency:" in result.stdout


def _prepare_clickhouse_stub(stub_root: Path) -> Path:

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


@pytest.mark.integration
@pytest.mark.platform_smoke
@pytest.mark.parametrize("database", ["duckdb", "sqlite", "clickhouse-local"])
def test_cli_dry_run_generates_expected_artifacts(tmp_path: Path, database: str):

    if database == "clickhouse-local" and not CLICKHOUSE_DRIVER_AVAILABLE:
        pytest.skip("clickhouse-driver not installed")
    output_dir = tmp_path / f"dry_run_{database}"
    output_dir.mkdir()

    args = [
        "run",
        "--platform",
        database,
        "--benchmark",
        "tpch",
        "--scale",
        "0.01",
        "--dry-run",
        str(output_dir),
    ]

    env = {}

    if database == "clickhouse-local":
        chdb_path = tmp_path / "chdb_store"
        chdb_path.mkdir()

        args.extend(
            [
                "--platform-option",
                f"data_path={chdb_path / 'benchbox.chdb'}",
                "--platform-option",
                "driver_auto_install=false",
            ]
        )

        stub_dir = _prepare_clickhouse_stub(tmp_path / "stubs")
        existing = os.environ.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{stub_dir}{os.pathsep}{existing}" if existing else str(stub_dir)

    result = run_cli_command(args, env=env)

    assert result.returncode == 0, result.stdout
    assert "Dry run completed" in result.stdout

    artifacts = list(output_dir.glob("*"))
    assert artifacts, f"No artifacts generated. Stdout:\n{result.stdout}"


@pytest.mark.integration
def test_cli_dry_run_data_only_mode(tmp_path: Path):
    output_dir = tmp_path / "dry_run_data_only"
    output_dir.mkdir()

    result = run_cli_command(
        [
            "run",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--phases",
            "generate",
            "--dry-run",
            str(output_dir),
        ]
    )

    assert result.returncode == 0
    assert "Data generation preview for tpch" in result.stdout

    json_outputs = list(output_dir.glob("tpch_data-only_*.json"))
    yaml_outputs = list(output_dir.glob("tpch_data-only_*.yaml"))
    query_dirs = [path for path in output_dir.glob("tpch_data-only_queries_*") if path.is_dir()]

    assert json_outputs, f"No JSON output generated. Stdout:\n{result.stdout}"
    assert yaml_outputs, f"No YAML output generated. Stdout:\n{result.stdout}"
    assert query_dirs, f"No query directory generated. Stdout:\n{result.stdout}"


@pytest.mark.integration
def test_cli_dry_run_requires_database_when_not_data_only(tmp_path: Path):
    output_dir = tmp_path / "dry_run_missing_database"
    output_dir.mkdir()

    result = run_cli_command(
        [
            "run",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--dry-run",
            str(output_dir),
        ]
    )

    assert result.returncode != 0
    assert "Dry run mode requires --platform and --benchmark parameters" in result.stdout
    assert "Use --phases generate" in result.stdout


