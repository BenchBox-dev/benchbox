from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

_HOST_LOCK_MODULE = "benchbox.utils.host_lock"
_HOST_LOCK_PATH = Path(__file__).resolve().parents[2] / "benchbox" / "utils" / "host_lock.py"


def _load_host_lock() -> Any:
    loaded = sys.modules.get(_HOST_LOCK_MODULE)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(_HOST_LOCK_MODULE, _HOST_LOCK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_HOST_LOCK_MODULE] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(_HOST_LOCK_MODULE, None)
        raise
    return module


host_lock = _load_host_lock()

_OWNER = host_lock.OWNER_ENV
_OUTPUT_DIR = "BENCHBOX_OUTPUT_DIR"
_OWNED_OUTPUT_DIR = "BENCHBOX_TEST_SESSION_OUTPUT_DIR"
_ENV_KEYS = ("HOME", "USERPROFILE", "BENCHBOX_TEST_LOCK_DIR", _OUTPUT_DIR, _OWNER, _OWNED_OUTPUT_DIR)
_state: dict[str, Any] | None = None
_SKIP_LOCK = "BENCHBOX_SKIP_TEST_LOCK"
_LOCK_DIR = host_lock.LOCK_DIR_ENV
_XDIST_WORKER = "PYTEST_XDIST_WORKER"


def lock_bypass_trigger() -> str | None:
    if os.environ.get(_XDIST_WORKER):
        return None
    owner = (os.environ.get(_OWNER) or "").strip()
    if owner and owner != host_lock.process_identity():
        return None
    if os.environ.get(_SKIP_LOCK):
        return _SKIP_LOCK
    configured = os.environ.get(_LOCK_DIR)
    if configured:
        try:
            custom = Path(configured).expanduser().resolve()
        except OSError:
            return _LOCK_DIR
        try:
            if custom != host_lock.default_lock_path().parent.resolve():
                return _LOCK_DIR
        except OSError:
            return _LOCK_DIR
    return None


def warn_lock_bypass() -> None:
    trigger = lock_bypass_trigger()
    if trigger is None:
        return
    sys.stderr.write(
        f"[benchbox] WARNING: test lock bypass active via {trigger} "
        f"(pid={os.getpid()}, cwd={os.getcwd()}); the default ~/.benchbox/test.lock is not held.\n"
    )
    sys.stderr.flush()


def start(acquire_lock: bool = True) -> bool:
    global _state
    if _state is not None:
        return False
    previous = {key: os.environ.get(key) for key in _ENV_KEYS}
    lock_dir = host_lock.lock_dir()
    lock_path = lock_dir / host_lock.LOCK_NAME
    handle = None
    if acquire_lock:
        handle = host_lock.acquire(lock_path, phase="pytest-session", gate="hermetic")
    inherited = handle is not None and handle.inherited
    if not inherited and (not acquire_lock or lock_path != host_lock.default_lock_path()):
        warn_lock_bypass()
    try:
        home = tempfile.TemporaryDirectory(
            prefix="benchbox-pytest-home-", dir=os.environ.get("JCODE_SCRATCH_DIR"), ignore_cleanup_errors=True
        )
    except BaseException:
        if handle is not None:
            handle.release()
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
        os.environ[_OWNER] = host_lock.process_identity()
    _state = {"previous": previous, "home": home, "lock": handle}
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
        if state["lock"] is not None:
            state["lock"].release()
