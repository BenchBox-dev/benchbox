#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path

import duckdb
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPECTED = Path(__file__).resolve().with_name("EXPECTED_DIFFERENCES.yaml")
DEFAULT_DEPARTURES = REPO_ROOT / "benchbox" / "core" / "tpcdi" / "etl" / "DEPARTURES.yaml"

NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "dimaccount": ("accountid", "effectivedate"),
    "dimbroker": ("brokerid",),
    "dimcompany": ("companyid", "effectivedate"),
    "dimcustomer": ("customerid", "effectivedate"),
    "dimdate": ("sk_dateid",),
    "dimsecurity": ("symbol", "effectivedate"),
    "dimtime": ("sk_timeid",),
    "dimtrade": ("tradeid", "sk_createdateid", "sk_createtimeid"),
    "factcashbalances": ("sk_customerid", "sk_accountid", "sk_dateid"),
    "factholdings": ("tradeid", "currenttradeid"),
    "factmarkethistory": ("sk_securityid", "sk_dateid"),
    "factwatches": ("sk_customerid", "sk_securityid", "sk_dateid_dateplaced"),
    "financial": ("sk_companyid", "fi_year", "fi_qtr"),
    "industry": ("in_id",),
    "prospect": ("agencyid",),
    "statustype": ("st_id",),
    "taxrate": ("tx_id",),
    "tradetype": ("tt_id",),
}

MESSAGES_TABLE = "dimessages"
MESSAGE_COLUMNS = ("batchid", "messagetype", "messagesource", "messagetext")
AUDIT_ABSENT = "<absent>"

KINDS: dict[str, set[str]] = {
    "table_missing": {"table", "side"},
    "column_missing": {"table", "column", "side"},
    "column_type": {"table", "column", "left_type", "right_type"},
    "column_value": {"table", "column", "condition"},
    "rows_only": {"table", "side", "condition"},
    "messages": {"condition"},
    "audit": {"test", "batch", "left_result", "right_result"},
}
ENTRY_COMMON = {"id", "kind", "clause", "reason", "departure"}
SIDES = {"left", "right"}

INTEGER_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
}
DECIMAL_PATTERN = re.compile(r"^DECIMAL\((\d+),\s*(\d+)\)$", re.IGNORECASE)

AuditRunner = Callable[[Path], list[tuple]]


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Expected:
    id: str
    kind: str
    clause: str
    reason: str
    departure: str | None
    fields: dict[str, object]

    def text(self, name: str) -> str:
        return str(self.fields[name])


@dataclass
class Difference:
    table: str
    kind: str
    subject: str
    count: int
    unexplained: int
    accepted_by: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.unexplained == 0


@dataclass
class TableReport:
    table: str
    left_rows: int | None
    right_rows: int | None
    key: list[str]
    differences: list[Difference] = field(default_factory=list)


