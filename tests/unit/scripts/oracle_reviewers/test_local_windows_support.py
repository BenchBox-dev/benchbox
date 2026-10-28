from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import local

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[4]
REVIEW_TEST = "tests/unit/scripts/oracle_reviewers/test_local_review.py"
SMART_QUOTE = "\u201d"


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


def _repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "commit.gpgsign", "false")
    for name, text in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "fixture", "--quiet")
    return path


def _windows_locale_run(real_run: Any) -> Any:
    def run(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("text") and "encoding" not in kwargs:
            kwargs["encoding"] = "cp1252"
        return real_run(*args, **kwargs)

    return run


def test_local_imports_without_fcntl() -> None:
    probe = "import sys; sys.modules['fcntl'] = None; import _project.scripts.oracle_reviewers.local"
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env={**os.environ, "MUTANT_UNDER_TEST": ""},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


def test_explicit_collection_skips_without_fcntl() -> None:
    probe = (
        "import sys; sys.modules['fcntl'] = None; import pytest; "
        "raise SystemExit(pytest.main(['"
        + REVIEW_TEST
        + "', '-q', '--no-header', '-p', 'no:cacheprovider', '-o', 'addopts=']))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env={**os.environ, "MUTANT_UNDER_TEST": ""},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode in (0, 5), proc.stdout + proc.stderr
    assert "skipped" in proc.stdout.lower()
    assert "error" not in proc.stdout.lower()


def test_git_show_decodes_smart_quotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "repo", {"note.md": f"quoted {SMART_QUOTE}text{SMART_QUOTE}\n"})
    monkeypatch.setattr(local, "subprocess", SimpleNamespace(run=_windows_locale_run(subprocess.run)))
    assert SMART_QUOTE in local._git(repo, "show", "HEAD:note.md")


def test_repository_remote_decodes_smart_quotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "repo", {"note.md": "plain\n"})
    _git(repo, "remote", "add", "origin", f"https://github.com/acme/r{SMART_QUOTE}epo")
    monkeypatch.setattr(local, "subprocess", SimpleNamespace(run=_windows_locale_run(subprocess.run)))
    assert local._repository(repo) == local.DEFAULT_REPOSITORY
