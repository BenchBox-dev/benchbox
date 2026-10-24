from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from benchbox.core.results.builder import normalize_benchmark_id
from benchbox.core.tuning.capability_registry import get_capability
from benchbox.core.tuning.interface import TuningType
from benchbox.core.tuning.platform_capabilities import (
    CLUSTERING,
    DATABRICKS_LIQUID_AUTO_RENDERING,
    DATABRICKS_LIQUID_MANUAL_RENDERING,
    DATABRICKS_RENDERINGS,
    DATABRICKS_Z_ORDER_RENDERING,
    DISTRIBUTION,
    MAPPED,
    PARTITIONING,
    SORTING,
    UNSUPPORTED,
    WAIVED,
    PlatformTuningMapping,
    candidate_cap_rank,
    map_candidate_to_platform,
    waiver_is_justified,
)
from benchbox.core.tuning.workload_profiles import (
    ACCEPTED,
    DROPPED_LOW_EVIDENCE,
    WorkloadTuningCandidate,
    WorkloadTuningProfile,
    load_tpc_tuning_profile,
)

TUNING_TYPES = (PARTITIONING, CLUSTERING, DISTRIBUTION, SORTING)
ERROR = "error"


@dataclass(frozen=True)
class TuningProfileValidationIssue:
    severity: str
    candidate_key: str
    message: str
    decision: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "candidate": self.candidate_key,
            "message": self.message,
            "decision": self.decision,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CandidateTemplateMapping:
    candidate: WorkloadTuningCandidate
    platform_mapping: PlatformTuningMapping
    mapped_tuning_types: tuple[str, ...]
    capped_tuning_types: tuple[str, ...] = ()

    @property
    def candidate_key(self) -> str:
        return candidate_key(self.candidate)

    @property
    def mapped(self) -> bool:
        return self.platform_mapping.decision == MAPPED and not self.missing_tuning_types

    @property
    def waived(self) -> bool:
        return self.platform_mapping.decision == WAIVED

    @property
    def capped(self) -> bool:
        missing = set(self.missing_tuning_types)
        return bool(missing) and missing.issubset(self.capped_tuning_types)

    @property
    def missing_tuning_types(self) -> tuple[str, ...]:
        mapped = set(self.mapped_tuning_types)
        return tuple(tuning_type for tuning_type in self.platform_mapping.tuning_types if tuning_type not in mapped)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate_key,
            "status": self.candidate.status,
            "logical_roles": list(self.candidate.roles),
            "decision": self.platform_mapping.decision,
            "mapped_tuning_types": list(self.mapped_tuning_types),
            "capped_tuning_types": list(self.capped_tuning_types),
            "physical_mechanisms": list(self.platform_mapping.physical_mechanisms),
            "reason": self.platform_mapping.reason,
        }


@dataclass(frozen=True)
class TuningProfileValidationResult:
    profile_id: str
    profile_version: str
    benchmark: str
    platform: str
    template_hash: str
    physical_rendering_id: str
    mappings: tuple[CandidateTemplateMapping, ...]
    issues: tuple[TuningProfileValidationIssue, ...]
    accepted_count: int
    dropped_low_evidence_count: int

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == ERROR for issue in self.issues)

    @property
    def required_count(self) -> int:
        return len(self.mappings)

    @property
    def mapped_count(self) -> int:
        return sum(1 for mapping in self.mappings if mapping.mapped)

    @property
    def capped_count(self) -> int:
        return sum(1 for mapping in self.mappings if mapping.capped)

    @property
    def unsupported_count(self) -> int:
        return sum(1 for mapping in self.mappings if mapping.platform_mapping.decision == UNSUPPORTED)

    @property
    def waived_count(self) -> int:
        return sum(1 for mapping in self.mappings if mapping.waived)

    @property
    def physical_mechanisms(self) -> tuple[str, ...]:
        mechanisms = {
            mechanism
            for mapping in self.mappings
            if mapping.mapped
            for mechanism in mapping.platform_mapping.physical_mechanisms
        }
        return tuple(sorted(mechanisms))

    @property
    def unmapped_logical_candidates(self) -> tuple[dict[str, Any], ...]:
        unmapped: list[dict[str, Any]] = []
        for mapping in self.mappings:
            if mapping.mapped or mapping.capped or mapping.waived:
                continue
            entry = {
                "candidate": mapping.candidate_key,
                "decision": mapping.platform_mapping.decision,
                "reason": mapping.platform_mapping.reason or "required candidate was not mapped to the template",
            }
            if mapping.missing_tuning_types:
                entry["missing_tuning_types"] = [
                    tuning_type
                    for tuning_type in mapping.missing_tuning_types
                    if tuning_type not in mapping.capped_tuning_types
                ]
            unmapped.append(entry)
        return tuple(unmapped)

    @property
    def waived_logical_candidates(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {"candidate": mapping.candidate_key, "reason": mapping.platform_mapping.reason}
            for mapping in self.mappings
            if mapping.waived
        )

    def to_metadata(self, *, max_unmapped: int = 20) -> dict[str, Any]:
        return {
            "logical_tuning_profile_id": self.profile_id,
            "logical_tuning_profile_version": self.profile_version,
            "tuning_template_hash": self.template_hash,
            "physical_rendering_id": self.physical_rendering_id,
            "platform_physical_tuning_mechanisms": list(self.physical_mechanisms),
            "logical_profile_coverage": {
                "accepted_count": self.accepted_count,
                "required_count": self.required_count,
                "mapped_count": self.mapped_count,
                "capped_count": self.capped_count,
                "unsupported_count": self.unsupported_count,
                "waived_count": self.waived_count,
                "unmapped_count": len(self.unmapped_logical_candidates),
                "dropped_low_evidence_count": self.dropped_low_evidence_count,
            },
            "unmapped_logical_candidates": list(self.unmapped_logical_candidates[:max_unmapped]),
            "waived_logical_candidates": list(self.waived_logical_candidates[:max_unmapped]),
            "validation_status": "passed" if self.is_valid else "failed",
        }