@dataclass
class Report:
    left_name: str
    right_name: str
    tables: list[TableReport] = field(default_factory=list)
    messages: dict[str, dict[str, int]] = field(default_factory=dict)
    audit: list[dict[str, object]] = field(default_factory=list)
    differences: list[Difference] = field(default_factory=list)
    unused_entries: list[str] = field(default_factory=list)

    def all_differences(self) -> list[Difference]:
        found = list(self.differences)
        for table in self.tables:
            found.extend(table.differences)
        return found

    def unaccepted(self) -> list[Difference]:
        return [difference for difference in self.all_differences() if not difference.accepted]


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def load_departure_ids(path: Path) -> set[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else (data or {}).get("departures", [])
    return {str(row["id"]) for row in rows}


def load_expected(path: Path, departures: Path | None = DEFAULT_DEPARTURES) -> list[Expected]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("entries")
    if not isinstance(raw, list):
        raise ConfigError(f"{path}: 'entries' must be a list")
    departure_ids = load_departure_ids(departures) if departures is not None and departures.exists() else None
    entries: list[Expected] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ConfigError(f"{path}: every entry must be a mapping")
        entry_id = str(item.get("id", ""))
        kind = item.get("kind")
        if not entry_id or entry_id in seen:
            raise ConfigError(f"{path}: entry id missing or duplicated: {entry_id!r}")
        seen.add(entry_id)
        if kind not in KINDS:
            raise ConfigError(f"{entry_id}: unknown kind {kind!r}")
        required = (ENTRY_COMMON - {"departure"}) | KINDS[kind]
        missing = sorted(name for name in required if not str(item.get(name, "")).strip())
        if missing:
            raise ConfigError(f"{entry_id}: missing {', '.join(missing)}")
        unknown = sorted(set(item) - ENTRY_COMMON - KINDS[kind])
        if unknown:
            raise ConfigError(f"{entry_id}: unknown fields {', '.join(unknown)}")
        if "side" in KINDS[kind] and item["side"] not in SIDES:
            raise ConfigError(f"{entry_id}: side must be left or right")
        departure = item.get("departure")
        if departure_ids is not None and str(departure) not in departure_ids:
            raise ConfigError(f"{entry_id}: departure {departure!r} is not a row of {departures}")
        entries.append(
            Expected(
                id=entry_id,
                kind=kind,
                clause=str(item["clause"]),
                reason=str(item["reason"]),
                departure=None if departure is None else str(departure),
                fields={name: item[name] for name in KINDS[kind]},
            )
        )
    return entries


def matches(pattern: object, value: object) -> bool:
    return fnmatch.fnmatchcase(str(value).lower(), str(pattern).lower())


def type_scale(type_name: str) -> int | None:
    found = DECIMAL_PATTERN.match(type_name)
    return int(found.group(2)) if found else None


def type_class(type_name: str) -> str:
    upper = type_name.upper()
    if DECIMAL_PATTERN.match(upper):
        return "decimal"
    if upper in ("DOUBLE", "FLOAT"):
        return "float"
    if upper in INTEGER_TYPES:
        return "integer"
    if upper == "BOOLEAN":
        return "boolean"
    return "other"


def type_family(type_name: str) -> str:
    kind = type_class(type_name)
    scale = type_scale(type_name)
    if kind == "integer" or (kind == "decimal" and scale == 0):
        return "integer"
    if kind == "decimal":
        return f"decimal({scale})"
    return type_name.upper()


def normalized(reference: str, own_type: str, other_type: str) -> str:
    if own_type.upper() == other_type.upper():
        return reference
    own, other = type_class(own_type), type_class(other_type)
    numeric = {"decimal", "float", "integer", "boolean"}
    if own not in numeric or other not in numeric:
        return f"CAST({reference} AS VARCHAR)"
    value = f"CAST({reference} AS INTEGER)" if own == "boolean" else reference
    own = "integer" if own == "boolean" else own
    other = "integer" if other == "boolean" else other
    scales = [scale for scale in (type_scale(own_type), type_scale(other_type)) if scale is not None]
    if scales:
        scale = max(scales)
        if own == "float":
            value = f"ROUND(CAST({value} AS DOUBLE), {scale})"
        return f"CAST({value} AS DECIMAL(38,{scale}))"
    if "float" in (own, other):
        return f"CAST({value} AS DOUBLE)"
    return f"CAST({value} AS HUGEINT)"


def connect(left: Path, right: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute(f"ATTACH '{left}' AS lhs (READ_ONLY)")
    connection.execute(f"ATTACH '{right}' AS rhs (READ_ONLY)")
    return connection


def list_tables(connection: duckdb.DuckDBPyConnection, catalog: str) -> dict[str, str]:
    rows = connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_catalog = ? AND table_schema = 'main' AND table_type = 'BASE TABLE'",
        [catalog],
    ).fetchall()
    return {name.lower(): name for (name,) in rows}


def list_columns(connection: duckdb.DuckDBPyConnection, catalog: str, table: str) -> dict[str, tuple[str, str]]:
    rows = connection.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_catalog = ? AND table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
        [catalog, table],
    ).fetchall()
    return {name.lower(): (name, data_type) for name, data_type in rows}


def source(catalog: str, table: str) -> str:
    return f"{catalog}.main.{quote(table)}"


def disjunction(entries: Sequence[Expected]) -> str:
    if not entries:
        return "FALSE"
    return " OR ".join(f"COALESCE(({entry.text('condition')}), FALSE)" for entry in entries)


def multiset_difference(
    left_table: str,
    right_table: str,
    left_cols: dict[str, tuple[str, str]],
    right_cols: dict[str, tuple[str, str]],
    columns: Sequence[str],
) -> str:
    def grouped(catalog: str, table: str, own: dict[str, tuple[str, str]], other: dict[str, tuple[str, str]]) -> str:
        values = [
            f"{normalized(quote(own[name][0]), own[name][1], other[name][1])} AS {quote(name)}" for name in columns
        ]
        return f"SELECT {', '.join([*values, 'count(*) AS n'])} FROM {source(catalog, table)} GROUP BY ALL"

    picks = [f"COALESCE(l.{quote(name)}, r.{quote(name)}) AS {quote(name)}" for name in columns]
    on = " AND ".join(f"l.{quote(name)} IS NOT DISTINCT FROM r.{quote(name)}" for name in columns) or "TRUE"
    return (
        f"(SELECT {', '.join([*picks, 'l.n AS left_n', 'r.n AS right_n'])} "
        f"FROM ({grouped('lhs', left_table, left_cols, right_cols)}) l "
        f"FULL JOIN ({grouped('rhs', right_table, right_cols, left_cols)}) r ON {on}) t"
    )


