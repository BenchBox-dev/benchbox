from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

FETCH_MODE_ROWS = "rows"
FETCH_MODE_ARROW = "arrow"
FETCH_MODES = (FETCH_MODE_ROWS, FETCH_MODE_ARROW)
LEGACY_FETCH_MODE = FETCH_MODE_ROWS
MIXED_FETCH_MODE = "mixed"
SUMMARY_FETCH_MODES = (*FETCH_MODES, MIXED_FETCH_MODE)


def parse_fetch_mode(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in FETCH_MODES:
        return value.strip().lower()
    raise ValueError(f"fetch_mode must be one of {list(FETCH_MODES)}, got {value!r}")


FETCH_MODE_ENV = "BENCHBOX_FETCH_MODE"


def env_fetch_mode() -> str | None:
    return os.environ.get(FETCH_MODE_ENV) or None


def resolve_fetch_mode(requested: Any, default: str) -> str:
    requested = requested if requested not in (None, "") else env_fetch_mode()
    if requested is None:
        return default
    return parse_fetch_mode(requested)


def summarize_fetch_modes(modes: Iterable[Any]) -> str | None:
    recorded = {parse_fetch_mode(mode) for mode in modes if mode is not None}
    if not recorded:
        return None
    if len(recorded) == 1:
        return next(iter(recorded))
    return MIXED_FETCH_MODE


FETCH_MODE_PLATFORMS = (
    "duckdb",
    "datafusion",
    "gizmosql",
    "clickhouse",
    "clickhouse-local",
    "spark",
    "pyspark",
    "lakesail",
    "velox",
)
FETCH_MODE_OPTION_HELP = (
    "Result fetch mode, rows or arrow. Arrow receives the full result as Arrow inside the query timer "
    "and builds Python rows only for digests and validation."
)


def register_fetch_mode_specs() -> None:
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionSpec

    spec = PlatformOptionSpec(name="fetch_mode", help=FETCH_MODE_OPTION_HELP, choices=FETCH_MODES)
    for platform in FETCH_MODE_PLATFORMS:
        if "fetch_mode" not in PlatformHookRegistry.list_option_specs(platform):
            PlatformHookRegistry.register_option_specs(platform, spec)
