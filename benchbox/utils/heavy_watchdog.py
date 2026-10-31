from __future__ import annotations

import argparse
import ctypes
import importlib.util
import os
import select
import shutil
import signal
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import IO, TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from benchbox.utils.process_tree import TreeMember, TreeSample

MODE_ENV = "BENCHBOX_HEAVY_WATCHDOG"
KILL_FREE_DISK_GIB_ENV = "BENCHBOX_HEAVY_KILL_FREE_DISK_GIB"
INTERVAL_ENV = "BENCHBOX_HEAVY_WATCHDOG_INTERVAL_SECONDS"
GRACE_ENV = "BENCHBOX_HEAVY_WATCHDOG_GRACE_SECONDS"
MEMORY_BUDGET_GIB_ENV = "BENCHBOX_HEAVY_MEMORY_BUDGET_GIB"
MODES = ("kill", "warn", "off")
DEFAULT_MODE = "kill"
DEFAULT_INTERVAL_SECONDS = 2.0
DEFAULT_GRACE_SECONDS = 10.0
DEFAULT_KILL_FREE_DISK_GIB = 20.0
CRITICAL_PRESSURE_LEVEL = 4
TOP_PROCESS_COUNT = 5
GIB = 1024**3
SIG_TERM = signal.Signals["SIGTERM"]
SIG_KILL = getattr(signal, "SIGKILL", SIG_TERM)
LOG_PREFIX = "[heavy-watchdog]"
FLAG_DESCENDANTS_KILLED = b"K"
FLAG_HOLDER_TERMINATED = b"H"
VM_NOTE = (
    "VMs and containers started through the Apple container runtime are children of launchd, not of the lock "
    "holder, so their footprint is not tree-attributed; VM memory is bounded by machine size and admission"
)


def _load_sibling(name: str) -> ModuleType:
    if __package__:
        from importlib import import_module

        return import_module(f"benchbox.utils.{name}")
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"benchbox_utils_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


process_tree = _load_sibling("process_tree")


def mode_from_env() -> str:
    raw = (os.environ.get(MODE_ENV) or "").strip().lower()
    return raw if raw in MODES else DEFAULT_MODE


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value == value and value >= minimum else default


@dataclass(frozen=True)
class WatchPolicy:
    holder_pid: int
    holder_created: float
    budget_bytes: int
    mode: str = DEFAULT_MODE
    kill_free_disk_gib: float = DEFAULT_KILL_FREE_DISK_GIB
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS
    grace_seconds: float = DEFAULT_GRACE_SECONDS
    lock_path: str | None = None
    lock_inode: int | None = None
    disks: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Breach:
    signal: str
    measured: str
    limit: str


@dataclass(frozen=True)
class DiskFree:
    label: str
    path: str
    free_gib: float | None


def read_pressure_level() -> int | None:
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        value = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        status = libc.sysctlbyname(
            b"kern.memorystatus_vm_pressure_level", ctypes.byref(value), ctypes.byref(size), None, 0
        )
    except (OSError, AttributeError):
        return None
    return int(value.value) if status == 0 else None


def read_disks(roots: Sequence[tuple[str, str]]) -> list[DiskFree]:
    readings: list[DiskFree] = []
    for label, raw in roots:
        path = Path(raw).expanduser()
        if not path.exists():
            path = next((ancestor for ancestor in path.parents if ancestor.exists()), Path("/"))
        try:
            free = shutil.disk_usage(path).free / GIB
        except OSError:
            free = None
        readings.append(DiskFree(label, raw, free))
    return readings


def evaluate_breaches(
    footprint_bytes: int, pressure_level: int | None, disks: Sequence[DiskFree], policy: WatchPolicy
) -> list[Breach]:
    breaches: list[Breach] = []
    if policy.budget_bytes > 0 and footprint_bytes > policy.budget_bytes:
        breaches.append(
            Breach(
                "tree footprint",
                f"{footprint_bytes / GIB:.2f} GiB",
                f"{policy.budget_bytes / GIB:.2f} GiB budget ({MEMORY_BUDGET_GIB_ENV})",
            )
        )
    if pressure_level is not None and pressure_level >= CRITICAL_PRESSURE_LEVEL:
        breaches.append(
            Breach("memory pressure", f"level {pressure_level}", f"below critical level {CRITICAL_PRESSURE_LEVEL}")
        )
    for disk in disks:
        if disk.free_gib is not None and disk.free_gib < policy.kill_free_disk_gib:
            breaches.append(
                Breach(
                    f"free disk ({disk.label} {disk.path})",
                    f"{disk.free_gib:.1f} GiB",
                    f"at least {policy.kill_free_disk_gib:g} GiB ({KILL_FREE_DISK_GIB_ENV})",
                )
            )
    return breaches