def validate_tuning_template(
    *,
    profile: WorkloadTuningProfile,
    benchmark: str,
    platform: str,
    tuning_config: Any,
) -> TuningProfileValidationResult:
    benchmark_key = _normalize_profile_benchmark(benchmark)
    platform_key = platform.lower().replace("_", "-")
    template_columns = extract_template_columns(tuning_config)
    physical_rendering_id = resolve_physical_rendering_id(platform_key, tuning_config)
    rendering_verified_types = _rendering_verified_tuning_types(platform_key, tuning_config)
    candidates = profile.candidates(benchmark_key)
    accepted_count = sum(1 for candidate in candidates if candidate.status == ACCEPTED)
    dropped_candidates = tuple(candidate for candidate in candidates if candidate.status == DROPPED_LOW_EVIDENCE)

    issues: list[TuningProfileValidationIssue] = []
    issues.extend(_config_validation_issues(platform_key, tuning_config))
    mappings: list[CandidateTemplateMapping] = []

    capped_slots: dict[tuple[str, str], int] = {}
    for candidate in sorted(candidates, key=lambda item: candidate_cap_rank(platform_key, item)):
        if candidate.is_dropped:
            if _candidate_present(template_columns, candidate):
                issues.append(
                    TuningProfileValidationIssue(
                        severity=ERROR,
                        candidate_key=candidate_key(candidate),
                        message="dropped-low-evidence candidate appears in the tuning template",
                        decision=DROPPED_LOW_EVIDENCE,
                        reason=candidate.rationale,
                    )
                )
            continue

        if not candidate.is_required_for_mapping:
            continue

        platform_mapping = map_candidate_to_platform(
            platform_key,
            candidate,
            physical_rendering_id=physical_rendering_id,
        )
        mapped_tuning_types = tuple(
            tuning_type
            for tuning_type in platform_mapping.tuning_types
            if candidate.column in template_columns.get(candidate.table, {}).get(tuning_type, set())
        )
        capped_types: list[str] = []
        if platform_mapping.max_columns is not None:
            capped_scope = platform_mapping.capped_tuning_types
            if capped_scope is None:
                capped_scope = platform_mapping.tuning_types
            for tuning_type in platform_mapping.tuning_types:
                if tuning_type not in capped_scope:
                    if tuning_type in mapped_tuning_types:
                        slot = (candidate.table, tuning_type)
                        capped_slots[slot] = capped_slots.get(slot, 0) + 1
                    continue
                slot = (candidate.table, tuning_type)
                used = capped_slots.get(slot, 0)
                if used >= platform_mapping.max_columns and tuning_type not in mapped_tuning_types:
                    capped_types.append(tuning_type)
                elif tuning_type in mapped_tuning_types:
                    capped_slots[slot] = used + 1
        issues.extend(_unjustified_waiver_issues(platform_key, candidate, platform_mapping))
        mappings.append(
            CandidateTemplateMapping(
                candidate=candidate,
                platform_mapping=platform_mapping,
                mapped_tuning_types=mapped_tuning_types,
                capped_tuning_types=tuple(capped_types),
            )
        )

        missing_tuning_types = tuple(t for t in mappings[-1].missing_tuning_types if t not in capped_types)
        cap_scope = platform_mapping.capped_tuning_types
        if cap_scope is None:
            cap_scope = platform_mapping.tuning_types
        if platform_mapping.max_columns is not None:
            for tuning_type in cap_scope:
                configured = template_columns.get(candidate.table, {}).get(tuning_type, set())
                if len(configured) > platform_mapping.max_columns:
                    issues.append(
                        TuningProfileValidationIssue(
                            severity=ERROR,
                            candidate_key=candidate_key(candidate),
                            message=(
                                f"template configures {len(configured)} {tuning_type} columns on "
                                f"{candidate.table} but {platform_key} allows at most "
                                f"{platform_mapping.max_columns}"
                            ),
                            decision=platform_mapping.decision,
                            reason=platform_mapping.reason,
                        )
                    )
        unrendered_tuning_types = tuple(
            t for t in mappings[-1].mapped_tuning_types if t not in rendering_verified_types
        )
        if unrendered_tuning_types:
            unrendered = ", ".join(unrendered_tuning_types)
            issues.append(
                TuningProfileValidationIssue(
                    severity=ERROR,
                    candidate_key=candidate_key(candidate),
                    message=(
                        f"template maps {unrendered} with no executed rendering on {platform_key}; "
                        "certification requires the configured columns to reach the physical layout"
                    ),
                    decision=platform_mapping.decision,
                    reason=platform_mapping.reason,
                )
            )
        elif platform_mapping.decision == MAPPED and missing_tuning_types:
            expected = ", ".join(missing_tuning_types) or "a mapped tuning type"
            issues.append(
                TuningProfileValidationIssue(
                    severity=ERROR,
                    candidate_key=candidate_key(candidate),
                    message=f"required logical candidate is missing template mapping for {expected}",
                    decision=platform_mapping.decision,
                    reason=platform_mapping.reason,
                )
            )

    issues.extend(_waived_table_issues(platform_key, mappings, template_columns))

    return TuningProfileValidationResult(
        profile_id=profile.id,
        profile_version=profile.version,
        benchmark=benchmark_key,
        platform=platform_key,
        template_hash=hash_tuning_template(tuning_config),
        physical_rendering_id=physical_rendering_id,
        mappings=tuple(mappings),
        issues=tuple(issues),
        accepted_count=accepted_count,
        dropped_low_evidence_count=len(dropped_candidates),
    )


