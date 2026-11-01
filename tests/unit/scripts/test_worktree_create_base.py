from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(sys.platform == "win32", reason="make worktree-* targets are POSIX shell tooling"),
]

ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Sandbox:
    tmp: Path
    repo: Path
    origin: Path
    env: dict[str, str]

    def git(self, cwd: Path, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=cwd, env=self.env, encoding="utf-8", capture_output=True, check=True)
        return done.stdout.strip()

    def commit(self, cwd: Path, name: str) -> str:
        (cwd / name).write_text(name, encoding="utf-8")
        self.git(cwd, "add", name)
        self.git(cwd, "commit", "-q", "-m", name)
        return self.git(cwd, "rev-parse", "HEAD")

    def create(self, branch: str, target: Path, base: str | None = None, env: dict[str, str] | None = None):
        args = ["make", "-f", str(ROOT / "Makefile"), "-s", "worktree-create", f"BRANCH={branch}"]
        args.append(f"WORKTREE_PATH={target}")
        if base is not None:
            args.append(f"BASE={base}")
        return subprocess.run(
            args, cwd=self.repo, env=env or self.env, encoding="utf-8", capture_output=True, check=False
        )

    def metadata(self, worktree: Path, key: str) -> str:
        return self.git(worktree, "config", "--worktree", "--get", f"benchbox.worktree.{key}")


