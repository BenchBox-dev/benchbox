from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

ALIAS_REMOVED_IN = "0.6.0"

_lock = threading.Lock()
_warned: set[tuple[str, str]] = set()


def begin_run() -> None:
    with _lock:
        _warned.clear()


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
    with _lock:
        if marker in _warned:
            return
        _warned.add(marker)
    kind = "Option" if alias.startswith("-") else "Platform option"
    logger.warning(
        "%s '%s' for '%s' is deprecated and will be removed in BenchBox %s; use %s instead.",
        kind,
        alias,
        platform,
        alias_removal_release(platform, alias),
        replacement,
    )


__all__ = [
    "ALIAS_REMOVED_IN",
    "alias_removal_release",
    "begin_run",
    "warn_deprecated_alias",
]
