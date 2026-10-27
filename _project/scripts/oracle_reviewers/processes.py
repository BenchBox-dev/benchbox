from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

IS_LINUX = sys.platform.startswith("linux")
PR_SET_CHILD_SUBREAPER = 36
SETTLE_SECONDS = 2.0
REAP_SETTLE_SECONDS = 5.0
SETTLE_POLLS = 20
ZOMBIE = "Z"


class HarnessLeakError(RuntimeError):
    def __init__(self, survivors: tuple[int, ...], reason: str = "") -> None:
        super().__init__(reason or f"the reviewer left {len(survivors)} process(es) running after it exited")
        self.survivors = survivors


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    pgid: int
    state: str


class OrphanWatch:
    active = False


def become_subreaper() -> bool:
    if not IS_LINUX:
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        return False
    OrphanWatch.active = True
    return True


def release_subreaper() -> None:
    if OrphanWatch.active:
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 0, 0, 0, 0)
        OrphanWatch.active = False


def snapshot() -> list[Process]:
    try:
        probe = subprocess.Popen(
            ["ps", "-A", "-o", "pid=,ppid=,pgid=,stat="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        stdout, _ = probe.communicate()
    except OSError as exc:
        raise HarnessLeakError((), f"the process list could not be read: {exc}") from exc
    if probe.returncode != 0:
        raise HarnessLeakError((), "the process list could not be read")
    rows: list[Process] = []
    for line in stdout.splitlines():
        fields = line.split()
        if len(fields) < 4 or not all(field.isdigit() for field in fields[:3]):
            continue
        pid = int(fields[0])
        if pid == probe.pid:
            continue
        rows.append(Process(pid, int(fields[1]), int(fields[2]), fields[3]))
    return rows


def children() -> frozenset[int]:
    me = os.getpid()
    return frozenset(item.pid for item in snapshot() if item.ppid == me)


def survivors(group: int, parent: int, bystanders: frozenset[int] = frozenset()) -> tuple[int, ...]:
    found = []
    for item in snapshot():
        if item.state.startswith(ZOMBIE) or item.pid == os.getpid() or item.pid in bystanders:
            continue
        if item.pgid == group or (OrphanWatch.active and item.ppid == parent):
            found.append(item.pid)
    return tuple(sorted(found))


def _signal(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


def _reap(pids: frozenset[int]) -> None:
    deadline = time.monotonic() + REAP_SETTLE_SECONDS
    pending = set(pids)
    while pending:
        for pid in sorted(pending):
            try:
                reaped, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pending.discard(pid)
                continue
            if reaped:
                pending.discard(pid)
        if pending:
            if time.monotonic() >= deadline:
                return
            time.sleep(REAP_SETTLE_SECONDS / SETTLE_POLLS)


def confirm_exit(group: int, bystanders: frozenset[int] = frozenset()) -> tuple[int, ...]:
    found: tuple[int, ...] = ()
    for _ in range(SETTLE_POLLS):
        found = survivors(group, os.getpid(), bystanders)
        if not found:
            break
        time.sleep(SETTLE_SECONDS / SETTLE_POLLS)
    else:
        for pid in found:
            _signal(pid)
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if OrphanWatch.active:
        _reap(children() - bystanders)
    return found
