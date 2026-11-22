from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from benchbox.utils.process_group import kill_process_group
from benchbox.utils.process_tree import tree_footprint_bytes

EXIT_TIMEOUT = 124
EXIT_MEMORY_LIMIT = 137

_REAP_TIMEOUT_S = 1.0
_MEMORY_POLL_S = 0.25


@dataclass(frozen=True)
class TimeoutResult:
    exit_code: int
    timed_out: bool
    elapsed_s: float
    stdout: Any = None
    stderr: Any = None
    memory_exceeded: bool = False
    peak_rss_bytes: int | None = None


class _MemoryLimitExceeded(Exception):
    pass


class _RssMeter:
    def __init__(self, pid: int, limit_bytes: int) -> None:
        self._pid = pid
        self.limit_bytes = limit_bytes
        self.peak_bytes = 0

    def over_limit(self) -> bool:
        total = tree_footprint_bytes(self._pid)
        if total is None:
            return False
        self.peak_bytes = max(self.peak_bytes, total)
        return total > self.limit_bytes


def _communicate(proc: subprocess.Popen, timeout_s: float | None, meter: _RssMeter | None) -> tuple[Any, Any]:
    if meter is None:
        return proc.communicate(timeout=timeout_s)
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        wait_s = _MEMORY_POLL_S if deadline is None else min(_MEMORY_POLL_S, max(deadline - time.monotonic(), 0.0))
        try:
            return proc.communicate(timeout=wait_s)
        except subprocess.TimeoutExpired:
            if meter.over_limit():
                raise _MemoryLimitExceeded from None
            if deadline is not None and time.monotonic() >= deadline:
                raise


def run_with_timeout(
    argv: list[str],
    timeout_s: int,
    *,
    stdout=None,
    stderr=None,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    memory_limit_bytes: int | None = None,
) -> TimeoutResult:
    start = time.monotonic()
    preexec = os.setsid if sys.platform != "win32" else None
    proc: subprocess.Popen | None = None
    meter: _RssMeter | None = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=stdout,
            stderr=stderr,
            env=env,
            cwd=cwd,
            preexec_fn=preexec,
        )
        if memory_limit_bytes:
            meter = _RssMeter(proc.pid, memory_limit_bytes)
        out, err = _communicate(proc, timeout_s or None, meter)
        return TimeoutResult(
            exit_code=proc.returncode,
            timed_out=False,
            elapsed_s=time.monotonic() - start,
            stdout=out,
            stderr=err,
            peak_rss_bytes=meter.peak_bytes if meter is not None else None,
        )
    except (subprocess.TimeoutExpired, _MemoryLimitExceeded) as exc:
        if proc is None:
            raise
        kill_process_group(proc)
        try:
            out, err = proc.communicate(timeout=_REAP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            _reap_in_background(proc)
            out, err = None, None
        memory_exceeded = isinstance(exc, _MemoryLimitExceeded)
        killed_exit_code = proc.returncode if proc.returncode is not None else -int(getattr(signal, "SIGKILL", 9))
        return TimeoutResult(
            exit_code=EXIT_MEMORY_LIMIT if memory_exceeded else killed_exit_code,
            timed_out=not memory_exceeded,
            elapsed_s=time.monotonic() - start,
            stdout=out,
            stderr=err,
            memory_exceeded=memory_exceeded,
            peak_rss_bytes=meter.peak_bytes if meter is not None else None,
        )
    except BaseException:
        if proc is not None:
            _kill_and_reap_process_group(proc)
        raise


def _kill_and_reap_process_group(proc: subprocess.Popen) -> None:
    try:
        kill_process_group(proc)
    except BaseException:
        pass

    deadline = time.monotonic() + _REAP_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            proc.wait(timeout=deadline - time.monotonic())
        except subprocess.TimeoutExpired:
            _reap_in_background(proc)
            return
        except BaseException:
            continue
        return


def _reap_in_background(proc: subprocess.Popen) -> None:
    def reap() -> None:
        with contextlib.suppress(ChildProcessError):
            _, status = os.waitpid(proc.pid, 0)
            if proc.returncode is None:
                proc.returncode = os.waitstatus_to_exitcode(status)

    target = proc.wait if sys.platform == "win32" else reap
    threading.Thread(target=target, name=f"reap-{proc.pid}", daemon=True).start()