def _unjustified_waiver_issues(
    platform_key: str,
    candidate: WorkloadTuningCandidate,
    platform_mapping: PlatformTuningMapping,
) -> tuple[TuningProfileValidationIssue, ...]:
    if platform_mapping.decision != WAIVED or waiver_is_justified(platform_key, candidate, platform_mapping):
        return ()
    return (
        TuningProfileValidationIssue(
            severity=ERROR,
            candidate_key=candidate_key(candidate),
            message=f"candidate is waived on {platform_key} without a justification the profile supports",
            decision=platform_mapping.decision,
            reason=platform_mapping.reason,
        ),
    )


def _waived_table_issues(
    platform_key: str,
    mappings: list[CandidateTemplateMapping],
    template_columns: dict[str, dict[str, set[str]]],
) -> list[TuningProfileValidationIssue]:
    first_waived: dict[tuple[str, str], CandidateTemplateMapping] = {}
    for mapping in mappings:
        if mapping.waived:
            first_waived.setdefault((mapping.candidate.benchmark, mapping.candidate.table), mapping)
    issues: list[TuningProfileValidationIssue] = []
    for mapping in first_waived.values():
        issues.extend(
            _waiver_violation_issues(platform_key, mapping.candidate, mapping.platform_mapping, template_columns)
        )
    return issues


def _waiver_violation_issues(
    platform_key: str,
    candidate: WorkloadTuningCandidate,
    platform_mapping: PlatformTuningMapping,
    template_columns: dict[str, dict[str, set[str]]],
) -> tuple[TuningProfileValidationIssue, ...]:
    configured = template_columns.get(candidate.table, {})
    return tuple(
        TuningProfileValidationIssue(
            severity=ERROR,
            candidate_key=f"{candidate.benchmark}.{candidate.table}.{column}",
            message=f"template applies {tuning_type} to {candidate.table}.{column}, but {candidate.table} is waived on {platform_key}",
            decision=platform_mapping.decision,
            reason=platform_mapping.reason,
        )
        for tuning_type in platform_mapping.waived_tuning_types
        for column in sorted(configured.get(tuning_type, set()))
    )


def build_tuning_profile_metadata(
    *,
    benchmark: str | None,
    platform: str | None,
    tuning_config: Any | None,
    profile: WorkloadTuningProfile | None = None,
) -> dict[str, Any] | None:
    if not benchmark or not platform or tuning_config is None:
        return None
    if not _get_value(tuning_config, "table_tunings") and (
        platform is None or resolve_physical_rendering_id(platform, tuning_config) != DATABRICKS_LIQUID_AUTO_RENDERING
    ):
        return None

    profile = profile or load_tpc_tuning_profile()
    benchmark_key = _normalize_profile_benchmark(benchmark)
    if not profile.candidates(benchmark_key):
        return None

    return validate_tuning_template(
        profile=profile,
        benchmark=benchmark_key,
        platform=platform,
        tuning_config=tuning_config,
    ).to_metadata()


