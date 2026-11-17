from __future__ import annotations

import math
import re
from collections.abc import Mapping

from benchbox.utils.heavy_run_events import current_run_id
from benchbox.utils.resource_limits import MEMORY_BUDGET_GIB_ENV, heavy_memory_budget_gib

MIN_ENGINE_CAP_GIB = 1
SPARK_NON_HEAP_ALLOWANCE_GIB = 4.5
DUCKDB_NON_BUFFER_ALLOWANCE_GIB = 3.5
DEFAULT_DUCKDB_MEMORY_LIMIT_GIB = 4

_SIZE_PATTERN = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*(?P<unit>b(?:ytes?)?|[kmgt](?:i?b|[a-z]*bytes?)?)?\s*$",
    re.IGNORECASE,
)
_UNIT_GIB = {"b": 1 / 1024**3, "k": 1 / 1024**2, "m": 1 / 1024, "g": 1.0, "t": 1024.0}


class EngineMemoryBudgetError(ValueError):
    pass


def admitted_budget_gib(environ: Mapping[str, str] | None = None) -> float | None:
    if current_run_id(environ) is None:
        return None
    return heavy_memory_budget_gib(environ)


def parse_size_gib(text: str, *, default_unit: str = "b") -> float:
    match = _SIZE_PATTERN.fullmatch(text)
    if match is None:
        raise EngineMemoryBudgetError(f"cannot read {text!r} as a memory size (use forms such as 3g or 3072m)")
    unit = (match.group("unit") or default_unit)[0].lower()
    return float(match.group("number")) * _UNIT_GIB[unit]


def derived_cap_gib(budget_gib: float, allowance_gib: float, *, engine: str, ceiling_gib: float | None = None) -> int:
    headroom = math.floor(budget_gib - allowance_gib)
    if headroom < MIN_ENGINE_CAP_GIB:
        raise EngineMemoryBudgetError(
            f"{engine} needs about {allowance_gib:g} GiB outside its memory cap plus at least {MIN_ENGINE_CAP_GIB} GiB "
            f"for the cap, but the admitted tree budget is {budget_gib:g} GiB ({MEMORY_BUDGET_GIB_ENV}); "
            f"raise the budget with owner approval or use a smaller host-side workload"
        )
    return headroom if ceiling_gib is None else max(MIN_ENGINE_CAP_GIB, min(headroom, math.floor(ceiling_gib)))


def check_explicit_cap(
    requested_gib: float, budget_gib: float, allowance_gib: float, *, engine: str, option: str
) -> None:
    if requested_gib + allowance_gib > budget_gib:
        raise EngineMemoryBudgetError(
            f"{option}={requested_gib:g} GiB plus the {allowance_gib:g} GiB {engine} uses outside that cap exceeds the "
            f"admitted tree budget of {budget_gib:g} GiB ({MEMORY_BUDGET_GIB_ENV}); the watchdog would stop the run. "
            f"Lower {option} to at most {max(budget_gib - allowance_gib, 0):g} GiB or raise the budget with owner approval"
        )
