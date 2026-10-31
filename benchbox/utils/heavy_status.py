from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from benchbox.utils import host_lock, resource_limits
from benchbox.utils.clock import elapsed_seconds, mono_time, utc_now
from benchbox.utils.footprint import pid_footprint_bytes, top_processes_by_footprint
from benchbox.utils.resource_limits import EXTERNAL_TIMEOUT_SECONDS, GIB, HostPressure, Runner

TOP_PROCESS_COUNT = 10
WORKTREE_MARKER = "BenchBox"
SERVER_NAMES = frozenset(
    {"java", "clickhouse", "clickhouse-server", "postgres", "mysqld", "mongod", "redis-server", "questdb", "trino"}
)
_STARTED = re.compile(r"\bstarted:(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_PID = re.compile(r"^pid:(\d+)")
_OWNER = re.compile(r"\bowner:(\d+:\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class HolderReading:
    record: str
    pid: int | None
    live: bool
    age_seconds: float | None


@dataclass(frozen=True)
class Orphan:
    pid: int
    command: str
    footprint_bytes: int | None
    cwd: str


@dataclass(frozen=True)
class StatusReading:
    lock_path: Path
    holder: HolderReading | None
    waiters: int
    pressure: HostPressure | None
    budget_gib: float | None
    top: tuple[tuple[int, str, int], ...] | None
    orphans: tuple[Orphan, ...] | None


def _holder_age(record: str) -> float | None:
    match = _STARTED.search(record)
    if not match:
        return None
    try:
        started = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").astimezone()
    except ValueError:
        return None
    return max(0.0, (utc_now() - started).total_seconds())


def _identity_is_live(pid: int, owner: str | None) -> bool:
    try:
        psutil = host_lock._psutil()
    except ImportError:
        return True
    try:
        if owner is None:
            return bool(psutil.pid_exists(pid))
        return host_lock.process_identity(psutil.Process(pid)) == owner
    except psutil.Error:
        return False


def read_holder_state(lock_path: Path) -> HolderReading | None:
    if not lock_path.is_file():
        return None
    record = host_lock.read_holder(lock_path)
    pid_match = _PID.match(record)
    if not pid_match:
        return HolderReading(record=record, pid=None, live=False, age_seconds=None)
    pid = int(pid_match.group(1))
    owner_match = _OWNER.search(record)
    live = _identity_is_live(pid, owner_match.group(1) if owner_match else None)
    return HolderReading(record=record, pid=pid, live=live, age_seconds=_holder_age(record))


def _command_name(command: str) -> str:
    first = command.split(None, 1)[0] if command.strip() else ""
    return os.path.basename(first)


def _is_server_candidate(command: str) -> bool:
    return _command_name(command) in SERVER_NAMES or "questdb" in command.lower()


def _in_worktree(cwd: str) -> bool:
    return any(part.startswith(WORKTREE_MARKER) for part in Path(cwd).parts)


def _parse_ps(text: str) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    for line in text.splitlines():
        fields = line.split(None, 2)
        if len(fields) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        if int(fields[1]) == 1 and _is_server_candidate(fields[2]):
            candidates.append((int(fields[0]), fields[2]))
    return candidates


def _parse_lsof_cwd(text: str) -> dict[int, str]:
    cwds: dict[int, str] = {}
    pid: int | None = None
    for line in text.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            cwds[pid] = line[1:]
    return cwds


def _read_cwds(pids: Sequence[int], runner: Runner, timeout: float) -> dict[int, str] | None:
    if not pids:
        return {}
    if sys.platform.startswith("linux"):
        found: dict[int, str] = {}
        for pid in pids:
            try:
                found[pid] = os.readlink(f"/proc/{pid}/cwd")
            except OSError:
                continue
        return found
    text = runner(["lsof", "-a", "-d", "cwd", "-Fpn", "-p", ",".join(str(p) for p in pids)], timeout)
    return None if text is None else _parse_lsof_cwd(text)


def find_orphans(
    runner: Runner = resource_limits.run_text,
    footprint: Callable[[int], int | None] = pid_footprint_bytes,
) -> tuple[Orphan, ...] | None:
    started = mono_time()
    listing = runner(["ps", "-axo", "pid=,ppid=,command="], EXTERNAL_TIMEOUT_SECONDS)
    if listing is None:
        return None
    candidates = _parse_ps(listing)
    remaining = max(0.0, EXTERNAL_TIMEOUT_SECONDS - elapsed_seconds(started))
    cwds = _read_cwds([pid for pid, _ in candidates], runner, remaining)
    if cwds is None:
        return None
    return tuple(
        Orphan(pid=pid, command=command, footprint_bytes=footprint(pid), cwd=cwds[pid])
        for pid, command in candidates
        if pid in cwds and _in_worktree(cwds[pid])
    )


def _read_top() -> tuple[tuple[int, str, int], ...] | None:
    try:
        return tuple(top_processes_by_footprint(TOP_PROCESS_COUNT))
    except Exception:
        return None


def _read_pressure(runner: Runner, which: Callable[[str], str | None]) -> HostPressure | None:
    try:
        return resource_limits.read_host_pressure(runner=runner, which=which)
    except Exception:
        return None


def collect(
    environ: Mapping[str, str] | None = None,
    *,
    runner: Runner = resource_limits.run_text,
    which: Callable[[str], str | None] = shutil.which,
    footprint: Callable[[int], int | None] = pid_footprint_bytes,
) -> StatusReading:
    path = host_lock.lock_path(environ)
    with ThreadPoolExecutor(max_workers=3) as pool:
        pressure_future = pool.submit(_read_pressure, runner, which)
        orphan_future = pool.submit(find_orphans, runner, footprint)
        top = _read_top()
        holder = read_holder_state(path)
        waiters = host_lock.live_waiter_count(path)
        pressure = pressure_future.result()
        orphans = orphan_future.result()
    return StatusReading(
        lock_path=path,
        holder=holder,
        waiters=waiters,
        pressure=pressure,
        budget_gib=resource_limits.heavy_memory_budget_gib(environ),
        top=top,
        orphans=orphans,
    )


def _gib(value: int) -> str:
    return f"{value / GIB:.2f} GiB"


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "age unknown"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s"


def _render_lock(reading: StatusReading) -> list[str]:
    holder = reading.holder
    lines = [f"lock file: {reading.lock_path}"]
    if holder is None or not holder.record or holder.record.startswith("("):
        lines.append("  holder: free (no holder record)")
    elif holder.live:
        lines.append(f"  holder: HELD for {_age(holder.age_seconds)}")
        lines.append(f"    {holder.record}")
    else:
        lines.append("  holder: free (last record belongs to a process that has exited)")
        lines.append(f"    stale: {holder.record}")
    lines.append(f"  live waiters: {reading.waiters}")
    return lines


def _render_pressure(reading: StatusReading) -> list[str]:
    if reading.pressure is None:
        return ["host readings: unmeasured"]
    policy = resource_limits.admission_policy_from_env()
    refusals = resource_limits.evaluate_admission(reading.pressure, policy)
    result = resource_limits.AdmissionResult(not refusals, refusals, reading.pressure)
    lines = resource_limits.format_admission_report(result, policy).splitlines()
    budget = "unmeasured" if reading.budget_gib is None else f"{reading.budget_gib:.2f} GiB"
    lines.append(f"  heavy-work memory budget: {budget}")
    return lines


def _render_top(reading: StatusReading) -> list[str]:
    lines = [f"top {TOP_PROCESS_COUNT} processes by physical footprint:"]
    if reading.top is None:
        return [*lines, "  unmeasured"]
    if not reading.top:
        return [*lines, "  none readable"]
    lines.extend(f"  {pid:>7}  {_gib(size):>10}  {name}" for pid, name, size in reading.top)
    return lines


def _render_orphans(reading: StatusReading) -> list[str]:
    lines = ["orphaned servers (ppid 1, cwd in a BenchBox worktree):"]
    if reading.orphans is None:
        return [*lines, "  unmeasured"]
    if not reading.orphans:
        return [*lines, "  none"]
    for orphan in reading.orphans:
        size = "unmeasured" if orphan.footprint_bytes is None else _gib(orphan.footprint_bytes)
        lines.append(f"  ORPHAN pid {orphan.pid}  {size}  cwd {orphan.cwd}")
        lines.append(f"    {orphan.command[:200]}")
    return lines


def render(reading: StatusReading) -> str:
    sections = (
        _render_lock(reading),
        _render_pressure(reading),
        _render_top(reading),
        _render_orphans(reading),
    )
    return "\n".join(line for section in sections for line in section)


def status_command() -> int:
    sys.stdout.write(render(collect()) + "\n")
    sys.stdout.flush()
    return 0
