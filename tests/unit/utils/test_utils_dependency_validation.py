import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


import tomllib

from packaging.requirements import Requirement
from packaging.version import Version

from benchbox.utils import dependency_validation
from benchbox.utils.dependency_validation import (
    _collect_locked_versions,
    _format_requirement,
    _print_matrix,
    _requirements_from_strings,
    _validate_requirement,
    build_matrix_summary,
    validate_dependency_versions,
)


@pytest.mark.unit
class TestDependencyValidation:
    pyproject_path = Path("pyproject.toml")
    lock_path = Path("uv.lock")

    def test_validate_dependency_versions_matches_lock(self):
        pyproject_data = tomllib.loads(self.pyproject_path.read_text())
        lock_data = tomllib.loads(self.lock_path.read_text())

        problems = dependency_validation.validate_dependency_versions(pyproject_data, lock_data)

        assert problems == []

    def test_cli_matrix_output(self, capsys, monkeypatch):
        import benchbox.utils.printing as printing

        monkeypatch.setattr(printing, "_QUIET", False)
        monkeypatch.setattr(printing, "_STD_CONSOLE", None)
        exit_code = dependency_validation.main(
            ["--matrix", "--pyproject", str(self.pyproject_path), "--lock", str(self.lock_path)]
        )

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Python compatibility" in captured.out
        assert "Optional dependency groups" in captured.out
        assert captured.err == ""


class TestCollectLockedVersions:
    def _lock(self, packages):
        return {"package": packages}

    def test_single_package(self):
        result = _collect_locked_versions(self._lock([{"name": "foo", "version": "1.2.3"}]))
        assert result == {"foo": {Version("1.2.3")}}

    def test_multiple_versions_same_package(self):
        result = _collect_locked_versions(
            self._lock(
                [
                    {"name": "foo", "version": "1.0.0"},
                    {"name": "foo", "version": "2.0.0"},
                ]
            )
        )
        assert result["foo"] == {Version("1.0.0"), Version("2.0.0")}

    def test_missing_name_skipped(self):
        result = _collect_locked_versions(self._lock([{"version": "1.0.0"}]))
        assert result == {}

    def test_missing_version_skipped(self):
        result = _collect_locked_versions(self._lock([{"name": "foo"}]))
        assert result == {}

    def test_canonical_name_normalization(self):
        result = _collect_locked_versions(self._lock([{"name": "PyYAML", "version": "6.0"}]))
        assert "pyyaml" in result


class TestRequirementsFromStrings:
    def test_two_requirements(self):
        reqs = _requirements_from_strings(["foo>=1.0", "bar==2.3.4"])
        assert len(reqs) == 2
        assert reqs[0].name == "foo"
        assert str(reqs[0].specifier) == ">=1.0"

    def test_bare_requirement_has_empty_specifier(self):
        reqs = _requirements_from_strings(["foo"])
        assert str(reqs[0].specifier) == ""


class TestValidateRequirement:
    def _req(self, spec):
        return Requirement(spec)

    def test_satisfying_version_present_returns_true(self):
        assert _validate_requirement(self._req("foo>=1.0"), {Version("1.2.3")}) is True

    def test_version_does_not_satisfy_returns_false(self):
        assert _validate_requirement(self._req("foo>=2.0"), {Version("1.0.0")}) is False

    def test_no_versions_returns_false(self):
        assert _validate_requirement(self._req("foo>=1.0"), set()) is False

    def test_no_specifier_any_version_acceptable(self):
        assert _validate_requirement(self._req("foo"), {Version("0.1.0")}) is True


class TestFormatRequirement:
    def test_with_specifier(self):
        assert _format_requirement(Requirement("foo>=1.0")) == "foo>=1.0"

    def test_without_specifier(self):
        assert _format_requirement(Requirement("foo")) == "foo"


