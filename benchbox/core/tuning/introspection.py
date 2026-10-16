# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import re
import string
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
ATTESTED = "attested"

_VERIFIABLE_VERDICTS = frozenset({CORROBORATED, ABSENT, MISMATCH, UNVERIFIABLE})

KIND_INDEX = "index"
KIND_SORT_KEY = "sort_key"
KIND_PARTITION_KEY = "partition_key"
KIND_CLUSTER_KEY = "cluster_key"
KIND_CONSTRAINT = "constraint"

CONSTRAINT_PRIMARY_KEY = "PRIMARY KEY"
CONSTRAINT_UNIQUE = "UNIQUE"
CONSTRAINT_FOREIGN_KEY = "FOREIGN KEY"

_DDL_PHASES = frozenset({PHASE_DDL, PHASE_POST_LOAD})


@dataclass
class IntrospectedObject:
    kind: str
    table: str | None
    columns: tuple[str, ...] = ()
    name: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    constraint_type: str | None = None
    referenced_table: str | None = None
    referenced_columns: tuple[str, ...] = ()


@dataclass
class IntrospectedState:
    platform: str
    objects: list[IntrospectedObject] = field(default_factory=list)
    error: str | None = None
    truncated: bool = False
    constraint_types: frozenset[str] = frozenset()


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
    constraint_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "statement": self.statement,
            "phase": self.phase,
            "verdict": self.verdict,
        }
        if self.kind:
            payload["kind"] = self.kind
        if self.constraint_type:
            payload["constraint_type"] = self.constraint_type
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
                    **({"constraint_type": obj.constraint_type} if obj.constraint_type else {}),
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
    constraint_type: str | None = None
    referenced_table: str | None = None
    referenced_columns: tuple[str, ...] = ()


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
_CONSTRAINT_KEYWORD_RE = re.compile(
    r"\b(?:primary\s+key|foreign\s+key|unique|references|check|constraint)\b", re.IGNORECASE
)
_FOLLOWING_STATEMENT_RE = re.compile(r";\s*\S")
_REFERENTIAL_ACTION_RE = re.compile(r"\bon\s+(?:delete|update)\b|\bmatch\b", re.IGNORECASE)
_TABLE_PRIMARY_KEY_RE = re.compile(r"\s*primary\s+key\s*\((?P<cols>[^()]*)\)\s*", re.IGNORECASE)
_TABLE_UNIQUE_RE = re.compile(r"\s*unique\s*\((?P<cols>[^()]*)\)\s*", re.IGNORECASE)
_TABLE_FOREIGN_KEY_RE = re.compile(
    r"\s*foreign\s+key\s*\((?P<cols>[^()]*)\)\s*references\s+(?P<table>[^\s(]+)\s*\((?P<ref>[^()]*)\)\s*",
    re.IGNORECASE,
)
_INLINE_REFERENCES_RE = re.compile(r"references\s+(?P<table>[^\s(]+)\s*\((?P<ref>[^()]*)\)", re.IGNORECASE)
_COLUMN_NAME_RE = re.compile(r'\s*(?P<name>"(?:[^"]|"")+"|[^\s"(),]+)')
_CREATE_TABLE_PREFIX_RE = re.compile(
    r"\s*create\s+(?:or\s+replace\s+)?table\s+(?:if\s+not\s+exists\s+)?", re.IGNORECASE
)
_QUOTED_IDENTIFIER_RE = re.compile(r'"(?P<body>(?:[^"]|"")+)"')
_BARE_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_IDENTIFIER_LIST_ITEM_RE = re.compile(r'\s*(?P<item>"(?:[^"]|"")*"|[^",\s]+)\s*(?P<sep>,|$)')
_NON_DOUBLE_QUOTE_OPENERS = ("`", "[", "'")
_UNMASKED_LITERAL_RES = (
    re.compile(r"\$(?:[^\W$]\w*)?\$"),
    re.compile(r"(?<![A-Za-z0-9_$])[eE]'"),
    re.compile(r"/\*(?:(?!\*/).)*?/\*", re.DOTALL),
    re.compile(r"/\*(?!.*\*/)", re.DOTALL),
    re.compile(r"\\['\"]"),
)
_DIALECT_DEPENDENT_QUOTE_RE = re.compile(r"[\[`]")
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_LAYOUT_KEYWORD_RES = (_ORDER_BY_KEYWORD_RE, _PARTITION_BY_KEYWORD_RE, _CLUSTER_BY_KEYWORD_RE)
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


