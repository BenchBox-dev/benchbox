from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"
FENCE = re.compile(r"```bash\n(.*?)```", re.DOTALL)


def _quickstart() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("## Quick Start")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def _core_dependency_names() -> set[str]:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {re.split(r"[<>=!\[; ]", spec, maxsplit=1)[0].strip() for spec in pyproject["project"]["dependencies"]}


def test_duckdb_is_not_a_core_dependency() -> None:
    assert "duckdb" not in _core_dependency_names()


def test_quickstart_uses_duckdb_so_it_must_install_duckdb() -> None:
    quickstart = _quickstart()
    assert "duckdb" in quickstart, "fixture assumption changed: the Quick Start no longer uses DuckDB"

    install_lines = [
        line.strip()
        for block in FENCE.findall(quickstart)
        for line in block.splitlines()
        if line.strip().startswith(("uv add benchbox", "uv pip install"))
    ]
    assert install_lines, "the Quick Start names no install command"

    assert any("duckdb" in line for line in install_lines), (
        "the Quick Start uses DuckDB but never installs it:\n  " + "\n  ".join(install_lines)
    )


def test_quickstart_does_not_offer_a_bare_install_as_the_local_path() -> None:
    install_lines = [
        line.strip()
        for block in FENCE.findall(_quickstart())
        for line in block.splitlines()
        if line.strip().startswith(("uv add benchbox", "uv pip install"))
    ]

    bare = [line for line in install_lines if line in ("uv add benchbox", "uv pip install benchbox")]

    assert not bare, (
        f"the Quick Start offers an install with no extra, which ships SQLite only and cannot run its own steps: {bare}"
    )


def test_quickstart_comes_before_the_installation_deep_dive() -> None:
    text = README.read_text(encoding="utf-8")

    assert text.index("## Quick Start") < text.index("\n## Installation")


def test_quickstart_commands_work_after_the_pip_install() -> None:
    quickstart = _quickstart()
    execution_lines = [
        line.strip()
        for block in FENCE.findall(quickstart)
        for line in block.splitlines()
        if "benchbox" in line and not line.strip().startswith(("uv add", "python -m pip"))
    ]

    assert execution_lines
    assert all(not line.startswith("uv run") for line in execution_lines)
    assert "prefix each command with `uv run --`" in re.sub(r"\s+", " ", quickstart)


def test_quickstart_does_not_document_ignored_results_limit() -> None:
    assert "benchbox results --limit" not in _quickstart()


def test_quickstart_describes_the_rendered_result_columns() -> None:
    quickstart = _quickstart()

    for column in ("benchmark", "platform", "timestamp", "duration", "query count", "BenchBox version"):
        assert column in quickstart
    assert "validation outcome" not in quickstart
    assert "query timings" not in quickstart


