from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
]


def _write_file(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_compat_lint_exempts_registry_backed_schema_emit_branch(tmp_path: Path):
    root = tmp_path / "benchbox" / "core"
    _write_file(
        root / "nyctaxi" / "schema.py",
        """
def build_sql(dialect: str) -> str:
    if dialect == "clickhouse":
        return "optimized"
    return "native"
""".lstrip(),
    )

    result = subprocess.run(
        [sys.executable, "scripts/compat_lint.py", "--root", str(root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_compat_lint_exempts_registry_backed_query_source_branch(tmp_path: Path):
    root = tmp_path / "benchbox" / "core"
    _write_file(
        root / "vector_search" / "queries.py",
        """
def get_query(dialect: str) -> str:
    if dialect == "snowflake":
        return "variant"
    return "native"
""".lstrip(),
    )

    result = subprocess.run(
        [sys.executable, "scripts/compat_lint.py", "--root", str(root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_compat_lint_reports_unregistered_branch(tmp_path: Path):
    root = tmp_path / "benchbox" / "core"
    _write_file(
        root / "example_benchmark" / "schema.py",
        """
def build_sql(dialect: str) -> str:
    if dialect == "clickhouse":
        return "optimized"
    return "native"
""".lstrip(),
    )

    result = subprocess.run(
        [sys.executable, "scripts/compat_lint.py", "--root", str(root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "example_benchmark/schema.py:2" in result.stderr.replace("\\", "/")


