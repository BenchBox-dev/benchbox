from __future__ import annotations

import datetime as _dt
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from tests.uat.phases.report import atomic_write_text

GATE_SUMMARY_SCHEMA_VERSION = 1
COMBINED_EVIDENCE_SCHEMA_VERSION = 1

GATE_SUMMARY_FILENAME = "uat_gate_summary.json"
COMPATIBILITY_PRUNED_FILENAME = "compatibility_pruned.jsonl"
CELLS_FILENAME = "cells.jsonl"
MAX_LISTED_CELLS = 10

VERDICT_GREEN = "green"
VERDICT_RED = "red"
VERDICT_DRY_RUN = "dry_run"

EXPECTED_RELEASE_GATE_STAGES = (
    "release-gate-01-native-dataframe",
    "release-gate-02-docker-nonoltp",
    "release-gate-03-docker-oltp",
)


def gate_summary_path(log_dir: Path) -> Path:
    return log_dir / GATE_SUMMARY_FILENAME


@dataclass(frozen=True)
class PhaseAccounting:
    attempted: int = 0
    passed: int = 0
    failed: int = 0
    timed_out: int = 0
    unreachable: int = 0
    startup_failed: int = 0
    died_mid_platform: int = 0
    skipped: int = 0
    compatibility_pruned: int = 0
    early_stop_pruned: int = 0
    registry_pruned: int = 0
    total_defined: int = 0
    unvalidated: int = 0


@dataclass(frozen=True)
class GateSummary:
    config_name: str
    source_commit_sha: str
    source_dirty: bool
    container_engine: str | None
    completed_at: str
    dry_run: bool
    aborted: bool
    abort_phase: str | None
    abort_reason: str | None
    phase_exit_codes: dict[str, int]
    accounting: PhaseAccounting
    unreachable_is_estimated: bool
    validator_clean_rate: float | None
    validator_clean_rate_floor: float | None
    validator_floor_breached: bool | None
    cross_scale_clean_pairs: int | None
    cross_scale_floor: int | None
    cross_scale_floor_breached: bool | None
    verdict: str
    abort_kind: str | None = None
    artifact_digests: dict[str, str | None] | None = None
    distribution_digest: str | None = None
    shard_platforms: list[str] | None = None
    version: int = GATE_SUMMARY_SCHEMA_VERSION

    def to_json_dict(self) -> dict:
        return asdict(self)


def derive_verdict(*, dry_run: bool, aborted: bool, phase_exit_codes: dict[str, int]) -> str:
    if dry_run:
        return VERDICT_DRY_RUN
    if aborted:
        return VERDICT_RED
    if any(code != 0 for code in phase_exit_codes.values()):
        return VERDICT_RED
    return VERDICT_GREEN


def write_gate_summary(log_dir: Path, summary: GateSummary) -> Path:
    path = gate_summary_path(log_dir)
    atomic_write_text(path, json.dumps(summary.to_json_dict(), indent=2, sort_keys=True) + "\n")
    return path


def _dataclass_from_payload(cls: type, payload: dict) -> object:
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in payload.items() if k in known})


def read_gate_summary(path: Path) -> GateSummary:
    payload = json.loads(path.read_text(encoding="utf-8"))
    accounting_payload = payload.get("accounting") or {}
    accounting = _dataclass_from_payload(PhaseAccounting, accounting_payload)
    payload = dict(payload)
    payload["accounting"] = accounting
    known = {f.name for f in fields(GateSummary)}
    return GateSummary(**{k: v for k, v in payload.items() if k in known})


@dataclass(frozen=True)
class CombinedGateEvidence:
    verdict: str
    source_commit_sha: str
    source_dirty: bool
    completed_at: str | None
    generated_at: str
    stage_names: tuple[str, ...]
    stage_verdicts: dict[str, str]
    ordering_violations: tuple[str, ...]
    reasons: tuple[str, ...]
    stage_artifact_digests: dict[str, dict[str, str | None]] | None = None
    distribution_digest: str | None = None
    stage_shard_platforms: dict[str, list[list[str]]] | None = None
    version: int = COMBINED_EVIDENCE_SCHEMA_VERSION

    def to_json_dict(self) -> dict:
        return asdict(self)


