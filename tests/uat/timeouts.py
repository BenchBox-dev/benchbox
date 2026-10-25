from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Mapping

EXIT_TIMEOUT = 124

_KILL_GRACE_S = 0.2
_REAP_TIMEOUT_S = 1.0


@dataclass(frozen=True)
class TimeoutResult:
    exit_code: int
    timed_out: bool
    elapsed_s: float
    stdout: Any = None
    stderr: Any = None


def run_with_timeout(
    argv: list[str],
    timeout_s: int,
    *,
    stdout=None,
    stderr=None,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> TimeoutResult:
    import time

    start = time.monotonic()
    preexec = os.setsid if sys.platform != "win32" else None

    if timeout_s == 0:
        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(
                argv,
                stdout=stdout,
                stderr=stderr,
                env=env,
                cwd=cwd,
                preexec_fn=preexec,
            )
            out, err = proc.communicate()
            return TimeoutResult(
                exit_code=proc.returncode,
                timed_out=False,
                elapsed_s=time.monotonic() - start,
                stdout=out,
                stderr=err,
            )
        except BaseException:
            if proc is not None:
                _kill_and_reap_process_group(proc)
            raise

    proc = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=stdout,
            stderr=stderr,
            env=env,
            cwd=cwd,
            preexec_fn=preexec,
        )
        out, err = proc.communicate(timeout=timeout_s)
        return TimeoutResult(
            exit_code=proc.returncode,
            timed_out=False,
            elapsed_s=time.monotonic() - start,
            stdout=out,
            stderr=err,
        )
    except subprocess.TimeoutExpired:
        if proc is None:
            raise
        _kill_process_group(proc)
        try:
            out, err = proc.communicate(timeout=_REAP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            out, err = None, None
        return TimeoutResult(
            exit_code=EXIT_TIMEOUT,
            timed_out=True,
            elapsed_s=time.monotonic() - start,
            stdout=out,
            stderr=err,
        )
    except BaseException:
        if proc is not None:
            _kill_and_reap_process_group(proc)
        raise


def _kill_and_reap_process_group(proc: subprocess.Popen) -> None:
    import time

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
    import time

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
