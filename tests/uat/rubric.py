from __future__ import annotations

import csv
import datetime as _dt
import json
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tests.uat.failure_signature import (
    KIND_INFRASTRUCTURE,
    SIGNATURE_VALIDATION_FAILURE,
    classify_failure,
    evidence_from_row,
)

WINDOW_RUNS = 10
INFRASTRUCTURE_RATE_THRESHOLD = 0.20
REPORT_SCHEMA_VERSION = 1

OUTCOME_PASSED = "passed"
OUTCOME_INFRASTRUCTURE = "infrastructure"
OUTCOME_PRODUCT = "product"

TITLE_PREFIX = "UAT envelope:"
RUBRIC_LINK = "uat-rubric"
ENVELOPE_SOURCE_LINK = "tests/uat/compatibility.py"

CELLS_FILENAME = "cells.jsonl"
GATE_SUMMARY_FILENAME = "uat_gate_summary.json"
VALIDATOR_ROLLUP_FILENAME = "validator_rollup.tsv"

CellKey = tuple[str, str, float]
PairKey = tuple[str, str]

_ATTEMPTED_STATUSES = frozenset({"passed", "failed", "timed-out"})


@dataclass(frozen=True)
class CellObservation:
    run_id: str
    completed_at: str
    cell: CellKey
    outcome: str
    signatures: tuple[str, ...] = ()
    retried: bool = False


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    completed_at: str
    verdict: str | None
    observations: tuple[CellObservation, ...]

    @property
    def has_product_signature(self) -> bool:
        return any(obs.outcome == OUTCOME_PRODUCT for obs in self.observations)


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _file_time(path: Path) -> str:
    return _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.timezone.utc).isoformat()


def _validator_error_cells(run_dir: Path) -> frozenset[CellKey]:
    path = run_dir / VALIDATOR_ROLLUP_FILENAME
    if not path.is_file():
        return frozenset()
    cells: set[CellKey] = set()
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("validator_status") == "error":
                try:
                    cells.add((row["platform"], row["benchmark"], float(row["scale"])))
                except (KeyError, TypeError, ValueError):
                    continue
    return frozenset(cells)


def observation_from_row(
    row: Mapping[str, object], *, run_id: str, completed_at: str, validator_errors: Collection[CellKey] = ()
) -> CellObservation | None:
    if str(row.get("status") or "") not in _ATTEMPTED_STATUSES:
        return None
    cell: CellKey = (str(row["platform"]), str(row["benchmark"]), float(str(row["scale"])))
    retry_signature = row.get("retry_signature")
    retried = row.get("retry_outcome") is not None
    failure = classify_failure(evidence_from_row(row))
    signatures: list[str] = []
    if retry_signature:
        signatures.append(str(retry_signature))
    outcome = OUTCOME_PASSED
    if failure is not None:
        signatures.append(failure.signature)
        outcome = OUTCOME_INFRASTRUCTURE if failure.kind == KIND_INFRASTRUCTURE else OUTCOME_PRODUCT
    elif retry_signature:
        outcome = OUTCOME_INFRASTRUCTURE
    if cell in validator_errors:
        signatures.append(SIGNATURE_VALIDATION_FAILURE)
        outcome = OUTCOME_PRODUCT
    return CellObservation(
        run_id=run_id,
        completed_at=completed_at,
        cell=cell,
        outcome=outcome,
        signatures=tuple(dict.fromkeys(signatures)),
        retried=retried,
    )


def load_run(cells_path: Path, history_dir: Path) -> RunRecord | None:
    run_dir = cells_path.parent
    summary = _read_json(run_dir / GATE_SUMMARY_FILENAME) or {}
    if summary.get("dry_run"):
        return None
    run_id = str(run_dir.relative_to(history_dir)) if run_dir != history_dir else run_dir.name
    completed_at = str(summary.get("completed_at") or _file_time(cells_path))
    validator_errors = _validator_error_cells(run_dir)
    observations: list[CellObservation] = []
    with cells_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            observation = observation_from_row(
                json.loads(line), run_id=run_id, completed_at=completed_at, validator_errors=validator_errors
            )
            if observation is not None:
                observations.append(observation)
    verdict = summary.get("verdict")
    return RunRecord(
        run_id=run_id,
        completed_at=completed_at,
        verdict=str(verdict) if verdict is not None else None,
        observations=tuple(observations),
    )