def required_cell_shortfalls(summary: GateSummary) -> list[str]:
    accounting = summary.accounting
    planned_pruning = accounting.compatibility_pruned + accounting.registry_pruned + accounting.early_stop_pruned
    shortfalls = {
        "skipped": max(accounting.skipped - planned_pruning, 0),
        "unreachable": accounting.unreachable,
        "startup_failed": accounting.startup_failed,
        "died_mid_platform": accounting.died_mid_platform,
        "early_stop_pruned": accounting.early_stop_pruned,
    }
    reasons = [f"{name}={count}" for name, count in shortfalls.items() if count]
    if accounting.attempted <= 0:
        reasons.append("no cells attempted")
    return reasons


CellKey = tuple[str, str, float]


@dataclass(frozen=True)
class StagePlan:
    cells: frozenset[CellKey]
    pruned: frozenset[CellKey]


@dataclass(frozen=True)
class ShardCells:
    label: str
    platforms: tuple[str, ...] | None
    cells: tuple[CellKey, ...]
    pruned: tuple[CellKey, ...]


def shard_label(summary: GateSummary) -> str:
    if summary.shard_platforms is None:
        return "unsharded"
    return ",".join(summary.shard_platforms)


def _cell_key(payload: dict) -> CellKey:
    return (str(payload["platform"]), str(payload["benchmark"]), float(payload["scale"]))


def read_cell_keys(path: Path) -> tuple[CellKey, ...]:
    keys: list[CellKey] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                keys.append(_cell_key(json.loads(line)))
    return tuple(keys)


def load_stage_plan(config_path: Path) -> StagePlan:
    from tests.uat.config import load_config
    from tests.uat.phases.enumerate import enumerate_cells_with_pruning

    enumeration = enumerate_cells_with_pruning(load_config(config_path))
    return StagePlan(
        cells=frozenset((c.platform, c.benchmark, float(c.scale)) for c in enumeration.cells),
        pruned=frozenset((c.platform, c.benchmark, float(c.scale)) for c in enumeration.compatibility_pruned),
    )


def _format_keys(keys: Iterable[CellKey]) -> str:
    ordered = sorted(keys)
    listed = ", ".join(f"{p}/{b}@{s:g}" for p, b, s in ordered[:MAX_LISTED_CELLS])
    extra = len(ordered) - MAX_LISTED_CELLS
    return f"{listed} (+{extra} more)" if extra > 0 else listed


def shard_union_violations(stage_name: str, shards: Sequence[ShardCells], plan: StagePlan) -> list[str]:
    reasons: list[str] = []
    for kind, planned, select in (
        ("cell", plan.cells, lambda shard: shard.cells),
        ("compatibility-pruned cell", plan.pruned, lambda shard: shard.pruned),
    ):
        owners: dict[CellKey, list[str]] = defaultdict(list)
        for shard in shards:
            for key in select(shard):
                owners[key].append(shard.label)
        duplicated = {key for key, labels in owners.items() if len(labels) > 1}
        missing = planned - owners.keys()
        unplanned = owners.keys() - planned
        if missing:
            reasons.append(
                f"stage {stage_name!r} shards are missing {len(missing)} planned {kind}(s): {_format_keys(missing)}"
            )
        if duplicated:
            reasons.append(
                f"stage {stage_name!r} shards duplicate {len(duplicated)} {kind}(s): {_format_keys(duplicated)}"
            )
        if unplanned:
            reasons.append(
                f"stage {stage_name!r} shards contain {len(unplanned)} {kind}(s) outside the stage plan: "
                f"{_format_keys(unplanned)}"
            )
    for shard in shards:
        if shard.platforms is None:
            continue
        outside = {key for key in (*shard.cells, *shard.pruned) if key[0] not in shard.platforms}
        if outside:
            reasons.append(
                f"stage {stage_name!r} shard [{shard.label}] holds cells for platforms it was not assigned: "
                f"{_format_keys(outside)}"
            )
    return reasons


