from __future__ import annotations

import datetime as _dt
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from tests.uat.phases.report import atomic_write_text

GATE_SUMMARY_SCHEMA_VERSION = 1
COMBINED_EVIDENCE_SCHEMA_VERSION = 1

GATE_SUMMARY_FILENAME = "uat_gate_summary.json"

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
    version: int = COMBINED_EVIDENCE_SCHEMA_VERSION

    def to_json_dict(self) -> dict:
        return asdict(self)


def build_combined_evidence(
    stage_summaries: Sequence[GateSummary],
    *,
    ordering_violations: Sequence[str],
    generated_at: _dt.datetime,
) -> CombinedGateEvidence:
    reasons: list[str] = []
    if len(stage_summaries) != 3:
        reasons.append(f"expected 3 release-gate stage summaries, got {len(stage_summaries)}")

    actual_stage_names = tuple(s.config_name for s in stage_summaries)
    if actual_stage_names != EXPECTED_RELEASE_GATE_STAGES:
        reasons.append(
            f"stage config names {list(actual_stage_names)} do not match the expected "
            f"release-gate stage set {list(EXPECTED_RELEASE_GATE_STAGES)}"
        )

    shas = {s.source_commit_sha for s in stage_summaries}
    source_commit_sha = next(iter(shas)) if len(shas) == 1 else ""
    if len(shas) > 1:
        reasons.append(f"source_commit_sha differs across stages: {sorted(shas)}")

    source_dirty = any(s.source_dirty for s in stage_summaries)
    if source_dirty:
        reasons.append("one or more stages captured a dirty source tree (source_dirty=true)")

    for stage in stage_summaries:
        if stage.dry_run:
            reasons.append(f"stage {stage.config_name!r} is a dry-run summary, not real evidence")
        if stage.verdict != VERDICT_GREEN:
            reasons.append(f"stage {stage.config_name!r} verdict is {stage.verdict!r}, not green")
        if stage.unreachable_is_estimated:
            reasons.append(f"stage {stage.config_name!r} accounting sidecar missing (unreachable_is_estimated=true)")
        if stage.validator_clean_rate_floor is None or stage.cross_scale_floor is None:
            reasons.append(f"stage {stage.config_name!r} floor gates were not configured")
        if stage.source_commit_sha == "unknown":
            reasons.append(
                f"stage {stage.config_name!r} source_commit_sha is 'unknown': "
                "git rev-parse failed to capture provenance — regenerate evidence "
                "from a repo checkout where `git rev-parse HEAD` succeeds"
            )
        if stage.artifact_digests is None:
            reasons.append(
                f"stage {stage.config_name!r} has no artifact digests: "
                "evidence was produced before artifact-digest provenance was added — "
                "regenerate evidence with the current orchestrator"
            )

    if ordering_violations:
        reasons.extend(ordering_violations)

    completed_at_values = sorted(s.completed_at for s in stage_summaries if s.completed_at)
    completed_at = completed_at_values[0] if completed_at_values else None

    verdict = VERDICT_GREEN if not reasons else VERDICT_RED

    stage_artifact_digests: dict[str, dict[str, str | None]] | None = None
    if all(s.artifact_digests is not None for s in stage_summaries):
        stage_artifact_digests = {s.config_name: dict(s.artifact_digests) for s in stage_summaries}

    return CombinedGateEvidence(
        verdict=verdict,
        source_commit_sha=source_commit_sha,
        source_dirty=source_dirty,
        completed_at=completed_at,
        generated_at=generated_at.isoformat(),
        stage_names=tuple(s.config_name for s in stage_summaries),
        stage_verdicts={s.config_name: s.verdict for s in stage_summaries},
        ordering_violations=tuple(ordering_violations),
        reasons=tuple(reasons),
        stage_artifact_digests=stage_artifact_digests,
    )


def write_combined_evidence(path: Path, evidence: CombinedGateEvidence) -> Path:
    atomic_write_text(path, json.dumps(evidence.to_json_dict(), indent=2, sort_keys=True) + "\n")
    return path


def read_combined_evidence(path: Path) -> CombinedGateEvidence:
    payload = json.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(CombinedGateEvidence)}
    return CombinedGateEvidence(**{k: v for k, v in payload.items() if k in known})
