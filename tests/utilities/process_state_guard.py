from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable, Iterator

import pytest

IS_LINUX = sys.platform.startswith("linux")
PR_SET_CHILD_SUBREAPER = 36
PR_GET_CHILD_SUBREAPER = 37

LEAK_MESSAGE = (
    "process state guard: the test left the shared test process as a child subreaper. A subreaper adopts "
    "every orphaned descendant, which wedges later tests and reaps processes they own. Run code that calls "
    "become_subreaper (for example the attempts command) in a subprocess, or call release_subreaper "
    "before the test ends."
)


def is_child_subreaper() -> bool:
    if not IS_LINUX:
        return False
    flag = ctypes.c_int(0)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_GET_CHILD_SUBREAPER, ctypes.byref(flag), 0, 0, 0) != 0:
        return False
    return flag.value == 1


def clear_child_subreaper() -> None:
    if IS_LINUX:
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 0, 0, 0, 0)


def subreaper_leak(
    probe: Callable[[], bool] = is_child_subreaper, clear: Callable[[], None] = clear_child_subreaper
) -> str | None:
    if not probe():
        return None
    clear()
    return LEAK_MESSAGE


@pytest.fixture(autouse=True)
def _no_subreaper_leak() -> Iterator[None]:
    yield
    leak = subreaper_leak()
    if leak:
        pytest.fail(leak, pytrace=False)
