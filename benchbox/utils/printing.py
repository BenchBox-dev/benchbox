from __future__ import annotations

import contextlib
import io
import sys
from collections.abc import Iterator
from typing import Any, cast

from rich.console import Console

_QUIET: bool = False
_STD_CONSOLE: Console | None = None
_STDERR_CONSOLE: Console | None = None
_SINK_CONSOLE: Console | None = None


def set_quiet(enabled: bool) -> None:
    global _QUIET
    _QUIET = bool(enabled)


def is_quiet() -> bool:
    return _QUIET


def get_console(quiet: bool | None = None, *, stderr: bool = False) -> Console:
    q = _QUIET if quiet is None else bool(quiet)
    if not q:
        if stderr:
            global _STDERR_CONSOLE
            if _STDERR_CONSOLE is None:
                _STDERR_CONSOLE = Console(stderr=True)
            return cast(Console, _STDERR_CONSOLE)

        global _STD_CONSOLE
        if _STD_CONSOLE is None:
            _STD_CONSOLE = Console()
        return cast(Console, _STD_CONSOLE)

    global _SINK_CONSOLE
    if _SINK_CONSOLE is None:
        _SINK_CONSOLE = Console(file=io.StringIO(), stderr=True, force_jupyter=False)
    return cast(Console, _SINK_CONSOLE)


@contextlib.contextmanager
def silence_output(enabled: bool = True) -> Iterator[None]:
    if not enabled:
        yield
        return

    stdout_backup, stderr_backup = sys.stdout, sys.stderr
    try:
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        yield
    finally:
        sys.stdout = stdout_backup
        sys.stderr = stderr_backup


def fatal(msg: Any) -> None:
    get_console(quiet=False, stderr=True).print(msg, markup=False, highlight=False, soft_wrap=True)


def info(msg: str) -> None:
    emit(msg)


def emit(msg: Any = "", *, quiet: bool | None = None, stderr: bool = False) -> None:
    q = _QUIET if quiet is None else bool(quiet)
    if q:
        return
    get_console(quiet=False, stderr=stderr).print(msg)


class QuietConsoleProxy:
    def __getattr__(self, item: str) -> Any:
        return getattr(get_console(), item)

    def __enter__(self) -> Console:
        return get_console().__enter__()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
        return get_console().__exit__(exc_type, exc_val, exc_tb)

    def __repr__(self) -> str:
        mode = "quiet" if is_quiet() else "verbose"
        return f"<QuietConsoleProxy mode={mode}>"


quiet_console = QuietConsoleProxy()


def warn(msg: str) -> None:
    emit(msg)


def error(msg: str) -> None:
    emit(msg, stderr=True)


def debug(msg: str) -> None:
    emit(msg)


def get_quiet_console() -> Console:
    return get_console(quiet=True)
