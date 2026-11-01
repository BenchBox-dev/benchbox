from __future__ import annotations

import ctypes
import ctypes.util
import importlib.util
import sys
from collections.abc import Collection
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType

import psutil

_MAX_ANCESTRY = 4096
_CHILD_BUFFER_START = 256
_CREATE_TIME_TOLERANCE = 1e-3


def _load_footprint() -> ModuleType:
    if __package__:
        from benchbox.utils import footprint

        return footprint
    path = Path(__file__).with_name("footprint.py")
    spec = importlib.util.spec_from_file_location("benchbox_utils_footprint", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


footprint = _load_footprint()


@dataclass(frozen=True)
class TreeMember:
    pid: int
    ppid: int
    name: str
    created: float
    depth: int
    footprint_bytes: int | None


@dataclass(frozen=True)
class TreeSample:
    root_pid: int
    members: tuple[TreeMember, ...]
    total_bytes: int

    def top(self, limit: int) -> list[TreeMember]:
        measured = [member for member in self.members if member.footprint_bytes is not None]
        measured.sort(key=lambda member: member.footprint_bytes or 0, reverse=True)
        return measured[:limit]


@lru_cache(maxsize=1)
def _libproc() -> ctypes.CDLL | None:
    if sys.platform != "darwin":
        return None
    path = ctypes.util.find_library("proc")
    if not path:
        return None
    try:
        lib = ctypes.CDLL(path, use_errno=True)
        lib.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        lib.proc_listchildpids.restype = ctypes.c_int
    except (OSError, AttributeError):
        return None
    return lib


def _darwin_children(lib: ctypes.CDLL, pid: int) -> list[int]:
    capacity = _CHILD_BUFFER_START
    while True:
        buffer = (ctypes.c_int * capacity)()
        count = lib.proc_listchildpids(pid, buffer, ctypes.sizeof(buffer))
        if count < capacity:
            return [child for child in buffer[: max(count, 0)] if child > 0]
        capacity *= 2


def _children_map() -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for proc in psutil.process_iter(["pid", "ppid"]):
        ppid = proc.info.get("ppid")
        if ppid is not None:
            children.setdefault(ppid, []).append(proc.info["pid"])
    return children


def _identify(pid: int) -> tuple[str, float] | None:
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            return proc.name(), proc.create_time()
    except (psutil.Error, OSError):
        return None


def sample_tree(root_pid: int, exclude: Collection[int] = ()) -> TreeSample | None:
    root = _identify(root_pid)
    if root is None:
        return None
    lib = _libproc()
    children = _children_map() if lib is None else None
    skipped = set(exclude)
    members: list[TreeMember] = []
    total = 0
    frontier = [(root_pid, 0, 0, root)]
    seen = {root_pid}
    while frontier:
        pid, ppid, depth, (name, created) = frontier.pop()
        value = footprint.pid_footprint_bytes(pid)
        members.append(TreeMember(pid, ppid, name, created, depth, value))
        total += value or 0
        kids = _darwin_children(lib, pid) if lib is not None else (children or {}).get(pid, ())
        for child in kids:
            if child in seen or child in skipped:
                continue
            seen.add(child)
            identity = _identify(child)
            if identity is not None:
                frontier.append((child, pid, depth + 1, identity))
    return TreeSample(root_pid, tuple(members), total)


def tree_footprint_bytes(root_pid: int, exclude: Collection[int] = ()) -> int | None:
    sample = sample_tree(root_pid, exclude)
    return None if sample is None else sample.total_bytes


def _same_process(proc: psutil.Process, created: float) -> bool:
    return abs(proc.create_time() - created) <= _CREATE_TIME_TOLERANCE


def verified_descendant(member: TreeMember, root_pid: int, root_created: float) -> psutil.Process | None:
    try:
        proc = psutil.Process(member.pid)
        if not _same_process(proc, member.created):
            return None
        current = proc
        for _ in range(_MAX_ANCESTRY):
            parent_pid = current.ppid()
            if parent_pid == root_pid:
                return proc if _same_process(psutil.Process(parent_pid), root_created) else None
            if parent_pid <= 1:
                return None
            parent = psutil.Process(parent_pid)
            if parent.create_time() > current.create_time() + _CREATE_TIME_TOLERANCE:
                return None
            current = parent
    except (psutil.Error, OSError):
        return None
    return None


def is_gone(pid: int, created: float) -> bool:
    try:
        proc = psutil.Process(pid)
        return not _same_process(proc, created) or proc.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True
    except (psutil.Error, OSError):
        return False
