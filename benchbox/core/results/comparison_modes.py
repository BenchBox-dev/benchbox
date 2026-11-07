from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from benchbox.core.results.fetch_mode import (
    LEGACY_FETCH_MODE,
    MIXED_FETCH_MODE,
    SUMMARY_FETCH_MODES,
    summarize_fetch_modes,
)


class ModeField(NamedTuple):
    name: str
    read: Callable[[Mapping[str, Any]], str | None]
    read_results: Callable[[Any], str | None]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def query_fetch_mode(query: Any) -> Any:
    if isinstance(query, Mapping):
        return query.get("fetch_mode")
    return getattr(query, "fetch_mode", None)


def _query_status(query: Any) -> Any:
    if isinstance(query, Mapping):
        return query.get("status")
    return getattr(query, "status", None)


def queries_fetch_mode(queries: Iterable[Any]) -> str:
    successful = [query_fetch_mode(query) for query in queries if _query_status(query) == "SUCCESS"]
    return summarize_fetch_modes(successful) or LEGACY_FETCH_MODE


def bundle_fetch_mode(bundle: Mapping[str, Any]) -> str:
    recorded = _mapping(bundle.get("run")).get("fetch_mode")
    if isinstance(recorded, str) and recorded in SUMMARY_FETCH_MODES:
        return recorded
    queries = bundle.get("queries")
    return queries_fetch_mode(queries if isinstance(queries, Sequence) else ())


def results_fetch_mode(results: Any) -> str:
    return queries_fetch_mode(getattr(results, "query_results", None) or ())


def stored_fetch_mode(metadata: Mapping[str, Any]) -> str:
    recorded = metadata.get("fetch_mode")
    return recorded if isinstance(recorded, str) and recorded in SUMMARY_FETCH_MODES else LEGACY_FETCH_MODE


RUN_MODE_FIELDS: tuple[ModeField, ...] = (ModeField("fetch mode", bundle_fetch_mode, results_fetch_mode),)


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
    if len(set(recorded)) <= 1 and MIXED_FETCH_MODE not in recorded:
        return None
    if MIXED_FETCH_MODE in recorded:
        return f"{name} is mixed within a run ({', '.join(recorded)})"
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
