from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import psutil

from benchbox.utils.footprint import pid_footprint_bytes

COUNT_ENV = "BENCHBOX_GIT_FLOOD_COUNT"
FRACTION_ENV = "BENCHBOX_GIT_FLOOD_RAM_FRACTION"
DEFAULT_COUNT = 16
DEFAULT_FRACTION = 0.25
TOP_COUNT = 5
GIB = 1024**3


@dataclass(frozen=True)
class GitProc:
    pid: int
    cwd: str
    command: str


@dataclass(frozen=True)
class GitFlood:
    count: int
    total_bytes: int
    measured: int
    top: tuple[tuple[GitProc, int | None], ...]
    count_limit: int
    bytes_limit: int | None
    breaches: tuple[str, ...]

    @property
    def breached(self) -> bool:
        return bool(self.breaches)


def _positive(environ: Mapping[str, str], key: str, default: float) -> float:
    try:
        value = float(environ.get(key, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def thresholds(environ: Mapping[str, str], total_ram: int | None) -> tuple[int, int | None]:
    count = max(1, int(_positive(environ, COUNT_ENV, DEFAULT_COUNT)))
    fraction = _positive(environ, FRACTION_ENV, DEFAULT_FRACTION)
    return count, None if not total_ram else int(total_ram * fraction)


def _under(path: str, roots: Sequence[str]) -> bool:
    normal = os.path.normpath(path)
    return any(normal == root or normal.startswith(root + os.sep) for root in roots)


def read_git_processes(roots: Sequence[str]) -> list[GitProc]:
    uid = os.getuid() if hasattr(os, "getuid") else None
    found: list[GitProc] = []
    for proc in psutil.process_iter(["pid", "name", "uids"]):
        info = proc.info
        if info["name"] != "git" or info["pid"] is None:
            continue
        uids = info["uids"]
        if uid is not None and uids is not None and uids.real != uid:
            continue
        try:
            detail = proc.as_dict(["cwd", "cmdline"], ad_value=None)
        except psutil.NoSuchProcess:
            continue
        cwd = detail["cwd"]
        if cwd and _under(cwd, roots):
            found.append(GitProc(info["pid"], cwd, " ".join(detail["cmdline"] or ("git",))[:200]))
    return found


def total_ram_bytes() -> int | None:
    try:
        return int(psutil.virtual_memory().total)
    except (psutil.Error, OSError):
        return None


def evaluate(
    procs: Sequence[GitProc],
    environ: Mapping[str, str],
    *,
    total_ram: int | None,
    footprint: Callable[[int], int | None] = pid_footprint_bytes,
) -> GitFlood:
    count_limit, bytes_limit = thresholds(environ, total_ram)
    sized = [(proc, footprint(proc.pid)) for proc in procs]
    total = sum(size for _, size in sized if size is not None)
    breaches: list[str] = []
    if len(procs) >= count_limit:
        breaches.append(f"{len(procs)} git processes >= {count_limit}")
    if bytes_limit is not None and total >= bytes_limit:
        breaches.append(f"git footprint {total / GIB:.2f} GiB >= {bytes_limit / GIB:.2f} GiB")
    ranked = sorted(sized, key=lambda item: item[1] or 0, reverse=True)[:TOP_COUNT]
    return GitFlood(
        count=len(procs),
        total_bytes=total,
        measured=sum(1 for _, size in sized if size is not None),
        top=tuple(ranked),
        count_limit=count_limit,
        bytes_limit=bytes_limit,
        breaches=tuple(breaches),
    )


def describe(flood: GitFlood) -> list[str]:
    lines = [
        f"git processes with cwd in a BenchBox checkout: {flood.count}, "
        f"{flood.total_bytes / GIB:.2f} GiB footprint ({flood.measured} measured)"
    ]
    for proc, size in flood.top:
        shown = "unmeasured" if size is None else f"{size / GIB:.2f} GiB"
        lines.append(f"  pid {proc.pid} {shown} cwd {proc.cwd} cmd {proc.command}")
    return lines
