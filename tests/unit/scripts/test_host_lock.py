from __future__ import annotations

import errno
import os
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import types
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest

from benchbox.utils import host_lock as hl
from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.medium]

HOST_LOCK_FILE = REPO_ROOT / "benchbox" / "utils" / "host_lock.py"
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process semantics")

PRELUDE = (
    "import importlib.util, os, sys, time\n"
    "spec = importlib.util.spec_from_file_location('benchbox.utils.host_lock', sys.argv[1])\n"
    "hl = importlib.util.module_from_spec(spec)\n"
    "sys.modules[spec.name] = hl\n"
    "spec.loader.exec_module(hl)\n"
)
HOLDER_PROGRAM = (
    PRELUDE
    + 'handle = hl.acquire(hl.lock_path(), wait_seconds=0, phase="test", command="holder-cmd --flag")\n'
    + 'print("ready", flush=True)\n'
    + "time.sleep(120)\n"
)
CHILD_ACQUIRE_PROGRAM = (
    PRELUDE
    + "try:\n"
    + '    handle = hl.acquire(hl.lock_path(), phase="child")\n'
    + "except hl.LockOwnershipError as exc:\n"
    + '    print("ownership:", exc)\n'
    + "    raise SystemExit(3)\n"
    + "except hl.LockUnavailable:\n"
    + '    print("unavailable")\n'
    + "    raise SystemExit(4)\n"
    + 'print("inherited", handle.inherited)\n'
)
CLI_PROGRAM = PRELUDE + "raise SystemExit(hl.main(sys.argv[2:]))\n"
STDIN_PROBE = 'import sys; print("got:" + repr(sys.stdin.readline()))'
BACKGROUND_PROGRAM = (
    PRELUDE
    + "pid = os.fork()\n"
    + "if pid == 0:\n"
    + "    os.setpgid(0, 0)\n"
    + "    os._exit(hl.run_command([sys.executable, '-c', 'import sys; print(\"got:\" + repr(sys.stdin.readline()))']))\n"
    + "_, status = os.waitpid(pid, 0)\n"
    + "raise SystemExit(os.waitstatus_to_exitcode(status))\n"
)


@pytest.fixture
def lock_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "lock"
    monkeypatch.setenv(hl.LOCK_DIR_ENV, str(directory))
    monkeypatch.setenv(hl.WAIT_ENV, "0")
    monkeypatch.delenv(hl.OWNER_ENV, raising=False)
    return directory / hl.LOCK_NAME


@pytest.fixture
def spawned() -> Iterator[list[subprocess.Popen]]:
    processes: list[subprocess.Popen] = []
    yield processes
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _env_with(**extra: str) -> dict[str, str]:
    return dict(os.environ, **extra)


def _spawn_holder(spawned: list[subprocess.Popen]) -> subprocess.Popen:
    holder = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLDER_PROGRAM, str(HOST_LOCK_FILE)],
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    spawned.append(holder)
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready", holder.stderr.read() if holder.stderr else ""
    return holder


def _run_child_acquire(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", CHILD_ACQUIRE_PROGRAM, str(HOST_LOCK_FILE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _cli(*args: str, env: dict[str, str] | None = None, preexec_fn=None) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-I", "-c", CLI_PROGRAM, str(HOST_LOCK_FILE), *args],
        env=dict(os.environ) if env is None else env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=preexec_fn,
    )


def _run_cli(*args: str, env: dict[str, str] | None = None, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", CLI_PROGRAM, str(HOST_LOCK_FILE), *args],
        env=dict(os.environ) if env is None else env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _wait_until(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")


@posix_only
def test_lock_path_ignores_a_rewritten_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pwd

    monkeypatch.delenv(hl.LOCK_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "scratch-home"))
    expected = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".benchbox" / hl.LOCK_NAME
    assert hl.lock_path() == expected
    assert hl.default_lock_path() == expected


def test_lock_dir_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hl.LOCK_DIR_ENV, str(tmp_path / "custom"))
    assert hl.lock_path() == (tmp_path / "custom").resolve() / hl.LOCK_NAME


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 3600.0), ("", 3600.0), ("0", 0.0), ("12.5", 12.5), ("-4", 0.0), ("junk", 3600.0)]
)
def test_wait_seconds_from_env(raw: str | None, expected: float) -> None:
    env = {} if raw is None else {hl.WAIT_ENV: raw}
    assert hl.wait_seconds_from_env(env) == expected