def load_history(history_dir: Path) -> list[RunRecord]:
    runs = [load_run(path, history_dir) for path in sorted(history_dir.rglob(CELLS_FILENAME))]
    return sorted((run for run in runs if run is not None), key=lambda run: (run.completed_at, run.run_id))


def observations_by_cell(runs: Iterable[RunRecord]) -> dict[CellKey, list[CellObservation]]:
    grouped: dict[CellKey, list[CellObservation]] = {}
    for run in runs:
        for observation in run.observations:
            grouped.setdefault(observation.cell, []).append(observation)
    return grouped


def _cell_dict(cell: CellKey) -> dict[str, object]:
    return {"platform": cell[0], "benchmark": cell[1], "scale": cell[2]}


def _cell_label(cell: CellKey) -> str:
    return f"{cell[0]}/{cell[1]}@{cell[2]:g}"


@dataclass(frozen=True)
class TrackerItemDraft:
    title: str
    links: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class FlakyCell:
    cell: CellKey
    window_runs: int
    infrastructure_failures: int
    rate: float
    signatures: dict[str, int]
    run_ids: tuple[str, ...]
    product_signatures_ever: dict[str, int]


def flaky_cells(
    grouped: Mapping[CellKey, list[CellObservation]],
    *,
    window: int = WINDOW_RUNS,
    threshold: float = INFRASTRUCTURE_RATE_THRESHOLD,
) -> list[FlakyCell]:
    flagged: list[FlakyCell] = []
    for cell in sorted(grouped):
        observations = grouped[cell]
        if len(observations) < window:
            continue
        recent = observations[-window:]
        failures = [obs for obs in recent if obs.outcome == OUTCOME_INFRASTRUCTURE]
        rate = len(failures) / window
        if rate <= threshold:
            continue
        signatures = Counter(sig for obs in failures for sig in obs.signatures)
        product = Counter(sig for obs in observations if obs.outcome == OUTCOME_PRODUCT for sig in obs.signatures)
        flagged.append(
            FlakyCell(
                cell=cell,
                window_runs=window,
                infrastructure_failures=len(failures),
                rate=rate,
                signatures=dict(sorted(signatures.items())),
                run_ids=tuple(obs.run_id for obs in recent),
                product_signatures_ever=dict(sorted(product.items())),
            )
        )
    return flagged


@dataclass(frozen=True)
class EnvelopeEntry:
    key: tuple[str, str, float]
    reason: str
    evidence: str
    reason_line: str
    evidence_line: str


def _envelope_entry(flaky: FlakyCell, remaining: tuple[float, ...], threshold: float) -> EnvelopeEntry:
    platform, benchmark, scale = flaky.cell
    signature_text = ", ".join(f"{name} x{count}" for name, count in flaky.signatures.items())
    remaining_text = ", ".join(f"SF{value:g}" for value in remaining)
    reason = (
        f"{platform} {benchmark} SF{scale:g} failed with infrastructure signatures ({signature_text}) in "
        f"{flaky.infrastructure_failures} of the last {flaky.window_runs} trunk UAT runs, above the "
        f"{threshold:.0%} envelope rubric threshold; {remaining_text} of {platform} {benchmark} stays in the gate."
    )
    evidence = (
        f"trunk UAT history {flaky.run_ids[0]} .. {flaky.run_ids[-1]}: "
        f"{flaky.infrastructure_failures}/{flaky.window_runs} infrastructure failures"
    )
    key_text = f"({json.dumps(platform)}, {json.dumps(benchmark)}, {float(scale)!r})"
    return EnvelopeEntry(
        key=(platform, benchmark, float(scale)),
        reason=reason,
        evidence=evidence,
        reason_line=f"{key_text}: {json.dumps(reason)},",
        evidence_line=f"{key_text}: {json.dumps(evidence)},",
    )


@dataclass(frozen=True)
class EnvelopeProposal:
    flaky: FlakyCell
    remaining_gated_scales: tuple[float, ...]
    envelope: EnvelopeEntry
    item: TrackerItemDraft


@dataclass(frozen=True)
class EnvelopeSuggestion:
    flaky: FlakyCell
    reason: str
    item: TrackerItemDraft


@dataclass(frozen=True)
class EnvelopeAssessment:
    proposals: list[EnvelopeProposal] = field(default_factory=list)
    suggestions: list[EnvelopeSuggestion] = field(default_factory=list)
    held_for_product_signal: list[FlakyCell] = field(default_factory=list)
    not_gated: list[FlakyCell] = field(default_factory=list)


