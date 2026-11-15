from __future__ import annotations

import os
import sys

NON_INTERACTIVE_ENV_VAR = "BENCHBOX_NON_INTERACTIVE"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_non_interactive_session(flag: bool = False) -> bool:
    if flag or os.environ.get(NON_INTERACTIVE_ENV_VAR, "").strip().lower() in _TRUTHY:
        return True
    return not (sys.stdin.isatty() and sys.stdout.isatty())
