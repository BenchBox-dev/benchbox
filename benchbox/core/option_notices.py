from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_warned: set[tuple[str, str]] = set()


def begin_run() -> None:
    with _lock:
        _warned.clear()


def warn_option_not_applied(platform: str, option: str, reason: str) -> None:
    marker = (platform.lower(), option.lower())
    with _lock:
        if marker in _warned:
            return
        _warned.add(marker)
    logger.warning("Option '%s' for '%s' has no effect and was not applied: %s", option, platform, reason)


__all__ = [
    "begin_run",
    "warn_option_not_applied",
]
