from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psutil
import pytest

from benchbox.utils import heavy_run_events as ev, host_lock as hl
from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.medium]

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process semantics")
SECRET = "super-secret-token-value"


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def by_event(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["event"]: row for row in rows}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def lock_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "lock"
    monkeypatch.setenv(hl.LOCK_DIR_ENV, str(directory))
    monkeypatch.setenv(hl.WAIT_ENV, "0")
    for name in (hl.OWNER_ENV, hl.RUN_ID_ENV, hl.ROOT_RUN_ID_ENV, hl.RUNS_FILE_ENV, hl.ADMIT_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(hl.WATCHDOG_MODE_ENV, "off")
    return directory


@pytest.fixture
def runs_file(lock_dir: Path) -> Path:
    return lock_dir / hl.RUNS_FILE_NAME


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(hl, "_now", fake)
    return fake


def noop(*extra: str) -> list[str]:
    return [sys.executable, "-c", "pass", *extra]


def test_event_path_prefers_explicit_file_then_lock_dir_then_home(tmp_path: Path) -> None:
    explicit = tmp_path / "x.jsonl"
    assert ev.events_path({ev.FILE_ENV: str(explicit), ev.LOCK_DIR_ENV: str(tmp_path / "d")}) == explicit
    assert ev.events_path({ev.LOCK_DIR_ENV: str(tmp_path / "d")}) == tmp_path / "d" / ev.DEFAULT_FILE_NAME
    assert ev.events_path({}) == hl.real_home() / ".benchbox" / ev.DEFAULT_FILE_NAME


def test_default_path_ignores_a_rewritten_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert ev.events_path({}) == hl.real_home() / ".benchbox" / ev.DEFAULT_FILE_NAME


def test_record_has_schema_identity_and_bounded_fields(tmp_path: Path) -> None:
    target = tmp_path / "out" / "runs.jsonl"
    ev.record_event(
        "probe",
        duration_seconds=1.23456789,
        outcome="ok",
        exit_code=3,
        details={"note": "x" * 5000, "count": 2},
        environ={ev.FILE_ENV: str(target)},
        run_id="abc",
    )
    (row,) = read_events(target)
    assert row["schema"] == ev.SCHEMA
    assert row["run_id"] == "abc" and row["pid"] == os.getpid() and row["cwd"] == os.getcwd()
    assert row["duration_seconds"] == 1.234568 and row["exit_code"] == 3 and row["outcome"] == "ok"
    assert len(row["details"]["note"]) == ev.MAX_TEXT_CHARS and row["details"]["count"] == 2
    assert row["ts"].endswith("Z")
    assert (target.stat().st_mode & 0o777) == 0o600


def test_oversized_record_is_reduced_to_a_marker_not_dropped(tmp_path: Path) -> None:
    target = tmp_path / "runs.jsonl"
    wide = {f"k{index}": "y" * ev.MAX_TEXT_CHARS for index in range(ev.MAX_DETAIL_ITEMS)}
    ev.record_event("wide", details=wide, environ={ev.FILE_ENV: str(target)})
    (row,) = read_events(target)
    assert len(target.read_bytes()) <= ev.MAX_RECORD_BYTES
    assert row["details"] == {"truncated": True}


def test_run_id_falls_back_to_the_active_run_in_the_environment(tmp_path: Path) -> None:
    target = tmp_path / "runs.jsonl"
    ev.record_event("vm_cleanup_started", environ={ev.FILE_ENV: str(target), ev.RUN_ID_ENV: "active1"})
    assert read_events(target)[0]["run_id"] == "active1"


def test_a_failed_write_warns_and_never_raises(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ev.record_event("probe", environ={ev.FILE_ENV: str(tmp_path)})
    assert "event log write failed" in capsys.readouterr().err


def test_concurrent_threads_and_processes_append_whole_lines(tmp_path: Path) -> None:
    target = tmp_path / "runs.jsonl"
    environ = {ev.FILE_ENV: str(target)}
    per_producer = 40

    def thread_body(label: str) -> None:
        for index in range(per_producer):
            ev.record_event("t", details={"label": label, "i": index, "pad": "p" * 300}, environ=environ)

    script = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('hre', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "for i in range(int(sys.argv[4])):\n"
        "    details = {'label': sys.argv[2], 'i': i, 'pad': 'p' * 300}\n"
        "    m.record_event('p', details=details, environ={m.FILE_ENV: sys.argv[3]})\n"
    )
    module_file = str(Path(ev.__file__))
    procs = [
        subprocess.Popen([sys.executable, "-I", "-c", script, module_file, f"proc{n}", str(target), str(per_producer)])
        for n in range(3)
    ]
    threads = [threading.Thread(target=thread_body, args=(f"thr{n}",)) for n in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [proc.wait(timeout=60) for proc in procs] == [0, 0, 0]
    rows = read_events(target)
    assert len(rows) == 6 * per_producer
    seen = {(row["details"]["label"], row["details"]["i"]) for row in rows}
    assert len(seen) == 6 * per_producer


def test_successful_run_separates_queue_execution_and_cleanup(lock_dir: Path, runs_file: Path) -> None:
    code = hl.run_command(noop(f"--token={SECRET}"), phase="unit", gate="g")
    assert code == 0
    rows = read_events(runs_file)
    assert [row["event"] for row in rows] == [
        "lock_requested",
        "lock_acquired",
        "execution_started",
        "cleanup_finished",
        "run_finished",
    ]
    assert len({row["run_id"] for row in rows}) == 1
    final = rows[-1]
    assert final["outcome"] == "success" and final["exit_code"] == 0
    details = final["details"]
    assert details["execution_seconds"] > 0
    assert details["admission_seconds"] == 0 and details["wait_seconds"] >= 0
    assert final["duration_seconds"] >= details["execution_seconds"]
    assert rows[0]["details"]["program"] == Path(sys.executable).name
    assert SECRET not in runs_file.read_text(encoding="utf-8")


def test_command_failure_is_not_reported_as_success_after_release(lock_dir: Path, runs_file: Path) -> None:
    code = hl.run_command([sys.executable, "-c", "raise SystemExit(7)"])
    assert code == 7
    rows = by_event(read_events(runs_file))
    assert rows["cleanup_finished"]["outcome"] == "released"
    assert rows["run_finished"]["outcome"] == "command_failed"
    assert rows["run_finished"]["exit_code"] == 7


def test_the_child_environment_carries_the_run_id_and_a_root_run_is_its_own_root(
    lock_dir: Path, runs_file: Path, tmp_path: Path
) -> None:
    probe = tmp_path / "child.txt"
    program = f"import os; open({str(probe)!r}, 'w').write(os.environ[{hl.RUN_ID_ENV!r}])"
    assert hl.run_command([sys.executable, "-c", program]) == 0
    outer = read_events(runs_file)[0]
    assert probe.read_text(encoding="utf-8") == outer["run_id"]
    assert outer["details"]["root_run_id"] == outer["run_id"] and "parent_run_id" not in outer["details"]


def test_an_inherited_child_names_its_parent_and_root(
    lock_dir: Path, runs_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(hl.RUN_ID_ENV, "parentrun")
    monkeypatch.setenv(hl.ROOT_RUN_ID_ENV, "rootrun")
    assert hl.run_command(noop()) == 0
    rows = read_events(runs_file)
    assert rows[0]["run_id"] not in ("parentrun", "rootrun")
    assert rows[0]["details"]["parent_run_id"] == "parentrun"
    assert rows[-1]["details"]["root_run_id"] == "rootrun"


def test_a_malformed_inherited_run_id_is_ignored(
    lock_dir: Path, runs_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(hl.RUN_ID_ENV, "bad id\nwith newline")
    assert hl.run_command(noop()) == 0
    first = read_events(runs_file)[0]
    assert "parent_run_id" not in first["details"]


def test_queue_timeout_records_the_injected_wait_and_a_single_terminal(
    lock_dir: Path, runs_file: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def blocked(path: Path, timeout: float) -> int:
        clock.now += 42.0
        raise hl.LockUnavailable("held elsewhere")

    monkeypatch.setattr(hl, "wait_for_lock", blocked)
    assert hl.run_command(noop()) == hl.EXIT_REFUSED
    rows = read_events(runs_file)
    assert [row["event"] for row in rows] == ["lock_requested", "run_finished"]
    assert rows[1]["outcome"] == "queue_timeout"
    assert rows[1]["details"]["wait_seconds"] == 42.0
    assert rows[1]["duration_seconds"] == 42.0


def test_admission_refusal_records_admission_duration_and_cleanup(
    lock_dir: Path, runs_file: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(**_: object) -> str:
        clock.now += 3.0
        raise hl.AdmissionRefused("pressure too high")

    monkeypatch.setattr(hl, "check_admission", refuse)
    marker = tmp_path / "ran"
    code = hl.run_command([sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"], admit=True)
    assert code == hl.EXIT_ADMISSION and not marker.exists()
    rows = read_events(runs_file)
    by = by_event(rows)
    assert "execution_started" not in by
    assert by["admission_finished"]["duration_seconds"] == 3.0
    assert by["admission_finished"]["outcome"] == "admission_refused"
    assert by["run_finished"]["outcome"] == "admission_refused"
    assert by["run_finished"]["details"]["admission_seconds"] == 3.0
    assert by["run_finished"]["details"]["execution_seconds"] == 0
    assert [row["event"] for row in rows].count("run_finished") == 1
    assert not hl.lock_is_held(lock_dir / hl.LOCK_NAME)


def test_ownership_error_is_recorded_as_such(lock_dir: Path, runs_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def scrubbed(path: Path, environ: object) -> bool:
        raise hl.LockOwnershipError("scrubbed")

    monkeypatch.setattr(hl, "_inherits_lock", scrubbed)
    assert hl.run_command(noop()) == hl.EXIT_OWNERSHIP
    assert read_events(runs_file)[-1]["outcome"] == "ownership_error"


def test_cancelled_acquire_is_recorded(lock_dir: Path, runs_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(path: Path, timeout: float) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(hl, "wait_for_lock", interrupted)
    assert hl.run_command(noop()) == 128 + signal.SIGINT
    assert read_events(runs_file)[-1]["outcome"] == "cancelled"


def test_watchdog_breach_is_reported_as_watchdog_kill_not_success(
    lock_dir: Path, runs_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Breached:
        breached = True

        def stop(self) -> None:
            pass

    monkeypatch.setattr(hl, "start_guard", lambda handle, environ=None: Breached())
    assert hl.run_command(noop(), guard=True) == hl.EXIT_WATCHDOG
    rows = by_event(read_events(runs_file))
    assert rows["guard_started"]["outcome"] == "started"
    final = rows["run_finished"]
    assert final["outcome"] == "watchdog_kill" and final["exit_code"] == hl.EXIT_WATCHDOG
    assert final["details"]["command_exit_code"] == 0


def test_nested_acquire_emits_one_run_and_one_terminal(lock_dir: Path, runs_file: Path) -> None:
    path = lock_dir / hl.LOCK_NAME
    outer = hl.acquire(path, wait_seconds=0, phase="outer")
    inner = hl.acquire(path, wait_seconds=0, phase="inner")
    inner.release()
    assert not runs_file.exists() or "run_finished" not in runs_file.read_text(encoding="utf-8")
    outer.release()
    names = [row["event"] for row in read_events(runs_file)]
    assert names == ["lock_requested", "lock_acquired", "execution_started", "cleanup_finished", "run_finished"]
    assert read_events(runs_file)[-1]["outcome"] == "released"


def test_run_command_nested_in_a_held_lock_adds_no_run_records(lock_dir: Path, runs_file: Path) -> None:
    path = lock_dir / hl.LOCK_NAME
    outer = hl.acquire(path, wait_seconds=0, phase="outer")
    try:
        assert hl.run_command(noop()) == 0
        assert [row["event"] for row in read_events(runs_file)] == [
            "lock_requested",
            "lock_acquired",
            "execution_started",
        ]
    finally:
        outer.release()
    assert [row["event"] for row in read_events(runs_file)].count("run_finished") == 1


def test_a_log_write_failure_cannot_change_the_command_status(
    lock_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(hl.RUNS_FILE_ENV, str(tmp_path))
    assert hl.run_command([sys.executable, "-c", "raise SystemExit(5)"]) == 5
    assert "event log write failed" in capsys.readouterr().err


def test_a_log_write_failure_cannot_change_an_admission_refusal(
    lock_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(hl.RUNS_FILE_ENV, str(tmp_path))

    def refuse(**_: object) -> str:
        raise hl.AdmissionRefused("no")

    monkeypatch.setattr(hl, "check_admission", refuse)
    assert hl.run_command(noop(), admit=True) == hl.EXIT_ADMISSION


def test_an_explicit_lock_file_outside_home_never_logs_to_the_real_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(hl.LOCK_DIR_ENV, raising=False)
    monkeypatch.delenv(hl.RUNS_FILE_ENV, raising=False)
    monkeypatch.delenv(hl.RUN_ID_ENV, raising=False)
    handle = hl.acquire(tmp_path / "somewhere" / "t.lock", wait_seconds=0, phase="p")
    handle.release()
    assert (tmp_path / "somewhere" / hl.RUNS_FILE_NAME).exists()


def test_host_lock_loaded_by_file_path_logs_without_importing_benchbox(tmp_path: Path) -> None:
    script = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('benchbox.utils.host_lock', sys.argv[1])\n"
        "hl = importlib.util.module_from_spec(spec); sys.modules[spec.name] = hl; spec.loader.exec_module(hl)\n"
        "h = hl.acquire(hl.lock_path(), wait_seconds=0, phase='iso'); h.release()\n"
        "print('benchbox' in sys.modules)\n"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", hl.OWNER_ENV) and not key.startswith(("BENCHBOX_HEAVY_RUN", "BENCHBOX_HEAVY_ROOT"))
    }
    env[hl.LOCK_DIR_ENV] = str(tmp_path / "lock")
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(Path(hl.__file__))],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("False")
    assert [row["event"] for row in read_events(tmp_path / "lock" / hl.RUNS_FILE_NAME)][-1] == "run_finished"


@posix_only
def test_a_sigkilled_supervisor_leaves_started_events_and_no_terminal(
    lock_dir: Path, runs_file: Path, tmp_path: Path
) -> None:
    child_pid_file = tmp_path / "child.pid"
    child_program = f"import os, time; open({str(child_pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)"
    script = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('benchbox.utils.host_lock', sys.argv[1])\n"
        "hl = importlib.util.module_from_spec(spec); sys.modules[spec.name] = hl; spec.loader.exec_module(hl)\n"
        "raise SystemExit(hl.run_command([sys.executable, '-c', sys.argv[2]]))\n"
    )
    supervisor = subprocess.Popen(
        [sys.executable, "-I", "-c", script, str(Path(hl.__file__)), child_program],
        cwd=tmp_path,
        env=dict(os.environ, **{hl.LOCK_DIR_ENV: str(lock_dir)}),
        stdin=subprocess.DEVNULL,
    )
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.05)
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        supervisor.kill()
        supervisor.wait(timeout=30)
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait(timeout=30)
        if child_pid is not None:
            try:
                psutil.Process(child_pid).kill()
            except psutil.NoSuchProcess:
                pass
    names = [row["event"] for row in read_events(runs_file)]
    assert names == ["lock_requested", "lock_acquired", "execution_started"]


def test_plain_acquire_records_the_held_execution_interval_without_faking_success(
    lock_dir: Path, runs_file: Path, clock: FakeClock
) -> None:
    handle = hl.acquire(lock_dir / hl.LOCK_NAME, wait_seconds=0, phase="session")
    clock.now += 90.0
    handle.release()
    rows = read_events(runs_file)
    assert [row["event"] for row in rows].count("execution_started") == 1
    final = rows[-1]
    assert final["outcome"] == "released"
    assert final["details"]["execution_seconds"] == 90.0 and final["details"]["held_seconds"] == 90.0


class ExplodingGuard:
    breached = False

    def stop(self) -> None:
        raise RuntimeError("teardown failed")


def test_a_guard_teardown_error_still_closes_the_lock_and_is_recorded(lock_dir: Path, runs_file: Path) -> None:
    path = lock_dir / hl.LOCK_NAME
    handle = hl.acquire(path, wait_seconds=0, phase="p")
    handle.guard = ExplodingGuard()
    with pytest.raises(RuntimeError, match="teardown failed"):
        handle.release()
    assert not hl.lock_is_held(path)
    by = by_event(read_events(runs_file))
    assert by["cleanup_finished"]["outcome"] == "error"
    assert by["cleanup_finished"]["details"]["error"] == "RuntimeError"
    assert by["run_finished"]["outcome"] == "error"


def test_run_command_still_writes_a_terminal_when_teardown_raises(
    lock_dir: Path, runs_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hl, "start_guard", lambda handle, environ=None: ExplodingGuard())
    with pytest.raises(RuntimeError, match="teardown failed"):
        hl.run_command(noop(), guard=True)
    assert not hl.lock_is_held(lock_dir / hl.LOCK_NAME)
    final = read_events(runs_file)[-1]
    assert final["event"] == "run_finished" and final["outcome"] == "error"
    assert final["details"]["command_exit_code"] == 0


def test_hostile_run_id_cwd_and_details_stay_within_the_record_bound(tmp_path: Path) -> None:
    target = tmp_path / "runs.jsonl"
    emoji = "\U0001f600" * 500
    ev.record_event(
        "e" * 500,
        outcome="o" * 500,
        details={f"{emoji}{n}": emoji for n in range(30)},
        environ={ev.FILE_ENV: str(target)},
        run_id=emoji + "\n" + "r" * 9000,
    )
    data = target.read_bytes()
    assert len(data) <= ev.MAX_RECORD_BYTES and data.count(b"\n") == 1
    row = json.loads(data)
    assert row["schema"] == ev.SCHEMA and row["run_id"].startswith(emoji[:5])
    assert len(row["run_id"]) <= ev.MAX_ID_CHARS


def test_a_unicode_working_directory_cannot_break_the_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "runs.jsonl"
    monkeypatch.setattr(ev, "_getcwd", lambda: "/" + "\U0001f600" * 4000)
    ev.record_event("probe", details={"k": "\U0001f600" * 200}, environ={ev.FILE_ENV: str(target)}, run_id="r" * 64)
    data = target.read_bytes()
    assert len(data) <= ev.MAX_RECORD_BYTES
    assert json.loads(data)["run_id"] == "r" * 64


def test_a_failed_event_module_load_warns_once_and_runs_unrecorded(
    lock_dir: Path, runs_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_load_sibling = hl._load_sibling

    def broken(stem: str) -> object:
        if stem == "heavy_run_events":
            raise ImportError("missing")
        return real_load_sibling(stem)

    monkeypatch.setattr(hl, "_load_sibling", broken)
    monkeypatch.setattr(hl, "_events_state", [])
    assert hl.run_command(noop()) == 0
    assert hl.run_command(noop()) == 0
    assert capsys.readouterr().err.count("heavy run event log disabled") == 1
    assert not runs_file.exists()


def test_api_acquire_environment_carries_run_ids_and_ledger_path_to_children(
    lock_dir: Path, runs_file: Path, tmp_path: Path
) -> None:
    handle = hl.acquire(lock_dir / hl.LOCK_NAME, wait_seconds=0, phase="session")
    try:
        env = hl.owner_environment(handle=handle)
        assert hl.owner_environment()[hl.OWNER_ENV] == env[hl.OWNER_ENV]
        assert hl.RUN_ID_ENV not in hl.owner_environment()
        program = (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('hre', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "m.record_event('vm_cleanup_started')\n"
        )
        env.pop(hl.LOCK_DIR_ENV)
        subprocess.run([sys.executable, "-I", "-c", program, str(Path(ev.__file__))], env=env, check=True, timeout=60)
        row = read_events(runs_file)[-1]
        assert row["event"] == "vm_cleanup_started" and row["run_id"] == handle.run.run_id
        assert env[hl.ROOT_RUN_ID_ENV] == handle.run.root_run_id
    finally:
        handle.release()


def test_a_reentrant_admission_and_guard_upgrade_lands_in_the_original_run(
    lock_dir: Path, runs_file: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Quiet:
        breached = False

        def stop(self) -> None:
            pass

    def admit(**_: object) -> str:
        clock.now += 2.0
        return "ok"

    def spawn(handle: object, environ: object = None) -> Quiet:
        clock.now += 0.5
        return Quiet()

    monkeypatch.setattr(hl, "check_admission", admit)
    monkeypatch.setattr(hl, "start_guard", spawn)
    path = lock_dir / hl.LOCK_NAME
    session = hl.acquire(path, wait_seconds=0, phase="pytest-session")
    clock.now += 5.0
    upgraded = hl.acquire(path, wait_seconds=0, phase="pytest-session", admit=True, guard=True)
    assert upgraded is session
    clock.now += 10.0
    upgraded.release()
    session.release()
    rows = read_events(runs_file)
    names = [row["event"] for row in rows]
    assert names.count("lock_requested") == 1 and names.count("execution_started") == 1
    assert names.count("run_finished") == 1 and names.count("admission_finished") == 1
    details = rows[-1]["details"]
    assert details["admission_seconds"] == 2.0 and details["guard_seconds"] == 0.5
    assert rows[-1]["outcome"] == "released"


def test_the_watchdog_launch_receives_the_run_identity(
    lock_dir: Path, runs_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    class StubGuard:
        breached = False
        holder_terminated = False

        def stop(self) -> None:
            pass

    def fake_spawn_guard(_handle: hl.HostLock, env: Mapping[str, str]) -> Any:
        seen.update(env)
        return StubGuard()

    monkeypatch.setattr(hl, "_spawn_guard", fake_spawn_guard)
    monkeypatch.setenv(hl.WATCHDOG_MODE_ENV, "kill")
    handle = hl.acquire(lock_dir / hl.LOCK_NAME, wait_seconds=0, phase="p", guard=True)
    try:
        assert seen[hl.RUN_ID_ENV] == handle.run.run_id
        assert seen[hl.RUNS_FILE_ENV] == str(runs_file)
    finally:
        handle.release()


def test_custom_lock_dir_does_not_pollute_outer_runs_file(
    lock_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outer_file = tmp_path / "outer" / hl.RUNS_FILE_NAME
    outer_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(hl.RUNS_FILE_ENV, str(outer_file))
    handle = hl.acquire(lock_dir / hl.LOCK_NAME, wait_seconds=0, phase="isolated")
    try:
        pass
    finally:
        handle.release()
    assert not outer_file.exists()
    assert (lock_dir / hl.RUNS_FILE_NAME).exists()


def test_cli_admission_refusal_is_recorded_as_admission_refused(
    lock_dir: Path, runs_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchbox.cli import host_lock_optin

    def refuse(**_: object) -> str:
        raise hl.AdmissionRefused("report")

    monkeypatch.setenv(host_lock_optin.HOST_LOCK_ENV, "1")
    monkeypatch.setattr(hl, "check_admission", refuse)
    with pytest.raises(SystemExit) as raised, host_lock_optin.heavy_guard("run"):
        pass
    assert raised.value.code == hl.EXIT_ADMISSION
    by = by_event(read_events(runs_file))
    assert "execution_started" not in by
    assert by["admission_finished"]["outcome"] == "admission_refused"
    assert by["run_finished"]["outcome"] == "admission_refused"


def test_cli_guard_exports_and_restores_the_trace_environment(
    lock_dir: Path, runs_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchbox.cli import host_lock_optin

    monkeypatch.setenv(host_lock_optin.HOST_LOCK_ENV, "1")
    monkeypatch.setattr(hl, "check_admission", lambda **_: "")
    with host_lock_optin.heavy_guard("run"):
        run_id = os.environ[hl.RUN_ID_ENV]
        assert os.environ[hl.RUNS_FILE_ENV] == str(runs_file)
        assert os.environ[hl.ROOT_RUN_ID_ENV] == run_id
    for name in (hl.RUN_ID_ENV, hl.ROOT_RUN_ID_ENV, hl.RUNS_FILE_ENV, hl.OWNER_ENV):
        assert name not in os.environ
    assert read_events(runs_file)[-1]["run_id"] == run_id


def test_session_isolation_exports_and_restores_the_trace_environment(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    script = (
        "import json, os\n"
        "from tests.utilities import session_isolation as isolation\n"
        "names = ['BENCHBOX_HEAVY_RUN_ID', 'BENCHBOX_HEAVY_ROOT_RUN_ID', 'BENCHBOX_HEAVY_RUNS_FILE']\n"
        "isolation.start()\n"
        "seen = {name: os.environ.get(name) for name in names}\n"
        "isolation.finish()\n"
        "print(json.dumps([seen, [os.environ.get(name) for name in names]]))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BENCHBOX_HEAVY_RUN", "BENCHBOX_HEAVY_ROOT"))}
    env.update(PYTHONPATH=str(REPO_ROOT), BENCHBOX_TEST_LOCK_DIR=str(lock), BENCHBOX_TEST_LOCK_WAIT_SECONDS="0")
    env.pop(hl.OWNER_ENV, None)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    seen, after = json.loads(result.stdout.strip().splitlines()[-1])
    assert after == [None, None, None]
    assert seen["BENCHBOX_HEAVY_RUNS_FILE"] == str(lock.resolve() / hl.RUNS_FILE_NAME)
    ledger = read_events(lock / hl.RUNS_FILE_NAME)
    assert ledger[0]["run_id"] == seen["BENCHBOX_HEAVY_RUN_ID"] and ledger[-1]["event"] == "run_finished"
