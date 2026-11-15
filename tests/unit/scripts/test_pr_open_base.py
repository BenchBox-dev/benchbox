from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
LINE = "release/0.5.x"

requires_make = pytest.mark.skipif(os.name == "nt" or shutil.which("make") is None, reason="needs POSIX make and sh")


def _git(repo: Path, *args: str) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, encoding="utf-8", env=env)


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "Makefile", root / "Makefile")
    shutil.copytree(ROOT / "make", root / "make")
    for helper in ("make_timing_shell.sh", "pr_base.py"):
        shutil.copy(ROOT / "scripts" / helper, root / "scripts" / helper)
    (root / "scripts" / "agent_write_preflight.sh").write_text("exit 0\n", encoding="utf-8")
    (root / "_project" / "decisions").mkdir(parents=True)
    for args in (
        ["init", "-q", "-b", "fix/thing"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["remote", "add", "origin", "https://github.com/BenchBox-dev/BenchBox.git"],
    ):
        _git(root, *args)
    (root / "file.txt").write_text("x\n", encoding="utf-8")
    _git(root, "add", "file.txt")
    _git(root, "commit", "-q", "-m", "fix: thing")
    return root


def _pr_open(tmp_path: Path, work: Path, *assignments: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "git.log"
    real_git = shutil.which("git")
    assert real_git is not None
    git_stub = bin_dir / "git"
    git_stub.write_text(
        f'#!/bin/sh\nif [ "$1" = fetch ]; then echo "$*" >> {log}; exit 0; fi\nexec {real_git} "$@"\n', encoding="utf-8"
    )
    git_stub.chmod(0o755)
    uv_stub = bin_dir / "uv"
    uv_stub.write_text('#!/bin/sh\nshift; [ "$1" = -- ] && shift; shift\nexec python3 "$@"\n', encoding="utf-8")
    uv_stub.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"BASE", "EVIDENCE", "BENCHBOX_TEST_SESSION_OWNER"} and not key.startswith(("GIT_", "PR_OPEN_"))
    }
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    done = subprocess.run(
        ["make", "-s", "pr-open", *assignments],
        cwd=work,
        env=env,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=120,
    )
    return done, log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@requires_make
@pytest.mark.parametrize("base", ["feature/x", "main", "release", "release/0.5", "origin/develop", "release/0.5.x/x"])
def test_any_other_base_is_refused_before_anything_is_fetched(tmp_path: Path, work: Path, base: str) -> None:
    done, fetches = _pr_open(tmp_path, work, f"BASE={base}")

    assert done.returncode == 2, done.stdout + done.stderr
    assert "BASE must be develop or a release/X.Y.x maintenance line" in done.stderr
    assert fetches == []


@requires_make
def test_the_evidence_transaction_is_refused_for_a_line(tmp_path: Path, work: Path) -> None:
    done, fetches = _pr_open(tmp_path, work, f"BASE={LINE}", "EVIDENCE=evidence.json")

    assert done.returncode == 2, done.stdout + done.stderr
    assert f"make pr-arm BASE={LINE}" in done.stderr
    assert fetches == []


@requires_make
def test_a_line_base_is_fetched_and_the_branch_point_is_checked_against_it(tmp_path: Path, work: Path) -> None:
    done, fetches = _pr_open(tmp_path, work, f"BASE={LINE}")

    assert fetches == [f"fetch origin {LINE} --quiet"]
    assert done.returncode == 2, done.stdout + done.stderr
    assert f"HEAD conflicts with origin/{LINE}" in done.stderr
    assert "origin/develop" not in done.stderr


@requires_make
@pytest.mark.parametrize("assignments", [[], ["BASE=develop"], ["BASE="]])
def test_develop_stays_the_default_base(tmp_path: Path, work: Path, assignments: list[str]) -> None:
    done, fetches = _pr_open(tmp_path, work, *assignments)

    assert fetches == ["fetch origin develop --quiet"]
    assert done.returncode == 2, done.stdout + done.stderr
    assert "HEAD conflicts with origin/develop" in done.stderr


def test_the_recipe_takes_every_base_dependent_value_from_the_validated_base() -> None:
    recipe = (ROOT / "make" / "pr-landing.mk").read_text(encoding="utf-8")
    recipe = recipe[recipe.index("\npr-open:\n") : recipe.index("\n.PHONY: pr-arm-auto-merge")]
    body = recipe[recipe.index("BASE_BRANCH=") :]
    assert 'pr_base.py "$$PR_OPEN_BASE"' in recipe
    for command in ("local_review.py", "pr_open_rest.py", "preflight_receipt.py verify", "changelog_fragments.py"):
        assert command in body
    assert "origin/develop" not in body
    assert '--trusted-ref "$$BASE_REMOTE"' in body
    assert '--release-base-sha "$$(git rev-parse "$$BASE_REMOTE")"' in body
    assert 'STACK_BASE="$$BASE_REMOTE"' in body
    assert 'BASE="$$BASE_BRANCH"' in body
    assert body.count('--base "$$BASE_BRANCH"') == 2
