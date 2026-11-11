from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium


pytest.importorskip("pandas")

from examples import unified_runner


def test_unified_runner_lists_platforms(capsys: pytest.CaptureFixture[str]) -> None:

    original_argv = list(sys.argv)
    try:
        sys.argv = ["unified_runner.py", "--list-platforms"]
        exit_code = unified_runner.main()
    finally:
        sys.argv = original_argv

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "duckdb" in captured.out.lower()


def test_unified_runner_duckdb_dry_run(tmp_path: Path) -> None:

    dry_run_dir = tmp_path / "dryrun"
    data_dir = tmp_path / "data"
    config_path = Path("examples/config/duckdb.yaml").resolve()

    original_argv = list(sys.argv)
    try:
        sys.argv = [
            "unified_runner.py",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--phases",
            "power",
            "--dry-run",
            str(dry_run_dir),
            "--tuning",
            "notuning",
            "--platform-config",
            str(config_path),
            "--output",
            str(data_dir),
            "--quiet",
        ]

        exit_code = unified_runner.main()
    finally:
        sys.argv = original_argv

    assert exit_code == 0

    json_files = list(dry_run_dir.glob("*.json"))
    assert json_files, "Expected dry run JSON artifact to be emitted"


