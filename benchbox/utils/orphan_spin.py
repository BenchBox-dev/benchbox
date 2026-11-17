from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from benchbox.utils.heavy_status import SHELL_NAMES

CPU_FRACTION_ENV = "BENCHBOX_ORPHAN_SPIN_CPU_FRACTION"
SECONDS_ENV = "BENCHBOX_ORPHAN_SPIN_SECONDS"
DEFAULT_CPU_FRACTION = 0.5
DEFAULT_SECONDS = 300.0
COMMAND_WIDTH = 200
ACTIVE_NAME = "orphan-spin.json"


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
    report: SpinReport | None = None


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

    def active(self) -> list[SpinReport]:
        return [track.report for track in self._tracks.values() if track.reported and track.report is not None]

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
                track.report = SpinReport(shell, fraction, now - track.busy_since)
                reports.append(track.report)
        return reports, ended


@dataclass(frozen=True)
class ActiveSpin:
    pid: int
    created: float
    name: str
    cwd: str
    command: str
    cpu_fraction: float
    busy_since: float


def write_active(path: Path, spins: Sequence[ActiveSpin]) -> None:
    if not spins:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    scratch.write_text(json.dumps([vars(spin) for spin in spins]), encoding="utf-8")
    os.replace(scratch, path)


def read_active(
    path: Path,
    *,
    processes: Callable[[list[str]], Iterable[Any]] | None = None,
) -> list[ActiveSpin]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        recorded = [ActiveSpin(**item) for item in raw]
    except (OSError, ValueError, TypeError):
        return []
    if not recorded:
        return []
    live = {
        (shell.pid, shell.created)
        for shell in read_orphan_shells(
            sorted({spin.cwd for spin in recorded}), processes=processes or psutil.process_iter
        )
    }
    return [spin for spin in recorded if (spin.pid, spin.created) in live]


def describe_active(spin: ActiveSpin, now: float | None = None) -> list[str]:
    busy = max(0.0, (time.time() if now is None else now) - spin.busy_since)
    return [
        f"  ORPHAN SPIN pid {spin.pid}  {spin.name}  {spin.cpu_fraction:.0%} CPU  busy {busy:.0f}s  cwd {spin.cwd}",
        f"    {spin.command[:COMMAND_WIDTH]}",
    ]