def extract_template_columns(tuning_config: Any) -> dict[str, dict[str, set[str]]]:
    table_tunings = _get_value(tuning_config, "table_tunings") or {}
    extracted: dict[str, dict[str, set[str]]] = {}

    if not isinstance(table_tunings, Mapping):
        return extracted

    for table_name, table_tuning in table_tunings.items():
        table_key = str(_get_value(table_tuning, "table_name") or table_name).upper()
        by_type: dict[str, set[str]] = {}
        for tuning_type in TUNING_TYPES:
            by_type[tuning_type] = _extract_column_names(_get_value(table_tuning, tuning_type))
        extracted[table_key] = by_type

    return extracted


def resolve_physical_rendering_id(platform: str, tuning_config: Any | None) -> str:
    platform_key = platform.lower().replace("_", "-")
    if platform_key != "databricks":
        return platform_key

    platform_opts = _get_value(tuning_config, "platform_optimizations") if tuning_config is not None else None
    explicit = _get_value(platform_opts, "physical_rendering_id")
    if explicit:
        return str(explicit)

    strategy = str(_get_value(platform_opts, "databricks_clustering_strategy") or "z_order").lower()
    liquid_enabled = bool(_get_value(platform_opts, "liquid_clustering_enabled"))
    liquid_columns = bool(_get_value(platform_opts, "liquid_clustering_columns"))

    if strategy == "liquid_clustering_auto":
        return DATABRICKS_LIQUID_AUTO_RENDERING
    if strategy == "liquid_clustering" or liquid_enabled or liquid_columns:
        return DATABRICKS_LIQUID_MANUAL_RENDERING
    return DATABRICKS_Z_ORDER_RENDERING


def hash_tuning_template(tuning_config: Any) -> str:
    if hasattr(tuning_config, "to_dict"):
        payload = tuning_config.to_dict()
    elif isinstance(tuning_config, Mapping):
        payload = dict(tuning_config)
    else:
        payload = repr(tuning_config)
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def candidate_key(candidate: WorkloadTuningCandidate) -> str:
    return f"{candidate.benchmark}.{candidate.table}.{candidate.column}"


def _normalize_profile_benchmark(benchmark: str) -> str:
    return normalize_benchmark_id(benchmark).replace("-", "_")


def _rendering_verified_tuning_types(platform: str, tuning_config: Any) -> frozenset[str]:
    if platform not in {"bigquery", "redshift", "snowflake"}:
        return frozenset(TUNING_TYPES)
    verified: set[str] = set()
    for tuning_type in TUNING_TYPES:
        capability = get_capability(platform, TuningType(tuning_type))
        if capability is None or capability.rendered_via == "none":
            continue
        if platform == "redshift" and tuning_type == SORTING:
            if _get_value(_get_value(tuning_config, "platform_optimizations"), "sorted_ingestion_mode") != "force":
                continue
        verified.add(tuning_type)
    return frozenset(verified)


def _candidate_present(template_columns: dict[str, dict[str, set[str]]], candidate: WorkloadTuningCandidate) -> bool:
    return any(candidate.column in columns for columns in template_columns.get(candidate.table, {}).values())


def _config_validation_issues(platform: str, tuning_config: Any) -> list[TuningProfileValidationIssue]:
    issues: list[TuningProfileValidationIssue] = []
    if hasattr(tuning_config, "validate_for_platform"):
        for error_message in tuning_config.validate_for_platform(platform):
            issues.append(
                TuningProfileValidationIssue(
                    severity=ERROR,
                    candidate_key="__config__",
                    message=error_message,
                    decision=UNSUPPORTED,
                    reason="effective tuning configuration is internally conflicting for the selected platform",
                )
            )

    if platform != "databricks":
        return issues

    physical_rendering_id = resolve_physical_rendering_id(platform, tuning_config)
    if physical_rendering_id not in DATABRICKS_RENDERINGS:
        issues.append(
            TuningProfileValidationIssue(
                severity=ERROR,
                candidate_key="__config__",
                message=f"unknown Databricks physical_rendering_id: {physical_rendering_id}",
                decision=UNSUPPORTED,
                reason="Databricks TPC profile validation requires a known physical rendering id",
            )
        )
    return issues


def _extract_column_names(raw_columns: Any) -> set[str]:
    if not raw_columns:
        return set()
    names: set[str] = set()
    for column in raw_columns:
        name = _get_value(column, "name")
        if name:
            names.add(str(name).upper())
    return names


def _get_value(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)
