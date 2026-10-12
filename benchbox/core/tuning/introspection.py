# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from benchbox.core.tuning.applied_ledger import (
    PHASE_DDL,
    PHASE_POST_LOAD,
    PHASE_SESSION,
    STATEMENT_FAILED,
    AppliedStatement,
    AppliedTuningLedger,
)

logger = logging.getLogger(__name__)

CORROBORATED = "corroborated"
ABSENT = "absent"
MISMATCH = "mismatch"
TRANSIENT = "transient"
MAINTENANCE = "maintenance"
UNVERIFIABLE = "unverifiable"

_VERIFIABLE_VERDICTS = frozenset({CORROBORATED, ABSENT, MISMATCH, UNVERIFIABLE})

KIND_INDEX = "index"
KIND_SORT_KEY = "sort_key"
KIND_PARTITION_KEY = "partition_key"
KIND_CLUSTER_KEY = "cluster_key"

_DDL_PHASES = frozenset({PHASE_DDL, PHASE_POST_LOAD})


@dataclass
class IntrospectedObject:
    kind: str
    table: str | None
    columns: tuple[str, ...] = ()
    name: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntrospectedState:
    platform: str
    objects: list[IntrospectedObject] = field(default_factory=list)
    error: str | None = None
    truncated: bool = False


@runtime_checkable
class Introspector(Protocol):
    platform: str

    def introspect(self, connection: Any, ledger: AppliedTuningLedger) -> IntrospectedState: ...


@dataclass
class ReceiptEntry:
    statement: str
    phase: str
    verdict: str
    kind: str | None = None
    table: str | None = None
    name: str | None = None
    expected_columns: tuple[str, ...] = ()
    observed_columns: tuple[str, ...] = ()
    diff: str | None = None
    reason: str | None = None
    evidence: dict[str, Any] | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "statement": self.statement,
            "phase": self.phase,
            "verdict": self.verdict,
        }
        if self.kind:
            payload["kind"] = self.kind
        if self.table:
            payload["table"] = self.table
        if self.name:
            payload["name"] = self.name
        if self.expected_columns:
            payload["expected_columns"] = list(self.expected_columns)
        if self.observed_columns:
            payload["observed_columns"] = list(self.observed_columns)
        if self.diff:
            payload["diff"] = self.diff
        if self.reason:
            payload["reason"] = self.reason
        if self.detail:
            payload["detail"] = self.detail
        if self.evidence:
            payload["evidence"] = self.evidence
        return payload


@dataclass
class IntrospectionReceipt:
    platform: str
    corroborated: bool
    entries: list[ReceiptEntry] = field(default_factory=list)
    observed: list[IntrospectedObject] = field(default_factory=list)
    error: str | None = None
    dropped: list[dict[str, str]] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.verdict] = counts.get(entry.verdict, 0) + 1
        counts["gate_relevant_total"] = sum(1 for e in self.entries if e.verdict in _VERIFIABLE_VERDICTS)
        return counts

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "platform": self.platform,
            "corroborated": self.corroborated,
            "summary": self.summary,
            "entries": [e.to_dict() for e in self.entries],
        }
        if self.observed:
            payload["observed"] = [
                {
                    "kind": obj.kind,
                    "table": obj.table,
                    "columns": list(obj.columns),
                    **({"name": obj.name} if obj.name else {}),
                    **({"evidence": obj.evidence} if obj.evidence else {}),
                }
                for obj in self.observed
            ]
        if self.dropped:
            payload["dropped"] = list(self.dropped)
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass
class _Intent:
    kind: str
    table: str | None
    columns: tuple[str, ...]
    name: str | None = None


