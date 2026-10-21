from __future__ import annotations

import importlib
import json
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

RECORDED = Path(__file__).parent / "recorded"
FROZEN = Path(__file__).parent / "frozen"


@dataclass(frozen=True)
class Case:
    id: str
    legacy: Callable[[], str]
    fork: Callable[[], str]


def outcome(produce: Callable[[], str]) -> str:
    try:
        return produce()
    except Exception as error:
        return f"<{type(error).__name__}>"


@cache
def _frozen(area: str) -> dict[str, str]:
    return json.loads((FROZEN / f"{area}.json").read_text(encoding="utf-8"))


def frozen_legacy(area: str, case_id: str) -> Callable[[], str]:
    return lambda: _frozen(area)[case_id]


def area_names() -> list[str]:
    package = importlib.import_module(__name__)
    return sorted(module.name for module in pkgutil.iter_modules(package.__path__) if module.name.startswith("area_"))


def area_cases(area: str) -> list[Case]:
    cases = importlib.import_module(f"{__name__}.{area}").cases()
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate case ids in {area}")
    return cases


def differences(area: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for case in area_cases(area):
        legacy, fork = outcome(case.legacy), outcome(case.fork)
        if legacy != fork:
            found[case.id] = {"legacy": legacy, "fork": fork}
    return found


CLASSES = ("equivalent", "legacy-bug", "fork-gap")
METADATA = ("classification", "reason")


def classify(area: str, case_id: str) -> dict[str, str] | None:
    module = importlib.import_module(f"{__name__}.{area}")
    for pattern, kind, reason in getattr(module, "CLASSIFICATIONS", ()):
        if re.fullmatch(pattern, case_id):
            return {"classification": kind, "reason": reason}
    return None


def classified(area: str) -> bool:
    return hasattr(importlib.import_module(f"{__name__}.{area}"), "CLASSIFICATIONS")


def recorded(area: str) -> dict[str, dict[str, str]]:
    path = RECORDED / f"{area}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_recorded(area: str) -> None:
    RECORDED.mkdir(exist_ok=True)
    found = differences(area)
    if classified(area):
        found = {case_id: {**pair, **(classify(area, case_id) or {})} for case_id, pair in found.items()}
    (RECORDED / f"{area}.json").write_text(
        json.dumps(found, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