@posix_only
def test_contended_acquire_names_holder_command_and_live_waiters(
    lock_file: Path, spawned: list[subprocess.Popen]
) -> None:
    holder = _spawn_holder(spawned)
    bystander = subprocess.Popen(["sleep", "60"])
    spawned.append(bystander)
    waiters = hl.waiters_dir(lock_file)
    waiters.mkdir(parents=True)
    (waiters / str(bystander.pid)).write_text(hl.process_identity(psutil.Process(bystander.pid)), encoding="utf-8")

    refused = _cli("run", "--", "true")
    spawned.append(refused)
    _, err = refused.communicate(timeout=30)

    assert refused.returncode == hl.EXIT_REFUSED
    assert f"pid:{holder.pid}" in err
    assert "holder-cmd --flag" in err
    assert "live waiters: 2" in err


@posix_only
def test_a_waiter_acquires_within_two_seconds_of_the_holder_being_killed(
    lock_file: Path, spawned: list[subprocess.Popen]
) -> None:
    holder = _spawn_holder(spawned)
    env = dict(os.environ, **{hl.WAIT_ENV: "60"})
    waiter = _cli("run", "--", "true", env=env)
    spawned.append(waiter)
    _wait_until(lambda: (hl.waiters_dir(lock_file) / str(waiter.pid)).exists())

    holder.kill()
    holder.wait(timeout=10)
    started = time.monotonic()
    waiter.wait(timeout=2)

    assert time.monotonic() - started < 2
    assert waiter.returncode == 0


@posix_only
def test_a_descendant_with_the_owner_env_skips_acquisition(lock_file: Path) -> None:
    handle = hl.acquire(lock_file, wait_seconds=0, phase="test")
    try:
        env = dict(os.environ, **{hl.OWNER_ENV: hl.process_identity()})
        result = _run_child_acquire(env)
    finally:
        handle.release()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "inherited True" in result.stdout


@posix_only
def test_an_unrelated_process_with_a_copied_owner_env_does_not_skip(
    lock_file: Path, spawned: list[subprocess.Popen]
) -> None:
    _spawn_holder(spawned)
    owner = hl.holder_owner(lock_file)
    assert owner is not None
    result = _run_child_acquire(_env_with(**{hl.OWNER_ENV: owner}))
    assert result.returncode == 4, result.stdout + result.stderr
    assert "unavailable" in result.stdout


@posix_only
def test_an_owner_env_with_the_wrong_create_time_is_rejected(lock_file: Path) -> None:
    handle = hl.acquire(lock_file, wait_seconds=0, phase="test")
    try:
        forged = f"{os.getpid()}:1.00"
        result = _run_child_acquire(_env_with(**{hl.OWNER_ENV: forged}))
    finally:
        handle.release()
    assert result.returncode == 3, result.stdout + result.stderr
    assert "ownership:" in result.stdout
    assert "inherited" not in result.stdout


@posix_only
def test_a_descendant_with_a_scrubbed_environment_fails_fast_instead_of_waiting(lock_file: Path) -> None:
    handle = hl.acquire(lock_file, wait_seconds=0, phase="test")
    try:
        env = {key: value for key, value in os.environ.items() if key != hl.OWNER_ENV}
        env[hl.WAIT_ENV] = "600"
        started = time.monotonic()
        result = _run_child_acquire(env)
        elapsed = time.monotonic() - started
    finally:
        handle.release()
    assert result.returncode == 3, result.stdout + result.stderr
    assert elapsed < 2
    assert "scrubbed" in result.stdout
    assert hl.OWNER_ENV in result.stdout


@posix_only
def test_the_wrapper_reports_an_environment_scrub_with_its_own_exit_code(lock_file: Path) -> None:
    handle = hl.acquire(lock_file, wait_seconds=0, phase="test")
    try:
        env = {key: value for key, value in os.environ.items() if key != hl.OWNER_ENV}
        env[hl.WAIT_ENV] = "600"
        started = time.monotonic()
        result = _run_cli("run", "--", "true", env=env)
        elapsed = time.monotonic() - started
    finally:
        handle.release()
    assert result.returncode == hl.EXIT_OWNERSHIP
    assert elapsed < 2
    assert "scrubbed" in result.stderr


