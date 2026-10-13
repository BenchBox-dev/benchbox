from __future__ import annotations

import errno
import importlib.util
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("local_validation", SCRIPTS / "local_validation.py")
assert spec is not None and spec.loader is not None
lv = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lv
spec.loader.exec_module(lv)


def test_wait_for_lock_closes_fd_on_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = tmp_path / "test.lock"
    opened: list[int] = []

    def interrupt(fd: int, lock_path: Path, timeout_seconds: float) -> None:
        opened.append(fd)
        raise KeyboardInterrupt

    monkeypatch.setattr(lv, "wait_on_fd", interrupt)
    with pytest.raises(KeyboardInterrupt):
        lv.wait_for_lock(lock, 5.0)
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_wait_on_fd_timeout_reports_holder(tmp_path: Path) -> None:
    import fcntl

    lock = tmp_path / "test.lock"
    lock.write_text("pid:1 started:old cmd:old\n")
    holder_fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        waiter_fd = os.open(str(lock), os.O_RDWR, 0o644)
        try:
            with pytest.raises(TimeoutError, match="pid:1"):
                lv.wait_on_fd(waiter_fd, lock, 0.3)
        finally:
            os.close(waiter_fd)
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)
    waiter_fd = os.open(str(lock), os.O_RDWR, 0o644)
    try:
        lv.wait_on_fd(waiter_fd, lock, 5.0)
    finally:
        os.close(waiter_fd)


def test_wait_on_fd_reports_progress_and_acquisition(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import fcntl

    lock = tmp_path / "progress.lock"
    holder_fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    waiter_fd = os.open(str(lock), os.O_RDWR, 0o644)

    def release() -> None:
        time.sleep(0.35)
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)

    thread = threading.Thread(target=release)
    thread.start()
    try:
        lv.wait_on_fd(waiter_fd, lock, 5.0)
    finally:
        os.close(waiter_fd)
    thread.join(timeout=5)
    err = capsys.readouterr().err
    assert "waiting for lock" in err
    assert "acquired lock after waiting" in err


def test_stale_holder_text_does_not_block_acquire(tmp_path: Path) -> None:
    lock = tmp_path / "test.lock"
    lock.write_text("pid:99999999 started:long-dead cmd:gone\n")
    fd = lv.wait_for_lock(lock, 5.0)
    os.close(fd)


def test_clear_inactive_lock_keeps_path_for_competing_openers(tmp_path: Path) -> None:
    import fcntl

    lock = tmp_path / "test.lock"
    lock.write_text("stale holder", encoding="utf-8")
    assert lv.clear_inactive_lock(lock) == 0
    assert lock.exists()
    assert lock.read_text(encoding="utf-8") == ""

    holder_fd = os.open(str(lock), os.O_RDWR)
    try:
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        contender_fd = os.open(str(lock), os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(contender_fd)
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_wait_for_lock_closes_fd_when_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = tmp_path / "cancel.lock"
    closed: list[int] = []
    original_close = os.close

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt

    def record_close(fd: int) -> None:
        closed.append(fd)
        original_close(fd)

    monkeypatch.setattr(lv, "wait_on_fd", cancel)
    monkeypatch.setattr(lv.os, "close", record_close)
    with pytest.raises(KeyboardInterrupt):
        lv.wait_for_lock(lock, 5.0)
    assert closed


def test_wait_on_fd_windows_uses_msvcrt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    lock = tmp_path / "win.lock"
    lock.write_text("", encoding="utf-8")
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    calls: list[tuple[int, int]] = []
    fake = types.ModuleType("msvcrt")
    fake.LK_NBLCK = 1
    fake.LK_UNLCK = 0

    def fake_locking(fd_arg: int, mode: int, nbytes: int) -> None:
        calls.append((mode, nbytes))

    fake.locking = fake_locking
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(lv.sys, "platform", "win32")
    try:
        lv.wait_on_fd(fd, lock, 5.0)
    finally:
        os.close(fd)
    assert calls == [(1, 1)]


def test_read_holder_replaces_invalid_utf8(tmp_path: Path) -> None:
    lock = tmp_path / "invalid.lock"
    lock.write_bytes(b"pid:\xff\n")
    assert lv.read_holder(lock) == "pid:\ufffd"


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl is POSIX-only")
def test_wait_on_fd_posix_propagates_non_contention_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    lock = tmp_path / "posix.lock"
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)

    def denied(_fd: int, _flags: int) -> None:
        raise PermissionError("lock denied")

    monkeypatch.setattr(fcntl, "flock", denied)
    try:
        with pytest.raises(PermissionError, match="lock denied"):
            lv.wait_on_fd(fd, lock, 5.0)
    finally:
        os.close(fd)


