from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

NOT_APPLICABLE = "not_applicable"
NOOP = "noop"
APPLIED_UNVERIFIED = "applied_unverified"
APPLIED_VERIFIED = "applied_verified"
FAILED = "failed"

NOT_VALIDATED = "not_validated"

TUNING_STATUS_VOCABULARY = frozenset(
    {
        NOT_APPLICABLE,
        NOOP,
        APPLIED_UNVERIFIED,
        APPLIED_VERIFIED,
        FAILED,
        NOT_VALIDATED,
    }
)

LEGACY_STATUS_MAP = {
    "NOT_APPLICABLE": NOT_APPLICABLE,
    "APPLIED": APPLIED_UNVERIFIED,
    "FAILED_TO_SAVE": APPLIED_UNVERIFIED,
    "NOT_VALIDATED": NOT_VALIDATED,
}

EXECUTED = "executed"
STATEMENT_FAILED = "failed"

PHASE_DDL = "ddl"
PHASE_POST_LOAD = "post_load"
PHASE_SESSION = "session"

LEDGER_PHASES = frozenset({PHASE_DDL, PHASE_POST_LOAD, PHASE_SESSION})

PHASE_ALIASES = {
    "pre_load": PHASE_DDL,
    "preload": PHASE_DDL,
    "pre-load": PHASE_DDL,
    "schema": PHASE_DDL,
    "create": PHASE_DDL,
    "postload": PHASE_POST_LOAD,
    "post-load": PHASE_POST_LOAD,
    "maintenance": PHASE_POST_LOAD,
}

SATISFIED_BY_PREEXISTING_STATE = -1

_READBACK_PREFIXES = ("select", "show", "describe", "desc ", "explain", "values ")

