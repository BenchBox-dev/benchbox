from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from benchbox.core.tuning.applied_ledger import (
    EXECUTED,
    PHASE_DDL,
    PHASE_POST_LOAD,
    STATEMENT_FAILED,
    AppliedStatement,
    AppliedTuningLedger,
)
from benchbox.core.tuning.capability_registry import TuningCapability, get_capability
from benchbox.core.tuning.interface import TuningType, UnifiedTuningConfiguration
from benchbox.core.tuning.introspection import normalize_identifier, statement_table

NOT_RENDERED_REASON = "adapter rendered no statement"
DDL_REALIZED_REASON = "realized by an executed DDL statement"
RECONCILIATION_FAILED_INTENT = "tuning_reconciliation"

_PHYSICAL_PHASES = frozenset({PHASE_DDL, PHASE_POST_LOAD})

LAYOUT_SLOTS: tuple[tuple[str, TuningType], ...] = (
    ("partitioning", TuningType.PARTITIONING),
    ("clustering", TuningType.CLUSTERING),
    ("distribution", TuningType.DISTRIBUTION),
    ("sorting", TuningType.SORTING),
)
CONSTRAINT_TOGGLES: tuple[tuple[str, TuningType], ...] = (
    ("primary_keys", TuningType.PRIMARY_KEYS),
    ("foreign_keys", TuningType.FOREIGN_KEYS),
    ("unique_constraints", TuningType.UNIQUE_CONSTRAINTS),
    ("check_constraints", TuningType.CHECK_CONSTRAINTS),
)
OPTIMIZATION_FLAGS: tuple[tuple[str, TuningType], ...] = (
    ("z_ordering_enabled", TuningType.Z_ORDERING),
    ("liquid_clustering_enabled", TuningType.LIQUID_CLUSTERING),
    ("auto_optimize_enabled", TuningType.AUTO_OPTIMIZE),
    ("auto_compact_enabled", TuningType.AUTO_COMPACT),
    ("bloom_filters_enabled", TuningType.BLOOM_FILTERS),
    ("materialized_views_enabled", TuningType.MATERIALIZED_VIEWS),
)

_ORDER_BY = r"\border\s+by\b"
_CLUSTER_BY = r"\bcluster(?:ed)?\s+by\b"
_ZORDER_BY = r"\bzorder\s+by\b"
_INDEX_COLUMNS = r"\A\s*create\s+(?:unique\s+)?index\b[^(]*\("

_SORTED_BY = r"\bsorted_by\b"
_PARTITIONED_BY_PROPERTY = r"\bpartitioned_by\b"
_PARTITIONING_PROPERTY = r"\bpartitioning\s*="
_DISTRIBUTION_PROPERTY = r"\bdistribution\s*="
_BUCKETED_BY = r"\bbucketed_by\b"
_OPTIMIZE_WRITE = r"\bautooptimize\.optimizewrite\b"
_AUTO_COMPACT = r"\bautooptimize\.autocompact\b"
_LITERAL_FOOTPRINTS = frozenset(
    {
        _SORTED_BY,
        _PARTITIONED_BY_PROPERTY,
        _PARTITIONING_PROPERTY,
        _DISTRIBUTION_PROPERTY,
        _BUCKETED_BY,
        _OPTIMIZE_WRITE,
        _AUTO_COMPACT,
    }
)

_FOOTPRINTS: dict[TuningType, tuple[str, ...]] = {
    TuningType.SORTING: (_ORDER_BY, r"\bsortkey\b", _SORTED_BY, _INDEX_COLUMNS),
    TuningType.CLUSTERING: (_CLUSTER_BY, _ZORDER_BY, _INDEX_COLUMNS),
    TuningType.PARTITIONING: (r"\bpartition(?:ed)?\s+by\b", _PARTITIONED_BY_PROPERTY, _PARTITIONING_PROPERTY),
    TuningType.DISTRIBUTION: (r"\bdistributed\s+by\b", r"\bdistkey\b", _DISTRIBUTION_PROPERTY, _BUCKETED_BY),
    TuningType.PRIMARY_KEYS: (r"\bprimary\s+key\b",),
    TuningType.FOREIGN_KEYS: (r"\bforeign\s+key\b", r"\breferences\b"),
    TuningType.UNIQUE_CONSTRAINTS: (r"\bunique\b",),
    TuningType.CHECK_CONSTRAINTS: (r"\bcheck\s*\(",),
    TuningType.Z_ORDERING: (_ZORDER_BY,),
    TuningType.LIQUID_CLUSTERING: (_CLUSTER_BY,),
    TuningType.AUTO_OPTIMIZE: (_OPTIMIZE_WRITE,),
    TuningType.AUTO_COMPACT: (_AUTO_COMPACT,),
    TuningType.BLOOM_FILTERS: (r"\bbloom_?filter",),
    TuningType.MATERIALIZED_VIEWS: (r"\bmaterialized\s+view\b",),
}

