from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.utilities.paths import REPO_ROOT

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(
        not (REPO_ROOT / ".gitattributes").is_file(),
        reason=".gitattributes is dev-only; release-cut removes it from the release tree",
    ),
]

BASE = "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- existing\n\n## [0.5.0] - 2026-10-01\n\n- old\n"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def _branch_with_entry(repo: Path, name: str, entry: str) -> None:
    _git(repo, "checkout", "-q", "-b", name, "develop")
    path = repo / "CHANGELOG.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("- existing\n", f"- existing\n- {entry}\n"), encoding="utf-8"
    )
    _git(repo, "commit", "-qam", name)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "develop")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / ".gitattributes").write_text((REPO_ROOT / ".gitattributes").read_text(encoding="utf-8"), encoding="utf-8")
    (path / "CHANGELOG.md").write_text(BASE, encoding="utf-8")
    _git(path, "add", ".gitattributes", "CHANGELOG.md")
    _git(path, "commit", "-qm", "base")
    return path


def test_repository_declares_union_merge_for_changelog() -> None:
    result = _git(REPO_ROOT, "check-attr", "merge", "--", "CHANGELOG.md")

    assert result.stdout.strip() == "CHANGELOG.md: merge: union"


def test_entries_added_under_the_same_heading_merge_without_conflict(repo: Path) -> None:
    _branch_with_entry(repo, "a", "entry from a")
    _branch_with_entry(repo, "b", "entry from b")
    _git(repo, "checkout", "-q", "a")

    merged = _git(repo, "merge", "b", check=False)

    assert merged.returncode == 0, merged.stdout + merged.stderr
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.count("- entry from a\n") == 1
    assert text.count("- entry from b\n") == 1
    assert "<<<<<<<" not in text


def test_rebase_of_one_entry_branch_onto_another_has_no_conflict(repo: Path) -> None:
    _branch_with_entry(repo, "a", "entry from a")
    _branch_with_entry(repo, "b", "entry from b")

    rebased = _git(repo, "rebase", "a", check=False)

    assert rebased.returncode == 0, rebased.stdout + rebased.stderr
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- entry from a\n" in text and "- entry from b\n" in text


def test_without_the_rule_the_same_merge_conflicts(repo: Path) -> None:
    (repo / ".gitattributes").write_text("", encoding="utf-8")
    _git(repo, "commit", "-qam", "drop rule")
    _branch_with_entry(repo, "a", "entry from a")
    _branch_with_entry(repo, "b", "entry from b")
    _git(repo, "checkout", "-q", "a")

    merged = _git(repo, "merge", "b", check=False)

    assert merged.returncode != 0
