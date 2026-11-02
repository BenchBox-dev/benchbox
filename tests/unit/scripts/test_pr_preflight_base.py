from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "_project" / "scripts" / "preflight_targets.py"


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    done = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, encoding="utf-8", env=env)
    return done.stdout.strip()


_revisions = itertools.count()


def _commit(repo: Path, rel: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"x = {next(_revisions)}\n", encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", rel)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(root, *args)
    _commit(root, "tests/unit/test_alpha.py")
    _commit(root, "tests/unit/test_beta.py")
    _commit(root, "scripts/alpha.py")
    _commit(root, "scripts/beta.py")
    _git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _git(root, "checkout", "-q", "-b", "feature")
    return root


def _select(repo: Path, kind: str, base: str | None = None) -> list[str]:
    args = [sys.executable, str(SCRIPT), kind, "--repo-root", str(repo)]
    if base is not None:
        args += ["--base-ref", base]
    done = subprocess.run(args, check=True, capture_output=True, encoding="utf-8")
    return done.stdout.split()


def test_selection_since_a_commit_excludes_paths_changed_before_it(repo: Path) -> None:
    _commit(repo, "scripts/alpha.py")
    integration = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "scripts/beta.py")

    assert _select(repo, "tests", "origin/develop") == ["tests/unit/test_alpha.py", "tests/unit/test_beta.py"]
    assert _select(repo, "tests", integration) == ["tests/unit/test_beta.py"]
    assert _select(repo, "python", integration) == ["scripts/beta.py"]


def _run_preflight(tmp_path: Path, base: str | None) -> tuple[list[str], str]:
    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "Makefile", work / "Makefile")
    shutil.copytree(ROOT / "make", work / "make")
    for helper in ("check_primary_clone_clean.sh", "make_timing_shell.sh"):
        shutil.copy(ROOT / "scripts" / helper, work / "scripts" / helper)
    (work / "_project" / "decisions").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv.log"
    stub = bin_dir / "uv"
    stub.write_text(f'#!/bin/sh\necho "$*" >> {log}\n', encoding="utf-8")
    stub.chmod(0o755)
    git_log = tmp_path / "git.log"
    real_git = shutil.which("git")
    assert real_git is not None
    git_stub = bin_dir / "git"
    git_stub.write_text(
        f'#!/bin/sh\nif [ "$1" = fetch ]; then echo "$*" >> {git_log}; exit 0; fi\nexec {real_git} "$@"\n',
        encoding="utf-8",
    )
    git_stub.chmod(0o755)
    _git(work, "init", "-q", "-b", "main")
    args = ["make", "-s", "pr-preflight"]
    if base is not None:
        args.append(f"BASE={base}")
    env = {key: value for key, value in os.environ.items() if key != "BASE" and not key.startswith("GIT_")}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    done = subprocess.run(args, cwd=work, env=env, encoding="utf-8", capture_output=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    selections = [line for line in log.read_text(encoding="utf-8").splitlines() if "preflight_targets.py" in line]
    fetches = git_log.read_text(encoding="utf-8") if git_log.exists() else ""
    return selections, fetches


def test_pr_preflight_selects_against_base_without_fetching_develop(tmp_path: Path) -> None:
    selections, fetches = _run_preflight(tmp_path, "abc1234")

    assert [line.split()[-1] for line in selections] == ["abc1234"] * 2
    assert fetches == ""


def test_pr_preflight_defaults_to_a_fetched_origin_develop(tmp_path: Path) -> None:
    selections, fetches = _run_preflight(tmp_path, None)

    assert [line.split()[-1] for line in selections] == ["origin/develop"] * 2
    assert fetches.split() == ["fetch", "origin", "develop", "--quiet"]