def tally(
    connection: duckdb.DuckDBPyConnection,
    from_clause: str,
    flagged: str,
    entries: Sequence[Expected],
    used: set[str],
    where: str = "TRUE",
    weight: str = "1",
) -> tuple[int, int, list[str]]:
    selects = [
        f"COALESCE(sum({weight}) FILTER (WHERE {flagged}), 0)",
        f"COALESCE(sum({weight}) FILTER (WHERE ({flagged}) AND NOT ({disjunction(entries)})), 0)",
    ]
    selects.extend(
        f"COALESCE(sum({weight}) FILTER (WHERE ({flagged}) AND COALESCE(({entry.text('condition')}), FALSE)), 0)"
        for entry in entries
    )
    row = connection.execute(f"SELECT {', '.join(selects)} FROM {from_clause} WHERE {where}").fetchone()
    accepted_by = [entry.id for entry, hits in zip(entries, row[2:], strict=True) if hits]
    used.update(accepted_by)
    return int(row[0]), int(row[1]), accepted_by


def entries_of(entries: Sequence[Expected], kind: str, **selectors: str) -> list[Expected]:
    return [
        entry
        for entry in entries
        if entry.kind == kind and all(matches(entry.fields[name], value) for name, value in selectors.items())
    ]


def entries_on_side(entries: Sequence[Expected], side: str) -> list[Expected]:
    return [entry for entry in entries if entry.fields["side"] == side]


def keys_are_unique(connection: duckdb.DuckDBPyConnection, catalog: str, table: str, key: Sequence[str]) -> bool:
    columns = ", ".join(quote(name) for name in key)
    duplicated = connection.execute(
        f"SELECT count(*) FROM (SELECT 1 FROM {source(catalog, table)} GROUP BY {columns} HAVING count(*) > 1)"
    ).fetchone()[0]
    return duplicated == 0


def structure_differences(
    table: str,
    left_cols: dict[str, tuple[str, str]],
    right_cols: dict[str, tuple[str, str]],
    entries: Sequence[Expected],
    used: set[str],
) -> list[Difference]:
    found: list[Difference] = []
    for side, present, absent in (("left", left_cols, right_cols), ("right", right_cols, left_cols)):
        for lowered, (name, _) in present.items():
            if lowered in absent:
                continue
            chosen = entries_on_side(entries_of(entries, "column_missing", table=table, column=name), side)
            used.update(entry.id for entry in chosen)
            found.append(
                Difference(table, f"column_only_{side}", name, 1, 0 if chosen else 1, [entry.id for entry in chosen])
            )
    for lowered, (name, left_type) in left_cols.items():
        if lowered not in right_cols:
            continue
        right_type = right_cols[lowered][1]
        if type_family(left_type) == type_family(right_type):
            continue
        chosen = [
            entry
            for entry in entries_of(entries, "column_type", table=table, column=name)
            if matches(entry.fields["left_type"], left_type) and matches(entry.fields["right_type"], right_type)
        ]
        used.update(entry.id for entry in chosen)
        found.append(
            Difference(
                table,
                "column_type",
                f"{name} {left_type} vs {right_type}",
                1,
                0 if chosen else 1,
                [e.id for e in chosen],
            )
        )
    return found


def equality(
    own_cols: dict[str, tuple[str, str]],
    other_cols: dict[str, tuple[str, str]],
    columns: Sequence[str],
    own_alias: str,
    other_alias: str,
    null_safe: bool,
) -> str:
    operator = "IS NOT DISTINCT FROM" if null_safe else "="
    terms = []
    for lowered in columns:
        own_name, own_type = own_cols[lowered]
        other_name, other_type = other_cols[lowered]
        terms.append(
            f"{normalized(f'{own_alias}.{quote(own_name)}', own_type, other_type)} {operator} "
            f"{normalized(f'{other_alias}.{quote(other_name)}', other_type, own_type)}"
        )
    return " AND ".join(terms) if terms else "TRUE"


