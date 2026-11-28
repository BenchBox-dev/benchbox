from __future__ import annotations

import contextvars
import queue
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from benchbox.core.tpcdi.appendix import load_appendix_sql
from benchbox.core.tpcdi.etl.staging import SOURCE_DIALECT
from benchbox.utils.clock import elapsed_seconds, mono_time, utc_now

VISIBILITY_INTERVAL_SECONDS = 60.0
ALLOWED_LATENESS_SECONDS = 5.0

VISIBILITY_1 = "Visibility_1"
VISIBILITY_2 = "Visibility_2"
APPENDIX_BY_KIND = {VISIBILITY_1: "visibility_1", VISIBILITY_2: "visibility_2"}

PHASE_START_TRIGGER = "phase_start"
INTERVAL_TRIGGER = "interval"
BOUNDARY_TRIGGER = "boundary"
AUDIT_TRIGGER = "audit"

_BOUNDARY = "boundary"
_STOP = "stop"


def allowed_gap_seconds() -> float:
    return VISIBILITY_INTERVAL_SECONDS + ALLOWED_LATENESS_SECONDS


@dataclass(frozen=True)
class VisibilityExecution:
    kind: str
    trigger: str
    start_mono: float
    end_mono: float
    started_at: datetime
    error: str | None = None
    retried: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "trigger": self.trigger,
            "started_at": self.started_at.isoformat(),
            "start_monotonic_seconds": self.start_mono,
            "end_monotonic_seconds": self.end_mono,
            "seconds": self.end_mono - self.start_mono,
            "error": self.error,
            "retried": self.retried,
        }


@dataclass(frozen=True)
class VisibilityGap:
    start_label: str
    end_label: str
    seconds: float
    late: bool

    def as_dict(self) -> dict[str, Any]:
        return {"from": self.start_label, "to": self.end_label, "seconds": self.seconds, "late": self.late}


@dataclass(frozen=True)
class VisibilityEvidence:
    executions: tuple[VisibilityExecution, ...]
    gaps: tuple[VisibilityGap, ...]
    missing: tuple[str, ...]
    interval_seconds: float
    allowed_lateness_seconds: float

    @property
    def late(self) -> tuple[VisibilityGap, ...]:
        return tuple(gap for gap in self.gaps if gap.late)

    def as_block(self) -> dict[str, Any]:
        return {
            "interval_seconds": self.interval_seconds,
            "allowed_lateness_seconds": self.allowed_lateness_seconds,
            "executions": [execution.as_dict() for execution in self.executions],
            "gaps": [gap.as_dict() for gap in self.gaps],
            "late": [gap.as_dict() for gap in self.late],
            "missing": list(self.missing),
        }


def evaluate_visibility(
    executions: Sequence[VisibilityExecution],
    *,
    iu1_start: float,
    iu2_end: float,
    thread_error: str | None = None,
    interval_seconds: float | None = None,
) -> VisibilityEvidence:
    interval = VISIBILITY_INTERVAL_SECONDS if interval_seconds is None else interval_seconds
    limit = interval + ALLOWED_LATENESS_SECONDS
    ordered = sorted(executions, key=lambda execution: execution.start_mono)
    during = [e for e in ordered if e.trigger != AUDIT_TRIGGER and e.ok and e.start_mono < iu2_end]
    missing: list[str] = []
    if thread_error is not None:
        missing.append(f"visibility thread stopped: {thread_error}")
    for execution in ordered:
        if not execution.ok:
            missing.append(f"{execution.kind} ({execution.trigger}) failed: {execution.error}")
    for kind in (VISIBILITY_1, VISIBILITY_2):
        if not any(e.kind == kind for e in during):
            missing.append(f"no {kind} ran before the end of the second incremental update")
    if not any(e.kind == VISIBILITY_1 and e.trigger == AUDIT_TRIGGER and e.ok for e in ordered):
        missing.append(f"no {VISIBILITY_1} ran at the start of the audit phase")

    points: list[tuple[str, float]] = [("incremental update 1 start", iu1_start)]
    counters = {VISIBILITY_1: 0, VISIBILITY_2: 0}
    for execution in during:
        counters[execution.kind] += 1
        points.append((f"{execution.kind} #{counters[execution.kind]} ({execution.trigger})", execution.start_mono))
    points.append(("incremental update 2 end", iu2_end))
    gaps = tuple(
        VisibilityGap(
            start_label=before[0],
            end_label=after[0],
            seconds=elapsed_seconds(before[1], after[1]),
            late=elapsed_seconds(before[1], after[1]) > limit,
        )
        for before, after in zip(points, points[1:])
    )
    return VisibilityEvidence(tuple(ordered), gaps, tuple(missing), interval, ALLOWED_LATENESS_SECONDS)


