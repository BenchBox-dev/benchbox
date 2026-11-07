from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple

from benchbox.core.results.maintenance_method import LEGACY_MAINTENANCE_METHOD


class ModeField(NamedTuple):
    name: str
    read: Callable[[Mapping[str, Any]], str | None]
    read_results: Callable[[Any], str | None]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def bundle_maintenance_method(bundle: Mapping[str, Any]) -> str | None:
    recorded = _mapping(bundle.get("run")).get("maintenance_method")
    if isinstance(recorded, str) and recorded:
        return recorded
    phase = _mapping(_mapping(bundle.get("phases")).get("maintenance_test"))
    if phase and phase.get("status") != "NOT_RUN":
        return str(phase.get("maintenance_method") or LEGACY_MAINTENANCE_METHOD)
    return None


def results_maintenance_method(results: Any) -> str | None:
    phase = getattr(getattr(results, "execution_phases", None), "maintenance_test", None)
    if phase is None:
        return None
    return phase.maintenance_method or LEGACY_MAINTENANCE_METHOD


RUN_MODE_FIELDS: tuple[ModeField, ...] = (
    ModeField("maintenance method", bundle_maintenance_method, results_maintenance_method),
)


class IncomparableModesError(ValueError):
    def __init__(self, differences: Sequence[str]) -> None:
        self.differences = list(differences)
        super().__init__(
            "Runs were measured under different modes: "
            + "; ".join(self.differences)
            + ". Results are compared and ranked within one mode only."
        )


def _difference(name: str, values: Sequence[str | None]) -> str | None:
    recorded = [value for value in values if value is not None]
    if len(set(recorded)) <= 1:
        return None
    return f"{name} differs ({' vs '.join(recorded)})"


def bundle_mode_differences(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    differences = (_difference(field.name, (field.read(baseline), field.read(current))) for field in RUN_MODE_FIELDS)
    return [difference for difference in differences if difference is not None]


def modes_comparable(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    return not bundle_mode_differences(baseline, current)


def require_comparable_modes(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    differences = bundle_mode_differences(baseline, current)
    if differences:
        raise IncomparableModesError(differences)


def results_mode_differences(results: Sequence[Any]) -> list[str]:
    differences = (
        _difference(field.name, [field.read_results(result) for result in results]) for field in RUN_MODE_FIELDS
    )
    return [difference for difference in differences if difference is not None]
