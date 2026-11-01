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

from benchbox.utils import git_flood as git_flood_module, host_lock, linux_machine, process_tree, resource_limits
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
UNREADABLE_RECORD = "(could not read lock file)"
SENTINEL_LOG_ENV = "BENCHBOX_SENTINEL_LOG"
SENTINEL_LOG_NAME = "sentinel.log"
SENTINEL_PID_NAME = "sentinel.pid"
SENTINEL_PID_ENV = "BENCHBOX_SENTINEL_PID"
SENTINEL_LAUNCHD_ERR_NAME = "sentinel.launchd.err"
LAUNCH_AGENT_LABEL = "dev.benchbox.sentinel"
_IDENTITY = re.compile(r"^(\d+):\d+(?:\.\d+)?$")
AGENTS_DIR_ENV = "BENCHBOX_SENTINEL_AGENTS_DIR"


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
class SentinelState:
    running: bool
    pid: int | None
    pid_path: Path
    installed: bool


@dataclass(frozen=True)
class StatusReading:
    lock_path: Path
    holder: HolderReading | None
    waiters: int
    pressure: HostPressure | None
    budget_gib: float | None
    top: tuple[tuple[int, str, int], ...] | None
    orphans: tuple[Orphan, ...] | None
    sentinel: SentinelState
    git_flood: git_flood_module.GitFlood | None = None
    vm: linux_machine.VmAssessment | None = None


def _holder_age(record: str) -> float | None:
    match = _STARTED.search(record)
    if not match:
        return None
    try:
        started = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").astimezone()
    except ValueError:
        return None
    return max(0.0, (utc_now() - started).total_seconds())


def read_holder_state(lock_path: Path) -> HolderReading | None:
    if not lock_path.is_file():
        return None
    record = host_lock.read_holder(lock_path)
    pid_match = _PID.match(record)
    if not pid_match:
        return HolderReading(record=record, pid=None, live=False, age_seconds=None)
    pid = int(pid_match.group(1))
    live = host_lock.process_is_live(pid, host_lock.holder_owner(lock_path) or "")
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


def _read_cwds(pids: Sequence[int], runner: Runner, started: float) -> dict[int, str]:
    found: dict[int, str] = {}
    for pid in pids:
        if sys.platform.startswith("linux"):
            try:
                found[pid] = os.readlink(f"/proc/{pid}/cwd")
            except OSError:
                pass
            continue
        remaining = EXTERNAL_TIMEOUT_SECONDS - elapsed_seconds(started)
        if remaining <= 0:
            break
        text = runner(["lsof", "-a", "-d", "cwd", "-Fpn", "-p", str(pid)], remaining)
        if text is not None:
            found.update(_parse_lsof_cwd(text))
    return found


def find_orphans(
    runner: Runner = resource_limits.run_text,
    footprint: Callable[[int], int | None] = pid_footprint_bytes,
) -> tuple[Orphan, ...] | None:
    started = mono_time()
    listing = runner(["ps", "-axo", "pid=,ppid=,command="], EXTERNAL_TIMEOUT_SECONDS)
    if listing is None:
        return None
    candidates = _parse_ps(listing)
    cwds = _read_cwds([pid for pid, _ in candidates], runner, started)
    if candidates and not cwds:
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


def _read_git_flood(
    environ: Mapping[str, str] | None, footprint: Callable[[int], int | None]
) -> git_flood_module.GitFlood | None:
    try:
        from benchbox.utils import heavy_sentinel

        worktrees = heavy_sentinel.discover_worktrees()
        if not worktrees:
            return None
        roots = heavy_sentinel._root_variants(worktrees)
        return git_flood_module.evaluate(
            git_flood_module.read_git_processes(roots),
            os.environ if environ is None else environ,
            total_ram=git_flood_module.total_ram_bytes(),
            footprint=footprint,
        )
    except Exception:
        return None


def _read_pressure(runner: Runner, which: Callable[[str], str | None]) -> HostPressure | None:
    try:
        return resource_limits.read_host_pressure(runner=runner, which=which)
    except Exception:
        return None


def sentinel_state_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(SENTINEL_LOG_ENV)
    if override:
        return Path(override).expanduser().parent
    return host_lock.real_home() / ".benchbox"


def sentinel_log_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(SENTINEL_LOG_ENV)
    if override:
        return Path(override).expanduser()
    return sentinel_state_dir(environ) / SENTINEL_LOG_NAME


def sentinel_pid_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(SENTINEL_PID_ENV)
    if override:
        return Path(override).expanduser()
    return host_lock.real_home() / ".benchbox" / SENTINEL_PID_NAME


def sentinel_launchd_err_path() -> Path:
    return host_lock.real_home() / ".benchbox" / SENTINEL_LAUNCHD_ERR_NAME


