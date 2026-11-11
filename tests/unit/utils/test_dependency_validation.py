from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.version import Version

import benchbox.utils.dependency_validation as dependency_validation

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_toml_raises_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"

    with pytest.raises(FileNotFoundError, match="Missing required file"):
        dependency_validation._load_toml(missing)


def test_collect_locked_versions_canonicalizes_and_ignores_invalid_entries() -> None:
    packages = dependency_validation._collect_locked_versions(
        {
            "package": [
                {"name": "Click", "version": "8.1.7"},
                {"name": "click", "version": "8.1.8"},
                {"name": "rich", "version": "13.7.0"},
                {"name": "missing-version"},
                {"version": "1.0.0"},
                "not-a-mapping",
            ]
        }
    )

    assert packages["click"] == {Version("8.1.7"), Version("8.1.8")}
    assert packages["rich"] == {Version("13.7.0")}
    assert "missing-version" not in packages


def test_validate_dependency_versions_requires_project_section() -> None:
    with pytest.raises(dependency_validation.DependencyValidationError, match="missing \\[project\\] section"):
        dependency_validation.validate_dependency_versions({}, {"package": []})


def test_validate_dependency_versions_reports_main_and_optional_dependency_gaps() -> None:
    pyproject_data = {
        "project": {
            "dependencies": ["click>=8.1", "rich==13.7.0"],
            "optional-dependencies": {
                "cloud": ["boto3>=1.34"],
                "viz": ["plotly"],
            },
        }
    }
    lock_data = {
        "package": [
            {"name": "click", "version": "8.2.1"},
            {"name": "rich", "version": "13.6.0"},
            {"name": "plotly", "version": "5.24.0"},
        ]
    }

    problems = dependency_validation.validate_dependency_versions(pyproject_data, lock_data)

    assert "Missing satisfying lock entry for rich==13.7.0" in problems
    assert "Missing satisfying lock entry for boto3>=1.34 (extra: cloud)" in problems
    assert all("plotly" not in problem for problem in problems)


def test_build_matrix_summary_sorts_optional_dependencies_and_preserves_markers() -> None:
    summary = dependency_validation.build_matrix_summary(
        {
            "project": {
                "optional-dependencies": {
                    "zeta": ["zstd>=1"],
                    "alpha": ["attrs>=23"],
                    "ignored": 123,
                }
            }
        },
        {
            "requires-python": ">=3.11",
            "resolution-markers": ["python_version >= '3.11'"],
        },
    )

    assert summary["python_requires"] == ">=3.11"
    assert summary["resolution_markers"] == ["python_version >= '3.11'"]
    assert list(summary["optional_dependencies"]) == ["alpha", "zeta"]


def test_print_matrix_renders_python_range_markers_and_optional_groups() -> None:
    summary = {
        "python_requires": ">=3.11",
        "resolution_markers": ["python_version >= '3.11'"],
        "optional_dependencies": {
            "cloud": ["boto3>=1.34", "s3fs>=2024.1"],
        },
    }

    with patch("benchbox.utils.dependency_validation.emit") as emit:
        dependency_validation._print_matrix(summary)

    emitted = [call.args[0] if call.args else "" for call in emit.call_args_list]
    assert "Python compatibility" in emitted
    assert "  Supported range: >=3.11" in emitted
    assert "  Solver markers:" in emitted
    assert "    - python_version >= '3.11'" in emitted
    assert "Optional dependency groups" in emitted
    assert "  * cloud: boto3>=1.34, s3fs>=2024.1" in emitted


def test_main_returns_zero_and_prints_matrix(tmp_path: Path) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    lock_path = tmp_path / "uv.lock"
    _write(
        pyproject_path,
        """
[project]
name = "benchbox"
version = "0.1.0"
dependencies = ["click>=8.1", "rich==13.7.0"]

[project.optional-dependencies]
cloud = ["boto3>=1.34"]
""".strip(),
    )
    _write(
        lock_path,
        """
version = 1
requires-python = ">=3.11"
resolution-markers = ["python_version >= '3.11'"]

[[package]]
name = "click"
version = "8.2.1"

[[package]]
name = "rich"
version = "13.7.0"

[[package]]
name = "boto3"
version = "1.34.10"
""".strip(),
    )

    with patch("benchbox.utils.dependency_validation.emit") as emit:
        result = dependency_validation.main(["--matrix", "--pyproject", str(pyproject_path), "--lock", str(lock_path)])

    assert result == 0
    emitted = [call.args[0] if call.args else "" for call in emit.call_args_list]
    assert "Python compatibility" in emitted
    assert any("cloud: boto3>=1.34" in item for item in emitted)


def test_main_returns_one_and_prints_missing_requirements(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    lock_path = tmp_path / "uv.lock"
    _write(
        pyproject_path,
        """
[project]
name = "benchbox"
version = "0.1.0"
dependencies = ["click>=8.1", "rich>=13.7"]
""".strip(),
    )
    _write(
        lock_path,
        """
version = 1

[[package]]
name = "click"
version = "8.2.1"
""".strip(),
    )

    result = dependency_validation.main(["--pyproject", str(pyproject_path), "--lock", str(lock_path)])
    captured = capsys.readouterr()

    assert result == 1
    assert "Missing satisfying lock entry for rich>=13.7" in captured.err