def compare_table(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    left_table: str,
    right_table: str,
    entries: Sequence[Expected],
    used: set[str],
) -> TableReport:
    left_cols = list_columns(connection, "lhs", left_table)
    right_cols = list_columns(connection, "rhs", right_table)
    left_rows = connection.execute(f"SELECT count(*) FROM {source('lhs', left_table)}").fetchone()[0]
    right_rows = connection.execute(f"SELECT count(*) FROM {source('rhs', right_table)}").fetchone()[0]
    report = TableReport(left_table, left_rows, right_rows, [])
    report.differences.extend(structure_differences(left_table, left_cols, right_cols, entries, used))
    common = [name for name in left_cols if name in right_cols]
    key = [name for name in NATURAL_KEYS.get(table, ()) if name in common]
    if key and len(key) != len(NATURAL_KEYS[table]):
        key = []
    if key and not (
        keys_are_unique(connection, "lhs", left_table, [left_cols[name][0] for name in key])
        and keys_are_unique(connection, "rhs", right_table, [right_cols[name][0] for name in key])
    ):
        report.differences.append(Difference(left_table, "key_not_unique", ", ".join(key), 1, 1))
        key = []
    report.key = key
    match_columns = key or common
    totals = {"left": 0, "right": 0}
    for side, catalog, own_table, own_cols, other_catalog, other_table, other_cols in (
        ("left", "lhs", left_table, left_cols, "rhs", right_table, right_cols),
        ("right", "rhs", right_table, right_cols, "lhs", left_table, left_cols),
    ):
        chosen = entries_on_side(entries_of(entries, "rows_only", table=left_table), side)
        if key:
            match = equality(own_cols, other_cols, key, "t", "o", null_safe=False)
            total, unexplained, accepted_by = tally(
                connection,
                f"{source(catalog, own_table)} t",
                "TRUE",
                chosen,
                used,
                f"NOT EXISTS (SELECT 1 FROM {source(other_catalog, other_table)} o WHERE {match})",
            )
        else:
            own, other = ("left_n", "right_n") if side == "left" else ("right_n", "left_n")
            total, unexplained, accepted_by = tally(
                connection,
                multiset_difference(left_table, right_table, left_cols, right_cols, match_columns),
                f"COALESCE({own}, 0) > COALESCE({other}, 0)",
                chosen,
                used,
                weight=f"{own} - COALESCE({other}, 0)",
            )
        totals[side] = total
        if total:
            report.differences.append(
                Difference(left_table, f"rows_only_{side}", f"{total} rows", total, unexplained, accepted_by)
            )
    if left_rows - right_rows != totals["left"] - totals["right"]:
        gap = abs(left_rows - right_rows - totals["left"] + totals["right"])
        report.differences.append(Difference(left_table, "row_count", f"{left_rows} vs {right_rows}", gap, gap))
    if not key:
        return report
    join = (
        f"{source('lhs', left_table)} l JOIN {source('rhs', right_table)} r ON "
        f"{equality(left_cols, right_cols, key, 'l', 'r', null_safe=False)}"
    )
    for lowered in common:
        if lowered in key:
            continue
        left_name, left_type = left_cols[lowered]
        right_name, right_type = right_cols[lowered]
        flagged = (
            f"NOT ({normalized(f'l.{quote(left_name)}', left_type, right_type)} IS NOT DISTINCT FROM "
            f"{normalized(f'r.{quote(right_name)}', right_type, left_type)})"
        )
        chosen = entries_of(entries, "column_value", table=left_table, column=left_name)
        total, unexplained, accepted_by = tally(connection, join, flagged, chosen, used)
        if total:
            report.differences.append(
                Difference(left_table, "column_value", left_name, total, unexplained, accepted_by)
            )
    return report


def message_groups(catalog: str, table: str, columns: dict[str, tuple[str, str]]) -> str:
    batch, kind, message_source, text = (quote(columns[name][0]) for name in MESSAGE_COLUMNS)
    return (
        f"SELECT CAST({batch} AS BIGINT) AS batchid, CAST({kind} AS VARCHAR) AS messagetype, "
        f"CAST({message_source} AS VARCHAR) AS messagesource, CAST({text} AS VARCHAR) AS messagetext, "
        f"count(*) AS n FROM {source(catalog, table)} GROUP BY ALL"
    )


