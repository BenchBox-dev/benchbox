from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(autouse=True)
def _isolated_ambient_environment(monkeypatch):
    for name in (*meta.SESSION_ENV_VARS, meta.HARNESS_PID_ENV):
        monkeypatch.delenv(name, raising=False)


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


def _record(worktree: Path):
    return meta._read_owner(worktree)


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
    assert _record(worktree)["session"] == "session-a"


def test_dead_owner_is_replaced(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)
    live_process.kill()
    live_process.wait()

    _as_session(monkeypatch, "session-b", 1)
    assert meta.claim_worktree(worktree) == "taken-over"
    assert _record(worktree)["session"] == "session-b"


def test_recycled_pid_with_different_start_time_is_stale(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)
    tampered = {**_record(worktree), "start": "Thu Jan  1 00:00:00 1970"}
    meta._write_owner(worktree, tampered)

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
    assert _record(worktree)["pid"] == live_process.pid

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
            _git(worktree, "config", "--worktree", "--unset-all", "benchbox.worktree.owner")
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
        meta._write_owner(
            worktree,
            {
                "session": "session-b",
                "pid": live_process.pid,
                "start": meta.process_start(live_process.pid),
                "claimed_at": "x",
            },
        )

    thread.join(timeout=30)
    assert len(outcome) == 1 and isinstance(outcome[0], meta.MetadataError)
    assert _record(worktree)["session"] == "session-b"


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
    assert _record(worktree) is None


def test_claim_is_reported_unsupported_where_flock_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PI_SESSION_ID", "session-a")
    monkeypatch.setattr(meta, "fcntl", None)

    assert meta.claim_worktree(tmp_path) == "unsupported"
    assert meta.release_session_claim(tmp_path) == "unsupported"
    assert "not enforced" in capsys.readouterr().err


def _fake_ps(monkeypatch, processes: dict[int, tuple[str, int, str | None]], caller_parent: int) -> None:
    def ps_field(pid: int, field: str):
        entry = processes.get(pid)
        if entry is None:
            return None
        comm, ppid, start = entry
        return {"comm": comm, "ppid": str(ppid), "lstart": start}[field]

    monkeypatch.setattr(meta, "_ps_field", ps_field)
    monkeypatch.setattr(meta, "process_start", lambda pid: ps_field(pid, "lstart"))
    monkeypatch.setattr(meta.os, "getppid", lambda: caller_parent)


START = "Wed Oct 28 10:00:00 2026"


def test_harness_walk_picks_first_non_wrapper_ancestor(monkeypatch):
    _fake_ps(
        monkeypatch,
        {
            400: ("/bin/sh", 300, START),
            300: ("/usr/bin/make", 200, START),
            200: ("/usr/bin/python3", 150, START),
            150: ("/usr/local/bin/omp", 1, START),
            1: ("/sbin/launchd", 0, START),
        },
        caller_parent=400,
    )
    assert meta.harness_pid() == 150


def test_harness_walk_refuses_an_all_wrapper_chain(monkeypatch):
    _fake_ps(
        monkeypatch,
        {400: ("sh", 300, START), 300: ("bash", 200, START), 200: ("make", 1, START)},
        caller_parent=400,
    )
    assert meta.harness_pid() is None


def test_harness_walk_refuses_a_chain_that_reaches_pid_1(monkeypatch):
    _fake_ps(monkeypatch, {1: ("init", 0, START)}, caller_parent=1)
    assert meta.harness_pid() is None


def test_harness_walk_refuses_when_ps_fails_mid_chain(monkeypatch):
    _fake_ps(monkeypatch, {400: ("sh", 300, START)}, caller_parent=400)
    assert meta.harness_pid() is None

    _fake_ps(monkeypatch, {400: ("sh", 300, START), 300: ("bash", 0, START)}, caller_parent=400)
    assert meta.harness_pid() is None


def test_harness_walk_requires_a_readable_start_time(monkeypatch):
    _fake_ps(monkeypatch, {400: ("sh", 300, START), 300: ("omp", 1, None)}, caller_parent=400)
    assert meta.harness_pid() is None