def test_wait_on_fd_windows_retries_contention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    lock = tmp_path / "win-contended.lock"
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    fake = types.ModuleType("msvcrt")
    fake.LK_NBLCK = 1
    calls = 0

    def locking(_fd: int, _mode: int, _nbytes: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EACCES, "lock held")

    fake.locking = locking
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(lv.sys, "platform", "win32")
    monkeypatch.setattr(lv.time, "sleep", lambda _seconds: None)
    try:
        lv.wait_on_fd(fd, lock, 5.0)
    finally:
        os.close(fd)
    assert calls == 2


@pytest.mark.parametrize("failure_errno", [errno.EBADF, errno.EINVAL])
def test_wait_on_fd_windows_propagates_non_contention_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_errno: int
) -> None:
    import types

    lock = tmp_path / "win-invalid.lock"
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    fake = types.ModuleType("msvcrt")
    fake.LK_NBLCK = 1
    calls = 0

    def locking(_fd: int, _mode: int, _nbytes: int) -> None:
        nonlocal calls
        calls += 1
        raise OSError(failure_errno, "lock failed")

    fake.locking = locking
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(lv.sys, "platform", "win32")
    try:
        with pytest.raises(OSError) as error:
            lv.wait_on_fd(fd, lock, 5.0)
    finally:
        os.close(fd)
    assert error.value.errno == failure_errno
    assert calls == 1


class _OffsetRecordingMsvcrt(types.ModuleType):
    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self) -> None:
        super().__init__("msvcrt")
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), nbytes))


@pytest.fixture
def offset_msvcrt(monkeypatch: pytest.MonkeyPatch) -> _OffsetRecordingMsvcrt:
    module = _OffsetRecordingMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", module)
    monkeypatch.setattr(lv.sys, "platform", "win32")
    return module


def test_the_locked_byte_lies_past_the_holder_payload(tmp_path: Path, offset_msvcrt: _OffsetRecordingMsvcrt) -> None:
    lock_path = tmp_path / "test.lock"
    fd = lv.wait_for_lock(lock_path, 0)
    lv.write_holder(fd, lock_path, phase="test")
    lv._close_lock(fd)

    assert [(mode, offset) for mode, offset, _ in offset_msvcrt.calls] == [
        (offset_msvcrt.LK_NBLCK, lv.LOCK_OFFSET),
        (offset_msvcrt.LK_UNLCK, lv.LOCK_OFFSET),
    ]
    assert len(lock_path.read_bytes()) < lv.LOCK_OFFSET
    assert lv.read_holder(lock_path).startswith(f"pid:{os.getpid()} ")


def test_clearing_an_inactive_lock_probes_the_same_byte(tmp_path: Path, offset_msvcrt: _OffsetRecordingMsvcrt) -> None:
    lock_path = tmp_path / "test.lock"

    assert lv.clear_inactive_lock(lock_path) == 0

    assert [(mode, offset) for mode, offset, _ in offset_msvcrt.calls] == [
        (offset_msvcrt.LK_NBLCK, lv.LOCK_OFFSET),
        (offset_msvcrt.LK_UNLCK, lv.LOCK_OFFSET),
    ]
