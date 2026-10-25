from __future__ import annotations

import errno
import os
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from scripts.local_validation import _close_lock, read_holder, seek_lock_byte, wait_for_lock, write_holder

_OWNER = "BENCHBOX_TEST_SESSION_OWNER"
_OUTPUT_DIR = "BENCHBOX_OUTPUT_DIR"
_OWNED_OUTPUT_DIR = "BENCHBOX_TEST_SESSION_OUTPUT_DIR"
_ENV_KEYS = ("HOME", "USERPROFILE", "BENCHBOX_TEST_LOCK_DIR", _OUTPUT_DIR, _OWNER, _OWNED_OUTPUT_DIR)
_state: dict[str, Any] | None = None


def _ancestor_holds_lock(lock_path: Path) -> bool:
    try:
        import psutil
    except ImportError:
        return False
    try:
        owner = int(os.environ.get(_OWNER, "0"))

        if owner not in {parent.pid for parent in psutil.Process().parents()}:
            return False
        if not read_holder(lock_path).startswith(f"pid:{owner} "):
            return False
        fd = os.open(lock_path, os.O_RDWR)
        try:
            if sys.platform == "win32":
                import msvcrt

                seek_lock_byte(fd)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    if exc.errno == errno.EACCES:
                        return True
                    raise
                seek_lock_byte(fd)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    except (psutil.Error, OSError, ValueError):
        return False
    return False


def start(acquire_lock: bool = True) -> bool:
    global _state
    if _state is not None:
        return False
    previous = {key: os.environ.get(key) for key in _ENV_KEYS}
    configured = os.environ.get("BENCHBOX_TEST_LOCK_DIR")
    lock_dir = Path(configured).expanduser().resolve() if configured else Path.home() / ".benchbox"
    lock_path = lock_dir / "test.lock"
    fd: int | None = None
    inherited = _ancestor_holds_lock(lock_path) if acquire_lock else False
    if acquire_lock and not inherited:
        try:
            wait = max(0.0, float(os.environ.get("BENCHBOX_TEST_LOCK_WAIT_SECONDS", "3600") or "3600"))
        except ValueError:
            wait = 3600.0
        fd = wait_for_lock(lock_path, wait)
        write_holder(fd, lock_path, phase="pytest-session", gate="hermetic")
    try:
        home = tempfile.TemporaryDirectory(
            prefix="benchbox-pytest-home-", dir=os.environ.get("JCODE_SCRATCH_DIR"), ignore_cleanup_errors=True
        )
    except BaseException:
        if fd is not None:
            _close_lock(fd)
        raise
    os.environ["HOME"] = home.name
    os.environ["USERPROFILE"] = home.name
    os.environ["BENCHBOX_TEST_LOCK_DIR"] = str(lock_dir)
    caller_output = (previous[_OUTPUT_DIR] or "").strip()
    if not caller_output or caller_output == previous[_OWNED_OUTPUT_DIR]:
        owned_output = str(Path(home.name) / "benchmark_runs")
        os.environ[_OUTPUT_DIR] = owned_output
        os.environ[_OWNED_OUTPUT_DIR] = owned_output
    if acquire_lock and not inherited:
        os.environ[_OWNER] = str(os.getpid())
    _state = {"previous": previous, "home": home, "fd": fd}
    return True


def own_environment(monkeypatch: pytest.MonkeyPatch, keys: Iterable[str]) -> None:
    for key in keys:
        value = os.environ.get(key)
        monkeypatch.setenv(key, value if value is not None else "")
        if value is None:
            monkeypatch.delenv(key)


def session_home() -> Path:
    if _state is None:
        raise RuntimeError("test-session isolation is not active")
    return Path(_state["home"].name)


def active() -> bool:
    return _state is not None


def finish() -> None:
    global _state
    if _state is None:
        return
    state, _state = _state, None
    try:
        for key, value in state["previous"].items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        state["home"].cleanup()
    finally:
        if state["fd"] is not None:
            _close_lock(state["fd"])
