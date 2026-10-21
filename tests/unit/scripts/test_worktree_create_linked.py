from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env=env, text=True, capture_output=True, check=True)
    return done.stdout.strip()


@pytest.mark.skipif(sys.platform == "win32", reason="make worktree-* targets are POSIX shell tooling")
def test_worktree_create_succeeds_when_invoked_from_a_linked_worktree(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
    _git(home, "config", "--file", str(home / ".gitconfig"), "user.name", "BenchBox Test", env=env)
    _git(home, "config", "--file", str(home / ".gitconfig"), "user.email", "test@example.com", env=env)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", env=env)
    (repo / "README.md").write_text("test repo\n", encoding="utf-8")
    _git(repo, "add", "README.md", env=env)
    _git(repo, "commit", "-q", "-m", "initial", env=env)
    _git(repo, "branch", "-M", "develop", env=env)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "--bare", "-q", str(repo), str(origin), env=env)
    _git(repo, "remote", "add", "origin", str(origin), env=env)

    def create(cwd: Path, branch: str, target: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "make",
                "-f",
                str(ROOT / "Makefile"),
                "-s",
                "worktree-create",
                f"BRANCH={branch}",
                f"WORKTREE_PATH={target}",
            ],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    first = tmp_path / "wt-first"
    second = tmp_path / "wt-second"
    _git(repo, "config", "benchbox.shared-sentinel", "keep", env=env)
    created = create(repo, "fix/first", first)
    assert created.returncode == 0, created.stderr
    _git(first, "config", "--worktree", "benchbox.seeded-inherited", "leaked", env=env)
    _git(first, "config", "--worktree", "--add", "benchbox.seeded-multi", "one", env=env)
    _git(first, "config", "--worktree", "--add", "benchbox.seeded-multi", "two", env=env)

    probe = tmp_path / "wt-probe"
    _git(first, "worktree", "add", "-q", "-b", "fix/probe", str(probe), "origin/develop", env=env)
    assert _git(probe, "config", "--worktree", "--get", "benchbox.seeded-inherited", env=env) == "leaked"
    assert _git(probe, "config", "--worktree", "--get", "benchbox.worktree.lifecycle-id", env=env)
    _git(first, "worktree", "remove", "--force", str(probe), env=env)

    nested = create(first, "fix/revert-1", second)
    assert nested.returncode == 0, nested.stderr

    def lifecycle_id(path: Path) -> str:
        return _git(path, "config", "--worktree", "--get", "benchbox.worktree.lifecycle-id", env=env)

    assert lifecycle_id(second) != lifecycle_id(first)
    assert _git(first, "config", "--worktree", "--get", "benchbox.seeded-inherited", env=env) == "leaked"
    inherited = subprocess.run(
        ["git", "config", "--worktree", "--get", "benchbox.seeded-inherited"],
        cwd=second,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert inherited.returncode != 0
    assert _git(repo, "config", "--get", "benchbox.shared-sentinel", env=env) == "keep"
    assert _git(second, "config", "--worktree", "--get", "benchbox.worktree.branch", env=env) == "fix/revert-1"
