from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

SCRIPT = Path(__file__).resolve().parents[3] / "_project" / "scripts" / "preflight_targets.py"
spec = importlib.util.spec_from_file_location("preflight_targets", SCRIPT)
assert spec is not None and spec.loader is not None
pt = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pt
spec.loader.exec_module(pt)


def _git(repo: Path, *args: str) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _write(repo: Path, rel: str, text: str = "x = 1\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(root, *args)
    _write(root, "benchbox/core/engine.py")
    _write(root, "benchbox/core/__init__.py", "")
    _write(root, "benchbox/platforms/other.py")
    _write(root, "scripts/tool.py")
    _write(root, "tests/unit/core/test_engine.py")
    _write(root, "tests/integration/test_engine.py")
    _write(root, "tests/unit/test_tool.py")
    _write(root, "tests/unit/test_unrelated.py")
    _write(root, "docs/guide.md", "# guide\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "init")
    _git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _git(root, "checkout", "-b", "feature")
    return root


def _commit_all(repo: Path, message: str = "change") -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def test_source_module_maps_to_every_matching_test_file(repo: Path) -> None:
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)

    paths = pt.changed_paths(repo, "origin/develop")

    assert pt.map_tests(repo, paths) == ["tests/integration/test_engine.py", "tests/unit/core/test_engine.py"]


def test_changed_test_file_maps_to_itself(repo: Path) -> None:
    _write(repo, "tests/unit/test_unrelated.py", "y = 2\n")
    _commit_all(repo)

    assert pt.map_tests(repo, pt.changed_paths(repo, "origin/develop")) == ["tests/unit/test_unrelated.py"]


def test_scripts_module_maps_to_its_test(repo: Path) -> None:
    _write(repo, "scripts/tool.py", "x = 2\n")
    _commit_all(repo)

    assert pt.map_tests(repo, pt.changed_paths(repo, "origin/develop")) == ["tests/unit/test_tool.py"]


def test_unmapped_changes_select_no_tests(repo: Path) -> None:
    _write(repo, "benchbox/platforms/other.py", "x = 2\n")
    _write(repo, "benchbox/core/__init__.py", "x = 2\n")
    _write(repo, "docs/guide.md", "# changed\n")
    _commit_all(repo)

    assert pt.map_tests(repo, pt.changed_paths(repo, "origin/develop")) == []


def test_uncommitted_and_untracked_changes_are_included(repo: Path) -> None:
    _write(repo, "benchbox/core/engine.py", "x = 3\n")
    _write(repo, "tests/unit/test_new.py")
    _git(repo, "add", "tests/unit/test_new.py")
    _write(repo, "scripts/untracked.py")

    paths = pt.changed_paths(repo, "origin/develop")

    assert paths == ["benchbox/core/engine.py", "scripts/untracked.py", "tests/unit/test_new.py"]
    assert pt.changed_python(repo, paths) == paths


def test_deleted_source_still_selects_its_remaining_tests(repo: Path) -> None:
    (repo / "benchbox/core/engine.py").unlink()
    _commit_all(repo)

    paths = pt.changed_paths(repo, "origin/develop")

    assert paths == ["benchbox/core/engine.py"]
    assert pt.changed_python(repo, paths) == []
    assert pt.map_tests(repo, paths) == ["tests/integration/test_engine.py", "tests/unit/core/test_engine.py"]


def test_deleted_test_file_is_not_selected(repo: Path) -> None:
    (repo / "tests/unit/test_unrelated.py").unlink()
    _commit_all(repo)

    paths = pt.changed_paths(repo, "origin/develop")

    assert paths == ["tests/unit/test_unrelated.py"]
    assert pt.map_tests(repo, paths) == []


def test_renamed_source_keeps_the_old_path_for_test_mapping(repo: Path) -> None:
    _git(repo, "mv", "benchbox/core/engine.py", "benchbox/core/motor.py")
    _commit_all(repo)

    paths = pt.changed_paths(repo, "origin/develop")

    assert paths == ["benchbox/core/engine.py", "benchbox/core/motor.py"]
    assert pt.map_tests(repo, paths) == ["tests/integration/test_engine.py", "tests/unit/core/test_engine.py"]


def test_project_scripts_module_maps_to_its_test(repo: Path) -> None:
    _write(repo, "_project/scripts/audit.py")
    _write(repo, "tests/unit/scripts/test_audit.py")
    _commit_all(repo)
    _write(repo, "_project/scripts/audit.py", "x = 2\n")

    assert pt.map_tests(repo, pt.changed_paths(repo, "origin/develop")) == ["tests/unit/scripts/test_audit.py"]


def test_cli_prints_selection_and_reports_missing_base(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)

    assert pt.main(["python", "--repo-root", str(repo)]) == 0
    assert capsys.readouterr().out.split() == ["benchbox/core/engine.py"]
    assert pt.main(["tests", "--repo-root", str(repo), "--base-ref", "origin/missing"]) == 2
