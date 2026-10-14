from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ALIAS_REMOVED_IN = "0.6.0"


@dataclass(frozen=True, slots=True)
class DeprecationNotice:
    platform: str
    option: str
    replacement: str
    removed_in: str

    @property
    def message(self) -> str:
        kind = "Option" if self.option.startswith("-") else "Platform option"
        return (
            f"{kind} '{self.option}' for '{self.platform}' is deprecated and will be removed in "
            f"BenchBox {self.removed_in}; use {self.replacement} instead."
        )


@dataclass(slots=True)
class _RunState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    warned: set[tuple[str, str]] = field(default_factory=set)
    notices: list[DeprecationNotice] = field(default_factory=list)
    log: bool = True


_latest_state = _RunState()
_context_state: ContextVar[_RunState | None] = ContextVar("benchbox_deprecation_run_state", default=None)


def _current_state() -> _RunState:
    return _context_state.get() or _latest_state


def begin_run() -> None:
    global _latest_state
    state = _RunState()
    _latest_state = state
    _context_state.set(state)


@contextmanager
def run_scope(*, log: bool = True) -> Iterator[_RunState]:
    state = _RunState(log=log)
    token = _context_state.set(state)
    try:
        yield state
    finally:
        _context_state.reset(token)


def run_notices() -> tuple[DeprecationNotice, ...]:
    state = _current_state()
    with state.lock:
        return tuple(state.notices)


def _platform_key(platform: str) -> str:
    from benchbox.core.platform_manifest import get_all_platform_aliases

    name = platform.lower().strip().split(":", 1)[0]
    return get_all_platform_aliases().get(name, name)


def alias_removal_release(platform: str, alias: str) -> str:
    from benchbox.core.execution_engine import DEPRECATED_OPTION_ALIASES

    key = alias.lower()
    entry = DEPRECATED_OPTION_ALIASES.get((platform.lower(), key)) or DEPRECATED_OPTION_ALIASES.get(
        (_platform_key(platform), key)
    )
    return entry.removed_in if entry is not None else ALIAS_REMOVED_IN


def warn_deprecated_alias(platform: str, alias: str, replacement: str, *, setting: str | None = None) -> None:
    marker = (_platform_key(platform), (setting or alias).lower())
    state = _current_state()
    notice = DeprecationNotice(
        platform=platform,
        option=alias,
        replacement=replacement,
        removed_in=alias_removal_release(platform, alias),
    )
    with state.lock:
        if marker in state.warned:
            return
        state.warned.add(marker)
        state.notices.append(notice)
    if state.log:
        logger.warning(notice.message)


__all__ = [
    "ALIAS_REMOVED_IN",
    "DeprecationNotice",
    "alias_removal_release",
    "begin_run",
    "run_notices",
    "run_scope",
    "warn_deprecated_alias",
]
