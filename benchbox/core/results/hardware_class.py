from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Final

HARDWARE_TABLE_VERSION: Final = "2026-11-17.1"
SCORE_UNIT: Final = "BenchBox CPU units"
TIER_EDGES: Final[tuple[float, ...]] = (2.5, 10.0, 40.0, 160.0, 640.0)

CLASS_MANAGED: Final = "managed"
CLASS_UNCLASSIFIED: Final = "unclassified"

MANAGED_RUNTIME_TYPES: Final = frozenset({"managed_cloud", "serverless"})
HOST_UNKNOWN_RUNTIME_TYPES: Final = frozenset({"remote_server"})

BASIS_MODEL_AND_COUNT: Final = "model_and_count"
BASIS_COUNT_RESOLVED: Final = "model_count_resolved"
BASIS_ATTESTED_MODEL: Final = "attested_model"
BASIS_ATTESTED_COUNT_RESOLVED: Final = "attested_model_count_resolved"
BASIS_CONTAINER_HOST_VENDOR: Final = "container_host_vendor_fallback"
BASIS_MANAGED: Final = "managed_service"
BASIS_REMOTE_HOST: Final = "remote_host_hardware_unknown"
BASIS_MODEL_MISSING: Final = "cpu_model_missing"
BASIS_MODEL_UNKNOWN: Final = "cpu_model_not_in_table"
BASIS_COUNT_AMBIGUOUS: Final = "cpu_count_missing_tiers_differ"

SPEC_CPU2017_SOURCE: Final = "SPEC CPU2017 SPECrate2017_int_base per copy, SMT results, fetched 2026-11-17"
BLENDER_SOURCE: Final = "Blender Open Data (CC0) Cycles bridge to EPYC 7763 per thread, fetched 2026-11-17"
SOURCE_RETRIEVED: Final = "2026-11-17"

SPEC_NOTICE: Final = (
    "SPEC and SPECrate are registered trademarks of the Standard Performance Evaluation Corporation. "
    "BenchBox CPU units are derived values computed by BenchBox from published SPEC CPU2017 results; "
    "they are not SPEC metrics and are not comparable with any SPEC result. Rows marked est. use a "
    "retail sibling as a stand-in. SPEC CPU2017 retires on 2026-11-17, and the table is not rebased to "
    "SPEC CPU2026 until that suite covers the same processors; results of the two suites must not be mixed."
)
BLENDER_NOTICE: Final = (
    "Apple Silicon factors use Blender Open Data, released under CC0 (https://opendata.blender.org)."
)


@dataclass(frozen=True)
class CpuClassRow:
    label: str
    pattern: re.Pattern[str]
    per_thread_factor: float
    source: str
    stand_in: str | None
    retrieved: str
    estimated: bool
    shipped_counts: tuple[int, ...] | None = None

    def score(self, logical_cpus: int) -> float:
        return round(self.per_thread_factor * logical_cpus, 2)


def _row(
    label: str,
    pattern: str,
    factor: float,
    source: str,
    *,
    stand_in: str | None = None,
    estimated: bool = False,
    shipped_counts: tuple[int, ...] | None = None,
) -> CpuClassRow:
    return CpuClassRow(
        label=label,
        pattern=re.compile(pattern),
        per_thread_factor=factor,
        source=source,
        stand_in=stand_in,
        retrieved=SOURCE_RETRIEVED,
        estimated=estimated,
        shipped_counts=shipped_counts,
    )


CPU_CLASS_ROWS: Final[tuple[CpuClassRow, ...]] = (
    _row("AMD EPYC 7763", r"\bepyc 7763\b", 1.00, SPEC_CPU2017_SOURCE + " (54 results; 3.20 per copy)"),
    _row(
        "Intel Xeon Platinum 8370C",
        r"\bxeon\b.*\bplatinum 8370c\b",
        1.08,
        SPEC_CPU2017_SOURCE,
        stand_in="Intel Xeon Platinum 8380",
        estimated=True,
    ),
    _row(
        "AMD EPYC 9V74",
        r"\bepyc 9v74\b",
        1.31,
        SPEC_CPU2017_SOURCE,
        stand_in="AMD EPYC 9654 / 9634",
        estimated=True,
    ),
    _row(
        "Intel Xeon Platinum 8573C",
        r"\bxeon\b.*\bplatinum 8573c\b",
        1.33,
        SPEC_CPU2017_SOURCE,
        stand_in="Intel Xeon Platinum 8580",
        estimated=True,
    ),
    _row(
        "Intel Xeon 6973P-C",
        r"\bxeon\b.*\b6973p-c\b",
        1.64,
        SPEC_CPU2017_SOURCE,
        stand_in="Intel Xeon 6972P",
        estimated=True,
    ),
    _row(
        "AMD EPYC 9V45",
        r"\bepyc 9v45\b",
        1.68,
        SPEC_CPU2017_SOURCE,
        stand_in="AMD EPYC 9655",
        estimated=True,
    ),
    _row(
        "Apple M4",
        r"^apple m4$",
        2.27,
        BLENDER_SOURCE + " (M4 at 10 threads 22.4 per thread against 10.2 for the 7763 at 4 threads)",
        estimated=True,
        shipped_counts=(8, 10),
    ),
)