def constraint_tables(ledger: AppliedTuningLedger) -> set[str]:
    tables: set[str] = set()
    for statement in ledger.executed_statements:
        _klass, intents = _classify(statement)
        tables.update(intent.table for intent in intents if intent.kind == KIND_CONSTRAINT and intent.table)
    return tables


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


def fold_physical_identifier(value: Any) -> str:
    return str(value).translate(_ASCII_LOWER)


def _physical_identifier(text: str) -> str | None:
    candidate = text.strip()
    quoted = _QUOTED_IDENTIFIER_RE.fullmatch(candidate)
    if quoted is not None:
        return fold_physical_identifier(quoted.group("body").replace('""', '"'))
    if _BARE_IDENTIFIER_RE.fullmatch(candidate):
        return fold_physical_identifier(candidate)
    return None


def _physical_identifier_list(text: str) -> tuple[str, ...] | None:
    if not text.strip():
        return None
    identifiers: list[str] = []
    position = 0
    while position < len(text):
        item = _IDENTIFIER_LIST_ITEM_RE.match(text, position)
        if item is None:
            return None
        identifier = _physical_identifier(item.group("item"))
        if identifier is None:
            return None
        identifiers.append(identifier)
        position = item.end()
        if not item.group("sep"):
            return tuple(identifiers)
    return None


def _create_table_body_span(stripped: str, start: int) -> tuple[int, int] | None:
    index = start
    while index < len(stripped) and stripped[index].isspace():
        index += 1
    if index >= len(stripped) or stripped[index] != "(":
        return None
    depth = 0
    for position in range(index, len(stripped)):
        if stripped[position] == "(":
            depth += 1
        elif stripped[position] == ")":
            depth -= 1
            if depth == 0:
                return index + 1, position
    return index + 1, -1


