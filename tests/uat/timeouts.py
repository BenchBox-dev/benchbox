from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Mapping

import psutil

EXIT_TIMEOUT = 124
EXIT_MEMORY_LIMIT = 137

_KILL_GRACE_S = 0.2
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
        try:
            root = psutil.Process(self._pid)
            members = [root, *root.children(recursive=True)]
        except psutil.Error:
            return False
        total = 0
        for member in members:
            try:
                total += member.memory_info().rss
            except psutil.Error:
                continue
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
        _kill_process_group(proc)
        try:
            out, err = proc.communicate(timeout=_REAP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            out, err = None, None
        memory_exceeded = isinstance(exc, _MemoryLimitExceeded)
        return TimeoutResult(
            exit_code=EXIT_MEMORY_LIMIT if memory_exceeded else EXIT_TIMEOUT,
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
        _kill_process_group(proc)
    except BaseException:
        pass

    deadline = time.monotonic() + _REAP_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            proc.wait(timeout=deadline - time.monotonic())
        except subprocess.TimeoutExpired:
            return
        except BaseException:
            continue
        return


def _kill_process_group(proc: subprocess.Popen) -> None:
    if not hasattr(os, "killpg"):
        proc.kill()
        return
    try:
        if not _killpg(proc.pid, signal.SIGTERM):
            return
        time.sleep(_KILL_GRACE_S)
    finally:
        _killpg(proc.pid, signal.SIGKILL)


def _killpg(pgid: int, sig: int) -> bool:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True