def assess_envelopes(
    flaky: Iterable[FlakyCell],
    gated_scales: Mapping[PairKey, Collection[float]],
    *,
    threshold: float = INFRASTRUCTURE_RATE_THRESHOLD,
) -> EnvelopeAssessment:
    assessment = EnvelopeAssessment()
    dropped: set[CellKey] = set()
    ordered = sorted(flaky, key=lambda item: (item.cell[0], item.cell[1], -item.cell[2]))
    for item in ordered:
        platform, benchmark, scale = item.cell
        if item.product_signatures_ever:
            assessment.held_for_product_signal.append(item)
            continue
        gated = {float(value) for value in gated_scales.get((platform, benchmark), ())}
        if scale not in gated:
            assessment.not_gated.append(item)
            continue
        remaining = tuple(
            sorted(value for value in gated if value != scale and (platform, benchmark, value) not in dropped)
        )
        label = _cell_label(item.cell)
        rate_text = f"{item.infrastructure_failures}/{item.window_runs} runs"
        if remaining:
            envelope = _envelope_entry(item, remaining, threshold)
            description = (
                f"{label} had infrastructure failures in {rate_text} "
                f"({', '.join(f'{name} x{count}' for name, count in item.signatures.items())}). "
                f"Scales still gated for {platform}/{benchmark}: {', '.join(f'{v:g}' for v in remaining)}.\n\n"
                "Add to _RELEASE_GATE_RUNTIME_ENVELOPES in tests/uat/compatibility.py:\n"
                f"    {envelope.reason_line}\n\n"
                "Add to _RELEASE_GATE_RUNTIME_ENVELOPE_EVIDENCE in tests/uat/compatibility.py:\n"
                f"    {envelope.evidence_line}"
            )
            item_draft = TrackerItemDraft(
                title=f"{TITLE_PREFIX} drop {label} from the release gate ({rate_text} infrastructure failures)",
                links=(RUBRIC_LINK, ENVELOPE_SOURCE_LINK),
                description=description,
            )
            dropped.add(item.cell)
            assessment.proposals.append(EnvelopeProposal(item, remaining, envelope, item_draft))
        else:
            reason = f"{label} is the last gated scale of {platform}/{benchmark}; the rubric never prunes it"
            item_draft = TrackerItemDraft(
                title=f"{TITLE_PREFIX} investigate {label} infrastructure failures (last gated scale, {rate_text})",
                links=(RUBRIC_LINK, ENVELOPE_SOURCE_LINK),
                description=(
                    f"{reason}. Infrastructure failures in {rate_text}: "
                    f"{', '.join(f'{name} x{count}' for name, count in item.signatures.items())}. "
                    "Find the infrastructure cause (runner size, image, network) instead of pruning the cell."
                ),
            )
            assessment.suggestions.append(EnvelopeSuggestion(item, reason, item_draft))
    return assessment


@dataclass(frozen=True)
class CellSignal:
    cell: CellKey
    runs: int
    passed: int
    product_failures: dict[str, int]
    infrastructure_failures: dict[str, int]

    @property
    def defect_classes_caught(self) -> list[str]:
        return sorted(self.product_failures)


def signal_record(grouped: Mapping[CellKey, list[CellObservation]]) -> list[CellSignal]:
    record: list[CellSignal] = []
    for cell in sorted(grouped):
        observations = grouped[cell]
        product = Counter(sig for obs in observations if obs.outcome == OUTCOME_PRODUCT for sig in obs.signatures)
        infrastructure = Counter(
            sig for obs in observations if obs.outcome == OUTCOME_INFRASTRUCTURE for sig in obs.signatures
        )
        record.append(
            CellSignal(
                cell=cell,
                runs=len(observations),
                passed=sum(1 for obs in observations if obs.outcome == OUTCOME_PASSED),
                product_failures=dict(sorted(product.items())),
                infrastructure_failures=dict(sorted(infrastructure.items())),
            )
        )
    return record


@dataclass(frozen=True)
class DropCandidate:
    signal: CellSignal
    remaining_gated_scales: tuple[float, ...]


@dataclass(frozen=True)
class DropList:
    candidates: list[DropCandidate] = field(default_factory=list)
    kept_last_gated_scale: list[CellSignal] = field(default_factory=list)