def _top_level_element_spans(stripped: str, begin: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth = 0
    element_start = begin
    for position in range(begin, end):
        char = stripped[position]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            spans.append((element_start, position))
            element_start = position + 1
    spans.append((element_start, end))
    return spans


def _constraint_intent(
    constraint_type: str,
    table: str,
    columns: tuple[str, ...],
    referenced_table: str | None = None,
    referenced_columns: tuple[str, ...] = (),
) -> _Intent:
    return _Intent(
        kind=KIND_CONSTRAINT,
        table=table,
        columns=columns,
        constraint_type=constraint_type,
        referenced_table=referenced_table,
        referenced_columns=referenced_columns,
    )


def _table_level_constraint(element: str, keywords: list[str], table: str) -> _Intent | None:
    if keywords == ["primary key"]:
        match = _TABLE_PRIMARY_KEY_RE.fullmatch(element)
        constraint_type = CONSTRAINT_PRIMARY_KEY
    elif keywords == ["unique"]:
        match = _TABLE_UNIQUE_RE.fullmatch(element)
        constraint_type = CONSTRAINT_UNIQUE
    elif keywords == ["foreign key", "references"]:
        match = _TABLE_FOREIGN_KEY_RE.fullmatch(element)
        constraint_type = CONSTRAINT_FOREIGN_KEY
    else:
        return None
    if match is None:
        return None
    columns = _physical_identifier_list(match.group("cols"))
    if columns is None:
        return None
    if constraint_type != CONSTRAINT_FOREIGN_KEY:
        return _constraint_intent(constraint_type, table, columns)
    referenced_table = _physical_identifier(match.group("table"))
    referenced_columns = _physical_identifier_list(match.group("ref"))
    if referenced_table is None or referenced_columns is None:
        return None
    return _constraint_intent(constraint_type, table, columns, referenced_table, referenced_columns)


def _column_level_constraints(element: str, keyword_matches: list[re.Match[str]], table: str) -> list[_Intent] | None:
    name = _COLUMN_NAME_RE.match(element)
    if name is None:
        return None
    column = _physical_identifier(name.group("name"))
    if column is None:
        return None
    intents: list[_Intent] = []
    for keyword_match in keyword_matches:
        keyword = re.sub(r"\s+", " ", keyword_match.group(0).lower())
        if keyword == "primary key":
            intents.append(_constraint_intent(CONSTRAINT_PRIMARY_KEY, table, (column,)))
        elif keyword == "unique":
            intents.append(_constraint_intent(CONSTRAINT_UNIQUE, table, (column,)))
        elif keyword == "references":
            reference = _INLINE_REFERENCES_RE.match(element, keyword_match.start())
            if reference is None:
                return None
            referenced_table = _physical_identifier(reference.group("table"))
            referenced_columns = _physical_identifier_list(reference.group("ref"))
            if referenced_table is None or referenced_columns is None:
                return None
            intents.append(
                _constraint_intent(CONSTRAINT_FOREIGN_KEY, table, (column,), referenced_table, referenced_columns)
            )
        else:
            return None
    return intents


def _constraint_intents(statement_text: str) -> list[_Intent] | None:
    stripped = _strip_sql_literals_and_comments(statement_text)
    quoted = _strip_sql_literals_and_comments(statement_text, keep_double_quoted=True)
    header = _CREATE_TABLE_RE.match(quoted)
    prefix = _CREATE_TABLE_PREFIX_RE.match(quoted)
    if header is None or prefix is None:
        return []
    body = _create_table_body_span(stripped, header.end())
    if body is None:
        return []
    if any(pattern.search(statement_text) for pattern in _UNMASKED_LITERAL_RES):
        return None
    if _DIALECT_DEPENDENT_QUOTE_RE.search(statement_text) and _CONSTRAINT_KEYWORD_RE.search(statement_text):
        return None
    begin, end = body
    if end < 0:
        return None if _CONSTRAINT_KEYWORD_RE.search(stripped, begin) else []
    if _FOLLOWING_STATEMENT_RE.search(stripped, end + 1):
        return None
    table = _physical_identifier(quoted[prefix.end() : begin - 1])
    intents: list[_Intent] = []
    for element_begin, element_end in _top_level_element_spans(stripped, begin, end):
        stripped_element = stripped[element_begin:element_end]
        keyword_matches = list(_CONSTRAINT_KEYWORD_RE.finditer(stripped_element))
        if not keyword_matches:
            continue
        if table is None or _REFERENTIAL_ACTION_RE.search(stripped_element):
            return None
        if statement_text[element_begin:element_end].lstrip()[:1] in _NON_DOUBLE_QUOTE_OPENERS:
            return None
        element = quoted[element_begin:element_end]
        if stripped_element[: keyword_matches[0].start()].strip():
            column_intents = _column_level_constraints(element, keyword_matches, table)
            if column_intents is None:
                return None
            intents.extend(column_intents)
            continue
        keywords = [re.sub(r"\s+", " ", match.group(0).lower()) for match in keyword_matches]
        intent = _table_level_constraint(element, keywords, table)
        if intent is None:
            return None
        intents.append(intent)
    return intents


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
    quoted_ct = _CREATE_TABLE_RE.match(
        _strip_sql_literals_and_comments(str(statement.statement or ""), keep_double_quoted=True).strip()
    )
    if ct or quoted_ct:
        intents: list[_Intent] = []
        if ct:
            table = normalize_identifier(ct.group("table"))
            order = _ORDER_BY_RE.search(text)
            if order:
                intents.append(_Intent(kind=KIND_SORT_KEY, table=table, columns=normalize_columns(order.group("cols"))))
            part = _PARTITION_BY_RE.search(text)
            if part:
                intents.append(
                    _Intent(kind=KIND_PARTITION_KEY, table=table, columns=normalize_columns(part.group("cols")))
                )
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
        elif any(pattern.search(text) for pattern in _LAYOUT_KEYWORD_RES):
            return UNVERIFIABLE, []
        constraints = _constraint_intents(str(statement.statement or ""))
        if constraints is None:
            return UNVERIFIABLE, []
        intents.extend(constraints)
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


def _references(columns: tuple[str, ...], table: str | None, referenced: tuple[str, ...]) -> str:
    return f"{list(columns)} references {table}{list(referenced)}"


def _foreign_key_diff(intent: _Intent, fact: IntrospectedObject | None) -> str:
    expected = _references(intent.columns, intent.referenced_table, intent.referenced_columns)
    if fact is None:
        return f"expected {expected} != observed nothing"
    observed_table = fold_physical_identifier(fact.referenced_table) if fact.referenced_table else None
    return f"expected {expected} != observed {_references(fact.columns, observed_table, fact.referenced_columns)}"


def _folded(identifiers: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(fold_physical_identifier(identifier) for identifier in identifiers)


def _has_reference(fact: IntrospectedObject) -> bool:
    return bool(fact.referenced_table) and bool(fact.referenced_columns)


def _match_constraint(intent: _Intent, state: IntrospectedState) -> tuple[str, IntrospectedObject | None, str | None]:
    if intent.constraint_type not in state.constraint_types:
        return UNVERIFIABLE, None, f"{state.platform} introspection reads no {intent.constraint_type} constraints"
    same_type = [
        obj
        for obj in state.objects
        if obj.kind == KIND_CONSTRAINT
        and obj.constraint_type == intent.constraint_type
        and obj.table is not None
        and fold_physical_identifier(obj.table) == intent.table
    ]
    if not same_type:
        return ABSENT, None, f"no {intent.constraint_type} constraint found in catalog"
    if not intent.columns:
        return MISMATCH, same_type[0], None
    same_columns = [obj for obj in same_type if _folded(obj.columns) == intent.columns]
    if intent.constraint_type != CONSTRAINT_FOREIGN_KEY:
        return (CORROBORATED, same_columns[0], None) if same_columns else (MISMATCH, same_type[0], None)
    if not intent.referenced_table or not intent.referenced_columns:
        return UNVERIFIABLE, None, "foreign key statement names no referenced table or columns"
    for obj in same_columns:
        if (
            _has_reference(obj)
            and fold_physical_identifier(obj.referenced_table) == intent.referenced_table
            and _folded(obj.referenced_columns) == intent.referenced_columns
        ):
            return CORROBORATED, obj, None
    incomplete = next((obj for obj in same_columns if not _has_reference(obj)), None)
    if incomplete is not None:
        return UNVERIFIABLE, incomplete, "catalog foreign key fact has no referenced table or columns"
    return MISMATCH, (same_columns or same_type)[0], None


def _match_object(intent: _Intent, state: IntrospectedState) -> tuple[str, IntrospectedObject | None]:
    if intent.kind == KIND_CONSTRAINT:
        verdict, fact, _reason = _match_constraint(intent, state)
        return verdict, fact
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


def _attested_sort_columns(statement_text: str) -> tuple[str, ...]:
    from benchbox.core.tuning.applied_ledger import _split_sql_script

    segments = [segment for segment in _split_sql_script(statement_text) if segment.strip()]
    for segment in segments:
        order = _ORDER_BY_RE.search(segment)
        if order:
            return normalize_columns(order.group("cols"))
    for segment in segments:
        keyword = _ORDER_BY_KEYWORD_RE.search(segment)
        if keyword is None:
            continue
        tail = segment[keyword.end() :]
        columns: list[str] = []
        for chunk in tail.split(","):
            token = re.sub(r"(?i)\b(asc|desc|nulls\s+first|nulls\s+last)\b", " ", chunk).strip()
            token = token.strip().strip(";")
            if token:
                columns.append(token)
        if columns:
            return normalize_columns(columns)
    return ()


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

        if stmt.phase == PHASE_POST_LOAD and stmt.mechanism == "sorted_ingestion":
            entries.append(
                ReceiptEntry(
                    statement=stmt.statement,
                    phase=stmt.phase,
                    verdict=ATTESTED,
                    kind=KIND_SORT_KEY,
                    table=stmt.table,
                    expected_columns=_attested_sort_columns(str(stmt.statement or "")),
                    reason="physical sort executed; no catalog footprint",
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
                        constraint_type=intent.constraint_type,
                    )
                )
                continue

            if intent.kind == KIND_CONSTRAINT:
                verdict, fact, reason = _match_constraint(intent, introspected_state)
            else:
                verdict, fact = _match_object(intent, introspected_state)
                reason = f"no {intent.kind} found in catalog" if verdict == ABSENT else None
            entry = ReceiptEntry(
                statement=stmt.statement,
                phase=stmt.phase,
                verdict=verdict,
                kind=intent.kind,
                table=intent.table,
                name=intent.name,
                expected_columns=intent.columns,
                reason=reason,
                constraint_type=intent.constraint_type,
            )
            if fact is not None:
                entry.observed_columns = fact.columns
                entry.evidence = dict(fact.evidence) if fact.evidence else None
            if verdict == MISMATCH:
                if intent.constraint_type == CONSTRAINT_FOREIGN_KEY:
                    entry.diff = _foreign_key_diff(intent, fact)
                else:
                    observed = fact.columns if fact is not None else ()
                    entry.diff = _short_diff(intent.columns, observed, intent.table)
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
    "ATTESTED",
    "CONSTRAINT_FOREIGN_KEY",
    "CONSTRAINT_PRIMARY_KEY",
    "CONSTRAINT_UNIQUE",
    "CORROBORATED",
    "Introspector",
    "IntrospectedObject",
    "IntrospectedState",
    "IntrospectionReceipt",
    "KIND_INDEX",
    "KIND_CLUSTER_KEY",
    "KIND_CONSTRAINT",
    "KIND_PARTITION_KEY",
    "KIND_SORT_KEY",
    "MAINTENANCE",
    "MISMATCH",
    "ReceiptEntry",
    "TRANSIENT",
    "UNVERIFIABLE",
    "constraint_tables",
    "corroborate",
    "fold_physical_identifier",
    "ledger_tables",
    "normalize_columns",
    "normalize_identifier",
]