def compare_messages(
    connection: duckdb.DuckDBPyConnection,
    left_table: str,
    right_table: str,
    entries: Sequence[Expected],
    used: set[str],
    report: Report,
) -> TableReport:
    left_cols = list_columns(connection, "lhs", left_table)
    right_cols = list_columns(connection, "rhs", right_table)
    table = TableReport(left_table, None, None, list(MESSAGE_COLUMNS))
    absent = [name for name in MESSAGE_COLUMNS if name not in left_cols or name not in right_cols]
    if absent:
        table.differences.append(Difference(left_table, "messages_columns_missing", ", ".join(absent), 1, 1))
        return table
    table.left_rows = connection.execute(f"SELECT count(*) FROM {source('lhs', left_table)}").fetchone()[0]
    table.right_rows = connection.execute(f"SELECT count(*) FROM {source('rhs', right_table)}").fetchone()[0]
    on = " AND ".join(f"l.{name} IS NOT DISTINCT FROM r.{name}" for name in MESSAGE_COLUMNS)
    pick = ", ".join(f"COALESCE(l.{name}, r.{name}) AS {name}" for name in MESSAGE_COLUMNS)
    grouped = (
        f"(SELECT {pick}, l.n AS left_count, r.n AS right_count "
        f"FROM ({message_groups('lhs', left_table, left_cols)}) l "
        f"FULL JOIN ({message_groups('rhs', right_table, right_cols)}) r ON {on}) g"
    )
    total, unexplained, accepted_by = tally(
        connection,
        grouped,
        "left_count IS DISTINCT FROM right_count",
        entries_of(entries, "messages"),
        used,
    )
    if total:
        table.differences.append(Difference(left_table, "messages", f"{total} groups", total, unexplained, accepted_by))
    for side, catalog, name, cols in (
        ("left", "lhs", left_table, left_cols),
        ("right", "rhs", right_table, right_cols),
    ):
        rows = connection.execute(
            f"SELECT batchid, messagetype, sum(n) FROM ({message_groups(catalog, name, cols)}) "
            "GROUP BY batchid, messagetype ORDER BY batchid, messagetype"
        ).fetchall()
        report.messages[side] = {f"batch {batch} {kind}": int(count) for batch, kind, count in rows}
    return table


def run_official_audit(path: Path, memory_limit: str | None = None) -> list[tuple]:
    from benchbox.core.tpcdi.appendix import load_appendix_sql

    connection = duckdb.connect(str(path), read_only=True)
    if memory_limit:
        connection.execute("SET memory_limit = ?", [memory_limit])
    try:
        rows: list[tuple] = []
        for statement in load_appendix_sql("audit", "duckdb").statements():
            rows.extend(connection.execute(statement).fetchall())
        return rows
    finally:
        connection.close()


def audit_outcomes(rows: Sequence[tuple]) -> dict[tuple[str, str, str], list[str]]:
    outcomes: dict[tuple[str, str, str], list[str]] = {}
    for test, batch, result, description in rows:
        outcomes.setdefault((str(test), "null" if batch is None else str(batch), str(description)), []).append(
            str(result)
        )
    return {key: sorted(results) for key, results in outcomes.items()}


def compare_audits(
    left_rows: Sequence[tuple],
    right_rows: Sequence[tuple],
    entries: Sequence[Expected],
    used: set[str],
    report: Report,
) -> None:
    left, right = audit_outcomes(left_rows), audit_outcomes(right_rows)
    for key in sorted(set(left) | set(right)):
        test, batch, description = key
        left_result = ",".join(left.get(key, [AUDIT_ABSENT]))
        right_result = ",".join(right.get(key, [AUDIT_ABSENT]))
        report.audit.append(
            {
                "test": test,
                "batch": batch,
                "description": description,
                "left": left_result,
                "right": right_result,
            }
        )
        if left_result == right_result and all(result == "OK" for result in left.get(key, [AUDIT_ABSENT])):
            continue
        chosen = [
            entry
            for entry in entries_of(entries, "audit", test=test, batch=batch)
            if matches(entry.fields["left_result"], left_result) and matches(entry.fields["right_result"], right_result)
        ]
        used.update(entry.id for entry in chosen)
        report.differences.append(
            Difference(
                "audit",
                "audit_outcome",
                f"{test} batch {batch}: {left_result} vs {right_result}",
                1,
                0 if chosen else 1,
                [entry.id for entry in chosen],
            )
        )