APPLE_CONTAINER_HOST_ROW: Final = _row(
    "Apple Silicon container host (M4 factor)",
    r"^$",
    2.27,
    BLENDER_SOURCE,
    stand_in="Apple M4",
    estimated=True,
)


@dataclass(frozen=True)
class HardwareClass:
    hardware_class: str
    tier: str | None
    score: float | None
    basis: str
    table_version: str
    row_label: str | None = None

    @property
    def is_tiered(self) -> bool:
        return self.tier is not None


def tier_for_score(score: float) -> str:
    return f"T{bisect_right(TIER_EDGES, score)}"


def _normalize_model(model: str) -> str:
    cleaned = re.sub(r"\((?:r|tm)\)", "", model.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _match_row(model: str) -> CpuClassRow | None:
    normalized = _normalize_model(model)
    return next((row for row in CPU_CLASS_ROWS if row.pattern.search(normalized)), None)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and value > 0 else None


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _unclassified(basis: str, label: str | None = None) -> HardwareClass:
    return HardwareClass(CLASS_UNCLASSIFIED, None, None, basis, HARDWARE_TABLE_VERSION, label)


def _tiered(score: float, basis: str, label: str) -> HardwareClass:
    tier = tier_for_score(score)
    return HardwareClass(tier, tier, score, basis, HARDWARE_TABLE_VERSION, label)


def _classify_row(row: CpuClassRow, count: int | None, attested: bool) -> HardwareClass:
    if count is not None:
        return _tiered(row.score(count), BASIS_ATTESTED_MODEL if attested else BASIS_MODEL_AND_COUNT, row.label)
    if not row.shipped_counts:
        return _unclassified(BASIS_COUNT_AMBIGUOUS, row.label)
    scores = [row.score(shipped) for shipped in row.shipped_counts]
    tiers = {tier_for_score(score) for score in scores}
    if len(tiers) != 1:
        return _unclassified(BASIS_COUNT_AMBIGUOUS, row.label)
    tier = tiers.pop()
    basis = BASIS_ATTESTED_COUNT_RESOLVED if attested else BASIS_COUNT_RESOLVED
    return HardwareClass(tier, tier, None, basis, HARDWARE_TABLE_VERSION, row.label)


def classify_hardware(
    *,
    cpu_model: Any = None,
    cpu_count: Any = None,
    cpu_vendor: Any = None,
    execution_class: Any = None,
    container_host_os: Any = None,
    runtime_type: Any = None,
    attested_cpu_model: Any = None,
) -> HardwareClass:
    if runtime_type in MANAGED_RUNTIME_TYPES:
        return HardwareClass(CLASS_MANAGED, None, None, BASIS_MANAGED, HARDWARE_TABLE_VERSION)
    if runtime_type in HOST_UNKNOWN_RUNTIME_TYPES:
        return _unclassified(BASIS_REMOTE_HOST)

    count = _positive_int(cpu_count)
    recorded = _text(cpu_model)
    attested = _text(attested_cpu_model)
    model = recorded or attested
    if model is None:
        apple_container = execution_class == "container" and (
            _text(cpu_vendor) == "Apple" or _text(container_host_os) == "macOS"
        )
        if apple_container and count is not None:
            return _tiered(
                APPLE_CONTAINER_HOST_ROW.score(count), BASIS_CONTAINER_HOST_VENDOR, APPLE_CONTAINER_HOST_ROW.label
            )
        return _unclassified(BASIS_MODEL_MISSING)
    row = _match_row(model)
    if row is None:
        return _unclassified(BASIS_MODEL_UNKNOWN)
    return _classify_row(row, count, attested=recorded is None)


def table_rows() -> list[dict[str, Any]]:
    return [
        {
            "cpu": row.label,
            "per_thread_factor": row.per_thread_factor,
            "source": row.source,
            "stand_in": row.stand_in,
            "retrieved": row.retrieved,
            "estimated": row.estimated,
            "shipped_counts": list(row.shipped_counts) if row.shipped_counts else None,
        }
        for row in CPU_CLASS_ROWS
    ]


__all__ = [
    "BASIS_ATTESTED_COUNT_RESOLVED",
    "BASIS_ATTESTED_MODEL",
    "BASIS_COUNT_AMBIGUOUS",
    "BASIS_COUNT_RESOLVED",
    "BASIS_CONTAINER_HOST_VENDOR",
    "BASIS_MANAGED",
    "BASIS_MODEL_AND_COUNT",
    "BASIS_MODEL_MISSING",
    "BASIS_MODEL_UNKNOWN",
    "BASIS_REMOTE_HOST",
    "BLENDER_NOTICE",
    "CLASS_MANAGED",
    "CLASS_UNCLASSIFIED",
    "CPU_CLASS_ROWS",
    "HARDWARE_TABLE_VERSION",
    "HardwareClass",
    "SCORE_UNIT",
    "SPEC_NOTICE",
    "TIER_EDGES",
    "classify_hardware",
    "table_rows",
    "tier_for_score",
]
