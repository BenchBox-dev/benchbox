from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
from collections.abc import Sequence

FLUSH_TIMEOUT_SECONDS = 30.0
FLUSH_COMMAND: tuple[str, ...] = ("sync",)

_default_logger = logging.getLogger(__name__)


def flush_filesystem(
    timeout_seconds: float | None = None,
    *,
    logger: logging.Logger | None = None,
    command: Sequence[str] | None = None,
) -> bool:
    log = logger or _default_logger
    argv = list(command if command is not None else FLUSH_COMMAND)
    limit = FLUSH_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=sys.platform != "win32",
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False

    try:
        process.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            process.kill()
        log.warning(
            "Skipped the filesystem flush: `%s` did not finish within %.0f s, so the filesystem may be stalled. "
            "Generation continues, and timing measurements may include unflushed writes.",
            " ".join(argv),
            limit,
        )
        return False
    return process.returncode == 0