_MECHANISM_FOOTPRINTS: dict[str, tuple[str, ...]] = {
    "clickhouse_ddl_generator:ORDER_BY": (_ORDER_BY,),
    "adapter_mixin:SnowflakeAdapter.apply_table_tunings": (_CLUSTER_BY,),
}

_CLAUSE_BOUNDARY = re.compile(
    r"\b(?:order\s+by|partition(?:ed)?\s+by|cluster(?:ed)?\s+by|distributed\s+by|zorder\s+by|primary\s+key|"
    r"sample\s+by|ttl|settings|sortkey|distkey|diststyle|tblproperties|location|options|buckets|where|include)\b|;",
    re.IGNORECASE,
)
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_SQL_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_OPTIMIZE_TARGET = re.compile(r"^\s*optimize\s+(?:table\s+)?(?P<table>[^\s;(]+)", re.IGNORECASE)
_SORTED_INGESTION_ENTRY = re.compile(r"^sorted_ingestion\s+(?P<table>\S+)\s+order\s+by\b", re.IGNORECASE)
_TYPED_ENTRY = re.compile(r"^(?P<type>[a-z_]+):\s*(?P<table>[^\s(]+)")
_OPTIMIZATION_ENTRY = re.compile(r"^platform_optimization:(?P<name>\w+)$")

_TUNING_TYPE_VALUES = {tuning_type.value: tuning_type for tuning_type in TuningType}
_OPTIMIZATION_NAMES = dict(OPTIMIZATION_FLAGS)


@dataclass(frozen=True)
class RequestedIntent:
    tuning_type: TuningType
    table: str | None = None
    columns: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        if self.table is None:
            return self.tuning_type.value
        return f"{self.tuning_type.value}:{self.table} ({', '.join(self.columns)})"


def declared_constraint_types(benchmark: Any) -> frozenset[TuningType] | None:
    generate = getattr(benchmark, "get_create_tables_sql", None)
    if not callable(generate):
        return None
    try:
        ddl = generate(dialect="duckdb", tuning_config=UnifiedTuningConfiguration())
    except Exception:
        return None
    if not isinstance(ddl, str):
        return None
    text = _SQL_STRING_LITERAL.sub(" ", _SQL_COMMENT.sub(" ", ddl))
    return frozenset(
        tuning_type
        for _attribute, tuning_type in CONSTRAINT_TOGGLES
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in _FOOTPRINTS[tuning_type])
    )


def requested_intents(config: Any, declared_constraints: frozenset[TuningType] | None = None) -> list[RequestedIntent]:
    intents: list[RequestedIntent] = []
    for table_name, table_tuning in (getattr(config, "table_tunings", None) or {}).items():
        for attribute, tuning_type in LAYOUT_SLOTS:
            columns = sorted(getattr(table_tuning, attribute, None) or [], key=lambda column: column.order)
            if columns:
                intents.append(RequestedIntent(tuning_type, str(table_name), tuple(c.name for c in columns)))
    for attribute, tuning_type in CONSTRAINT_TOGGLES:
        toggle = getattr(config, attribute, None)
        declared = declared_constraints is None or tuning_type in declared_constraints
        if toggle is not None and getattr(toggle, "enabled", False) and declared:
            intents.append(RequestedIntent(tuning_type))
    optimizations = getattr(config, "platform_optimizations", None)
    for attribute, tuning_type in OPTIMIZATION_FLAGS:
        if optimizations is not None and getattr(optimizations, attribute, False):
            intents.append(RequestedIntent(tuning_type))
    return intents


def reconcile_requested_intents(
    config: Any,
    ledger: AppliedTuningLedger,
    platform: str,
    *,
    sorted_tables: Iterable[str] = (),
    declared_constraints: frozenset[TuningType] | None = None,
) -> None:
    executed_sorts = {_bare_table(table) for table in sorted_tables}
    for intent in requested_intents(config, declared_constraints):
        capability = get_capability(platform, intent.tuning_type)
        footprints = _footprints(intent.tuning_type, capability)

        index = _realizing_index(intent, footprints, ledger)
        if index is not None:
            if _realized_in_ddl(capability, ledger.statements[index]) and not _covered(
                intent, footprints, (entry.intent for entry in ledger.satisfied)
            ):
                ledger.record_satisfied(intent.label, index, DDL_REALIZED_REASON)
            continue
        if _accounted(intent, footprints, ledger):
            continue
        if intent.tuning_type is TuningType.SORTING and intent.table and _bare_table(intent.table) in executed_sorts:
            continue
        ledger.record_dropped(intent.label, _drop_reason(capability))


def _realizing_index(intent: RequestedIntent, footprints: tuple[str, ...], ledger: AppliedTuningLedger) -> int | None:
    for index, statement in enumerate(ledger.statements):
        if (
            statement.status == EXECUTED
            and statement.phase in _PHYSICAL_PHASES
            and _shows(intent, statement, footprints, match_columns=True)
        ):
            return index
    return None


def _footprints(tuning_type: TuningType, capability: TuningCapability | None) -> tuple[str, ...]:
    extra = _MECHANISM_FOOTPRINTS.get(capability.mechanism_id, ()) if capability is not None else ()
    return _FOOTPRINTS.get(tuning_type, ()) + extra


