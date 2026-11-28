from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.tpcdi.audit import AuditOutcome, AuditResult
from benchbox.core.tpcdi.phases import PhaseRecord
from benchbox.core.tpcdi.validity import (
    INCREMENTAL_LIMIT_SECONDS,
    combined_status,
    evaluate_validity,
    incremental_timings,
    reported_seconds,
)
from benchbox.core.tpcdi.visibility import (
    AUDIT_TRIGGER,
    BOUNDARY_TRIGGER,
    PHASE_START_TRIGGER,
    VISIBILITY_1,
    VISIBILITY_2,
    VisibilityExecution,
    evaluate_visibility,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcdi]

NOW = datetime(2026, 11, 26, 12, 0, 0)


def phase(name: str, batch: int, mono: float) -> PhaseRecord:
    return PhaseRecord(name, batch, NOW, mono)


def phases_with(first: float, second: float) -> tuple[PhaseRecord, ...]:
    return (
        phase("initialization", 0, 0.0),
        phase("historical_load", 1, 10.0),
        phase("incremental_update_1", 2, 10.0 + first),
        phase("incremental_update_2", 3, 10.0 + first + second),
    )


def visibility_for(records: tuple[PhaseRecord, ...], *, v2_at: float | None = None, v2: bool = True):
    start, end = records[1].completed_mono, records[3].completed_mono
    executions = [
        VisibilityExecution(VISIBILITY_1, PHASE_START_TRIGGER, start + 0.1, start + 0.2, NOW),
        VisibilityExecution(VISIBILITY_1, AUDIT_TRIGGER, end + 1.0, end + 1.1, NOW),
    ]
    if v2:
        at = start + 30.0 if v2_at is None else v2_at
        executions.append(VisibilityExecution(VISIBILITY_2, BOUNDARY_TRIGGER, at, at + 0.1, NOW))
    return evaluate_visibility(executions, iu1_start=start, iu2_end=end)


def audit_outcome(*results: AuditResult) -> AuditOutcome:
    return AuditOutcome(tuple(results), (), (), "0" * 64, (), 3)


OK_TEST = AuditResult("DimTrade row count", 1, "OK", "", True)
BAD_TEST = AuditResult("DimTrade Type", None, "Bad value", "", True)


@pytest.mark.parametrize(
    ("elapsed", "reported"),
    [(3600.0, 3600.0), (3600.01, 3600.1), (1799.9, 1799.9), (1799.91, 1800.0), (0.04, 0.1), (12.0, 12.0)],
)
def test_reported_seconds_round_up_to_a_tenth(elapsed, reported):
    assert reported_seconds(elapsed) == reported


def test_an_update_of_exactly_3600_seconds_is_valid():
    validity = evaluate_validity(phases_with(3600.0, 20.0), visibility_for(phases_with(3600.0, 20.0), v2_at=3610.0))
    assert [update.over_limit for update in validity.updates] == [False, False]


def test_an_update_above_3600_seconds_is_not_valid_and_names_its_batch():
    records = phases_with(20.0, 3600.01)
    validity = evaluate_validity(records, visibility_for(records, v2_at=40.0))
    assert [update.over_limit for update in validity.updates] == [False, True]
    assert not validity.valid
    assert any("BatchID 3" in reason and "3600" in reason for reason in validity.reasons)


def test_an_update_below_1800_seconds_is_valid_and_flagged():
    records = phases_with(1799.9, 1800.0)
    updates = incremental_timings(records)
    assert [update.below_1800_seconds for update in updates] == [True, False]
    assert [update.batch_id for update in updates] == [2, 3]


def test_a_run_with_on_time_visibility_and_short_updates_is_valid():
    records = phases_with(20.0, 20.0)
    validity = evaluate_validity(records, visibility_for(records, v2_at=30.0))
    assert validity.valid
    assert validity.reasons == ()
    assert combined_status(audit_outcome(OK_TEST), validity) == "NOT_VALIDATED"


def test_a_missing_visibility_query_makes_the_run_failed_with_the_reason():
    records = phases_with(20.0, 20.0)
    validity = evaluate_validity(records, visibility_for(records, v2=False))
    assert not validity.valid
    assert any("no Visibility_2" in reason for reason in validity.reasons)
    assert combined_status(audit_outcome(OK_TEST), validity) == "FAILED"


def test_a_late_visibility_gap_makes_the_run_failed_with_the_reason():
    records = phases_with(40.0, 90.0)
    validity = evaluate_validity(records, visibility_for(records, v2_at=records[1].completed_mono + 30.0))
    assert not validity.valid
    assert any("visibility gap" in reason and "Visibility_2" in reason for reason in validity.reasons)
    assert combined_status(audit_outcome(OK_TEST), validity) == "FAILED"


def test_an_audit_failure_makes_a_valid_run_failed():
    records = phases_with(20.0, 20.0)
    validity = evaluate_validity(records, visibility_for(records, v2_at=30.0))
    assert validity.valid
    assert combined_status(audit_outcome(OK_TEST, BAD_TEST), validity) == "FAILED"


def test_no_combination_reads_passed():
    records = phases_with(20.0, 20.0)
    validity = evaluate_validity(records, visibility_for(records, v2_at=30.0))
    assert combined_status(audit_outcome(), validity) not in {"PASSED", "VALID"}


def test_the_validity_block_lists_updates_and_visibility():
    records = phases_with(20.0, 20.0)
    block = evaluate_validity(records, visibility_for(records, v2_at=30.0)).as_block()
    assert block["valid"] is True
    assert block["reasons"] == []
    assert [update["batch_id"] for update in block["incremental_updates"]] == [2, 3]
    assert set(block["incremental_updates"][0]) == {
        "batch_id",
        "elapsed_seconds",
        "reported_seconds",
        "over_limit",
        "below_1800_seconds",
    }
    assert set(block["visibility"]) >= {"executions", "gaps", "late", "missing"}


def test_the_limit_constant_is_read_at_evaluation_time(monkeypatch):
    from benchbox.core.tpcdi import validity

    records = phases_with(20.0, 20.0)
    monkeypatch.setattr(validity, "INCREMENTAL_LIMIT_SECONDS", 10.0)
    assert [update.over_limit for update in incremental_timings(records)] == [True, True]
    assert INCREMENTAL_LIMIT_SECONDS == 3600.0