@pytest.fixture()
def sandbox(tmp_path: Path) -> Sandbox:
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    env.pop("BASE", None)
    for key, value in (("user.name", "BenchBox Test"), ("user.email", "test@example.com")):
        subprocess.run(["git", "config", "--file", str(home / ".gitconfig"), key, value], env=env, check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    box = Sandbox(tmp_path, repo, tmp_path / "origin.git", env)
    box.git(repo, "init", "-q", "-b", "develop")
    box.commit(repo, "README.md")
    box.git(tmp_path, "clone", "--bare", "-q", str(repo), str(box.origin))
    box.git(repo, "remote", "add", "origin", str(box.origin))
    box.git(repo, "fetch", "-q", "origin")
    return box


def test_local_branch_base_creates_the_worktree_at_its_commit_and_records_it(sandbox: Sandbox) -> None:
    sandbox.git(sandbox.repo, "checkout", "-q", "-b", "feat/integration")
    head = sandbox.commit(sandbox.repo, "integration.txt")
    sandbox.git(sandbox.repo, "checkout", "-q", "develop")
    target = sandbox.tmp / "wt"

    created = sandbox.create("feat/member", target, base="feat/integration")

    assert created.returncode == 0, created.stderr
    assert sandbox.git(target, "rev-parse", "HEAD") == head
    assert sandbox.metadata(target, "base-oid") == head
    assert sandbox.metadata(target, "base-ref") == "feat/integration"


def test_commit_base_is_recorded_as_given(sandbox: Sandbox) -> None:
    first = sandbox.git(sandbox.repo, "rev-parse", "HEAD")
    sandbox.commit(sandbox.repo, "later.txt")
    target = sandbox.tmp / "wt"

    created = sandbox.create("fix/pinned", target, base=first)

    assert created.returncode == 0, created.stderr
    assert sandbox.git(target, "rev-parse", "HEAD") == first
    assert sandbox.metadata(target, "base-oid") == first
    assert sandbox.metadata(target, "base-ref") == first


def test_default_base_fetches_origin_develop_and_records_its_commit(sandbox: Sandbox) -> None:
    other = sandbox.tmp / "other"
    sandbox.git(sandbox.tmp, "clone", "-q", str(sandbox.origin), str(other))
    advanced = sandbox.commit(other, "advanced.txt")
    sandbox.git(other, "push", "-q", "origin", "HEAD:develop")
    target = sandbox.tmp / "wt"

    created = sandbox.create("fix/default", target)

    assert created.returncode == 0, created.stderr
    assert sandbox.git(target, "rev-parse", "HEAD") == advanced
    assert sandbox.metadata(target, "base-oid") == advanced
    assert sandbox.metadata(target, "base-ref") == "origin/develop"
    assert sandbox.git(target, "rev-parse", "--abbrev-ref", "fix/default@{upstream}") == "origin/develop"


def test_remote_tracking_base_other_than_develop_is_fetched(sandbox: Sandbox) -> None:
    other = sandbox.tmp / "other"
    sandbox.git(sandbox.tmp, "clone", "-q", str(sandbox.origin), str(other))
    sandbox.git(other, "checkout", "-q", "-b", "integration")
    head = sandbox.commit(other, "integration.txt")
    sandbox.git(other, "push", "-q", "origin", "integration")
    target = sandbox.tmp / "wt"

    created = sandbox.create("fix/remote-base", target, base="origin/integration")

    assert created.returncode == 0, created.stderr
    assert sandbox.git(target, "rev-parse", "HEAD") == head
    assert sandbox.metadata(target, "base-ref") == "origin/integration"


@pytest.mark.parametrize(
    ("base", "moving_ref"),
    [(None, "refs/remotes/origin/develop"), ("feat/integration", "refs/heads/feat/integration")],
)
def test_ref_moving_after_resolution_does_not_change_the_created_commit(
    sandbox: Sandbox, base: str | None, moving_ref: str
) -> None:
    sandbox.git(sandbox.repo, "branch", "feat/integration")
    resolved = sandbox.git(sandbox.repo, "rev-parse", "HEAD")
    sandbox.git(sandbox.repo, "checkout", "-q", "-b", "scratch")
    moved = sandbox.commit(sandbox.repo, "moved.txt")
    sandbox.git(sandbox.repo, "checkout", "-q", "develop")
    sandbox.git(sandbox.repo, "update-ref", "refs/heads/scratch-tip", moved)

    real_git = shutil.which("git", path=sandbox.env["PATH"])
    assert real_git is not None
    bin_dir = sandbox.tmp / "bin"
    bin_dir.mkdir()
    marker = sandbox.tmp / "moved.marker"
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = worktree ] && [ "$2" = add ] && [ ! -e "$MOVE_MARKER" ]; then\n'
        '  : > "$MOVE_MARKER"\n'
        f'  {real_git} update-ref "$MOVE_REF" "$MOVE_TO"\n'
        "fi\n"
        f'exec {real_git} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    env = {
        **sandbox.env,
        "PATH": f"{bin_dir}{os.pathsep}{sandbox.env['PATH']}",
        "MOVE_MARKER": str(marker),
        "MOVE_REF": moving_ref,
        "MOVE_TO": moved,
    }
    target = sandbox.tmp / "wt"

    created = sandbox.create("fix/race", target, base=base, env=env)

    assert created.returncode == 0, created.stderr
    assert marker.exists()
    assert sandbox.git(sandbox.repo, "rev-parse", moving_ref) == moved
    assert sandbox.git(target, "rev-parse", "HEAD") == resolved
    assert sandbox.metadata(target, "base-oid") == resolved


@pytest.mark.parametrize("base", ["no/such-ref", "--upload-pack=x"])
def test_unresolvable_base_is_refused_without_leaving_a_branch(sandbox: Sandbox, base: str) -> None:
    target = sandbox.tmp / "wt"

    created = sandbox.create("fix/bad-base", target, base=base)

    assert created.returncode != 0
    assert not target.exists()
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/fix/bad-base"], cwd=sandbox.repo, env=sandbox.env
        ).returncode
        != 0
    )


@pytest.mark.parametrize(
    ("branch", "message"),
    [("develop", "protected branch"), ("wip/thing", "Conventional Commit type"), ("fix/existing", "already exists")],
)
def test_existing_refusals_still_fire_with_a_base(sandbox: Sandbox, branch: str, message: str) -> None:
    sandbox.git(sandbox.repo, "branch", "fix/existing")
    target = sandbox.tmp / "wt"

    created = sandbox.create(branch, target, base="develop")

    assert created.returncode != 0
    assert message in created.stderr
    assert not target.exists()


def test_existing_path_is_refused_with_a_base(sandbox: Sandbox) -> None:
    target = sandbox.tmp / "wt"
    target.mkdir()

    created = sandbox.create("fix/path", target, base="develop")

    assert created.returncode != 0
    assert "Worktree path already exists" in created.stderr
