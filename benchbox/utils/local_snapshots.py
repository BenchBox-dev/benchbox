from __future__ import annotations

import shutil
import sys
from collections.abc import Callable, Sequence

from benchbox.utils.resource_limits import EXTERNAL_TIMEOUT_SECONDS, Runner, run_text

TIME_MACHINE_PREFIX = "com.apple.TimeMachine."
TIME_MACHINE_SUFFIX = ".local"
HEADER_PREFIX = "Snapshots for"


def parse_snapshot_names(text: str) -> tuple[str, ...]:
    names = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith(HEADER_PREFIX) and " " not in line:
            names.append(line)
    return tuple(names)


def _display_name(name: str) -> str:
    if name.startswith(TIME_MACHINE_PREFIX):
        stem = name[len(TIME_MACHINE_PREFIX) :]
        return stem[: -len(TIME_MACHINE_SUFFIX)] if stem.endswith(TIME_MACHINE_SUFFIX) else stem
    return f"{name} (third-party)"


def format_snapshots(names: Sequence[str] | None) -> str:
    if names is None:
        return "local snapshots on /: unmeasured"
    if not names:
        return "local snapshots on /: none"
    listed = ", ".join(_display_name(name) for name in names)
    return (
        f"local snapshots on /: {len(names)} ({listed}); snapshots can hold deleted data that df still counts as used"
    )


def describe_local_snapshots(
    *,
    runner: Runner = run_text,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
) -> str | None:
    if (sys.platform if platform is None else platform) != "darwin":
        return None
    try:
        tool = which("tmutil")
        if tool is None:
            return None
        text = runner([tool, "listlocalsnapshots", "/"], EXTERNAL_TIMEOUT_SECONDS)
        return format_snapshots(None if text is None else parse_snapshot_names(text))
    except Exception:
        return format_snapshots(None)
