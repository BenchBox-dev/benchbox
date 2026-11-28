from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from benchbox.core.tpcdi import visibility
from benchbox.core.tpcdi.schema import TPCDISchemaManager
from benchbox.core.tpcdi.visibility import (
    AUDIT_TRIGGER,
    BOUNDARY_TRIGGER,
    INTERVAL_TRIGGER,
    PHASE_START_TRIGGER,
    VISIBILITY_1,
    VISIBILITY_2,
    VisibilityExecution,
    VisibilityRunner,
    evaluate_visibility,
    is_transaction_conflict,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcdi]

duckdb = pytest.importorskip("duckdb")

NOW = datetime(2026, 11, 26, 12, 0, 0)


def warehouse():
    connection = duckdb.connect()
    TPCDISchemaManager().create_schema(connection, "duckdb")
    return connection


def execution(kind: str, trigger: str, start: float, error: str | None = None) -> VisibilityExecution:
    return VisibilityExecution(kind, trigger, start, start + 0.1, NOW, error)


def complete_run(*, v1: float = 0.5, v2: float = 30.0, audit: float = 101.0) -> list[VisibilityExecution]:
    return [
        execution(VISIBILITY_1, PHASE_START_TRIGGER, v1),
        execution(VISIBILITY_2, BOUNDARY_TRIGGER, v2),
        execution(VISIBILITY_1, AUDIT_TRIGGER, audit),
    ]


def evaluate(executions, *, end: float = 60.0, **options):
    return evaluate_visibility(executions, iu1_start=0.0, iu2_end=end, **options)


def test_a_run_with_every_query_on_time_has_no_late_gap_and_nothing_missing():
    evidence = evaluate(complete_run())

    assert evidence.missing == ()
    assert evidence.late == ()
    assert [round(gap.seconds, 1) for gap in evidence.gaps] == [0.5, 29.5, 30.0]


def test_a_gap_of_65_seconds_is_on_time_and_longer_is_late():
    assert evaluate(complete_run(v2=30.0), end=95.0).late == ()
    late = evaluate(complete_run(v2=30.0), end=95.1).late
    assert [gap.end_label for gap in late] == ["incremental update 2 end"]


def test_the_first_gap_is_measured_from_the_start_of_the_first_incremental_update():
    evidence = evaluate(complete_run(v1=65.1, v2=70.0), end=100.0)
    assert evidence.gaps[0].late is True
    assert evidence.gaps[0].start_label == "incremental update 1 start"


def test_a_missing_first_query_is_reported():
    evidence = evaluate([e for e in complete_run() if e.kind != VISIBILITY_1 or e.trigger == AUDIT_TRIGGER])
    assert any(reason.startswith(f"no {VISIBILITY_1} ran before") for reason in evidence.missing)


def test_a_missing_second_query_is_reported():
    evidence = evaluate([e for e in complete_run() if e.kind != VISIBILITY_2])
    assert any(reason.startswith(f"no {VISIBILITY_2} ran before") for reason in evidence.missing)


def test_a_failed_execution_is_missing_and_leaves_a_longer_gap():
    executions = [
        execution(VISIBILITY_1, PHASE_START_TRIGGER, 0.5),
        execution(VISIBILITY_2, INTERVAL_TRIGGER, 40.0, error="IOException: gone"),
        execution(VISIBILITY_2, INTERVAL_TRIGGER, 90.0),
        execution(VISIBILITY_1, AUDIT_TRIGGER, 101.0),
    ]
    evidence = evaluate(executions, end=100.0)
    assert any("IOException: gone" in reason for reason in evidence.missing)
    assert [gap.late for gap in evidence.gaps] == [False, True, False]


def test_a_failing_audit_phase_query_is_missing():
    executions = complete_run()
    executions[-1] = execution(VISIBILITY_1, AUDIT_TRIGGER, 101.0, error="boom")
    evidence = evaluate(executions)
    assert any("boom" in reason for reason in evidence.missing)


def test_no_audit_phase_query_is_missing():
    evidence = evaluate(complete_run()[:-1])
    assert any("start of the audit phase" in reason for reason in evidence.missing)


