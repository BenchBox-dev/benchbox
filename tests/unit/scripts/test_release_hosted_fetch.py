from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import release_artifact_consumer as consumer

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout.strip()


def test_pruning_hosted_fetch_keeps_an_existing_remote_develop_ref(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(origin)], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "-b", "develop", str(clone)], check=True)
    _git(clone, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "c")
    _git(clone, "push", "-q", str(origin), "develop")
    tip = _git(clone, "rev-parse", "HEAD")
    _git(clone, "update-ref", "refs/remotes/origin/develop", tip)
    _git(clone, "fetch", "-q", "--no-tags", "--prune", str(origin), *consumer.hosted_refspecs(None))
    assert _git(clone, "rev-parse", "refs/remotes/origin/develop") == tip