def test_malformed_record_is_ignored_with_a_warning_and_replaced(worktree, live_process, monkeypatch, capsys):
    _as_session(monkeypatch, "session-a", live_process.pid)
    _git(worktree, "config", "extensions.worktreeConfig", "true")
    meta._set_worktree_config("owner", '{"session": "ghost"}', worktree)

    assert meta.claim_worktree(worktree) == "claimed"
    assert "malformed" in capsys.readouterr().err
    assert _record(worktree)["session"] == "session-a"


def test_claim_is_written_as_a_single_record(worktree, live_process, monkeypatch):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)

    listing = subprocess.run(
        ["git", "config", "--worktree", "--get-regexp", r"benchbox\.worktree\.owner"],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.splitlines()
    assert len(listing) == 1
    record = _record(worktree)
    assert set(record) == {"session", "pid", "start", "claimed_at"}


def _main(monkeypatch, worktree: Path, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["worktree_lifecycle_metadata.py", *argv, "--worktree-path", str(worktree)])
    return meta.main()


def test_cli_claim_and_session_release_paths(worktree, live_process, monkeypatch, capsys):
    _as_session(monkeypatch, "session-a", live_process.pid)
    assert _main(monkeypatch, worktree, "claim") == 0

    _as_session(monkeypatch, "session-b", 1)
    assert _main(monkeypatch, worktree, "claim") == 1
    assert "session-a" in capsys.readouterr().err
    assert _main(monkeypatch, worktree, "session-release") == 1
    assert _record(worktree)["session"] == "session-a"

    assert _main(monkeypatch, worktree, "claim", "--takeover") == 0
    assert _record(worktree)["session"] == "session-b"

    _as_session(monkeypatch, "session-a", live_process.pid)
    assert _main(monkeypatch, worktree, "session-release", "--force") == 0
    assert "released" in capsys.readouterr().out
    assert _record(worktree) is None


def _fake_proc(root: Path, pid: int, starttime: int, comm: str = "omp") -> None:
    (root / str(pid)).mkdir(parents=True)
    (root / str(pid) / "stat").write_text(
        f"{pid} ({comm} (x)) S 1 1 1 0 -1 4194560 100 0 0 0 1 1 0 0 20 0 1 0 {starttime} 1000 100 18446744073709551615\n",
        encoding="utf-8",
    )
    boot = root / "sys" / "kernel" / "random"
    boot.mkdir(parents=True, exist_ok=True)
    (boot / "boot_id").write_text("boot-a\n", encoding="utf-8")


def test_proc_start_uses_boot_id_and_start_ticks_not_wall_clock(tmp_path):
    _fake_proc(tmp_path, 42, 987654)
    assert meta._proc_start(tmp_path, 42) == "boot-a:987654"
    assert meta._proc_start(tmp_path, 43) is None


def test_declared_harness_pid_is_used_only_when_it_is_live(monkeypatch, live_process):
    monkeypatch.setenv(meta.HARNESS_PID_ENV, str(live_process.pid))
    assert meta.harness_pid() == live_process.pid

    monkeypatch.setenv(meta.HARNESS_PID_ENV, "not-a-pid")
    assert meta.harness_pid() is None

    monkeypatch.setenv(meta.HARNESS_PID_ENV, "1")
    assert meta.harness_pid() is None

    live_process.kill()
    live_process.wait()
    monkeypatch.setenv(meta.HARNESS_PID_ENV, str(live_process.pid))
    assert meta.harness_pid() is None


def test_release_fails_loudly_when_the_record_cannot_be_cleared(worktree, live_process, monkeypatch, capsys):
    _as_session(monkeypatch, "session-a", live_process.pid)
    meta.claim_worktree(worktree)
    real_run_git = meta._run_git

    def failing_unset(args, cwd):
        if "--unset-all" in args:
            return subprocess.CompletedProcess(args, 5, "", "could not lock config file")
        return real_run_git(args, cwd)

    monkeypatch.setattr(meta, "_run_git", failing_unset)

    assert _main(monkeypatch, worktree, "session-release") == 1
    captured = capsys.readouterr()
    assert "released" not in captured.out
    assert "could not lock" in captured.err
    assert _record(worktree)["session"] == "session-a"
