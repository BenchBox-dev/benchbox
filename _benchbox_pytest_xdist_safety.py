from __future__ import annotations

import os
import sys
from typing import Optional

import pytest

from tests.utilities import session_isolation


def _safe_worker_count() -> int:
    override = os.environ.get("BENCHBOX_MAX_XDIST_WORKERS")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass

    ncpu = os.cpu_count() or 4
    platform_cap = 8 if sys.platform == "darwin" else 4
    cpu_cap = min(platform_cap, max(2, ncpu // 2))

    try:
        import psutil

        avail_mb = psutil.virtual_memory().available / (1024 * 1024)
        worker_mb = 200
        mem_cap = max(2, int(avail_mb / worker_mb) - 1)
    except (ImportError, Exception):
        mem_cap = cpu_cap

    return min(cpu_cap, mem_cap)


def _requested_numprocesses(raw: str) -> Optional[int]:
    if raw in {"auto", "logical"}:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _rewrite_numprocesses_args(args: list[str], safe: int) -> str | None:
    candidate: tuple[int, int, str, bool] | None = None

    for idx, arg in enumerate(args):
        value: str | None = None
        value_idx: int | None = None
        compact = False

        if arg in {"-n", "--numprocesses"} and idx + 1 < len(args):
            value = args[idx + 1]
            value_idx = idx + 1
        elif arg.startswith("--numprocesses="):
            value = arg.split("=", 1)[1]
            value_idx = idx
        elif arg.startswith("-n") and arg != "-n":
            value = arg[2:]
            value_idx = idx
            compact = True

        if value is not None and value_idx is not None:
            candidate = (idx, value_idx, value, compact)

    if candidate is None:
        return None

    idx, value_idx, value, compact = candidate
    requested = _requested_numprocesses(value)
    if value not in {"auto", "logical"} and requested is not None and requested <= safe:
        return None

    if value_idx == idx and compact:
        args[idx] = f"-n{safe}"
    elif value_idx == idx:
        args[idx] = f"--numprocesses={safe}"
    else:
        args[value_idx] = str(safe)
    return value


def _last_option_value(args: list[str], long_name: str, short_name: str) -> str | None:
    value: str | None = None
    for idx, arg in enumerate(args):
        if arg in {short_name, long_name} and idx + 1 < len(args):
            value = args[idx + 1]
        elif arg.startswith(f"{long_name}="):
            value = arg.split("=", 1)[1]
        elif arg.startswith(short_name) and not arg.startswith("--") and len(arg) > len(short_name):
            value = arg[len(short_name) :]
    return value


def _wants_parallel_lock(args: list[str]) -> bool:
    if os.environ.get("BENCHBOX_SKIP_TEST_LOCK") or os.environ.get("PYTEST_XDIST_WORKER"):
        return False
    value = _last_option_value(args, "--numprocesses", "-n")
    if value is None:
        return False
    return value in {"auto", "logical"} or bool(_requested_numprocesses(value))


def _selects_only_live_tests(args: list[str]) -> bool:
    expression = _last_option_value(args, "--markers-expression", "-m")
    if not expression:
        return False
    try:
        from _pytest.mark.expression import Expression

        compiled = Expression.compile(expression)
        selects_live_only = bool(compiled.evaluate(lambda name, **_: name == "live_integration"))
        selects_unmarked = bool(compiled.evaluate(lambda name, **_: False))
    except Exception:
        return False
    return selects_live_only and not selects_unmarked


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args: list[str]):
    if not _selects_only_live_tests(args) and session_isolation.start(acquire_lock=_wants_parallel_lock(args)):
        early_config.add_cleanup(session_isolation.finish)
    safe = _safe_worker_count()
    requested = _rewrite_numprocesses_args(args, safe)
    if requested is not None:
        os.environ["BENCHBOX_XDIST_CAP_REQUESTED"] = requested
        os.environ["BENCHBOX_XDIST_CAP_EFFECTIVE"] = str(safe)
    return (yield)


def pytest_xdist_auto_num_workers(config) -> int:
    return _safe_worker_count()


FAULTHANDLER_TIMEOUT_REFUSAL = (
    "[benchbox] faulthandler_timeout is not supported here. Its watchdog thread dumps the stack of a test "
    "that is still running Python code without holding the GIL, so it can read a frame while that frame is "
    "being replaced. On CPython 3.12 the watchdog can then spin forever inside the dump: the stack is cut "
    "off mid-dump, the test thread blocks in cancel_dump_traceback_later when the test ends, and the "
    "pytest-xdist worker hangs or dies. Use pytest-timeout (--timeout or @pytest.mark.timeout) instead: "
    "its signal method prints the stacks from the test thread itself."
)


def _faulthandler_timeout(config) -> float:
    try:
        return float(config.getini("faulthandler_timeout") or 0.0)
    except (ValueError, TypeError):
        return 0.0


def pytest_configure(config) -> None:
    if hasattr(config, "workerinput"):
        return

    if _faulthandler_timeout(config) > 0:
        pytest.exit(FAULTHANDLER_TIMEOUT_REFUSAL, returncode=pytest.ExitCode.USAGE_ERROR)

    requested = os.environ.pop("BENCHBOX_XDIST_CAP_REQUESTED", None)
    effective = os.environ.pop("BENCHBOX_XDIST_CAP_EFFECTIVE", None)
    if requested and effective:
        sys.stderr.write(
            f"[benchbox] Capped pytest-xdist workers from {requested} to {effective} "
            f"(set BENCHBOX_MAX_XDIST_WORKERS to override).\n"
        )
        sys.stderr.flush()