def _shard_description(summary: GateSummary) -> str:
    if summary.shard_platforms is None:
        return f"stage {summary.config_name!r}"
    return f"stage {summary.config_name!r} shard [{shard_label(summary)}]"


def _stage_verdict(shards: Sequence[GateSummary]) -> str:
    return next((s.verdict for s in shards if s.verdict != VERDICT_GREEN), VERDICT_GREEN)


def _sharded_stage_reasons(shards: Sequence[GateSummary]) -> list[str]:
    stage_name = shards[0].config_name
    if all(s.shard_platforms is None for s in shards):
        if len(shards) > 1:
            return [f"stage {stage_name!r} has {len(shards)} run directories but they are not shard runs"]
        return []
    reasons: list[str] = []
    if any(s.shard_platforms is None for s in shards):
        reasons.append(f"stage {stage_name!r} mixes shard runs with an unsharded run")
        return reasons
    assigned: dict[str, int] = defaultdict(int)
    for shard in shards:
        for platform in shard.shard_platforms or ():
            assigned[platform] += 1
    repeated = sorted(platform for platform, count in assigned.items() if count > 1)
    if repeated:
        reasons.append(f"stage {stage_name!r} assigns platforms to more than one shard: {repeated}")
    floors = {s.cross_scale_floor for s in shards}
    pairs = [s.cross_scale_clean_pairs for s in shards]
    if len(floors) > 1:
        reasons.append(f"stage {stage_name!r} shards disagree on the cross-scale floor: {sorted(map(str, floors))}")
    elif None not in floors and None not in pairs:
        floor = next(iter(floors))
        total = sum(pairs)
        if total < floor:
            reasons.append(
                f"stage {stage_name!r} cross-scale clean pairs {total} across shards are below the floor {floor}"
            )
    return reasons


def _summary_reasons(stage: GateSummary, expected_distribution_digest: str | None) -> list[str]:
    label = _shard_description(stage)
    reasons: list[str] = []
    if stage.dry_run:
        reasons.append(f"{label} is a dry-run summary, not real evidence")
    if stage.verdict != VERDICT_GREEN:
        reasons.append(f"{label} verdict is {stage.verdict!r}, not green")
    if stage.unreachable_is_estimated:
        reasons.append(f"{label} accounting sidecar missing (unreachable_is_estimated=true)")
    if stage.validator_clean_rate_floor is None or stage.cross_scale_floor is None:
        reasons.append(f"{label} floor gates were not configured")
    if stage.source_commit_sha == "unknown":
        reasons.append(
            f"{label} source_commit_sha is 'unknown': "
            "git rev-parse failed to capture provenance — regenerate evidence "
            "from a repo checkout where `git rev-parse HEAD` succeeds"
        )
    shortfalls = required_cell_shortfalls(stage)
    if shortfalls:
        reasons.append(f"{label} did not execute every required cell: {', '.join(shortfalls)}")
    if expected_distribution_digest is not None and stage.distribution_digest != expected_distribution_digest:
        reasons.append(
            f"{label} ran against distribution digest "
            f"{stage.distribution_digest or 'absent'}, expected {expected_distribution_digest}"
        )
    if stage.artifact_digests is None:
        reasons.append(
            f"{label} has no artifact digests: "
            "evidence was produced before artifact-digest provenance was added — "
            "regenerate evidence with the current orchestrator"
        )
    return reasons


def _stage_artifact_digests(stages: Sequence[Sequence[GateSummary]]) -> dict[str, dict[str, str | None]] | None:
    if not all(s.artifact_digests is not None for shards in stages for s in shards):
        return None
    digests: dict[str, dict[str, str | None]] = {}
    for shards in stages:
        merged: dict[str, str | None] = {}
        for shard in shards:
            prefix = "" if len(shards) == 1 else f"{shard_label(shard)}:"
            merged.update({f"{prefix}{key}": value for key, value in shard.artifact_digests.items()})
        digests[shards[0].config_name] = merged
    return digests