def test_a_dead_thread_is_missing():
    evidence = evaluate(complete_run(), thread_error="RuntimeError: died")
    assert any("died" in reason for reason in evidence.missing)


def test_a_late_query_is_judged_against_the_interval_and_lateness_constants(monkeypatch):
    monkeypatch.setattr(visibility, "ALLOWED_LATENESS_SECONDS", -59.0)
    assert evaluate(complete_run(v2=1.0), end=2.0).late == ()
    assert evaluate(complete_run(v2=1.0), end=2.1).late != ()


def test_only_transaction_conflicts_are_retried():
    class TransactionException(Exception):
        pass

    assert is_transaction_conflict(TransactionException("conflict"))
    assert not is_transaction_conflict(RuntimeError("other"))


def test_the_runner_runs_visibility_1_first_then_periodic_and_boundary_visibility_2():
    connection = warehouse()
    runner = VisibilityRunner(connection.cursor(), "duckdb", interval_seconds=0.05)
    runner.start()
    time.sleep(0.4)
    runner.boundary()
    runner.stop()
    audit = runner.run_audit_query()
    runner.close()

    executions = runner.executions
    assert executions[0].kind == VISIBILITY_1
    assert executions[0].trigger == PHASE_START_TRIGGER
    triggers = [e.trigger for e in executions if e.kind == VISIBILITY_2]
    assert INTERVAL_TRIGGER in triggers
    assert triggers[-1] == BOUNDARY_TRIGGER
    assert audit.trigger == AUDIT_TRIGGER
    assert all(e.ok for e in executions)
    starts = [e.start_mono for e in executions[:-1]]
    assert starts == sorted(starts)
    assert (
        connection.execute(
            "SELECT count(*) FROM DImessages WHERE MessageType IN ('Visibility_1', 'Visibility_2')"
        ).fetchone()[0]
        > 0
    )


def test_the_runner_stops_without_running_a_query_after_the_stop():
    connection = warehouse()
    runner = VisibilityRunner(connection.cursor(), "duckdb", interval_seconds=3600.0)
    runner.start()
    runner.stop()
    count = len(runner.executions)
    time.sleep(0.1)
    assert len(runner.executions) == count == 1
    assert not any(thread.name == "tpcdi-visibility" for thread in threading.enumerate())
    runner.close()


def test_a_failing_query_is_recorded_and_the_loop_continues():
    class Broken:
        def __init__(self):
            self.calls = 0

        def execute(self, _statement):
            self.calls += 1
            raise RuntimeError("no such table")

        def close(self):
            pass

    session = Broken()
    runner = VisibilityRunner(session, "duckdb", interval_seconds=3600.0)
    runner.start()
    runner.boundary()
    runner.stop()
    assert [e.error for e in runner.executions] == ["RuntimeError: no such table"] * 2
    assert runner.thread_error is None


def test_a_transaction_conflict_is_retried_once_and_recorded():
    class Conflicting:
        def __init__(self):
            self.calls = 0

        def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                raise type("TransactionException", (Exception,), {})("conflict")

        def close(self):
            pass

    runner = VisibilityRunner(Conflicting(), "duckdb", interval_seconds=3600.0)
    execution_record = runner.run_audit_query()
    assert execution_record.ok
    assert execution_record.retried is True


def test_the_thread_runs_in_the_callers_context():
    import contextvars

    marker = contextvars.ContextVar("marker", default="unset")
    seen: list[str] = []

    class Spy:
        def execute(self, _statement):
            seen.append(marker.get())

        def close(self):
            pass

    marker.set("caller")
    runner = VisibilityRunner(Spy(), "duckdb", interval_seconds=3600.0)
    runner.start()
    runner.stop()
    assert seen and set(seen) == {"caller"}


def test_the_evidence_block_lists_executions_gaps_late_and_missing():
    block = evaluate(complete_run()).as_block()
    assert set(block) == {"interval_seconds", "allowed_lateness_seconds", "executions", "gaps", "late", "missing"}
    assert block["interval_seconds"] == 60.0
    assert block["allowed_lateness_seconds"] == 5.0
    assert [entry["trigger"] for entry in block["executions"]] == [
        PHASE_START_TRIGGER,
        BOUNDARY_TRIGGER,
        AUDIT_TRIGGER,
    ]