def compare(
    left: Path,
    right: Path,
    entries: Sequence[Expected],
    left_name: str = "left",
    right_name: str = "right",
    audit: AuditRunner | None = None,
) -> Report:
    connection = connect(left, right)
    report = Report(left_name, right_name)
    used: set[str] = set()
    try:
        left_tables = list_tables(connection, "lhs")
        right_tables = list_tables(connection, "rhs")
        for lowered in sorted(set(left_tables) | set(right_tables)):
            if lowered not in left_tables or lowered not in right_tables:
                side = "left" if lowered in left_tables else "right"
                name = left_tables.get(lowered) or right_tables[lowered]
                chosen = entries_on_side(entries_of(entries, "table_missing", table=name), side)
                used.update(entry.id for entry in chosen)
                report.differences.append(
                    Difference(name, f"table_only_{side}", name, 1, 0 if chosen else 1, [e.id for e in chosen])
                )
                continue
            if lowered == MESSAGES_TABLE:
                table = compare_messages(connection, left_tables[lowered], right_tables[lowered], entries, used, report)
            else:
                table = compare_table(connection, lowered, left_tables[lowered], right_tables[lowered], entries, used)
            report.tables.append(table)
    finally:
        connection.close()
    if audit is not None:
        compare_audits(audit(left), audit(right), entries, used, report)
    report.unused_entries = [entry.id for entry in entries if entry.id not in used]
    return report


def render(report: Report) -> str:
    lines = []
    for table in report.tables:
        lines.append(
            f"table {table.table}: rows {report.left_name}={table.left_rows} {report.right_name}={table.right_rows} "
            f"key={','.join(table.key) or '-'}"
        )
    for side, name in (("left", report.left_name), ("right", report.right_name)):
        for label, count in report.messages.get(side, {}).items():
            lines.append(f"DImessages {name} {label}: {count}")
    for row in report.audit:
        lines.append(
            f"audit {row['test']} batch {row['batch']}: {report.left_name}={row['left']} {report.right_name}={row['right']}"
        )
    for difference in report.all_differences():
        status = "ACCEPTED" if difference.accepted else "FINDING"
        by = f" by {','.join(difference.accepted_by)}" if difference.accepted_by else ""
        lines.append(
            f"{status} table={difference.table} kind={difference.kind} {difference.subject} "
            f"unexplained={difference.unexplained} of {difference.count}{by}"
        )
    for entry_id in report.unused_entries:
        lines.append(f"UNUSED expected difference {entry_id}")
    unaccepted = report.unaccepted()
    tables = sorted({difference.table for difference in unaccepted})
    lines.append(f"RESULT: {len(unaccepted)} unaccepted differences" + (f" in {', '.join(tables)}" if tables else ""))
    return "\n".join(lines)


def render_audit(rows: Sequence[tuple]) -> tuple[str, int]:
    lines = []
    failed = 0
    for (test, batch, description), results in sorted(audit_outcomes(rows).items()):
        lines.append(f"audit {test} batch {batch}: {','.join(results)} ({description})")
        failed += sum(result != "OK" for result in results)
    lines.append(
        f"RESULT: {sum(len(results) for results in audit_outcomes(rows).values())} audit rows, {failed} not OK"
    )
    return "\n".join(lines), failed


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two TPC-DI DuckDB warehouses built from one staging manifest")
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path, nargs="?")
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument("--departures", type=Path, default=DEFAULT_DEPARTURES)
    parser.add_argument("--left-name", default="benchbox")
    parser.add_argument("--right-name", default="duckdb-implementation")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--memory-limit")
    parser.add_argument("--json", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.audit_only == (arguments.right is not None):
        parser.error("give one database with --audit-only, or two databases without it")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    if arguments.audit_only:
        rows = run_official_audit(arguments.left, arguments.memory_limit)
        text, failed = render_audit(rows)
        print(text)
        if arguments.json:
            outcomes = [
                {"test": test, "batch": batch, "description": description, "results": results}
                for (test, batch, description), results in sorted(audit_outcomes(rows).items())
            ]
            arguments.json.write_text(json.dumps(outcomes, indent=2), encoding="utf-8")
        return 1 if failed else 0
    try:
        entries = load_expected(arguments.expected, arguments.departures)
    except (ConfigError, OSError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    report = compare(
        arguments.left,
        arguments.right,
        entries,
        arguments.left_name,
        arguments.right_name,
        partial(run_official_audit, memory_limit=arguments.memory_limit) if arguments.audit else None,
    )
    print(render(report))
    if arguments.json:
        arguments.json.write_text(json.dumps(asdict(report), indent=2, default=str), encoding="utf-8")
    return 1 if report.unaccepted() else 0


if __name__ == "__main__":
    sys.exit(main())
