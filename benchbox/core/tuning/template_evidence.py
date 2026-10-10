from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from benchbox.core.tuning.capability_registry import resolve_platform_key

REGISTRY_PATH = Path(__file__).resolve().parent / "profiles" / "template_evidence.yaml"

UNMEASURED = "unmeasured"

BENCHMARK_ALIASES = {
    "tpc-h": "tpch",
    "tpc-ds": "tpcds",
    "tpc-di": "tpcdi",
    "star-schema": "ssb",
    "star_schema": "ssb",
}

_REGISTRY: dict[str, Any] | None = None


def load_registry(*, refresh: bool = False) -> dict[str, Any]:
    global _REGISTRY
    if _REGISTRY is None or refresh:
        loaded = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
        _REGISTRY = loaded if isinstance(loaded, dict) else {}
    return _REGISTRY


def normalize_benchmark(benchmark: Any) -> str | None:
    if not benchmark:
        return None
    lowered = str(benchmark).strip().lower()
    if not lowered:
        return None
    return BENCHMARK_ALIASES.get(lowered, lowered)


def evidence_state(platform: Any, benchmark: Any) -> str | None:
    normalized_benchmark = normalize_benchmark(benchmark)
    if not platform or not normalized_benchmark:
        return None
    platforms = load_registry()
    entries = platforms.get(resolve_platform_key(str(platform)))
    if not isinstance(entries, Mapping):
        return None
    entry = entries.get(normalized_benchmark)
    if not isinstance(entry, Mapping):
        return None
    state = entry.get("state")
    return str(state) if state else None


def is_tuned_template_ref(source_file: Any) -> bool:
    if not source_file or not isinstance(source_file, str):
        return False
    base = source_file.rsplit("/", 1)[-1].split(":")[0].lower()
    return base.endswith("_tuned.yaml") and len(base) > len("_tuned.yaml")


def template_cell(source_file: Any, platform: Any, benchmark: Any) -> tuple[str, str] | None:
    if not is_tuned_template_ref(source_file):
        return None
    ref = str(source_file).split(":")[0]
    head, _, stem = ref.rpartition("/")
    stem = stem.lower()
    if stem.endswith("_liquid_tuned.yaml"):
        file_benchmark = stem[: -len("_liquid_tuned.yaml")]
    else:
        file_benchmark = stem[: -len("_tuned.yaml")]
    registry = load_registry()
    dir_platform = resolve_platform_key(head.rsplit("/", 1)[-1]) if head else ""
    if dir_platform in registry:
        platform_key = dir_platform
    elif platform:
        platform_key = resolve_platform_key(str(platform))
    else:
        return None
    benchmark_key = file_benchmark or normalize_benchmark(benchmark) or ""
    if not benchmark_key:
        return None
    return platform_key, benchmark_key


def evidence_state_for_run(
    source_file: Any,
    platform: Any,
    benchmark: Any,
    *,
    tunings_applied: Any,
) -> str | None:
    if not tunings_applied:
        return None
    cell = template_cell(source_file, platform, benchmark)
    if cell is None:
        return None
    return evidence_state(*cell)


def evidence_warning(
    platform: Any,
    benchmark: Any,
    *,
    source_file: Any,
    tunings_applied: Any,
) -> str | None:
    cell = template_cell(source_file, platform, benchmark)
    if cell is None or not tunings_applied:
        return None
    if evidence_state(*cell) != UNMEASURED:
        return None
    return (
        f"Tuned template {cell[0]}/{cell[1]} has no measured benefit: the result shows "
        "the template was applied, not that it is faster than notuning."
    )


__all__ = [
    "BENCHMARK_ALIASES",
    "REGISTRY_PATH",
    "UNMEASURED",
    "evidence_state",
    "evidence_state_for_run",
    "evidence_warning",
    "is_tuned_template_ref",
    "load_registry",
    "normalize_benchmark",
    "template_cell",
]
