from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any

from benchbox.core.results.tpcdi_audit import UNAUDITED_VALIDATION_STATUS
from benchbox.core.tpcdi.audit import FAILED_STATUS, AuditOutcome
from benchbox.core.tpcdi.phases import PhaseRecord
from benchbox.core.tpcdi.visibility import VisibilityEvidence
from benchbox.utils.clock import elapsed_seconds

INCREMENTAL_LIMIT_SECONDS = 3600.0
INCREMENTAL_TARGET_SECONDS = 1800.0
FIRST_INCREMENTAL_BATCH = 2


def reported_seconds(elapsed: float) -> float:
    tenths = (Decimal(str(elapsed)) * 10).to_integral_value(rounding=ROUND_CEILING)
    return float(tenths / 10)


@dataclass(frozen=True)
class IncrementalUpdateTiming:
    batch_id: int
    elapsed_seconds: float
    reported_seconds: float
    over_limit: bool
    below_1800_seconds: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "elapsed_seconds": self.elapsed_seconds,
            "reported_seconds": self.reported_seconds,
            "over_limit": self.over_limit,
            "below_1800_seconds": self.below_1800_seconds,
        }


@dataclass(frozen=True)
class Validity:
    updates: tuple[IncrementalUpdateTiming, ...]
    visibility: VisibilityEvidence
    reasons: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.reasons

    def as_block(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reasons": list(self.reasons),
            "incremental_updates": [update.as_dict() for update in self.updates],
            "visibility": self.visibility.as_block(),
        }


def incremental_timings(phases: Sequence[PhaseRecord]) -> tuple[IncrementalUpdateTiming, ...]:
    timings = []
    for previous, current in zip(phases, phases[1:]):
        if current.batch_id < FIRST_INCREMENTAL_BATCH:
            continue
        elapsed = elapsed_seconds(previous.completed_mono, current.completed_mono)
        reported = reported_seconds(elapsed)
        timings.append(
            IncrementalUpdateTiming(
                batch_id=current.batch_id,
                elapsed_seconds=elapsed,
                reported_seconds=reported,
                over_limit=reported > INCREMENTAL_LIMIT_SECONDS,
                below_1800_seconds=reported < INCREMENTAL_TARGET_SECONDS,
            )
        )
    return tuple(timings)


def evaluate_validity(phases: Sequence[PhaseRecord], visibility: VisibilityEvidence) -> Validity:
    updates = incremental_timings(phases)
    reasons: list[str] = []
    for update in updates:
        if update.over_limit:
            reasons.append(
                f"incremental update for BatchID {update.batch_id} took {update.reported_seconds} s, "
                f"over the {INCREMENTAL_LIMIT_SECONDS:g} s limit"
            )
    reasons.extend(visibility.missing)
    for gap in visibility.late:
        reasons.append(
            f"visibility gap from {gap.start_label} to {gap.end_label} was {gap.seconds:.3f} s, "
            f"over {visibility.interval_seconds + visibility.allowed_lateness_seconds:g} s"
        )
    return Validity(updates, visibility, tuple(reasons))


def combined_status(outcome: AuditOutcome, validity: Validity) -> str:
    if outcome.failures or not validity.valid:
        return FAILED_STATUS
    return UNAUDITED_VALIDATION_STATUS
