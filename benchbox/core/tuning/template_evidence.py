from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from benchbox.core.results.environment import system_profile_snapshot
from benchbox.core.tuning.capability_registry import resolve_platform_key

REGISTRY_PATH = Path(__file__).resolve().parent / "profiles" / "template_evidence.yaml"

SHIPPED_TEMPLATE_ROOTS = (
    ("benchbox", "core", "tuning", "templates"),
    ("examples", "tunings"),
)

UNMEASURED = "unmeasured"
EVIDENCE_STATES = (
    "unmeasured",
    "measured-benefit",
    "measured-neutral",
    "measured-regression",
)

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


def _memory_scope(execution_environment: Any, system_profile: Any) -> dict[str, Any] | None:
    if isinstance(execution_environment, Mapping):
        container = execution_environment.get("container")
        client_host = execution_environment.get("client_host")
        platform_runtime = execution_environment.get("platform_runtime")
    else:
        container = getattr(execution_environment, "container", None)
        client_host = getattr(execution_environment, "client_host", None)
        platform_runtime = getattr(execution_environment, "platform_runtime", None)
    if container is not None:
        if isinstance(container, Mapping):
            memory_limit = container.get("memory_limit")
        else:
            memory_limit = getattr(container, "memory_limit", None)
        if isinstance(memory_limit, str) and memory_limit.strip():
            return {"basis": "container_limit", "value": memory_limit}
        if isinstance(memory_limit, (int, float)) and not isinstance(memory_limit, bool) and memory_limit > 0:
            return {"basis": "container_limit", "value": memory_limit}
        return None
    if isinstance(platform_runtime, Mapping):
        runtime_type = platform_runtime.get("runtime_type")
    else:
        runtime_type = getattr(platform_runtime, "runtime_type", None)
    if runtime_type not in {"local_process", "dataframe_process"}:
        return None
    if isinstance(client_host, Mapping):
        host_memory = client_host.get("memory_gb")
    else:
        host_memory = getattr(client_host, "memory_gb", None)
    if host_memory is None and system_profile is not None:
        profile = system_profile_snapshot(system_profile)
        host_memory = next(
            (
                profile[key]
                for key in ("memory_gb", "memory_total_gb", "total_memory_gb")
                if profile.get(key) is not None
            ),
            None,
        )
    if isinstance(host_memory, (int, float)) and not isinstance(host_memory, bool) and host_memory > 0:
        return {"basis": "host_total_gb", "value": host_memory}
    return None


def evidence_state(
    platform: Any,
    benchmark: Any,
    *,
    scale_factor: Any = None,
    memory: Any = None,
    engine_version: Any = None,
) -> str | None:
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
    if scale_factor is None or memory is None or not engine_version:
        return UNMEASURED
    measurements = entry.get("measurements")
    if not isinstance(measurements, list):
        return UNMEASURED
    matches = [
        measurement
        for measurement in measurements
        if isinstance(measurement, Mapping)
        and measurement.get("scale_factor") == scale_factor
        and measurement.get("memory") == memory
        and measurement.get("engine_version") == engine_version
    ]
    if len(matches) != 1:
        return UNMEASURED
    state = matches[0].get("state")
    return str(state) if state in EVIDENCE_STATES else UNMEASURED


def is_tuned_template_ref(source_file: Any) -> bool:
    if not source_file or not isinstance(source_file, str):
        return False
    base = source_file.rsplit("/", 1)[-1].split(":")[0].lower()
    return base.endswith("_tuned.yaml") and len(base) > len("_tuned.yaml")


def _is_shipped_template_ref(parts: list[str], platform_key: str, stem: str) -> bool:
    for root in SHIPPED_TEMPLATE_ROOTS:
        tail = (*root, platform_key, stem)
        if len(parts) >= len(tail) and tuple(parts[-len(tail) :]) == tail:
            return True
    return False


def template_cell(source_file: Any, platform: Any, benchmark: Any) -> tuple[str, str] | None:
    if not is_tuned_template_ref(source_file):
        return None
    ref = str(source_file).split(":")[0].replace("\\", "/")
    parts = [part for part in ref.split("/") if part not in ("", ".")]
    if not parts:
        return None
    stem = parts[-1].lower()
    if stem.endswith("_liquid_tuned.yaml"):
        file_benchmark = stem[: -len("_liquid_tuned.yaml")]
    else:
        file_benchmark = stem[: -len("_tuned.yaml")]
    run_platform = resolve_platform_key(str(platform)) if platform else None
    run_benchmark = normalize_benchmark(benchmark) if benchmark else None
    head = parts[:-1]
    dir_platform = resolve_platform_key(head[-1]) if head else ""
    registry = load_registry()
    if dir_platform in registry:
        if run_platform and dir_platform != run_platform:
            return None
        platform_key = dir_platform
    elif run_platform:
        platform_key = run_platform
    else:
        return None
    if run_benchmark and file_benchmark != run_benchmark:
        return None
    benchmark_key = file_benchmark or run_benchmark or ""
    if not benchmark_key:
        return None
    if head and not _is_shipped_template_ref(parts, platform_key, stem):
        return None
    return platform_key, benchmark_key


def evidence_state_for_run(
    source_file: Any,
    platform: Any,
    benchmark: Any,
    *,
    tunings_applied: Any,
    scale_factor: Any = None,
    execution_environment: Any = None,
    system_profile: Any = None,
    engine_version: Any = None,
) -> str | None:
    if not tunings_applied:
        return None
    cell = template_cell(source_file, platform, benchmark)
    if cell is None:
        return None
    return evidence_state(
        *cell,
        scale_factor=scale_factor,
        memory=_memory_scope(execution_environment, system_profile),
        engine_version=engine_version,
    )


def evidence_warning(
    platform: Any,
    benchmark: Any,
    *,
    source_file: Any,
    tunings_applied: Any,
    scale_factor: Any = None,
    execution_environment: Any = None,
    system_profile: Any = None,
    engine_version: Any = None,
) -> str | None:
    if not tunings_applied:
        return None
    cell = template_cell(source_file, platform, benchmark)
    if cell is None:
        return None
    state = evidence_state_for_run(
        source_file,
        platform,
        benchmark,
        tunings_applied=tunings_applied,
        scale_factor=scale_factor,
        execution_environment=execution_environment,
        system_profile=system_profile,
        engine_version=engine_version,
    )
    if state != UNMEASURED:
        return None
    return (
        f"Tuned template {cell[0]}/{cell[1]} has no measured benefit: the result shows "
        "the template was applied, not that it is faster than notuning."
    )


__all__ = [
    "BENCHMARK_ALIASES",
    "EVIDENCE_STATES",
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
