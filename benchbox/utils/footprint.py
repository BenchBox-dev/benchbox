from __future__ import annotations

import ctypes
import ctypes.util
import sys
from functools import lru_cache

_RUSAGE_INFO_V2 = 2
_PROC_ALL_PIDS = 1
_FOOTPRINT_INDEX = 7
_UUID_BYTES = 16
_RUSAGE_WORDS = 48


class _RusageBuffer(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * _UUID_BYTES), ("words", ctypes.c_uint64 * _RUSAGE_WORDS)]


@lru_cache(maxsize=1)
def _libproc() -> ctypes.CDLL | None:
    if sys.platform != "darwin":
        return None
    path = ctypes.util.find_library("proc")
    if not path:
        return None
    try:
        lib = ctypes.CDLL(path, use_errno=True)
        lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        lib.proc_pid_rusage.restype = ctypes.c_int
        lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        lib.proc_listpids.restype = ctypes.c_int
        lib.proc_name.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        lib.proc_name.restype = ctypes.c_int
    except (OSError, AttributeError):
        return None
    return lib


def _psutil():
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def _darwin_footprint(lib: ctypes.CDLL, pid: int) -> int | None:
    buf = _RusageBuffer()
    if lib.proc_pid_rusage(pid, _RUSAGE_INFO_V2, ctypes.byref(buf)) != 0:
        return None
    return int(buf.words[_FOOTPRINT_INDEX])


def _psutil_footprint(pid: int) -> int | None:
    psutil = _psutil()
    if psutil is None:
        return None
    try:
        proc = psutil.Process(pid)
        try:
            return int(proc.memory_full_info().uss)
        except (psutil.AccessDenied, AttributeError, NotImplementedError):
            return int(proc.memory_info().rss)
    except (psutil.Error, OSError):
        return None


def pid_footprint_bytes(pid: int) -> int | None:
    if pid <= 0:
        return None
    lib = _libproc()
    if lib is not None:
        value = _darwin_footprint(lib, pid)
        if value is not None:
            return value
    return _psutil_footprint(pid)


def _darwin_pids(lib: ctypes.CDLL) -> list[int]:
    size = lib.proc_listpids(_PROC_ALL_PIDS, 0, None, 0)
    if size <= 0:
        return []
    count = size // ctypes.sizeof(ctypes.c_int) + 64
    arr = (ctypes.c_int * count)()
    written = lib.proc_listpids(_PROC_ALL_PIDS, 0, arr, ctypes.sizeof(arr))
    if written <= 0:
        return []
    return [p for p in arr[: written // ctypes.sizeof(ctypes.c_int)] if p > 0]


def _darwin_name(lib: ctypes.CDLL, pid: int) -> str:
    name = ctypes.create_string_buffer(256)
    if lib.proc_name(pid, name, len(name)) <= 0:
        return ""
    return name.value.decode("utf-8", "replace")


def top_processes_by_footprint(limit: int) -> list[tuple[int, str, int]]:
    if limit <= 0:
        return []
    rows: list[tuple[int, str, int]] = []
    lib = _libproc()
    if lib is not None:
        for pid in _darwin_pids(lib):
            value = _darwin_footprint(lib, pid)
            if value is not None:
                rows.append((pid, _darwin_name(lib, pid), value))
    else:
        psutil = _psutil()
        if psutil is None:
            return []
        for proc in psutil.process_iter(["pid", "name"]):
            value = _psutil_footprint(proc.info["pid"])
            if value is not None:
                rows.append((proc.info["pid"], proc.info["name"] or "", value))
    rows.sort(key=lambda row: row[2], reverse=True)
    return rows[:limit]
