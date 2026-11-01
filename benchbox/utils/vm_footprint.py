from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

MACHINE_ENV = "BENCHBOX_HEAVY_LINUX_MACHINE"
DEFAULT_MACHINE = "benchbox-agent"
WRAPPER_NAME = "linux_machine_run.sh"
SHELL_NAMES = frozenset({"bash", "sh", "zsh", "dash"})
VM_ALLOWANCE_GIB_ENV = "BENCHBOX_HEAVY_VM_ALLOWANCE_GIB"
VM_ALLOWANCE_MARGIN = 1.1
VM_PROCESS_NAME = "com.apple.Virtualization.VirtualMachine"
MACHINE_PATH_PARENTS = ("machine-apiserver", "machines")
EXTERNAL_TIMEOUT_SECONDS = 5.0
GIB = 1024**3
NEGATIVE_CACHE_SECONDS = 30.0

ProcessLister = Callable[[], Sequence["VmProcess"]]
OpenPathsReader = Callable[[int], "list[str] | None"]
FootprintReader = Callable[[int], "int | None"]
MemoryReader = Callable[[str], "float | None"]
CmdlineReader = Callable[[int], "Sequence[str] | None"]


@dataclass(frozen=True)
class VmProcess:
    pid: int
    created: float


@dataclass(frozen=True)
class VmReading:
    machine: str
    pids: tuple[int, ...]
    footprint_bytes: int
    allowance_bytes: int | None

    @property
    def over_allowance(self) -> bool:
        return self.allowance_bytes is not None and self.footprint_bytes > self.allowance_bytes


def basename(value: str) -> str:
    return os.path.basename(value.rstrip("/"))


def machine_name_from_env(environ: Mapping[str, str]) -> str:
    return (environ.get(MACHINE_ENV) or "").strip() or DEFAULT_MACHINE


def argv_runs_wrapper(argv: Sequence[str], machine: str) -> bool:
    if not argv or basename(argv[0]) not in SHELL_NAMES:
        return False
    for index, token in enumerate(argv[1:-1], start=1):
        if basename(token) == WRAPPER_NAME:
            return argv[index + 1] == machine
    return False


def parse_lsof_names(text: str) -> list[str]:
    return [line[1:] for line in text.splitlines() if line.startswith("n") and len(line) > 1]


def _run_text(argv: Sequence[str], timeout: float) -> str | None:
    try:
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def read_open_paths(pid: int) -> list[str] | None:
    text = _run_text(["lsof", "-p", str(pid), "-Fn"], EXTERNAL_TIMEOUT_SECONDS)
    return None if text is None else parse_lsof_names(text)


def list_vm_processes() -> list[VmProcess]:
    try:
        import psutil
    except ImportError:
        return []
    uid = os.getuid() if hasattr(os, "getuid") else None
    found: list[VmProcess] = []
    for proc in psutil.process_iter(["pid", "name", "uids", "create_time"]):
        info = proc.info
        if info.get("name") != VM_PROCESS_NAME:
            continue
        uids = info.get("uids")
        if uid is None or uids is None or uids.real != uid or uids.effective != uid:
            continue
        if info.get("pid") is None or info.get("create_time") is None:
            continue
        found.append(VmProcess(int(info["pid"]), float(info["create_time"])))
    return found


def read_footprint(pid: int) -> int | None:
    from benchbox.utils import footprint

    return footprint.pid_footprint_bytes(pid)


def read_configured_gib(machine: str) -> float | None:
    text = _run_text(["container", "machine", "list", "--format", "json"], EXTERNAL_TIMEOUT_SECONDS)
    if text is None:
        return None
    try:
        items = json.loads(text)
    except ValueError:
        return None
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict) or item.get("id") != machine:
            continue
        memory = item.get("memory")
        if isinstance(memory, bool) or not isinstance(memory, (int, float)) or memory <= 0:
            return None
        return memory / GIB
    return None


def read_cmdline(pid: int) -> Sequence[str] | None:
    try:
        import psutil
    except ImportError:
        return None
    try:
        return psutil.Process(pid).cmdline()
    except (OSError, psutil.Error):
        return None


def allowance_override_gib(environ: Mapping[str, str] | None = None) -> float | None:
    env = os.environ if environ is None else environ
    raw = env.get(VM_ALLOWANCE_GIB_ENV)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value == value and value > 0 else None


