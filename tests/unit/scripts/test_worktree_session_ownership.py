from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
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
    if sys.platform == "win32":
        pytest.skip("session ownership is explicitly not enforced on Windows")
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


def test_renewal_from_short_lived_child_keeps_live_anchor(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)

    _as_session(monkeypatch, "session-a", 1)
    assert meta.claim_worktree(worktree) == "renewed"
    assert meta._get_worktree_config("owner-pid", worktree) == str(live_process.pid)

    _as_session(monkeypatch, "session-b", 1)
    with pytest.raises(meta.MetadataError):
        meta.claim_worktree(worktree)


def test_liveness_is_independent_of_caller_timezone(worktree, live_process, monkeypatch):
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)

    monkeypatch.setenv("TZ", "America/Los_Angeles")
    _as_session(monkeypatch, "session-b", 1)
    with pytest.raises(meta.MetadataError):
        meta.claim_worktree(worktree)


def test_concurrent_claimants_exactly_one_wins(worktree, live_process, monkeypatch):
    local = threading.local()
    monkeypatch.setattr(meta, "current_session_id", lambda: local.session)
    monkeypatch.setattr(meta, "harness_pid", lambda: live_process.pid)
    racers = 4

    for round_index in range(8):
        if round_index:
            _git(worktree, "config", "--worktree", "--unset-all", "benchbox.worktree.owner-session")
        barrier = threading.Barrier(racers)
        outcomes: list[str] = []

        def race(n: int) -> None:
            local.session = f"racer-{n}"
            barrier.wait()
            try:
                outcomes.append(meta.claim_worktree(worktree))
            except meta.MetadataError:
                outcomes.append("refused")

        threads = [threading.Thread(target=race, args=(n,)) for n in range(racers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert sorted(outcomes) == ["claimed"] + ["refused"] * (racers - 1)


def test_release_waits_for_an_in_flight_claim_and_rechecks_the_owner(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)
    outcome: list[object] = []

    def release() -> None:
        try:
            outcome.append(meta.release_session_claim(worktree))
        except meta.MetadataError as exc:
            outcome.append(exc)

    with meta._worktree_lock(worktree):
        thread = threading.Thread(target=release)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive()
        meta._set_worktree_config("owner-session", "session-b", worktree)

    thread.join(timeout=30)
    assert len(outcome) == 1 and isinstance(outcome[0], meta.MetadataError)
    assert meta._get_worktree_config("owner-session", worktree) == "session-b"


def test_live_owner_is_not_replaced_when_process_start_cannot_be_read(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)

    _as_session(monkeypatch, "session-b", 1)
    real_process_start = meta.process_start
    monkeypatch.setattr(meta, "process_start", lambda pid: None if pid == live_process.pid else real_process_start(pid))
    with pytest.raises(meta.MetadataError):
        meta.claim_worktree(worktree)

    live_process.kill()
    live_process.wait()
    assert meta.claim_worktree(worktree) == "taken-over"


def test_claim_without_a_long_lived_harness_warns_and_records_nothing(worktree, monkeypatch, capsys):
    for name in meta.SESSION_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PI_SESSION_ID", "session-a")
    monkeypatch.setattr(meta, "_ps_field", lambda pid, field: None)

    assert meta.claim_worktree(worktree) == "unverifiable"
    assert "NOT recorded" in capsys.readouterr().err
    assert meta._get_worktree_config("owner-session", worktree) is None


def test_claim_is_reported_unsupported_where_flock_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PI_SESSION_ID", "session-a")
    monkeypatch.setattr(meta, "fcntl", None)

    assert meta.claim_worktree(tmp_path) == "unsupported"
    assert meta.release_session_claim(tmp_path) == "unsupported"
    assert "not enforced" in capsys.readouterr().err