def test_nested_acquire_in_one_process_is_released_only_by_the_outer_release(lock_file: Path) -> None:
    outer = hl.acquire(lock_file, wait_seconds=0, phase="outer")
    inner = hl.acquire(lock_file, wait_seconds=0, phase="inner")
    assert inner is outer
    assert outer.count == 2

    inner.release()
    assert hl.lock_is_held(lock_file)
    outer.release()
    assert not hl.lock_is_held(lock_file)


def test_hold_context_manager_nests(lock_file: Path) -> None:
    with hl.hold(lock_file, wait_seconds=0, phase="outer") as outer:
        with hl.hold(lock_file, wait_seconds=0, phase="inner") as inner:
            assert inner is outer
        assert hl.lock_is_held(lock_file)
    assert not hl.lock_is_held(lock_file)


@posix_only
def test_a_waiter_on_an_unlinked_inode_never_reports_itself_as_holder(lock_file: Path) -> None:
    lock_file.parent.mkdir(parents=True)
    stale_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    lock_file.unlink()
    replacement_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        assert hl.try_lock(replacement_fd)
        with pytest.raises(TimeoutError):
            hl.wait_on_fd(stale_fd, lock_file, 0.3)
        with pytest.raises(OSError):
            os.fstat(stale_fd)
    finally:
        os.close(replacement_fd)


@posix_only
def test_an_orphaned_descriptor_is_replaced_by_one_on_the_visible_file(lock_file: Path) -> None:
    lock_file.parent.mkdir(parents=True)
    stale_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    lock_file.unlink()
    lock_file.write_text("", encoding="utf-8")
    fd = hl.wait_on_fd(stale_fd, lock_file, 5.0)
    try:
        assert os.fstat(fd).st_ino == os.stat(lock_file).st_ino
        assert hl.lock_is_held(lock_file)
    finally:
        os.close(fd)


@posix_only
def test_held_inode_matches_detects_a_replaced_lock_file(lock_file: Path) -> None:
    handle = hl.acquire(lock_file, wait_seconds=0, phase="test")
    try:
        assert hl.held_inode_matches(handle)
        lock_file.unlink()
        assert not hl.held_inode_matches(handle)
        lock_file.write_text("", encoding="utf-8")
        assert not hl.held_inode_matches(handle)
    finally:
        handle.release()


@posix_only
def test_waiters_register_while_blocked_and_leave_no_marker(
    lock_file: Path, spawned: list[subprocess.Popen], monkeypatch: pytest.MonkeyPatch
) -> None:
    _spawn_holder(spawned)
    seen: list[list[str]] = []
    original = hl.live_waiter_count

    def recording(path: Path) -> int:
        seen.append(sorted(entry.name for entry in hl.waiters_dir(path).iterdir()))
        return original(path)

    monkeypatch.setattr(hl, "live_waiter_count", recording)
    with pytest.raises(hl.LockUnavailable, match=r"live waiters: 1"):
        hl.wait_for_lock(lock_file, 0.3)
    assert seen[0] == [str(os.getpid())]
    assert list(hl.waiters_dir(lock_file).iterdir()) == []


def test_dead_waiters_are_not_counted(lock_file: Path) -> None:
    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    waiters = hl.waiters_dir(lock_file)
    waiters.mkdir(parents=True)
    (waiters / finished.stdout.strip()).write_text("1:1.00", encoding="utf-8")
    (waiters / str(os.getpid())).write_text(hl.process_identity(), encoding="utf-8")
    (waiters / "not-a-pid").write_text("", encoding="utf-8")
    assert hl.live_waiter_count(lock_file) == 1


def test_holder_record_carries_the_owner_identity(lock_file: Path) -> None:
    handle = hl.acquire(lock_file, wait_seconds=0, phase="test", gate="unit", command="probe --x")
    try:
        record = hl.read_holder(lock_file)
        assert record.startswith(f"pid:{os.getpid()} owner:{hl.process_identity()} ")
        assert "phase:test gate:unit cmd:probe --x" in record
        assert hl.holder_owner(lock_file) == hl.process_identity()
    finally:
        handle.release()


def test_read_holder_replaces_invalid_utf8(tmp_path: Path) -> None:
    lock = tmp_path / "invalid.lock"
    lock.write_bytes(b"pid:\xff\n")
    assert hl.read_holder(lock) == "pid:\ufffd"


def test_stale_holder_text_does_not_block_acquire(lock_file: Path) -> None:
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("pid:99999999 owner:99999999:1.00 started:long-dead cmd:gone\n", encoding="utf-8")
    handle = hl.acquire(lock_file, wait_seconds=0, phase="test")
    handle.release()