class VmAttributor:
    def __init__(
        self,
        *,
        process_lister: ProcessLister = list_vm_processes,
        open_paths: OpenPathsReader = read_open_paths,
        footprint_reader: FootprintReader = read_footprint,
        configured_gib: MemoryReader = read_configured_gib,
        cmdline_reader: CmdlineReader = read_cmdline,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._process_lister = process_lister
        self._open_paths = open_paths
        self._footprint = footprint_reader
        self._configured_gib = configured_gib
        self._cmdline = cmdline_reader
        self._mapping: dict[tuple[int, float], str | None] = {}
        self._first_seen: dict[tuple[int, float], float] = {}
        self._clock = clock
        self._memory: dict[str, float | None] = {}

    def _machine_of(self, process: VmProcess) -> str | None:
        key = (process.pid, process.created)
        if key in self._mapping:
            return self._mapping[key]
        paths = self._open_paths(process.pid)
        if paths is None:
            return None
        named = _machine_from_paths(paths)
        now = self._clock()
        first_seen = self._first_seen.setdefault(key, now)
        if named is not None or now - first_seen >= NEGATIVE_CACHE_SECONDS:
            self._mapping[key] = named
        return named

    def machine_pids(self, machine: str) -> tuple[int, ...]:
        pids = []
        for process in self._process_lister():
            named = self._machine_of(process)
            if named == machine:
                pids.append(process.pid)
        return tuple(pids)

    def _allowance_bytes(self, machine: str, environ: Mapping[str, str] | None) -> int | None:
        override = allowance_override_gib(environ)
        if override is not None:
            return int(override * GIB)
        if machine not in self._memory or self._memory[machine] is None:
            try:
                self._memory[machine] = self._configured_gib(machine)
            except Exception:
                self._memory[machine] = None
        configured = self._memory[machine]
        return None if configured is None else int(configured * VM_ALLOWANCE_MARGIN * GIB)

    def read(self, machine: str, environ: Mapping[str, str] | None = None) -> VmReading | None:
        pids = self.machine_pids(machine)
        if not pids:
            return None
        values = [self._footprint(pid) for pid in pids]
        measured = [value for value in values if value is not None]
        if not measured:
            return None
        return VmReading(machine, pids, sum(measured), self._allowance_bytes(machine, environ))

    def tree_runs_wrapper(self, pids: Iterable[int], machine: str) -> bool:
        for pid in pids:
            argv = self._cmdline(pid)
            if argv is not None and argv_runs_wrapper(argv, machine):
                return True
        return False

    def attribute(
        self, tree_pids: Iterable[int], machine: str, environ: Mapping[str, str] | None = None
    ) -> VmReading | None:
        if not self.tree_runs_wrapper(tree_pids, machine):
            return None
        return self.read(machine, environ)


def _machine_from_paths(paths: Iterable[str]) -> str | None:
    names: set[str] = set()
    for path in paths:
        if not path.startswith("/"):
            continue
        parts = PurePosixPath(path).parts
        width = len(MACHINE_PATH_PARENTS)
        for index in range(1, len(parts) - width):
            if parts[index : index + width] == MACHINE_PATH_PARENTS:
                names.add(parts[index + width])
    return next(iter(names)) if len(names) == 1 else None


def vm_footprint_line(reading: VmReading) -> str:
    allowance = (
        f"allowance {reading.allowance_bytes / GIB:.2f} GiB ({VM_ALLOWANCE_GIB_ENV})"
        if reading.allowance_bytes is not None
        else "allowance unknown"
    )
    pids = ",".join(str(pid) for pid in reading.pids)
    return (
        f"VM {reading.machine} footprint {reading.footprint_bytes / GIB:.2f} GiB "
        f"(pid {pids}, {allowance}; not counted in the shared budget)"
    )


def vm_breach_detail(reading: VmReading) -> tuple[str, str] | None:
    if not reading.over_allowance or reading.allowance_bytes is None:
        return None
    return (
        f"{reading.footprint_bytes / GIB:.2f} GiB",
        f"{reading.allowance_bytes / GIB:.2f} GiB VM allowance ({VM_ALLOWANCE_GIB_ENV})",
    )