def launch_agents_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(AGENTS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return host_lock.real_home() / "Library" / "LaunchAgents"


def launch_agent_path(environ: Mapping[str, str] | None = None) -> Path:
    return launch_agents_dir(environ) / f"{LAUNCH_AGENT_LABEL}.plist"


def read_sentinel_pid(pid_path: Path) -> tuple[int, str] | None:
    try:
        identity = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    match = _IDENTITY.match(identity)
    if match is None:
        return None
    return int(match.group(1)), identity


def read_sentinel_state(environ: Mapping[str, str] | None = None) -> SentinelState:
    pid_path = sentinel_pid_path(environ)
    recorded = read_sentinel_pid(pid_path)
    running = recorded is not None and host_lock.process_is_live(recorded[0], recorded[1])
    return SentinelState(
        running=running,
        pid=recorded[0] if running and recorded is not None else None,
        pid_path=pid_path,
        installed=launch_agent_path(environ).is_file(),
    )


def held_tree_pids(
    lock_path: Path, sampler: Callable[[int], process_tree.TreeSample | None] = process_tree.sample_tree
) -> frozenset[int]:
    holder = read_holder_state(lock_path)
    if holder is None or not holder.live or holder.pid is None:
        return frozenset()
    sample = sampler(holder.pid)
    if sample is None:
        return frozenset({holder.pid})
    return frozenset(member.pid for member in sample.members)


def _read_vm(
    environ: Mapping[str, str] | None, runner: Runner, which: Callable[[str], str | None]
) -> linux_machine.VmAssessment | None:
    machine = linux_machine.machine_name(environ)
    state = linux_machine.read_machine_state(machine, runner, which)
    if state is None:
        return None
    try:
        processes = linux_machine.read_machine_processes(machine, state.is_default)
    except Exception:
        return None
    return linux_machine.assess(machine, state, processes, held_tree_pids(host_lock.lock_path(environ)))


def collect(
    environ: Mapping[str, str] | None = None,
    *,
    runner: Runner = resource_limits.run_text,
    which: Callable[[str], str | None] = shutil.which,
    footprint: Callable[[int], int | None] = pid_footprint_bytes,
    top_reader: Callable[[], tuple[tuple[int, str, int], ...] | None] = _read_top,
    pressure_reader: Callable[[], HostPressure | None] | None = None,
    git_flood_reader: Callable[[], git_flood_module.GitFlood | None] | None = None,
    vm_reader: Callable[[], linux_machine.VmAssessment | None] | None = None,
) -> StatusReading:
    path = host_lock.lock_path(environ)
    with ThreadPoolExecutor(max_workers=4) as pool:
        pressure_future = pool.submit(pressure_reader or (lambda: _read_pressure(runner, which)))
        orphan_future = pool.submit(find_orphans, runner, footprint)
        vm_future = pool.submit(vm_reader or (lambda: _read_vm(environ, runner, which)))
        top = top_reader()
        holder = read_holder_state(path)
        waiters = host_lock.live_waiter_count(path)
        pressure = pressure_future.result()
        orphans = orphan_future.result()
        vm = vm_future.result()
    return StatusReading(
        lock_path=path,
        holder=holder,
        waiters=waiters,
        pressure=pressure,
        budget_gib=resource_limits.heavy_memory_budget_gib(environ),
        top=top,
        orphans=orphans,
        sentinel=read_sentinel_state(environ),
        git_flood=(git_flood_reader or (lambda: _read_git_flood(environ, footprint)))(),
        vm=vm,
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
    if holder is not None and holder.record == UNREADABLE_RECORD:
        lines.append("  holder: unmeasured (could not read the lock file)")
    elif holder is None or not holder.record or holder.record.startswith("("):
        lines.append("  holder: free (no holder record)")
    elif holder.live:
        lines.append(f"  holder: HELD for {_age(holder.age_seconds)}")
        lines.append(f"    {holder.record}")
    else:
        lines.append("  holder: free (last record belongs to a process that has exited)")
        lines.append(f"    stale: {holder.record}")
    lines.append(f"  live waiters: {reading.waiters}")
    return lines


def _render_pressure(reading: StatusReading, environ: Mapping[str, str] | None) -> list[str]:
    if reading.pressure is None:
        return ["host readings: unmeasured"]
    policy = resource_limits.admission_policy_from_env(environ)
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


def _render_sentinel(reading: StatusReading) -> list[str]:
    state = reading.sentinel
    if state.running:
        return [f"sentinel: running (pid {state.pid}, pid file {state.pid_path})"]
    installed = "LaunchAgent installed" if state.installed else "LaunchAgent not installed; make heavy-sentinel-install"
    return [f"sentinel: not running ({installed})"]


def _render_vm(reading: StatusReading) -> list[str]:
    vm = reading.vm
    if vm is None:
        return []
    memory = "unmeasured" if vm.state.memory_gib is None else f"{vm.state.memory_gib:.2f} GiB"
    lines: list[str] = []
    if vm.idle:
        lines.append(
            f"WARNING VM running with no lock holder: machine '{vm.machine}' state {vm.state.status}, "
            f"configured memory {memory}"
        )
        lines.append(f"  remedy: container machine stop {vm.machine}")
    else:
        lines.append(f"linux machine '{vm.machine}': {vm.state.status or 'unknown'}, configured memory {memory}")
    lines.extend(
        f"UNGUARDED VM RUN pid {pid} (outside every lock holder's tree; never signalled)" for pid in vm.unguarded
    )
    return lines


def _render_git_flood(reading: StatusReading) -> list[str]:
    flood = reading.git_flood
    if flood is None:
        return ["git processes in BenchBox checkouts: unmeasured"]
    lines = git_flood_module.describe(flood)
    if flood.breached:
        lines.insert(0, f"WARNING git flood: {'; '.join(flood.breaches)} (never signalled)")
    return lines


def render(reading: StatusReading, environ: Mapping[str, str] | None = None) -> str:
    sections = (
        _render_lock(reading),
        _render_pressure(reading, environ),
        _render_sentinel(reading),
        _render_vm(reading),
        _render_git_flood(reading),
        _render_top(reading),
        _render_orphans(reading),
    )
    return "\n".join(line for section in sections for line in section)


def status_command() -> int:
    sys.stdout.write(render(collect()) + "\n")
    sys.stdout.flush()
    return 0
