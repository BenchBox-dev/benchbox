from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "worktree_lifecycle_metadata", REPO_ROOT / "scripts" / "worktree_lifecycle_metadata.py"
)
meta = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = meta
spec.loader.exec_module(meta)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8")


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--allow-empty", "-qm", "init")
    return repo


@pytest.fixture
def live_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait()


def _as_session(monkeypatch: pytest.MonkeyPatch, session: str, pid: int) -> None:
    for name in meta.SESSION_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PI_SESSION_ID", session)
    monkeypatch.setattr(meta, "harness_pid", lambda: pid)


def test_unowned_worktree_is_claimed_and_owner_renews(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    assert meta.claim_worktree(worktree) == "claimed"
    assert meta.claim_worktree(worktree) == "renewed"


def test_second_live_session_is_refused(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)

    _as_session(monkeypatch, "session-b", 1)
    with pytest.raises(meta.MetadataError, match="session-a"):
        meta.claim_worktree(worktree)
    assert meta._get_worktree_config("owner-session", worktree) == "session-a"


def test_dead_owner_is_replaced(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)
    live_process.kill()
    live_process.wait()

    _as_session(monkeypatch, "session-b", 1)
    assert meta.claim_worktree(worktree) == "taken-over"
    assert meta._get_worktree_config("owner-session", worktree) == "session-b"


def test_recycled_pid_with_different_start_time_is_stale(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)
    meta._set_worktree_config("owner-pid-start", "Thu Jan  1 00:00:00 1970", worktree)

    _as_session(monkeypatch, "session-b", 1)
    assert meta.claim_worktree(worktree) == "taken-over"


def test_owner_can_release_and_others_can_then_claim(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)
    assert meta.release_session_claim(worktree) == "released"

    _as_session(monkeypatch, "session-b", 1)
    assert meta.claim_worktree(worktree) == "claimed"


def test_non_owner_cannot_release_without_force(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)

    _as_session(monkeypatch, "session-b", 1)
    with pytest.raises(meta.MetadataError):
        meta.release_session_claim(worktree)
    assert meta.release_session_claim(worktree, force=True) == "released"


def test_explicit_takeover_replaces_live_owner(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)

    _as_session(monkeypatch, "session-b", 1)
    assert meta.claim_worktree(worktree, takeover=True) == "taken-over"


def test_no_session_identity_skips_guard(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)

    for name in meta.SESSION_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert meta.claim_worktree(worktree) == "no-session"
