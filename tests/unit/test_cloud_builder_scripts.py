from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

from tests.utilities.paths import REPO_ROOT
from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


BOOTSTRAP = REPO_ROOT / "scripts" / "cloud_bootstrap.sh"
VERIFY = REPO_ROOT / "scripts" / "cloud_verify.sh"

CLOUD_ENV = {
    "BENCHBOX_EPHEMERAL_CLONE": "1",
    "BENCHBOX_GIT_NAME": "Joe Harris",
    "BENCHBOX_GIT_EMAIL": "joeharris76@gmail.com",
}


def _run(script: Path, *args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    skip_without_posix_shell()
    command = " ".join(shlex.quote(part) for part in ["sh", script.as_posix(), *args])
    return run_posix_shell(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", check=False)


def _clone(tmp_path: Path) -> Path:
    repo = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Seed", "-c", "user.email=seed@example.com"]
        + ["commit", "-q", "--allow-empty", "-m", "seed"],
        check=True,
    )
    return repo


def _env(**overrides: str | None) -> dict[str, str]:
    env = {**os.environ, **CLOUD_ENV}
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _local_identity(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get-regexp", r"^user\."],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def test_bootstrap_refuses_without_ephemeral_declaration(tmp_path: Path) -> None:
    repo = _clone(tmp_path)

    result = _run(BOOTSTRAP, cwd=repo, env=_env(BENCHBOX_EPHEMERAL_CLONE=None))

    assert result.returncode == 1
    assert "BENCHBOX_EPHEMERAL_CLONE=1 is not set" in result.stderr
    assert _local_identity(repo) == ""


def test_bootstrap_refuses_a_clone_with_linked_worktrees(tmp_path: Path) -> None:
    repo = _clone(tmp_path)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(tmp_path / "linked")], check=True)

    result = _run(BOOTSTRAP, cwd=repo, env=_env())

    assert result.returncode == 1
    assert "2 registered worktrees" in result.stderr
    assert _local_identity(repo) == ""


@pytest.mark.parametrize("missing", ["BENCHBOX_GIT_NAME", "BENCHBOX_GIT_EMAIL"])
def test_bootstrap_refuses_without_a_human_author(tmp_path: Path, missing: str) -> None:
    repo = _clone(tmp_path)

    result = _run(BOOTSTRAP, cwd=repo, env=_env(**{missing: None}))

    assert result.returncode == 1
    assert "BENCHBOX_GIT_NAME and BENCHBOX_GIT_EMAIL" in result.stderr
    assert _local_identity(repo) == ""


def test_verify_rejects_an_unknown_level(tmp_path: Path) -> None:
    repo = _clone(tmp_path)

    result = _run(VERIFY, "everything", cwd=repo, env=_env())

    assert result.returncode == 2
    assert "LEVEL must be static or tests" in result.stderr
