from __future__ import annotations

from benchbox.core.hooks.platform_hooks import (
    PlatformHookRegistry,
    PlatformOptionError,
    PlatformOptionSpec,
    parse_bool as parse_bool,
)

__all__ = [
    "PlatformHookRegistry",
    "PlatformOptionError",
    "PlatformOptionSpec",
]
