from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import psutil

from benchbox.utils.heavy_status import SHELL_NAMES

CPU_FRACTION_ENV = "BENCHBOX_ORPHAN_SPIN_CPU_FRACTION"
SECONDS_ENV = "BENCHBOX_ORPHAN_SPIN_SECONDS"
DEFAULT_CPU_FRACTION = 0.5
DEFAULT_SECONDS = 300.0
COMMAND_WIDTH = 200


@dataclass(frozen=True)
class OrphanShell:
    pid: int
    created: float
    name: str
    cwd: str
    command: str
    cpu_seconds: float


@dataclass(frozen=True)
class SpinReport:
    shell: OrphanShell
    cpu_fraction: float
    busy_seconds: float


@dataclass
class _Track:
    sampled_at: float
    cpu_seconds: float
    busy_since: float | None = None
    reported: bool = False


def _positive(environ: Mapping[str, str], key: str, default: float) -> float:
    try:
        value = float(environ.get(key, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _under(path: str, roots: Sequence[str]) -> bool:
    normal = os.path.normpath(path)
    return any(normal == root or normal.startswith(root + os.sep) for root in roots)


def read_orphan_shells(
    roots: Sequence[str],
    *,
    processes: Callable[[list[str]], Iterable[Any]] = psutil.process_iter,
    uid: int | None = None,
) -> list[OrphanShell]:
    if uid is None and hasattr(os, "getuid"):
        uid = os.getuid()
    found: list[OrphanShell] = []
    for proc in processes(["pid", "ppid", "name", "create_time", "uids"]):
        info = proc.info
        if info["pid"] is None or info["create_time"] is None or info["ppid"] != 1 or info["name"] not in SHELL_NAMES:
            continue
        uids = info["uids"]
        if uid is not None and uids is not None and uids.real != uid:
            continue
        try:
            detail = proc.as_dict(["cwd", "cmdline", "cpu_times"], ad_value=None)
        except psutil.NoSuchProcess:
            continue
        cwd, times = detail["cwd"], detail["cpu_times"]
        if not cwd or times is None or not _under(cwd, roots):
            continue
        found.append(
            OrphanShell(
                pid=info["pid"],
                created=info["create_time"],
                name=info["name"],
                cwd=cwd,
                command=" ".join(detail["cmdline"] or (info["name"],))[:COMMAND_WIDTH],
                cpu_seconds=float(times.user + times.system),
            )
        )
    return found


class SpinTracker:
    def __init__(self, environ: Mapping[str, str]) -> None:
        self._cpu_fraction = _positive(environ, CPU_FRACTION_ENV, DEFAULT_CPU_FRACTION)
        self._sustain_seconds = _positive(environ, SECONDS_ENV, DEFAULT_SECONDS)
        self._tracks: dict[tuple[int, float], _Track] = {}

    @property
    def cpu_fraction(self) -> float:
        return self._cpu_fraction

    @property
    def sustain_seconds(self) -> float:
        return self._sustain_seconds

    def observe(self, shells: Sequence[OrphanShell], now: float) -> tuple[list[SpinReport], list[int]]:
        seen = {(shell.pid, shell.created): shell for shell in shells}
        gone = [key for key in self._tracks if key not in seen]
        ended = [key[0] for key in gone if self._tracks[key].reported]
        for key in gone:
            del self._tracks[key]
        reports: list[SpinReport] = []
        for key, shell in seen.items():
            track = self._tracks.get(key)
            if track is None:
                self._tracks[key] = _Track(sampled_at=now, cpu_seconds=shell.cpu_seconds)
                continue
            elapsed = now - track.sampled_at
            if elapsed <= 0:
                continue
            fraction = (shell.cpu_seconds - track.cpu_seconds) / elapsed
            if fraction >= self._cpu_fraction:
                if track.busy_since is None:
                    track.busy_since = track.sampled_at
            else:
                track.busy_since = None
                track.reported = False
            track.sampled_at = now
            track.cpu_seconds = shell.cpu_seconds
            if track.busy_since is not None and not track.reported and now - track.busy_since >= self._sustain_seconds:
                track.reported = True
                reports.append(SpinReport(shell, fraction, now - track.busy_since))
        return reports, ended
