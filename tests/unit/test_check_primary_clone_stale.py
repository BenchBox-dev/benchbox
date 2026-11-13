from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_primary_clone_stale as stale

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LOCK = 'version = 1\n\n[[package]]\nname = "tractable"\nversion = "{version}"\nsource = {{ path = "x.whl" }}\n'


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def _write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: Path, relative: str, text: str) -> None:
    _write(repo, relative, text)
    _git(repo, "add", relative)
    _git(repo, "commit", "-q", "-m", f"change {relative}")


def _primary(tmp_path: Path, installed: str = "1.1.1") -> Path:
    repo = tmp_path / "primary"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "develop")
    _commit(repo, "README.md", "one\n")
    _commit(repo, "_project/scripts/uv.lock", LOCK.format(version="1.1.1"))
    dist_info = repo / "_project/scripts/.venv/lib/python3.12/site-packages" / f"tractable-{installed}.dist-info"
    dist_info.mkdir(parents=True)
    return repo


def _advance_remote(repo: Path, relative: str, text: str) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    _commit(repo, relative, text)
    _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _git(repo, "reset", "-q", "--hard", head)


def test_behind_on_watched_path_warns(tmp_path: Path) -> None:
    repo = _primary(tmp_path)
    _advance_remote(repo, ".claude/settings.json", "{}\n")

    warnings = stale.stale_warnings(repo)

    assert len(warnings) == 1
    assert ".claude/settings.json" in warnings[0]
    assert "git -C" in stale.message(repo, warnings)
    assert "pull --ff-only origin develop" in stale.message(repo, warnings)
    assert "uv sync --project" in stale.message(repo, warnings)


def test_behind_only_on_unwatched_paths_is_silent(tmp_path: Path) -> None:
    repo = _primary(tmp_path)
    _advance_remote(repo, "benchbox/core.py", "x = 1\n")

    assert stale.stale_warnings(repo) == []


def test_tracker_version_mismatch_warns(tmp_path: Path) -> None:
    repo = _primary(tmp_path, installed="1.1.0")

    warnings = stale.stale_warnings(repo, remote_ref="HEAD")

    assert len(warnings) == 1
    assert "1.1.0" in warnings[0]
    assert "1.1.1" in warnings[0]


def test_matching_version_and_current_clone_is_silent(tmp_path: Path) -> None:
    repo = _primary(tmp_path)
    _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")

    assert stale.stale_warnings(repo) == []


def test_missing_remote_ref_is_silent_and_exit_status_is_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _primary(tmp_path)
    _advance_remote(repo, ".claude/settings.json", "{}\n")
    _git(repo, "update-ref", "-d", "refs/remotes/origin/develop")

    assert stale.main(["check_primary_clone_stale.py", str(repo)]) == 0
    assert capsys.readouterr().err == ""
