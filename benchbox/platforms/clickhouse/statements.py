from __future__ import annotations

import re

from benchbox.utils.dialect_utils import is_unfiltered_update_or_delete, split_sql_statements

_NAME = r"(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*"
_LEADING_KEYWORDS = ("UPDATE", "DELETE", "CREATE")
_UPDATE_TARGET_RE = re.compile(rf"^\s*UPDATE\s+(?P<table>{_NAME})\s+SET\b", re.IGNORECASE)
_CREATE_TABLE_RE = re.compile(
    rf"^(?P<head>\s*CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?P<if_missing>IF\s+NOT\s+EXISTS\s+)?(?P<table>{_NAME}))"
    r"(?P<rest>\s*[(]|\s+AS\b|\s+ENGINE\b)",
    re.IGNORECASE,
)
_CTAS_BODY_RE = re.compile(r"\s+AS\s+(?=SELECT\b|WITH\b|\()", re.IGNORECASE)
_DROP_RE = re.compile(rf"^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<table>{_NAME})", re.IGNORECASE)
_RENAME_RE = re.compile(r"^\s*RENAME\s+TABLE\s+(?P<pairs>.+)$", re.IGNORECASE | re.DOTALL)
_RENAME_PAIR_RE = re.compile(rf"^\s*(?P<source>{_NAME})\s+TO\s+(?P<target>{_NAME})\s*$", re.IGNORECASE)
_EXCHANGE_RE = re.compile(rf"^\s*EXCHANGE\s+TABLES\s+(?P<left>{_NAME})\s+AND\s+(?P<right>{_NAME})", re.IGNORECASE)
_USE_RE = re.compile(r"^\s*USE\s+(?P<database>[A-Za-z_]\w*)\s*$", re.IGNORECASE)
_DEFAULT_ENGINE = " ENGINE = MergeTree ORDER BY tuple()"
_UPDATE_SETTING_KEYS = ("enable_block_number_column", "enable_block_offset_column")
_UPDATE_SETTINGS = ", ".join(f"{key} = 1" for key in _UPDATE_SETTING_KEYS)
_UPDATE_SETTING_RE = re.compile(
    r"\b(?P<key>enable_block_(?:number|offset)_column)\s*=\s*(?P<value>'[^']*'|[^\s,]+)", re.IGNORECASE
)
_SETTINGS_RE = re.compile(r"\bSETTINGS\s+", re.IGNORECASE)
_ENGINE_RE = re.compile(r"\bENGINE\s*=\s*(?P<name>\w+)", re.IGNORECASE)
_GENERATE_SERIES_RE = re.compile(
    r"\barrayJoin\(\s*GENERATE_SERIES\(\s*(?P<lo>-?\d+)\s*,\s*(?P<hi>-?\d+)\s*\)\s*\)", re.IGNORECASE
)

CREATE_READY = "create-ready"
CREATE_PLAIN = "create-plain"
DROP = "drop"
RENAME = "rename"
EXCHANGE = "exchange"
USE = "use"
IF_MISSING = "if-missing"


def merge_tree_create_target(statement: str) -> str | None:
    create = _CREATE_TABLE_RE.match(statement)
    if create is None:
        return None
    engine = _ENGINE_RE.search(statement)
    if engine is not None and not engine.group("name").lower().endswith("mergetree"):
        return None
    return create.group("table")


def lightweight_update_target(statement: str) -> str | None:
    match = _UPDATE_TARGET_RE.match(statement)
    return match.group("table") if match else None


def _bare(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _definition(statement: str) -> str:
    body = _CTAS_BODY_RE.search(statement)
    return statement[: body.start()] if body else statement


def _enables_lightweight_updates(statement: str) -> bool:
    values = {
        match["key"].lower(): match["value"].strip("'").lower() for match in _UPDATE_SETTING_RE.finditer(statement)
    }
    return all(values.get(key) in ("1", "true") for key in _UPDATE_SETTING_KEYS)


def table_lifecycle(statement: str) -> list[tuple[str, ...]]:
    create = _CREATE_TABLE_RE.match(statement)
    if create is not None:
        ready = merge_tree_create_target(statement) is not None and _enables_lightweight_updates(_definition(statement))
        flag = IF_MISSING if create.group("if_missing") else ""
        return [(CREATE_READY if ready else CREATE_PLAIN, create.group("table"), flag)]
    if (drop := _DROP_RE.match(statement)) is not None:
        return [(DROP, drop.group("table"))]
    if (exchange := _EXCHANGE_RE.match(statement)) is not None:
        return [(EXCHANGE, exchange.group("left"), exchange.group("right"))]
    if (use := _USE_RE.match(statement)) is not None:
        return [(USE, use.group("database"))]
    if (rename := _RENAME_RE.match(statement)) is not None:
        events: list[tuple[str, ...]] = []
        for pair in rename.group("pairs").split(","):
            match = _RENAME_PAIR_RE.match(pair)
            if match is not None:
                events.append((RENAME, match.group("source"), match.group("target")))
        return events
    return []


def _with_merge_tree_settings(statement: str) -> str:
    head = _definition(statement)
    tail = statement[len(head) :]
    head = _UPDATE_SETTING_RE.sub(lambda match: f"{match['key']} = 1", head)
    if _enables_lightweight_updates(head):
        return f"{head}{tail}"
    settings = _SETTINGS_RE.search(head)
    if settings is not None:
        position = settings.end()
        return f"{head[:position]}{_UPDATE_SETTINGS}, {head[position:]}{tail}"
    return f"{head} SETTINGS {_UPDATE_SETTINGS}{tail}"


def _prepare_statement(statement: str, updated_later: frozenset[str]) -> str:
    statement = _GENERATE_SERIES_RE.sub(lambda m: f"arrayJoin(range({m['lo']}, {int(m['hi']) + 1}))", statement.strip())
    if statement.upper().startswith(("UPDATE", "DELETE")) and is_unfiltered_update_or_delete(statement, "clickhouse"):
        return f"{statement} WHERE 1"
    create = _CREATE_TABLE_RE.match(statement)
    if create is None:
        return statement
    if not re.search(r"\bENGINE\s*=", statement, re.IGNORECASE):
        if create.group("rest").lstrip().startswith("("):
            statement = f"{statement}{_DEFAULT_ENGINE}"
        else:
            statement = f"{create.group('head')}{_DEFAULT_ENGINE}{statement[create.end('head') :]}"
    target = merge_tree_create_target(statement)
    if target is not None and _bare(target) in updated_later:
        statement = _with_merge_tree_settings(statement)
    return statement


def prepare_clickhouse_statements(query: str) -> list[str]:
    if (
        ";" not in query
        and not query.lstrip().upper().startswith(_LEADING_KEYWORDS)
        and "generate_series" not in query.lower()
    ):
        return [query]
    statements = split_sql_statements(query, "clickhouse")
    updated_later: set[str] = set()
    prepared: list[str] = []
    for statement in reversed(statements):
        prepared.append(_prepare_statement(statement, frozenset(updated_later)))
        if (table := lightweight_update_target(statement)) is not None:
            updated_later.add(_bare(table))
        for event in reversed(table_lifecycle(statement)):
            if event[0] == RENAME and _bare(event[2]) in updated_later:
                updated_later.add(_bare(event[1]))
            elif event[0] == EXCHANGE and {_bare(event[1]), _bare(event[2])} & updated_later:
                updated_later.update({_bare(event[1]), _bare(event[2])})
    return prepared[::-1]