_SQL_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_SQL_QUOTED_TEXT_RE = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`")
_SCHEMA_TUNING_STATEMENT_RE = re.compile(
    r"^\s*(?:create\s+index|create\s+(?:or\s+replace\s+)?table|alter\s+table)\b",
    re.IGNORECASE,
)
_SCHEMA_TUNING_FOOTPRINT_RE = re.compile(
    r"\b(?:cluster\s+by|clustered\s+by|distributed\s+by|foreign\s+key|order\s+by|partition\s+by|"
    r"partitioned\s+by|primary\s+key|primary\s+index|duplicate\s+key|sortkey|distkey|diststyle|"
    r"sorted_by|bucketed_by|unique|check)\b|\b(?:distribution|partitioning)\s*=",
    re.IGNORECASE,
)


def _sql_shape(statement: Any) -> str:
    text = str(statement)
    text = _SQL_COMMENT_RE.sub(" ", text)
    return _SQL_QUOTED_TEXT_RE.sub(" ", text)


def is_schema_tuning_statement(statement: Any) -> bool:
    shape = _sql_shape(statement)
    if not _SCHEMA_TUNING_STATEMENT_RE.match(shape):
        return False
    if re.match(r"^\s*create\s+index\b", shape, re.IGNORECASE):
        return True
    return _SCHEMA_TUNING_FOOTPRINT_RE.search(shape) is not None


def _split_sql_script(script: Any) -> list[str]:
    text = str(script)
    if not text.strip():
        return []

    try:
        import sqlite3

        statements: list[str] = []
        start = 0
        for index, char in enumerate(text):
            if char != ";":
                continue
            candidate = text[start : index + 1]
            if sqlite3.complete_statement(candidate):
                if candidate.strip():
                    statements.append(candidate.strip())
                start = index + 1
        tail = text[start:].strip()
        if tail:
            statements.append(tail)
        return statements
    except Exception:  # pragma: no cover
        return [text]


def normalize_ledger_phase(phase: Any) -> str:
    try:
        text = str(phase).strip().lower()
        if text in LEDGER_PHASES:
            return text
        if text in PHASE_ALIASES:
            return PHASE_ALIASES[text]
        logger.warning("applied-ledger unknown phase %r; recording as %r", phase, PHASE_DDL)
        return PHASE_DDL
    except Exception:  # pragma: no cover
        return PHASE_DDL


def _is_recordable_statement(statement: Any) -> bool:
    try:
        text = str(statement).lstrip().lstrip("(").lstrip().lower()
    except Exception:  # pragma: no cover
        return True
    return not text.startswith(_READBACK_PREFIXES)


@dataclass
class AppliedStatement:
    statement: str
    phase: str
    status: str = EXECUTED
    mechanism: str | None = None
    table: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "statement": self.statement,
            "phase": self.phase,
            "status": self.status,
        }
        if self.mechanism:
            payload["mechanism"] = self.mechanism
        if self.table:
            payload["table"] = self.table
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass
class DroppedIntent:
    intent: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"intent": self.intent, "reason": self.reason}


@dataclass
class SatisfiedIntent:
    intent: str
    satisfied_by: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"intent": self.intent, "satisfied_by": self.satisfied_by, "reason": self.reason}


@dataclass
class AppliedTuningLedger:
    statements: list[AppliedStatement] = field(default_factory=list)
    dropped: list[DroppedIntent] = field(default_factory=list)
    satisfied: list[SatisfiedIntent] = field(default_factory=list)

    def record(
        self,
        statement: str,
        phase: str,
        *,
        status: str = EXECUTED,
        mechanism: str | None = None,
        table: str | None = None,
        error: Any | None = None,
    ) -> None:
        try:
            self.statements.append(
                AppliedStatement(
                    statement=str(statement),
                    phase=normalize_ledger_phase(phase),
                    status=status,
                    mechanism=mechanism,
                    table=table,
                    error=(str(error) if error is not None else None),
                )
            )
        except Exception as exc:
            logger.debug("applied-ledger record degraded: %s", exc)

    def record_dropped(self, intent: str, reason: str) -> None:
        try:
            self.dropped.append(DroppedIntent(intent=str(intent), reason=str(reason)))
        except Exception as exc:
            logger.debug("applied-ledger dropped-record degraded: %s", exc)

    def record_satisfied(self, intent: str, satisfied_by: int, reason: str) -> None:
        try:
            self.satisfied.append(
                SatisfiedIntent(intent=str(intent), satisfied_by=int(satisfied_by), reason=str(reason))
            )
        except Exception as exc:
            logger.debug("applied-ledger satisfied-record degraded: %s", exc)

    def executed_statement_index(self, predicate: Callable[[AppliedStatement], bool]) -> int | None:
        for index, statement in enumerate(self.statements):
            if statement.status == EXECUTED and predicate(statement):
                return index
        return None

    @property
    def executed_statements(self) -> list[AppliedStatement]:
        return [s for s in self.statements if s.status == EXECUTED]

    def overall_status(self, *, tuning_enabled: bool, has_config: bool) -> str:
        if not tuning_enabled or not has_config:
            return NOT_APPLICABLE
        physical_statements = [s for s in self.statements if s.phase in {PHASE_DDL, PHASE_POST_LOAD}]
        if physical_statements and all(s.status == STATEMENT_FAILED for s in physical_statements):
            return FAILED
        if any(s.status == EXECUTED for s in self.statements):
            return APPLIED_UNVERIFIED
        if self.statements:
            return FAILED
        return NOOP

    def snapshot(self) -> tuple[int, int, int]:
        return len(self.executed_statements), self._failed_count(), len(self.dropped)

    def _failed_count(self) -> int:
        return sum(1 for s in self.statements if s.status == STATEMENT_FAILED)

    def describe_apply_step(self, since: tuple[int, int, int]) -> str:
        executed_before, failed_before, dropped_before = since
        executed, failed, dropped = self.snapshot()
        statements = executed - executed_before
        noun = "statement" if statements == 1 else "statements"
        failures = failed - failed_before
        failed_note = f", {failures} failed" if failures else ""
        return (
            f"Tuning apply step complete ({statements} {noun}{failed_note}; "
            f"{dropped - dropped_before} intents not rendered at apply time)"
        )

    def describe_outcome(self) -> str:
        return (
            f"Tuning outcome after load: {len(self.executed_statements)} executed, "
            f"{self._failed_count()} failed, {len(self.dropped)} dropped"
        )

    def applied_ledger_hash(self) -> str | None:
        executed = [s.to_dict() for s in self.executed_statements]
        if not executed:
            return None
        payload = json.dumps(executed, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_payload(
        self,
        *,
        status: str,
        receipt: dict[str, Any] | None = None,
        drift_check: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "applied_ledger_hash": self.applied_ledger_hash(),
            "statements": [s.to_dict() for s in self.statements],
            "dropped": [d.to_dict() for d in self.dropped],
        }
        if self.satisfied:
            payload["satisfied"] = [item.to_dict() for item in self.satisfied]
        if receipt is not None:
            payload["receipt"] = receipt
        if drift_check is not None:
            payload["drift_check"] = drift_check
        return payload

    def is_empty(self) -> bool:
        return not self.statements and not self.dropped and not self.satisfied


class _RecordingProxy:
    __slots__ = ("_ledger", "_phase", "_statement_filter")

    def __init__(
        self,
        ledger: AppliedTuningLedger,
        phase: str,
        statement_filter: Callable[[Any], bool] | None = None,
    ) -> None:
        object.__setattr__(self, "_ledger", ledger)
        object.__setattr__(self, "_phase", phase)
        object.__setattr__(self, "_statement_filter", statement_filter)

    def _should_record(self, statement: Any) -> bool:
        if not _is_recordable_statement(statement):
            return False
        if self._statement_filter is None:
            return True
        try:
            return bool(self._statement_filter(statement))
        except Exception as exc:
            logger.debug("applied-ledger statement filter degraded: %s", exc)
            return True

    def _recordable_statements(self, statement: Any) -> list[str]:
        try:
            pieces = _split_sql_script(statement)
            if len(pieces) <= 1:
                pieces = [statement]
            return [str(piece) for piece in pieces if self._should_record(piece)]
        except Exception as exc:
            logger.debug("applied-ledger statement capture degraded: %s", exc)
            return []

    def _record_all(self, statements: list[str], status: str, error: Any | None = None) -> None:
        for statement in statements:
            self._ledger.record(statement, self._phase, status=status, error=error)

    def _run(self, fn: Any, statement: Any, args: tuple, kwargs: dict) -> Any:
        statements = self._recordable_statements(statement)
        try:
            result = fn(statement, *args, **kwargs)
        except Exception as exc:
            self._record_all(statements, STATEMENT_FAILED, exc)
            raise
        self._record_all(statements, EXECUTED)
        return result

    def _run_verb(self, fn: Any, statement: Any, args: tuple, kwargs: dict) -> Any:
        statements = [] if _is_dry_run(args, kwargs) else self._recordable_statements(statement)
        try:
            result = fn(statement, *args, **kwargs)
        except Exception as exc:
            self._record_all(statements, STATEMENT_FAILED, exc)
            raise
        if not statements:
            return result
        if callable(getattr(result, "result", None)) and callable(getattr(result, "done", None)):
            return _RecordingJob(result, self._ledger, self._phase, statements)
        self._record_all(statements, EXECUTED)
        return result


def _is_dry_run(args: tuple, kwargs: dict) -> bool:
    config = kwargs.get("job_config") or (args[0] if args else None)
    return getattr(config, "dry_run", False) is True


class _RecordingJob:
    __slots__ = ("_job", "_ledger", "_phase", "_statements", "_settled")

    def __init__(self, job: Any, ledger: AppliedTuningLedger, phase: str, statements: list[str]) -> None:
        object.__setattr__(self, "_job", job)
        object.__setattr__(self, "_ledger", ledger)
        object.__setattr__(self, "_phase", phase)
        object.__setattr__(self, "_statements", statements)
        object.__setattr__(self, "_settled", False)

    def _settle(self, status: str, error: Any | None = None) -> None:
        if self._settled:
            return
        object.__setattr__(self, "_settled", True)
        for statement in self._statements:
            self._ledger.record(statement, self._phase, status=status, error=error)

    def result(self, *args: Any, **kwargs: Any) -> Any:
        from concurrent.futures import TimeoutError as FutureTimeoutError

        try:
            outcome = self._job.result(*args, **kwargs)
        except FutureTimeoutError:
            raise
        except Exception as exc:
            self._settle(STATEMENT_FAILED, exc)
            raise
        self._settle(EXECUTED)
        return outcome

    def exception(self, *args: Any, **kwargs: Any) -> Any:
        error = self._job.exception(*args, **kwargs)
        if error is not None:
            self._settle(STATEMENT_FAILED, error)
        elif self._job.done():
            self._settle(EXECUTED)
        return error

    def __getattr__(self, name: str) -> Any:
        return getattr(self._job, name)


class RecordingConnection(_RecordingProxy):
    __slots__ = ("_conn", "_verbs")

    def __init__(
        self,
        connection: Any,
        ledger: AppliedTuningLedger,
        phase: str,
        statement_filter: Callable[[Any], bool] | None = None,
        execute_verbs: tuple[str, ...] = (),
    ) -> None:
        super().__init__(ledger, phase, statement_filter)
        object.__setattr__(self, "_conn", connection)
        object.__setattr__(self, "_verbs", frozenset(execute_verbs))

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return self._run(self._conn.execute, statement, args, kwargs)

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        return _RecordingCursor(self._conn.cursor(*args, **kwargs), self._ledger, self._phase, self._statement_filter)

    def executescript(self, script: Any, *args: Any, **kwargs: Any) -> Any:
        result = self._conn.executescript(script, *args, **kwargs)
        for statement in _split_sql_script(script):
            if self._should_record(statement):
                self._ledger.record(statement, self._phase, status=EXECUTED)
        return result

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._conn, name)
        if name in self._verbs and callable(attribute):

            def call(statement: Any, *args: Any, **kwargs: Any) -> Any:
                return self._run_verb(attribute, statement, args, kwargs)

            return call
        return attribute

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._conn, name, value)

    def __enter__(self) -> Any:
        self._conn.__enter__()
        return self

    def __exit__(self, *exc_info: Any) -> Any:
        return self._conn.__exit__(*exc_info)

    @property
    def raw_connection(self) -> Any:
        return self._conn


class _RecordingCursor(_RecordingProxy):
    __slots__ = ("_cur",)

    def __init__(
        self,
        cursor: Any,
        ledger: AppliedTuningLedger,
        phase: str,
        statement_filter: Callable[[Any], bool] | None = None,
    ) -> None:
        super().__init__(ledger, phase, statement_filter)
        object.__setattr__(self, "_cur", cursor)

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return self._run(self._cur.execute, statement, args, kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cur, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._cur, name, value)

    def __iter__(self) -> Any:
        return iter(self._cur)

    def __enter__(self) -> Any:
        self._cur.__enter__()
        return self

    def __exit__(self, *exc_info: Any) -> Any:
        return self._cur.__exit__(*exc_info)


def recording_connection(
    connection: Any,
    ledger: AppliedTuningLedger | None,
    phase: str,
    statement_filter: Callable[[Any], bool] | None = None,
    execute_verbs: tuple[str, ...] = (),
) -> Any:
    if ledger is None:
        return connection
    try:
        return RecordingConnection(connection, ledger, phase, statement_filter, execute_verbs)
    except Exception as exc:  # pragma: no cover
        logger.debug("applied-ledger connection wrap degraded: %s", exc)
        return connection