def build_combined_evidence(
    stage_shards: Sequence[Sequence[GateSummary]],
    *,
    ordering_violations: Sequence[str],
    generated_at: _dt.datetime,
    expected_distribution_digest: str | None = None,
    shard_violations: Sequence[str] = (),
) -> CombinedGateEvidence:
    reasons: list[str] = []
    if len(stage_shards) != 3:
        reasons.append(f"expected 3 release-gate stages, got {len(stage_shards)}")
    empty_stage = any(not shards for shards in stage_shards)
    if empty_stage:
        reasons.append("a release-gate stage has no run directories")
    stages = [list(shards) for shards in stage_shards if shards]
    all_summaries = [s for shards in stages for s in shards]

    actual_stage_names = tuple(shards[0].config_name for shards in stages)
    if actual_stage_names != EXPECTED_RELEASE_GATE_STAGES:
        reasons.append(
            f"stage config names {list(actual_stage_names)} do not match the expected "
            f"release-gate stage set {list(EXPECTED_RELEASE_GATE_STAGES)}"
        )
    for shards in stages:
        names = {s.config_name for s in shards}
        if len(names) > 1:
            reasons.append(f"stage directories {sorted(names)} are not all from the same stage config")

    shas = {s.source_commit_sha for s in all_summaries}
    source_commit_sha = next(iter(shas)) if len(shas) == 1 else ""
    if len(shas) > 1:
        reasons.append(f"source_commit_sha differs across stages and shards: {sorted(shas)}")

    digests = {s.distribution_digest for s in all_summaries}
    if len(digests) > 1:
        reasons.append(
            f"distribution digest differs across stages and shards: {sorted(d or 'absent' for d in digests)}"
        )

    source_dirty = any(s.source_dirty for s in all_summaries)
    if source_dirty:
        reasons.append("one or more stages captured a dirty source tree (source_dirty=true)")

    for stage in all_summaries:
        reasons.extend(_summary_reasons(stage, expected_distribution_digest))

    for shards in stages:
        reasons.extend(_sharded_stage_reasons(shards))
    reasons.extend(shard_violations)

    if ordering_violations:
        reasons.extend(ordering_violations)

    completed_at_values = sorted(s.completed_at for s in all_summaries if s.completed_at)
    completed_at = completed_at_values[0] if completed_at_values else None

    verdict = VERDICT_GREEN if not reasons else VERDICT_RED

    stage_artifact_digests = _stage_artifact_digests(stages)

    stage_shard_platforms = {
        shards[0].config_name: [list(s.shard_platforms) for s in shards]
        for shards in stages
        if all(s.shard_platforms is not None for s in shards)
    } or None

    return CombinedGateEvidence(
        verdict=verdict,
        source_commit_sha=source_commit_sha,
        source_dirty=source_dirty,
        completed_at=completed_at,
        generated_at=generated_at.isoformat(),
        stage_names=actual_stage_names,
        stage_verdicts={shards[0].config_name: _stage_verdict(shards) for shards in stages},
        ordering_violations=tuple(ordering_violations),
        reasons=tuple(reasons),
        stage_artifact_digests=stage_artifact_digests,
        distribution_digest=expected_distribution_digest if verdict == VERDICT_GREEN else None,
        stage_shard_platforms=stage_shard_platforms,
    )


def write_combined_evidence(path: Path, evidence: CombinedGateEvidence) -> Path:
    atomic_write_text(path, json.dumps(evidence.to_json_dict(), indent=2, sort_keys=True) + "\n")
    return path


def read_combined_evidence(path: Path) -> CombinedGateEvidence:
    payload = json.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(CombinedGateEvidence)}
    return CombinedGateEvidence(**{k: v for k, v in payload.items() if k in known})
