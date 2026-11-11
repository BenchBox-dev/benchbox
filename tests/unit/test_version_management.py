from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

import benchbox
from benchbox.utils.version import (
    check_version_consistency,
    format_version_report,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


def test_pyproject_version_matches_package() -> None:

    with PYPROJECT_PATH.open("rb") as handle:
        data = tomllib.load(handle)

    pyproject_version = data["project"]["version"]
    assert pyproject_version == benchbox.__version__


def test_format_version_report_includes_release_tag_and_docs() -> None:

    report = format_version_report()
    assert f"BenchBox Version: {benchbox.__version__}" in report
    assert f"Release Tag: v{benchbox.__version__}" in report
    assert "README.md Version:" in report
    assert "docs" in report and "README.md Version:" in report
    assert "benchbox" in report and "VERSION_MANAGEMENT.md Version:" in report


def test_format_version_report_supports_json_payload() -> None:

    report = format_version_report(as_json=True)
    payload = json.loads(report)
    assert payload["benchbox_version"] == benchbox.__version__
    assert payload["version_consistent"] is True
    assert payload["expected_version"] == benchbox.__version__
    assert "README.md" in " ".join(payload["documentation_versions"].keys())


def test_check_version_consistency_reports_clean_state() -> None:

    result = check_version_consistency()
    assert result.consistent, f"Expected consistent versions, got: {result.message} ({result.sources})"
    assert result.expected_version == benchbox.__version__
    assert not result.missing_sources
    assert not result.mismatched_sources


def test_check_version_consistency_normalizes_versions() -> None:

    result = check_version_consistency()
    values = {src: val for src, val in result.normalized_sources.items() if val}
    assert all(val == benchbox.__version__ for val in values.values())