def _realized_in_ddl(capability: TuningCapability | None, statement: AppliedStatement) -> bool:
    return capability is not None and capability.rendered_via == "ddl" and statement.phase == PHASE_DDL


def _drop_reason(capability: TuningCapability | None) -> str:
    if capability is None:
        return NOT_RENDERED_REASON
    if capability.rendered_via == "none" or ":preview_only" in capability.mechanism_id:
        return capability.notes or f"not rendered at execution ({capability.mechanism_id})"
    return NOT_RENDERED_REASON


def _accounted(intent: RequestedIntent, footprints: tuple[str, ...], ledger: AppliedTuningLedger) -> bool:
    for statement in ledger.statements:
        if (
            statement.status == STATEMENT_FAILED
            and statement.phase in _PHYSICAL_PHASES
            and _shows(intent, statement, footprints, match_columns=False)
        ):
            return True
    entries = [entry.intent for entry in ledger.dropped] + [entry.intent for entry in ledger.satisfied]
    return _covered(intent, footprints, entries)


def _covered(intent: RequestedIntent, footprints: tuple[str, ...], entries: Iterable[str]) -> bool:
    target_table = _bare_table(intent.table) if intent.table is not None else None
    for entry in entries:
        parsed = _entry_target(entry)
        if parsed is not None:
            tuning_type, table = parsed
            if tuning_type is intent.tuning_type and (target_table is None or table == target_table):
                return True
            continue
        pseudo = AppliedStatement(statement=entry, phase=PHASE_DDL, status=EXECUTED)
        if _shows(intent, pseudo, footprints, match_columns=False):
            return True
    return False


def _entry_target(entry: str) -> tuple[TuningType, str | None] | None:
    text = str(entry).strip()
    optimization = _OPTIMIZATION_ENTRY.match(text)
    if optimization is not None:
        tuning_type = _OPTIMIZATION_NAMES.get(optimization.group("name"))
        return (tuning_type, None) if tuning_type is not None else None
    sorted_ingestion = _SORTED_INGESTION_ENTRY.match(text)
    if sorted_ingestion is not None:
        return TuningType.SORTING, _bare_table(sorted_ingestion.group("table"))
    typed = _TYPED_ENTRY.match(text)
    if typed is not None and typed.group("type") in _TUNING_TYPE_VALUES:
        return _TUNING_TYPE_VALUES[typed.group("type")], _bare_table(typed.group("table"))
    if text in _TUNING_TYPE_VALUES:
        return _TUNING_TYPE_VALUES[text], None
    return None


def _shows(
    intent: RequestedIntent,
    statement: AppliedStatement,
    footprints: tuple[str, ...],
    *,
    match_columns: bool,
) -> bool:
    if intent.table is not None and _statement_target(statement) != _bare_table(intent.table):
        return False
    raw = _SQL_COMMENT.sub(" ", str(statement.statement or ""))
    bare = _SQL_STRING_LITERAL.sub(" ", raw)
    if intent.table is None or not match_columns:
        return any(re.search(pattern, _searched(pattern, raw, bare), re.IGNORECASE) for pattern in footprints)
    wanted = [normalize_identifier(column) for column in intent.columns]
    return any(_in_order(wanted, _clause_identifiers(tail)) for tail in _clause_tails(raw, bare, footprints))


def _searched(pattern: str, raw: str, bare: str) -> str:
    return raw if pattern in _LITERAL_FOOTPRINTS else bare


def _clause_tails(raw: str, bare: str, footprints: tuple[str, ...]) -> Iterable[str]:
    for pattern in footprints:
        text = _searched(pattern, raw, bare)
        for match in re.finditer(pattern, text, re.IGNORECASE):
            tail = text[match.end() :]
            boundary = _CLAUSE_BOUNDARY.search(tail)
            yield tail[: boundary.start()] if boundary is not None else tail


def _clause_identifiers(clause: str) -> list[str]:
    return [token.casefold() for token in _IDENTIFIER.findall(clause)]


def _in_order(wanted: list[str], identifiers: list[str]) -> bool:
    remaining = iter(identifiers)
    return all(column in remaining for column in wanted)


def _statement_target(statement: AppliedStatement) -> str | None:
    table = statement_table(statement)
    if table is None:
        optimize = _OPTIMIZE_TARGET.match(str(statement.statement or ""))
        table = optimize.group("table") if optimize is not None else None
    return _bare_table(table) if table is not None else None


def _bare_table(name: str) -> str:
    last = str(name).strip().rsplit(".", 1)[-1]
    return normalize_identifier(last.strip('"`[]'))


__all__ = [
    "CONSTRAINT_TOGGLES",
    "DDL_REALIZED_REASON",
    "LAYOUT_SLOTS",
    "NOT_RENDERED_REASON",
    "OPTIMIZATION_FLAGS",
    "RECONCILIATION_FAILED_INTENT",
    "RequestedIntent",
    "declared_constraint_types",
    "reconcile_requested_intents",
    "requested_intents",
]
