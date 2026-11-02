from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.scripts.batch_loop_world import ROOT, bi, git

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(shutil.which("make") is None, reason="make is required"),
]

MAKEFILE = ROOT / "Makefile"
REFUSAL = "batch member worktree"


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    for key, value in {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }.items():
        monkeypatch.setenv(key, value)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "develop", str(origin)], check=True)
    primary = tmp_path / "primary"
    subprocess.run(["git", "clone", "-q", str(origin), str(primary)], check=True, capture_output=True)
    git(primary, "checkout", "-q", "-b", "develop")
    git(primary, "commit", "-q", "--allow-empty", "-m", "seed")
    git(primary, "push", "-q", "origin", "develop")
    member = tmp_path / "member"
    git(primary, "worktree", "add", "-q", "-b", "feat/b--m", str(member))
    ordinary = tmp_path / "ordinary"
    git(primary, "worktree", "add", "-q", "-b", "feat/plain", str(ordinary))
    bi._config_set(member, "role", "member")
    return primary, member, ordinary


def _make(cwd: Path, target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-f", str(MAKEFILE), target], cwd=cwd, check=False, capture_output=True, text=True, timeout=120
    )


def _origin_refs(primary: Path) -> str:
    return git(primary, "ls-remote", "origin")


def test_pr_open_refuses_in_a_member_worktree_before_anything_is_pushed(repos: tuple[Path, Path, Path]) -> None:
    primary, member, _ = repos
    before = _origin_refs(primary)
    proc = _make(member, "pr-open")
    assert proc.returncode != 0 and REFUSAL in proc.stderr
    assert _origin_refs(primary) == before


def test_pr_fanout_refuses_when_run_from_a_member_worktree(repos: tuple[Path, Path, Path]) -> None:
    primary, member, _ = repos
    before = _origin_refs(primary)
    proc = _make(member, "pr-fanout")
    assert proc.returncode != 0 and REFUSAL in proc.stderr
    assert _origin_refs(primary) == before


def test_pr_fanout_skips_member_worktrees_and_never_opens_them(repos: tuple[Path, Path, Path]) -> None:
    primary, member, ordinary = repos
    bi._config_set(ordinary, "role", "integration")
    proc = _make(primary, "pr-fanout")
    output = proc.stdout + proc.stderr
    assert f"(skip {member}: batch member)" in output
    assert f"==> {member}" not in output
    assert f"==> {ordinary}" in output


def test_ordinary_worktrees_are_not_refused(repos: tuple[Path, Path, Path]) -> None:
    _, _, ordinary = repos
    proc = _make(ordinary, "pr-open")
    assert REFUSAL not in proc.stdout + proc.stderr