def propose_drop_list(
    signals: Iterable[CellSignal],
    gated_scales: Mapping[PairKey, Collection[float]],
    *,
    min_runs: int = WINDOW_RUNS,
) -> DropList:
    drop = DropList()
    dropped: set[CellKey] = set()
    eligible = [
        signal
        for signal in signals
        if signal.runs >= min_runs and signal.infrastructure_failures and not signal.product_failures
    ]
    for signal in sorted(eligible, key=lambda item: (item.cell[0], item.cell[1], -item.cell[2])):
        platform, benchmark, scale = signal.cell
        gated = {float(value) for value in gated_scales.get((platform, benchmark), ())}
        if scale not in gated:
            continue
        remaining = tuple(
            sorted(value for value in gated if value != scale and (platform, benchmark, value) not in dropped)
        )
        if remaining:
            dropped.add(signal.cell)
            drop.candidates.append(DropCandidate(signal, remaining))
        else:
            drop.kept_last_gated_scale.append(signal)
    return drop


def red_shard_product_share(runs: Iterable[RunRecord]) -> dict[str, object]:
    red = [run for run in runs if run.verdict == "red"]
    with_product = sum(1 for run in red if run.has_product_signature)
    return {
        "red_shards": len(red),
        "with_product_signature": with_product,
        "share": (with_product / len(red)) if red else None,
    }


def _signal_dict(signal: CellSignal) -> dict[str, object]:
    return {
        **_cell_dict(signal.cell),
        "runs": signal.runs,
        "passed": signal.passed,
        "defect_classes_caught": signal.defect_classes_caught,
        "product_failures": signal.product_failures,
        "infrastructure_failures": signal.infrastructure_failures,
    }


def _flaky_dict(flaky: FlakyCell) -> dict[str, object]:
    return {
        **_cell_dict(flaky.cell),
        "window_runs": flaky.window_runs,
        "infrastructure_failures": flaky.infrastructure_failures,
        "rate": flaky.rate,
        "signatures": flaky.signatures,
        "run_ids": list(flaky.run_ids),
    }


def _history_span(runs: list[RunRecord]) -> dict[str, object]:
    return {
        "runs": len(runs),
        "first_run": runs[0].run_id if runs else None,
        "last_run": runs[-1].run_id if runs else None,
    }


def build_envelope_report(
    runs: list[RunRecord],
    gated_scales: Mapping[PairKey, Collection[float]],
    *,
    window: int = WINDOW_RUNS,
    threshold: float = INFRASTRUCTURE_RATE_THRESHOLD,
) -> dict[str, object]:
    grouped = observations_by_cell(runs)
    assessment = assess_envelopes(
        flaky_cells(grouped, window=window, threshold=threshold), gated_scales, threshold=threshold
    )
    return {
        "schema": REPORT_SCHEMA_VERSION,
        "history": _history_span(runs),
        "window_runs": window,
        "threshold": threshold,
        "proposals": [
            {
                **_flaky_dict(proposal.flaky),
                "remaining_gated_scales": list(proposal.remaining_gated_scales),
                "envelope": asdict(proposal.envelope),
                "tracker_item": asdict(proposal.item),
            }
            for proposal in assessment.proposals
        ],
        "tracker_item_suggestions": [
            {**_flaky_dict(suggestion.flaky), "reason": suggestion.reason, "tracker_item": asdict(suggestion.item)}
            for suggestion in assessment.suggestions
        ],
        "held_for_product_signal": [
            {**_flaky_dict(item), "product_signatures_ever": item.product_signatures_ever}
            for item in assessment.held_for_product_signal
        ],
        "not_gated": [_flaky_dict(item) for item in assessment.not_gated],
        "red_shards": red_shard_product_share(runs),
    }


def build_signal_report(
    runs: list[RunRecord],
    gated_scales: Mapping[PairKey, Collection[float]],
    *,
    min_runs: int = WINDOW_RUNS,
) -> dict[str, object]:
    grouped = observations_by_cell(runs)
    signals = signal_record(grouped)
    drop = propose_drop_list(signals, gated_scales, min_runs=min_runs)
    return {
        "schema": REPORT_SCHEMA_VERSION,
        "history": _history_span(runs),
        "cells": [_signal_dict(signal) for signal in signals],
        "drop_list": {
            "status": "proposed_for_owner_review",
            "min_runs": min_runs,
            "candidates": [
                {**_signal_dict(candidate.signal), "remaining_gated_scales": list(candidate.remaining_gated_scales)}
                for candidate in drop.candidates
            ],
            "kept_last_gated_scale": [_signal_dict(signal) for signal in drop.kept_last_gated_scale],
        },
        "red_shards": red_shard_product_share(runs),
    }


def write_report(path: Path, report: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
