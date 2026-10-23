from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from benchbox.utils.formatting import format_bytes

MIN_FREE_PERCENT_ENV = "BENCHBOX_MIN_FREE_DISK_PERCENT"
DEFAULT_MIN_FREE_PERCENT = 5.0
DEFAULT_TOP_N = 5
DEFAULT_SCAN_BUDGET_SECONDS = 10.0


class LowDiskSpaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Consumer:
    name: str
    size_bytes: int
    complete: bool


@dataclass(frozen=True)
class DiskStatus:
    path: Path
    free_bytes: int
    total_bytes: int
    min_free_percent: float

    @property
    def free_percent(self) -> float:
        if self.total_bytes <= 0:
            return 100.0
        return 100.0 * self.free_bytes / self.total_bytes

    @property
    def ok(self) -> bool:
        return self.free_percent >= self.min_free_percent


def resolve_min_free_percent(value: float | str | None = None, *, env: Mapping[str, str] | None = None) -> float:
    if value is None:
        env_map = os.environ if env is None else env
        value = env_map.get(MIN_FREE_PERCENT_ENV)
    if value is None or str(value).strip() == "":
        return DEFAULT_MIN_FREE_PERCENT
    try:
        percent = float(value)
    except ValueError as exc:
        raise ValueError(f"minimum free disk percent must be a number, got {value!r}") from exc
    if not 0.0 <= percent < 100.0:
        raise ValueError(f"minimum free disk percent must be in [0, 100), got {percent}")
    return percent


def _nearest_existing(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def check_free_space(path: str | Path, min_free_percent: float | None = None) -> DiskStatus:
    threshold = resolve_min_free_percent(min_free_percent)
    target = _nearest_existing(Path(path))
    usage = shutil.disk_usage(target)
    return DiskStatus(path=target, free_bytes=usage.free, total_bytes=usage.total, min_free_percent=threshold)


def _tree_size(path: Path, deadline: float) -> tuple[int, bool]:
    total = 0
    stack = [path]
    while stack:
        if time.monotonic() > deadline:
            return total, False
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total, True


def largest_consumers(
    path: str | Path,
    top_n: int = DEFAULT_TOP_N,
    budget_seconds: float = DEFAULT_SCAN_BUDGET_SECONDS,
) -> list[Consumer]:
    root = _nearest_existing(Path(path))
    if not root.is_dir():
        return []
    deadline = time.monotonic() + budget_seconds
    consumers: list[Consumer] = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    for child in children:
        if child.is_symlink():
            continue
        if child.is_dir():
            size, complete = _tree_size(child, deadline)
        else:
            try:
                size, complete = child.stat().st_size, True
            except OSError:
                continue
        consumers.append(Consumer(child.name, size, complete))
    consumers.sort(key=lambda item: item.size_bytes, reverse=True)
    return consumers[:top_n]


def format_low_disk_message(status: DiskStatus, consumers: list[Consumer]) -> str:
    lines = [
        f"Low disk space on {status.path}: {format_bytes(status.free_bytes)} free of "
        f"{format_bytes(status.total_bytes)} ({status.free_percent:.2f}% free, "
        f"minimum {status.min_free_percent:g}%; free={status.free_bytes} total={status.total_bytes} bytes).",
    ]
    if consumers:
        lines.append("Largest top-level consumers under this path:")
        for item in consumers:
            suffix = "" if item.complete else " (partial, scan time budget reached)"
            lines.append(f"  {format_bytes(item.size_bytes):>12}  {item.name}{suffix}")
    lines.append(
        f"Free space or lower the threshold with {MIN_FREE_PERCENT_ENV} (0 disables the check). "
        "BenchBox does not delete run data automatically."
    )
    return "\n".join(lines)


def ensure_free_space(
    path: str | Path,
    min_free_percent: float | None = None,
    *,
    top_n: int = DEFAULT_TOP_N,
    scan_budget_seconds: float = DEFAULT_SCAN_BUDGET_SECONDS,
) -> DiskStatus:
    status = check_free_space(path, min_free_percent)
    if status.ok:
        return status
    consumers = largest_consumers(status.path, top_n=top_n, budget_seconds=scan_budget_seconds)
    raise LowDiskSpaceError(format_low_disk_message(status, consumers))