@posix_only
def test_an_interrupted_wait_closes_its_descriptor(lock_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_file.parent.mkdir(parents=True)
    holder_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    assert hl.try_lock(holder_fd)
    opened: list[int] = []
    original = hl._open_lock_file

    def recording(path: Path) -> int:
        fd = original(path)
        opened.append(fd)
        return fd

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(hl, "_open_lock_file", recording)
    monkeypatch.setattr(hl.time, "sleep", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            hl.wait_for_lock(lock_file, 5.0)
    finally:
        os.close(holder_fd)
    with pytest.raises(OSError):
        os.fstat(opened[0])


@posix_only
def test_wait_on_fd_reports_progress_and_acquisition(lock_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lock_file.parent.mkdir(parents=True)
    holder_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    assert hl.try_lock(holder_fd)
    waiter_fd = os.open(str(lock_file), os.O_RDWR, 0o644)

    def release() -> None:
        time.sleep(0.35)
        os.close(holder_fd)

    thread = threading.Thread(target=release)
    thread.start()
    fd = hl.wait_on_fd(waiter_fd, lock_file, 5.0)
    thread.join(timeout=5)
    os.close(fd)
    err = capsys.readouterr().err
    assert "waiting for lock" in err
    assert "acquired lock after waiting" in err


@posix_only
def test_wait_on_fd_timeout_reports_holder(lock_file: Path) -> None:
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("pid:1 started:old cmd:old\n", encoding="utf-8")
    holder_fd = os.open(str(lock_file), os.O_RDWR)
    assert hl.try_lock(holder_fd)
    try:
        waiter_fd = os.open(str(lock_file), os.O_RDWR)
        with pytest.raises(TimeoutError, match="pid:1"):
            hl.wait_on_fd(waiter_fd, lock_file, 0.3)
    finally:
        os.close(holder_fd)
    waiter_fd = os.open(str(lock_file), os.O_RDWR)
    os.close(hl.wait_on_fd(waiter_fd, lock_file, 5.0))


@posix_only
def test_clear_inactive_lock_keeps_path_for_competing_openers(lock_file: Path) -> None:
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("stale holder", encoding="utf-8")
    inode = lock_file.stat().st_ino
    assert hl.clear_inactive_lock(lock_file) == 0
    assert lock_file.stat().st_ino == inode
    assert lock_file.read_text(encoding="utf-8") == ""

    holder_fd = os.open(str(lock_file), os.O_RDWR)
    try:
        assert hl.try_lock(holder_fd)
        contender_fd = os.open(str(lock_file), os.O_RDWR)
        try:
            assert not hl.try_lock(contender_fd)
        finally:
            os.close(contender_fd)
    finally:
        os.close(holder_fd)


@posix_only
def test_clear_retains_an_active_lock(lock_file: Path, spawned: list[subprocess.Popen]) -> None:
    holder = _spawn_holder(spawned)
    before = lock_file.read_text(encoding="utf-8")
    cleared = _run_cli("clear")
    assert cleared.returncode == 1
    assert "active lock retained" in cleared.stderr
    assert lock_file.read_text(encoding="utf-8") == before
    assert f"pid:{holder.pid} " in before


@posix_only
def test_clear_via_cli_empties_an_inactive_lock(lock_file: Path) -> None:
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("stale holder", encoding="utf-8")
    cleared = _run_cli("clear", str(lock_file))
    assert cleared.returncode == 0, cleared.stderr
    assert lock_file.exists()
    assert lock_file.read_text(encoding="utf-8") == ""


@posix_only
def test_run_holds_the_lock_for_the_childs_lifetime_and_releases_on_exit(lock_file: Path) -> None:
    wrapper = _cli("run", "--", "sleep", "2")
    try:
        _wait_until(lambda: lock_file.exists() and hl.lock_is_held(lock_file))
        refused = _run_cli("run", "--", "true")
        assert refused.returncode == hl.EXIT_REFUSED
        assert "sleep 2" in refused.stderr
        assert wrapper.wait(timeout=15) == 0
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait()
        for stream in (wrapper.stdout, wrapper.stderr):
            if stream is not None:
                stream.close()
    assert not hl.lock_is_held(lock_file)


@posix_only
def test_run_exports_the_owner_env_and_propagates_the_exit_code(lock_file: Path) -> None:
    probe = "import os; print(os.environ['BENCHBOX_TEST_SESSION_OWNER'], os.getppid())"
    result = _run_cli("run", "--", sys.executable, "-c", probe)
    assert result.returncode == 0, result.stderr
    owner, parent = result.stdout.split()
    assert owner.startswith(f"{parent}:")

    failing = _run_cli("run", "--", "sh", "-c", "exit 7")
    assert failing.returncode == 7


@posix_only
def test_run_reports_a_missing_command(lock_file: Path) -> None:
    result = _run_cli("run", "--", "definitely-not-a-command-xyz")
    assert result.returncode == 127
    assert not hl.lock_is_held(lock_file)


@posix_only
@pytest.mark.parametrize("sig_name", ["SIGTERM", "SIGINT", "SIGHUP"])
def test_a_signal_to_the_wrapper_stops_the_child_and_releases_the_lock(lock_file: Path, sig_name: str) -> None:
    sig = getattr(signal, sig_name)
    wrapper = _cli("run", "--", "sleep", "60")
    try:
        _wait_until(lambda: lock_file.exists() and hl.lock_is_held(lock_file))
        _wait_until(lambda: bool(psutil.Process(wrapper.pid).children()))
        child = psutil.Process(wrapper.pid).children()[0]
        os.kill(wrapper.pid, sig)
        assert wrapper.wait(timeout=10) == 128 + int(sig)
        assert not psutil.pid_exists(child.pid)
        assert not hl.lock_is_held(lock_file)
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait()
        for stream in (wrapper.stdout, wrapper.stderr):
            if stream is not None:
                stream.close()


def test_the_package_attribute_resolves_to_the_loaded_module() -> None:
    import benchbox.utils

    assert benchbox.utils.host_lock is hl


@posix_only
def test_a_waiter_marker_without_identity_is_not_counted_once_its_pid_is_dead(lock_file: Path) -> None:
    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    waiters = hl.waiters_dir(lock_file)
    waiters.mkdir(parents=True)
    (waiters / finished.stdout.strip()).write_text("", encoding="utf-8")
    (waiters / str(os.getpid())).write_text("", encoding="utf-8")
    assert hl.live_waiter_count(lock_file) == 1


@posix_only
def test_test_unlock_resolves_its_path_through_the_module_not_home(tmp_path: Path) -> None:
    home = tmp_path / "rewritten-home"
    home.mkdir()
    env = {key: value for key, value in os.environ.items() if key != hl.LOCK_DIR_ENV}
    env["HOME"] = str(home)
    result = subprocess.run(
        ["make", "-n", "--no-print-directory", "test-unlock"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "python3 -I benchbox/utils/host_lock.py clear"
    assert str(home) not in result.stdout
    assert not (home / ".benchbox").exists()


@posix_only
def test_an_ignored_hangup_stays_ignored_for_the_child(lock_file: Path) -> None:
    hangup = signal.Signals["SIGHUP"]
    wrapper = _cli("run", "--", "sleep", "2", preexec_fn=lambda: signal.signal(hangup, signal.SIG_IGN))
    try:
        _wait_until(lambda: lock_file.exists() and hl.lock_is_held(lock_file))
        _wait_until(lambda: bool(psutil.Process(wrapper.pid).children()))
        os.kill(wrapper.pid, hangup)
        assert wrapper.wait(timeout=15) == 0
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait()
        for stream in (wrapper.stdout, wrapper.stderr):
            if stream is not None:
                stream.close()


@posix_only
def test_a_failed_spawn_restores_the_signal_handlers() -> None:
    before = {sig: signal.getsignal(sig) for sig in hl._FORWARDED_SIGNALS}
    with pytest.raises(FileNotFoundError):
        hl._supervise_posix(["definitely-not-a-command-xyz"], dict(os.environ))
    assert {sig: signal.getsignal(sig) for sig in hl._FORWARDED_SIGNALS} == before


def _read_pty(master: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.2)
        if not ready:
            continue
        try:
            data = os.read(master, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    return b"".join(chunks)


def _become_session_leader() -> None:
    import fcntl
    import termios

    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _pty_session(spawn, feed: bytes | None, timeout: float = 30.0) -> tuple[int, bytes]:
    import pty

    master, slave = pty.openpty()
    process = spawn(slave)
    os.close(slave)
    deadline = time.monotonic() + timeout
    try:
        if feed is not None:
            os.write(master, feed)
        output = _read_pty(master, deadline)
        try:
            code = process.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise AssertionError(f"pty child did not finish: {output!r}") from None
        return code, output
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


@posix_only
@pytest.mark.skipif(shutil.which("make") is None, reason="make is required")
def test_a_child_reading_stdin_under_make_does_not_hang(lock_file: Path, tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        f'run:\n\t"{sys.executable}" -I "{HOST_LOCK_FILE}" run -- "{sys.executable}" -c \'{STDIN_PROBE}\'\n',
        encoding="utf-8",
    )

    def spawn(slave: int) -> subprocess.Popen:
        return subprocess.Popen(
            ["make", "--no-print-directory", "-f", str(makefile), "run"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=tmp_path,
            preexec_fn=_become_session_leader,
        )

    code, output = _pty_session(spawn, b"hello\n")

    assert code == 0, output
    assert b"got:'hello\\n'" in output


@posix_only
def test_a_wrapper_outside_the_terminal_foreground_gives_the_child_no_stdin(lock_file: Path) -> None:
    def spawn(slave: int) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-I", "-c", BACKGROUND_PROGRAM, str(HOST_LOCK_FILE)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=_become_session_leader,
        )

    code, output = _pty_session(spawn, None)

    assert code == 0, output
    assert b"got:''" in output


@posix_only
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
def test_ctrl_z_returns_the_terminal_to_the_shell_and_fg_resumes_the_child(lock_file: Path) -> None:
    import pty

    master, slave = pty.openpty()
    shell = subprocess.Popen(
        ["bash", "--norc", "--noprofile", "-i"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        preexec_fn=_become_session_leader,
        env=dict(os.environ, PS1="", HISTFILE=os.devnull),
    )
    os.close(slave)
    seen = bytearray()

    def expect(marker: bytes, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while marker not in seen:
            if time.monotonic() > deadline:
                raise AssertionError(f"{marker!r} not seen in {bytes(seen)!r}")
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                seen.extend(os.read(master, 4096))

    def sleeper() -> psutil.Process:
        found = [c for c in psutil.Process(shell.pid).children(recursive=True) if c.name() == "sleep"]
        assert found
        return found[0]

    try:
        os.write(master, f'"{sys.executable}" -I "{HOST_LOCK_FILE}" run -- sleep 60\n'.encode())
        _wait_until(lambda: lock_file.exists() and hl.lock_is_held(lock_file))
        _wait_until(lambda: any(c.name() == "sleep" for c in psutil.Process(shell.pid).children(recursive=True)))
        child = sleeper()
        time.sleep(0.5)
        os.write(master, b"\x1a")
        os.write(master, b"echo BACK_$((20+22))\n")
        expect(b"BACK_42")
        _wait_until(lambda: child.status() == psutil.STATUS_STOPPED)
        os.write(master, b"fg\n")
        _wait_until(lambda: child.status() != psutil.STATUS_STOPPED)
        _wait_until(lambda: os.tcgetpgrp(master) == os.getpgid(child.pid))
        os.write(master, b"\x03")
        _wait_until(lambda: not hl.lock_is_held(lock_file))
        os.write(master, b"echo DONE_$((1+1))\n")
        expect(b"DONE_2")
        os.write(master, b"exit\n")
        deadline = time.monotonic() + 20
        while shell.poll() is None and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    os.read(master, 4096)
                except OSError:
                    break
        assert shell.poll() == 0
    finally:
        for leftover in psutil.Process(shell.pid).children(recursive=True) if shell.poll() is None else []:
            leftover.kill()
        os.close(master)
        if shell.poll() is None:
            shell.kill()
            shell.wait(timeout=10)


def test_the_module_entry_point_runs_without_import_warnings(lock_file: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "benchbox.utils.host_lock", "run", "--", sys.executable, "-c", "pass"],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "RuntimeWarning" not in result.stderr


def test_data_fetch_locking_has_no_private_lock_primitives() -> None:
    source = (REPO_ROOT / "benchbox" / "core" / "data_fetch" / "locking.py").read_text(encoding="utf-8")
    assert "fcntl" not in source
    assert "msvcrt" not in source


class _FakeMsvcrt(types.ModuleType):
    LK_NBLCK = 2
    LK_LOCK = 1
    LK_UNLCK = 0

    def __init__(self, failures: list[int] | None = None) -> None:
        super().__init__("msvcrt")
        self.calls: list[tuple[int, int, int]] = []
        self.failures = list(failures or [])

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), nbytes))
        if mode == self.LK_NBLCK and self.failures:
            raise OSError(self.failures.pop(0), "lock failed")


@pytest.fixture
def windows_msvcrt(monkeypatch: pytest.MonkeyPatch) -> _FakeMsvcrt:
    module = _FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", module)
    monkeypatch.setattr(hl.sys, "platform", "win32")
    return module


def test_wait_on_fd_windows_uses_msvcrt(lock_file: Path, windows_msvcrt: _FakeMsvcrt) -> None:
    lock_file.parent.mkdir(parents=True)
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    returned = hl.wait_on_fd(fd, lock_file, 5.0)
    os.close(returned)
    assert [(mode, offset) for mode, offset, _ in windows_msvcrt.calls] == [(windows_msvcrt.LK_NBLCK, hl.LOCK_OFFSET)]


def test_wait_on_fd_windows_retries_contention(
    lock_file: Path, windows_msvcrt: _FakeMsvcrt, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows_msvcrt.failures = [errno.EACCES]
    monkeypatch.setattr(hl.time, "sleep", lambda _seconds: None)
    lock_file.parent.mkdir(parents=True)
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    os.close(hl.wait_on_fd(fd, lock_file, 5.0))
    assert len([call for call in windows_msvcrt.calls if call[0] == windows_msvcrt.LK_NBLCK]) == 2


@pytest.mark.parametrize("failure_errno", [errno.EBADF, errno.EINVAL])
def test_wait_on_fd_windows_propagates_non_contention_error(
    lock_file: Path, windows_msvcrt: _FakeMsvcrt, failure_errno: int
) -> None:
    windows_msvcrt.failures = [failure_errno]
    lock_file.parent.mkdir(parents=True)
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    with pytest.raises(OSError) as error:
        hl.wait_on_fd(fd, lock_file, 5.0)
    assert error.value.errno == failure_errno
    assert len([call for call in windows_msvcrt.calls if call[0] == windows_msvcrt.LK_NBLCK]) == 1
    with pytest.raises(OSError):
        os.fstat(fd)


@posix_only
def test_wait_on_fd_posix_propagates_non_contention_error(lock_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    lock_file.parent.mkdir(parents=True)
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)

    def denied(_fd: int, _flags: int) -> None:
        raise PermissionError("lock denied")

    monkeypatch.setattr(fcntl, "flock", denied)
    with pytest.raises(PermissionError, match="lock denied"):
        hl.wait_on_fd(fd, lock_file, 5.0)


def test_the_locked_byte_lies_past_the_holder_payload(lock_file: Path, windows_msvcrt: _FakeMsvcrt) -> None:
    handle = hl.acquire(lock_file, wait_seconds=0, phase="test")
    handle.release()

    assert [(mode, offset) for mode, offset, _ in windows_msvcrt.calls] == [
        (windows_msvcrt.LK_NBLCK, hl.LOCK_OFFSET),
        (windows_msvcrt.LK_UNLCK, hl.LOCK_OFFSET),
    ]
    assert len(lock_file.read_bytes()) < hl.LOCK_OFFSET
    assert hl.read_holder(lock_file).startswith(f"pid:{os.getpid()} ")


def test_clearing_an_inactive_lock_probes_the_same_byte(lock_file: Path, windows_msvcrt: _FakeMsvcrt) -> None:
    assert hl.clear_inactive_lock(lock_file) == 0

    assert [(mode, offset) for mode, offset, _ in windows_msvcrt.calls] == [
        (windows_msvcrt.LK_NBLCK, hl.LOCK_OFFSET),
        (windows_msvcrt.LK_UNLCK, hl.LOCK_OFFSET),
    ]


def test_blocking_lock_primitives_use_the_same_byte_on_windows(lock_file: Path, windows_msvcrt: _FakeMsvcrt) -> None:
    lock_file.parent.mkdir(parents=True)
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        hl.lock_exclusive(fd)
        hl.unlock(fd)
    finally:
        os.close(fd)
    assert [(mode, offset) for mode, offset, _ in windows_msvcrt.calls] == [
        (windows_msvcrt.LK_LOCK, hl.LOCK_OFFSET),
        (windows_msvcrt.LK_UNLCK, hl.LOCK_OFFSET),
    ]