class Watchdog:
    def __init__(
        self,
        policy: WatchPolicy,
        *,
        sampler: Callable[..., TreeSample | None] = process_tree.sample_tree,
        pressure_reader: Callable[[], int | None] = read_pressure_level,
        disk_reader: Callable[[Sequence[tuple[str, str]]], list[DiskFree]] = read_disks,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        out: IO[str] | None = None,
        flag_fd: int | None = None,
    ) -> None:
        self.policy = policy
        self._sampler = sampler
        self._pressure_reader = pressure_reader
        self._disk_reader = disk_reader
        self._sleeper = sleeper
        self._clock = clock
        self._out = sys.stderr if out is None else out
        self._flag_fd = flag_fd
        self._exclude = frozenset({os.getpid()})
        self._in_breach = False
        self._inode_warned = False

    def log(self, message: str) -> None:
        try:
            if not self._writable():
                return
            self._out.write(f"{LOG_PREFIX} {message}\n")
            self._out.flush()
        except (BlockingIOError, OSError, ValueError):
            pass

    def _writable(self) -> bool:
        try:
            fd = self._out.fileno()
        except (OSError, ValueError, AttributeError):
            return True
        return bool(select.select([], [fd], [], 0)[1])

    def _flag(self, value: bytes) -> None:
        if self._flag_fd is None:
            return
        try:
            os.write(self._flag_fd, value)
        except OSError:
            pass

    def _holder_gone(self) -> bool:
        return process_tree.is_gone(self.policy.holder_pid, self.policy.holder_created)

    def _check_inode(self) -> None:
        if self.policy.lock_path is None or self.policy.lock_inode is None:
            return
        try:
            current = os.stat(self.policy.lock_path).st_ino
        except OSError:
            current = None
        if current == self.policy.lock_inode:
            self._inode_warned = False
            return
        if not self._inode_warned:
            self._inode_warned = True
            self.log(
                f"WARNING: lock file {self.policy.lock_path} is no longer the file this holder locked "
                f"(inode {self.policy.lock_inode} -> {current}); mutual exclusion may be broken. Not killing anything"
            )

    def _report(self, sample: TreeSample, breaches: Sequence[Breach]) -> None:
        action = "kill" if self.policy.mode == "kill" else "warn only"
        for breach in breaches:
            self.log(f"BREACH {breach.signal}: measured {breach.measured}, limit {breach.limit}; action: {action}")
        self.log(
            f"top {TOP_PROCESS_COUNT} processes in the holder tree by footprint (holder pid {self.policy.holder_pid}):"
        )
        for member in sample.top(TOP_PROCESS_COUNT):
            self.log(f"  pid {member.pid} {member.name or '?'} {(member.footprint_bytes or 0) / GIB:.2f} GiB")
        unmeasured = [m for m in sample.members if m.footprint_bytes is None]
        if unmeasured:
            names = ", ".join(f"{m.pid} {m.name or '?'}" for m in unmeasured)
            self.log(f"footprint unreadable (counted as 0): {names}")
        self.log(f"note: {VM_NOTE}")

    def _signal_descendant(self, member: TreeMember, sig: int) -> None:
        proc = process_tree.verified_descendant(member, self.policy.holder_pid, self.policy.holder_created)
        label = signal.Signals(sig).name
        if proc is None:
            self.log(f"skipped {label} for pid {member.pid} ({member.name}): no longer a descendant of the holder")
            return
        try:
            proc.send_signal(sig)
            self.log(f"sent {label} to pid {member.pid} ({member.name})")
        except (psutil.Error, OSError) as exc:
            self.log(f"could not send {label} to pid {member.pid}: {exc}")

    def _wait_gone(self, members: Sequence[TreeMember], deadline: float | None = None) -> list[TreeMember]:
        limit = self._clock() + self.policy.grace_seconds if deadline is None else deadline
        remaining = list(members)
        while remaining:
            remaining = [m for m in remaining if not process_tree.is_gone(m.pid, m.created)]
            if not remaining or self._clock() >= limit:
                break
            self._sleeper(0.1)
        return remaining

    def _terminate_holder(self) -> None:
        try:
            proc = psutil.Process(self.policy.holder_pid)
            if abs(proc.create_time() - self.policy.holder_created) > 1e-3:
                return
            self._flag(FLAG_HOLDER_TERMINATED)
            proc.send_signal(SIG_TERM)
            self.log(
                f"holder pid {self.policy.holder_pid} is still over budget after its descendants were killed; sent SIGTERM"
            )
            holder = process_tree.TreeMember(self.policy.holder_pid, 0, "holder", self.policy.holder_created, 0, None)
            if self._wait_gone([holder]):
                proc.send_signal(SIG_KILL)
                self.log(f"holder pid {self.policy.holder_pid} ignored SIGTERM for the grace period; sent SIGKILL")
        except (psutil.Error, OSError) as exc:
            self.log(f"could not terminate holder pid {self.policy.holder_pid}: {exc}")

    def _kill_tree(self, sample: TreeSample) -> None:
        levels: dict[int, list[TreeMember]] = {}
        for member in sample.members:
            if member.depth > 0:
                levels.setdefault(member.depth, []).append(member)
        self._flag(FLAG_DESCENDANTS_KILLED)
        deadline = self._clock() + self.policy.grace_seconds
        for depth in sorted(levels, reverse=True):
            level = levels[depth]
            for member in level:
                self._signal_descendant(member, SIG_TERM)
            for member in self._wait_gone(level, deadline):
                self._signal_descendant(member, SIG_KILL)
        after = self._sampler(self.policy.holder_pid, self._exclude)
        if after is not None and self.policy.budget_bytes > 0 and after.total_bytes > self.policy.budget_bytes:
            self._terminate_holder()

    def step(self) -> bool:
        if self._holder_gone():
            return False
        sample = self._sampler(self.policy.holder_pid, self._exclude)
        if sample is None:
            return False
        self._check_inode()
        breaches = evaluate_breaches(
            sample.total_bytes,
            self._pressure_reader(),
            self._disk_reader(self.policy.disks),
            self.policy,
        )
        if not breaches:
            self._in_breach = False
            return True
        if self.policy.mode == "warn":
            if not self._in_breach:
                self._in_breach = True
                self._report(sample, breaches)
            return True
        self._report(sample, breaches)
        self._kill_tree(sample)
        return True

    def run(self) -> None:
        while self.step():
            self._sleeper(self.policy.interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="heavy_watchdog")
    parser.add_argument("--holder-pid", type=int, required=True)
    parser.add_argument("--holder-created", type=float, required=True)
    parser.add_argument("--budget-bytes", type=int, default=0)
    parser.add_argument("--lock-path")
    parser.add_argument("--lock-inode", type=int)
    parser.add_argument("--flag-fd", type=int)
    parser.add_argument("--disk", action="append", default=[], metavar="LABEL=PATH")
    return parser


def policy_from_args(args: argparse.Namespace) -> WatchPolicy:
    disks = tuple(tuple(item.split("=", 1)) for item in args.disk if "=" in item)
    return WatchPolicy(
        holder_pid=args.holder_pid,
        holder_created=args.holder_created,
        budget_bytes=args.budget_bytes,
        mode=mode_from_env(),
        kill_free_disk_gib=_float_env(KILL_FREE_DISK_GIB_ENV, DEFAULT_KILL_FREE_DISK_GIB),
        interval_seconds=_float_env(INTERVAL_ENV, DEFAULT_INTERVAL_SECONDS, minimum=0.05),
        grace_seconds=_float_env(GRACE_ENV, DEFAULT_GRACE_SECONDS),
        lock_path=args.lock_path,
        lock_inode=args.lock_inode,
        disks=disks,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for name in ("SIGINT", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_IGN)
    Watchdog(policy_from_args(args), flag_fd=args.flag_fd).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