_CREATE_INDEX_RE = re.compile(
    r"^\s*create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?"
    r"(?P<name>[^\s(]+)\s+on\s+(?P<table>[^\s(]+)\s*\((?P<cols>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
_KEY_CLAUSE_COLUMNS = r"(?P<cols>(?:[^()]|\([^()]*\))*)"
_ORDER_BY_RE = re.compile(rf"\border\s+by\s*\({_KEY_CLAUSE_COLUMNS}\)", re.IGNORECASE | re.DOTALL)
_PARTITION_BY_RE = re.compile(rf"\bpartition\s+by\s*\({_KEY_CLAUSE_COLUMNS}\)", re.IGNORECASE | re.DOTALL)
_ORDER_BY_KEYWORD_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
_PARTITION_BY_KEYWORD_RE = re.compile(r"\bpartition\s+by\b", re.IGNORECASE)
_ORDER_BY_NOOP_RE = re.compile(r"\border\s+by\s+tuple\s*\(\s*\)", re.IGNORECASE)
_CREATE_TABLE_RE = re.compile(
    r"^\s*create\s+(?:or\s+replace\s+)?table\s+(?:if\s+not\s+exists\s+)?(?P<table>[^\s(]+)",
    re.IGNORECASE,
)
_OPTIMIZE_RE = re.compile(r"^\s*optimize\s+table\s+(?P<table>[^\s;]+)", re.IGNORECASE)
_CLUSTER_BY_RE = re.compile(rf"\bcluster\s+by\s*\({_KEY_CLAUSE_COLUMNS}\)", re.IGNORECASE | re.DOTALL)
_CLUSTER_BY_KEYWORD_RE = re.compile(r"\bcluster\s+by\b", re.IGNORECASE)
_ALTER_TABLE_RE = re.compile(r"^\s*alter\s+table\s+(?P<table>[^\s(]+)", re.IGNORECASE)
_RECLUSTER_RE = re.compile(r"^\s*alter\s+table\s+\S+\s+(?:(?:resume|suspend)\s+)?recluster\b", re.IGNORECASE)
_TRANSIENT_PREFIXES = ("set ", "pragma ", "set\t", "pragma\t", "reset ", "use ")
_MAINTENANCE_PREFIXES = ("optimize ", "vacuum", "analyze", "compact ")


def _strip_sql_literals_and_comments(statement: str, keep_double_quoted: bool = False) -> str:
    chars = list(statement)
    index = 0
    quote_end: str | None = None
    while index < len(chars):
        char = chars[index]
        following = chars[index + 1] if index + 1 < len(chars) else ""
        if quote_end is not None:
            keep_quoted = keep_double_quoted and quote_end == '"'
            if char == quote_end:
                if not keep_quoted:
                    chars[index] = " "
                if following == quote_end and quote_end != "]":
                    if not keep_quoted:
                        chars[index + 1] = " "
                    index += 2
                    continue
                quote_end = None
            elif char != "\n" and not keep_quoted:
                chars[index] = " "
            index += 1
            continue
        if char == "-" and following == "-":
            while index < len(chars) and chars[index] != "\n":
                chars[index] = " "
                index += 1
            continue
        if char == "/" and following == "*":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index < len(chars):
                if chars[index] == "*" and index + 1 < len(chars) and chars[index + 1] == "/":
                    chars[index] = chars[index + 1] = " "
                    index += 2
                    break
                if chars[index] != "\n":
                    chars[index] = " "
                index += 1
            continue
        if char in "'\"`":
            quote_end = char
            if not (keep_double_quoted and char == '"'):
                chars[index] = " "
        elif char == "[":
            quote_end = "]"
            chars[index] = " "
        index += 1
    return "".join(chars)


def has_order_by_clause(statement: str) -> bool:
    return _ORDER_BY_KEYWORD_RE.search(_strip_sql_literals_and_comments(statement)) is not None


def normalize_identifier(value: Any) -> str:
    text = str(value).strip()
    if (
        len(text) >= 2
        and text[0] in "\"`'"
        and text[-1] == text[0]
        or len(text) >= 2
        and text[0] == "["
        and text[-1] == "]"
    ):
        text = text[1:-1]
    return text.strip().casefold()


def _has_balanced_outer_parentheses(text: str) -> bool:
    if not text.startswith("(") or not text.endswith(")"):
        return False
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
            if depth == 0 and index != len(text) - 1:
                return False
    return depth == 0


def normalize_columns(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        text = str(raw).strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        elif text[:6].casefold() == "tuple(" and text.endswith(")"):
            text = text[6:-1].strip()
        elif _has_balanced_outer_parentheses(text):
            text = text[1:-1].strip()
        items = text.split(",") if text else []
    normalized = [normalize_identifier(item) for item in items]
    return tuple(col for col in normalized if col)


def _statement_table(statement: AppliedStatement) -> str | None:
    text = str(statement.statement or "")
    m = _CREATE_INDEX_RE.match(text)
    if m:
        return normalize_identifier(m.group("table"))
    om = _OPTIMIZE_RE.match(text)
    if om:
        return normalize_identifier(om.group("table"))
    at = _ALTER_TABLE_RE.match(text)
    if at:
        return normalize_identifier(at.group("table"))
    ct = _CREATE_TABLE_RE.match(text)
    if ct:
        return normalize_identifier(ct.group("table"))
    alter = _ALTER_TABLE_RE.match(text)
    if alter:
        return normalize_identifier(alter.group("table"))
    if statement.table:
        return normalize_identifier(statement.table)
    return None


def statement_table(statement: AppliedStatement) -> str | None:
    return _statement_table(statement)


def statement_order_by_columns(statement: AppliedStatement) -> tuple[str, ...] | None:
    match = _ORDER_BY_RE.search(_strip_sql_literals_and_comments(str(statement.statement or "")))
    return normalize_columns(match.group("cols")) if match else None


def statement_index_columns(statement: AppliedStatement) -> tuple[str, ...] | None:
    text = _strip_sql_literals_and_comments(str(statement.statement or ""), keep_double_quoted=True).strip()
    match = _CREATE_INDEX_RE.match(text)
    return normalize_columns(match.group("cols")) if match else None


def ledger_tables(ledger: AppliedTuningLedger) -> set[str]:
    tables: set[str] = set()
    for stmt in ledger.executed_statements:
        table = _statement_table(stmt)
        if table:
            tables.add(table)
    return tables


def _classify(statement: AppliedStatement) -> tuple[str, list[_Intent]]:
    text = _strip_sql_literals_and_comments(str(statement.statement or "")).strip()
    lowered = text.lower()

    if statement.phase == PHASE_SESSION or lowered.startswith(_TRANSIENT_PREFIXES):
        return TRANSIENT, []
    if lowered.startswith(_MAINTENANCE_PREFIXES):
        return MAINTENANCE, []

    m = _CREATE_INDEX_RE.match(
        _strip_sql_literals_and_comments(str(statement.statement or ""), keep_double_quoted=True).strip()
    )
    if m:
        return "verifiable", [
            _Intent(
                kind=KIND_INDEX,
                table=normalize_identifier(m.group("table")),
                columns=normalize_columns(m.group("cols")),
                name=normalize_identifier(m.group("name")),
            )
        ]

    ct = _CREATE_TABLE_RE.match(text)
    if ct:
        table = normalize_identifier(ct.group("table"))
        intents: list[_Intent] = []
        order = _ORDER_BY_RE.search(text)
        if order:
            intents.append(_Intent(kind=KIND_SORT_KEY, table=table, columns=normalize_columns(order.group("cols"))))
        part = _PARTITION_BY_RE.search(text)
        if part:
            intents.append(_Intent(kind=KIND_PARTITION_KEY, table=table, columns=normalize_columns(part.group("cols"))))
        cluster = _CLUSTER_BY_RE.search(text)
        if cluster:
            intents.append(
                _Intent(kind=KIND_CLUSTER_KEY, table=table, columns=normalize_columns(cluster.group("cols")))
            )
        if order is None and _ORDER_BY_KEYWORD_RE.search(text) and not _ORDER_BY_NOOP_RE.search(text):
            return UNVERIFIABLE, []
        if cluster is None and _CLUSTER_BY_KEYWORD_RE.search(text):
            return UNVERIFIABLE, []
        if part is None and _PARTITION_BY_KEYWORD_RE.search(text):
            return UNVERIFIABLE, []
        if intents:
            return "verifiable", intents
        return UNVERIFIABLE, []

    at = _ALTER_TABLE_RE.match(text)
    if at:
        if _RECLUSTER_RE.match(text):
            return MAINTENANCE, []
        table = normalize_identifier(at.group("table"))
        cluster = _CLUSTER_BY_RE.search(text)
        if cluster:
            return "verifiable", [
                _Intent(kind=KIND_CLUSTER_KEY, table=table, columns=normalize_columns(cluster.group("cols")))
            ]
        return UNVERIFIABLE, []

    return UNVERIFIABLE, []


_BOUND_REFERENCE_RE = re.compile(r"^\((?P<table>[^.()]+)\.(?P<column>[^.()]+)\)$")
BOUND_EXPRESSION_DIFF_NOTE = "catalog stored a bound expression; identifier case in the DDL differs from the catalog"


def _short_diff(expected: tuple[str, ...], observed: tuple[str, ...], table: str | None = None) -> str:
    diff = f"expected {list(expected)} != observed {list(observed)}"
    if table and any(
        (match := _BOUND_REFERENCE_RE.match(column)) and match.group("table") == table for column in observed
    ):
        return f"{diff}; {BOUND_EXPRESSION_DIFF_NOTE}"
    return diff


def _match_object(intent: _Intent, state: IntrospectedState) -> tuple[str, IntrospectedObject | None]:
    same_table = [
        obj
        for obj in state.objects
        if obj.kind == intent.kind and normalize_identifier(obj.table or "") == (intent.table or "")
    ]
    if not same_table:
        return ABSENT, None
    if not intent.columns:
        return MISMATCH, same_table[0]
    for obj in same_table:
        if obj.columns == intent.columns:
            return CORROBORATED, obj
    named = next(
        (o for o in same_table if intent.name and normalize_identifier(o.name or "") == intent.name),
        None,
    )
    return MISMATCH, named or same_table[0]


def corroborate(ledger: AppliedTuningLedger, introspected_state: IntrospectedState | None) -> IntrospectionReceipt:
    platform = introspected_state.platform if introspected_state else "unknown"
    state_error = None
    if introspected_state is None:
        state_error = "introspection unavailable (no state)"
    elif introspected_state.error:
        state_error = introspected_state.error
    elif introspected_state.truncated:
        state_error = "introspection truncated (catalog row bound hit)"

    entries: list[ReceiptEntry] = []
    for stmt in ledger.statements:
        if stmt.status == STATEMENT_FAILED:
            is_session_failure = stmt.phase == PHASE_SESSION
            reason = (
                "session/config statement failed -- transient, not corroboration-eligible"
                if is_session_failure
                else "ddl/post_load statement failed -- blocks corroboration"
            )
            entries.append(
                ReceiptEntry(
                    statement=stmt.statement,
                    phase=stmt.phase,
                    verdict=TRANSIENT if is_session_failure else UNVERIFIABLE,
                    table=stmt.table,
                    reason=reason,
                )
            )
            continue

        klass, intents = _classify(stmt)

        if klass == TRANSIENT:
            entries.append(
                ReceiptEntry(
                    statement=stmt.statement,
                    phase=stmt.phase,
                    verdict=TRANSIENT,
                    reason="session/config statement -- transient, not corroboration-eligible",
                )
            )
            continue
        if klass == MAINTENANCE:
            entries.append(
                ReceiptEntry(
                    statement=stmt.statement,
                    phase=stmt.phase,
                    verdict=MAINTENANCE,
                    table=stmt.table,
                    reason="maintenance op -- no distinct catalog footprint",
                )
            )
            continue
        if klass == UNVERIFIABLE or not intents:
            entries.append(
                ReceiptEntry(
                    statement=stmt.statement,
                    phase=stmt.phase,
                    verdict=UNVERIFIABLE,
                    table=stmt.table,
                    reason="no corroboration rule for this statement",
                )
            )
            continue

        for intent in intents:
            if state_error is not None or introspected_state is None:
                entries.append(
                    ReceiptEntry(
                        statement=stmt.statement,
                        phase=stmt.phase,
                        verdict=UNVERIFIABLE,
                        kind=intent.kind,
                        table=intent.table,
                        name=intent.name,
                        expected_columns=intent.columns,
                        reason="introspection degraded",
                        detail=state_error,
                    )
                )
                continue

            verdict, fact = _match_object(intent, introspected_state)
            entry = ReceiptEntry(
                statement=stmt.statement,
                phase=stmt.phase,
                verdict=verdict,
                kind=intent.kind,
                table=intent.table,
                name=intent.name,
                expected_columns=intent.columns,
            )
            if fact is not None:
                entry.observed_columns = fact.columns
                entry.evidence = dict(fact.evidence) if fact.evidence else None
            if verdict == MISMATCH:
                observed = fact.columns if fact is not None else ()
                entry.diff = _short_diff(intent.columns, observed, intent.table)
            elif verdict == ABSENT:
                entry.reason = f"no {intent.kind} found in catalog"
            entries.append(entry)

    verifiable = [e for e in entries if e.verdict in _VERIFIABLE_VERDICTS]
    corroborated = bool(verifiable) and all(e.verdict == CORROBORATED for e in verifiable)
    if state_error is not None:
        corroborated = False
    if ledger.dropped:
        corroborated = False

    return IntrospectionReceipt(
        platform=platform,
        corroborated=corroborated,
        entries=entries,
        observed=list(introspected_state.objects) if introspected_state else [],
        dropped=[item.to_dict() for item in ledger.dropped],
        error=state_error,
    )


__all__ = [
    "ABSENT",
    "CORROBORATED",
    "Introspector",
    "IntrospectedObject",
    "IntrospectedState",
    "IntrospectionReceipt",
    "KIND_INDEX",
    "KIND_CLUSTER_KEY",
    "KIND_PARTITION_KEY",
    "KIND_SORT_KEY",
    "MAINTENANCE",
    "MISMATCH",
    "ReceiptEntry",
    "TRANSIENT",
    "UNVERIFIABLE",
    "corroborate",
    "ledger_tables",
    "normalize_columns",
    "normalize_identifier",
]