def is_transaction_conflict(error: BaseException) -> bool:
    return "Transaction" in type(error).__name__


class VisibilityRunner:
    def __init__(self, session: Any, platform: str = SOURCE_DIALECT, *, interval_seconds: float | None = None):
        self._session = session
        self._interval = VISIBILITY_INTERVAL_SECONDS if interval_seconds is None else interval_seconds
        self._statements = {
            kind: load_appendix_sql(appendix, platform).statements() for kind, appendix in APPENDIX_BY_KIND.items()
        }
        self._commands: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._executions: list[VisibilityExecution] = []
        self._thread: threading.Thread | None = None
        self._thread_error: str | None = None

    @property
    def interval_seconds(self) -> float:
        return self._interval

    @property
    def executions(self) -> tuple[VisibilityExecution, ...]:
        return tuple(self._executions)

    @property
    def thread_error(self) -> str | None:
        return self._thread_error

    def start(self) -> None:
        context = contextvars.copy_context()
        self._thread = threading.Thread(target=context.run, args=(self._loop,), name="tpcdi-visibility", daemon=True)
        self._thread.start()

    def boundary(self) -> None:
        self._commands.put(_BOUNDARY)

    def stop(self) -> None:
        self._commands.put(_STOP)
        if self._thread is not None:
            self._thread.join()

    def run_audit_query(self) -> VisibilityExecution:
        return self._run(VISIBILITY_1, AUDIT_TRIGGER)

    def evidence(self, *, iu1_start: float, iu2_end: float) -> VisibilityEvidence:
        return evaluate_visibility(
            self._executions,
            iu1_start=iu1_start,
            iu2_end=iu2_end,
            thread_error=self._thread_error,
            interval_seconds=self._interval,
        )

    def close(self) -> None:
        self._session.close()

    def _loop(self) -> None:
        try:
            first = self._run(VISIBILITY_1, PHASE_START_TRIGGER)
            next_due = first.start_mono + self._interval
            while True:
                try:
                    command = self._commands.get(timeout=max(0.0, elapsed_seconds(mono_time(), next_due)))
                except queue.Empty:
                    command = INTERVAL_TRIGGER
                if command == _STOP:
                    return
                trigger = BOUNDARY_TRIGGER if command == _BOUNDARY else INTERVAL_TRIGGER
                execution = self._run(VISIBILITY_2, trigger)
                next_due = execution.start_mono + self._interval
        except BaseException as error:
            self._thread_error = f"{type(error).__name__}: {error}"

    def _run(self, kind: str, trigger: str) -> VisibilityExecution:
        start = mono_time()
        started_at = utc_now()
        error: str | None = None
        retried = False
        for attempt in (0, 1):
            try:
                for statement in self._statements[kind]:
                    self._session.execute(statement)
                error = None
                break
            except Exception as failure:
                error = f"{type(failure).__name__}: {failure}"
                if attempt == 0 and is_transaction_conflict(failure):
                    retried = True
                    continue
                break
        execution = VisibilityExecution(kind, trigger, start, mono_time(), started_at, error, retried)
        self._executions.append(execution)
        return execution