class TestValidateDependencyVersionsSynthetic:
    def _pyproject(self, deps, extras=None):
        data = {"project": {"dependencies": deps}}
        if extras:
            data["project"]["optional-dependencies"] = extras
        return data

    def _lock(self, packages):
        return {"package": [{"name": n, "version": v} for n, v in packages.items()]}

    def test_all_satisfied_returns_empty(self):
        pyproject = self._pyproject(["foo>=1.0"])
        lock = self._lock({"foo": "1.5.0"})
        assert validate_dependency_versions(pyproject, lock) == []

    def test_one_unsatisfied_returns_error(self):
        pyproject = self._pyproject(["foo>=2.0"])
        lock = self._lock({"foo": "1.0.0"})
        problems = validate_dependency_versions(pyproject, lock)
        assert len(problems) == 1
        assert "foo>=2.0" in problems[0]

    def test_multiple_failures_all_returned(self):
        pyproject = self._pyproject(["foo>=2.0", "bar>=3.0"])
        lock = self._lock({"foo": "1.0.0", "bar": "1.0.0"})
        problems = validate_dependency_versions(pyproject, lock)
        assert len(problems) == 2

    def test_extras_unsatisfied_includes_extra_name(self):
        pyproject = self._pyproject([], extras={"mcp": ["mcp-sdk>=1.0"]})
        lock = self._lock({})
        problems = validate_dependency_versions(pyproject, lock)
        assert any("extra: mcp" in p for p in problems)


class TestBuildMatrixSummary:
    def test_extracts_python_requires(self):
        pyproject = {"project": {"dependencies": []}}
        lock = {"requires-python": ">=3.11", "resolution-markers": [], "package": []}
        summary = build_matrix_summary(pyproject, lock)
        assert summary["python_requires"] == ">=3.11"

    def test_extracts_resolution_markers(self):
        pyproject = {"project": {"dependencies": []}}
        lock = {"requires-python": ">=3.11", "resolution-markers": ["sys_platform == 'linux'"], "package": []}
        summary = build_matrix_summary(pyproject, lock)
        assert "sys_platform == 'linux'" in summary["resolution_markers"]

    def test_extracts_optional_dependencies(self):
        pyproject = {"project": {"optional-dependencies": {"mcp": ["mcp-sdk>=1.0"]}}}
        lock = {"package": []}
        summary = build_matrix_summary(pyproject, lock)
        assert "mcp" in summary["optional_dependencies"]


class TestPrintMatrix:
    def test_python_compat_header_in_output(self, capsys, monkeypatch):
        import benchbox.utils.printing as printing

        monkeypatch.setattr(printing, "_QUIET", False)
        monkeypatch.setattr(printing, "_STD_CONSOLE", None)

        summary = {
            "python_requires": ">=3.11",
            "resolution_markers": ["sys_platform == 'linux'"],
            "optional_dependencies": {},
        }
        _print_matrix(summary)
        out = capsys.readouterr().out
        assert "Python compatibility" in out

    def test_no_optional_deps_shows_message(self, capsys, monkeypatch):
        import benchbox.utils.printing as printing

        monkeypatch.setattr(printing, "_QUIET", False)
        monkeypatch.setattr(printing, "_STD_CONSOLE", None)

        summary = {
            "python_requires": ">=3.11",
            "resolution_markers": [],
            "optional_dependencies": {},
        }
        _print_matrix(summary)
        out = capsys.readouterr().out
        assert "No optional dependencies" in out


class TestMain:
    def test_matrix_flag_prints_compat(self, tmp_path, capsys, monkeypatch):
        import benchbox.utils.printing as printing

        monkeypatch.setattr(printing, "_QUIET", False)
        monkeypatch.setattr(printing, "_STD_CONSOLE", None)

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""
            [project]
            name = "test"
            dependencies = ["foo>=1.0"]
        """)
        )
        lock = tmp_path / "uv.lock"
        lock.write_text(
            textwrap.dedent("""
            requires-python = ">=3.11"
            [[package]]
            name = "foo"
            version = "1.5.0"
        """)
        )
        code = dependency_validation.main(["--matrix", "--pyproject", str(pyproject), "--lock", str(lock)])
        assert code == 0
        out = capsys.readouterr().out
        assert "Python compatibility" in out

    def test_missing_dep_returns_exit_code_1(self, tmp_path, capsys):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""
            [project]
            name = "test"
            dependencies = ["missing-pkg>=1.0"]
        """)
        )
        lock = tmp_path / "uv.lock"
        lock.write_text(
            textwrap.dedent("""
            requires-python = ">=3.11"
        """)
        )
        code = dependency_validation.main(["--pyproject", str(pyproject), "--lock", str(lock)])
        assert code == 1
        err = capsys.readouterr().err
        assert "missing-pkg" in err


