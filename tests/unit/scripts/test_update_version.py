from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "update_version.py"

spec = importlib.util.spec_from_file_location("update_version", SCRIPT_PATH)
update_version = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(update_version)


def test_update_version_in_pyproject_only_changes_project_version(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """[project]
name = "benchbox"
version = "0.4.0"
requires-python = ">=3.11"

[tool.ruff]
line-length = 120
target-version = "py311"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(update_version, "get_project_root", lambda: tmp_path)

    assert update_version.update_version_in_pyproject("0.4.1")

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["version"] == "0.4.1"
    assert data["tool"]["ruff"]["target-version"] == "py311"
    assert update_version.get_current_version_from_pyproject() == "0.4.1"


def test_update_version_in_pyproject_ignores_version_keys_in_earlier_tables(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """[build-system]
requires = ["hatchling"]

[tool.example]
version = "9.9.9"

[project]
name = "benchbox"
version = "0.4.0"
dependencies = [
    "duckdb>=1.5.0",
]

[tool.other]
version = "1.2.3"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(update_version, "get_project_root", lambda: tmp_path)

    assert update_version.get_current_version_from_pyproject() == "0.4.0"
    assert update_version.update_version_in_pyproject("0.4.1")

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["version"] == "0.4.1"
    assert data["tool"]["example"]["version"] == "9.9.9"
    assert data["tool"]["other"]["version"] == "1.2.3"


def test_update_version_in_pyproject_reports_missing_project_table(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    original = '[tool.example]\nversion = "9.9.9"\n'
    pyproject.write_text(original, encoding="utf-8")
    monkeypatch.setattr(update_version, "get_project_root", lambda: tmp_path)

    assert update_version.get_current_version_from_pyproject() is None
    assert not update_version.update_version_in_pyproject("0.4.1")
    assert pyproject.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("marker", ["`", ""])
@pytest.mark.parametrize("version", ["0.5.0rc1", "0.5.0b2", "0.5.0"])
def test_doc_release_marker_reads_the_whole_version(marker, version):
    match = update_version.DOC_RELEASE_PATTERN.search(f"Current release: {marker}v{version}{marker}.")

    assert match is not None
    assert match.group("version") == version
