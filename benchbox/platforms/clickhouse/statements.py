from __future__ import annotations

import re

from benchbox.utils.dialect_utils import is_unfiltered_update_or_delete, split_sql_statements

_LEADING_KEYWORDS = ("UPDATE", "DELETE", "CREATE")
_UPDATE_TARGET_RE = re.compile(r"^\s*UPDATE\s+(?P<table>[A-Za-z_]\w*)\s+SET\b", re.IGNORECASE)
_CREATE_TABLE_RE = re.compile(
    r"^(?P<head>\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)(?P<rest>\s*[(]|\s+AS\b)",
    re.IGNORECASE,
)
_DEFAULT_ENGINE = " ENGINE = MergeTree ORDER BY tuple()"
_SETTINGS_CLAUSE = "SETTINGS enable_block_number_column = 1, enable_block_offset_column = 1"
_SETTINGS_RE = re.compile(r"\bSETTINGS\s+", re.IGNORECASE)
_ENGINE_RE = re.compile(r"\bENGINE\s*=\s*(?P<name>\w+)", re.IGNORECASE)
_CREATE_TARGET_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<table>(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)$", re.IGNORECASE
)


def merge_tree_create_target(statement: str) -> str | None:
    create = _CREATE_TABLE_RE.match(statement)
    if create is None:
        return None
    engine = _ENGINE_RE.search(statement)
    if engine is not None and not engine.group("name").lower().endswith("mergetree"):
        return None
    match = _CREATE_TARGET_RE.match(create.group("head"))
    return match.group("table") if match else None


def _with_merge_tree_settings(statement: str) -> str:
    if re.search(r"\bAS\s+SELECT\b", statement, re.IGNORECASE):
        return statement
    if "enable_block_number_column" in statement and "enable_block_offset_column" in statement:
        return statement
    settings_match = _SETTINGS_RE.search(statement)
    if settings_match is not None:
        pos = settings_match.end()
        return f"{statement[:pos]}enable_block_number_column = 1, enable_block_offset_column = 1, {statement[pos:]}"
    return f"{statement} {_SETTINGS_CLAUSE}"


_GENERATE_SERIES_RE = re.compile(
    r"\barrayJoin\(\s*GENERATE_SERIES\(\s*(?P<lo>-?\d+)\s*,\s*(?P<hi>-?\d+)\s*\)\s*\)", re.IGNORECASE
)


def lightweight_update_target(statement: str) -> str | None:
    match = _UPDATE_TARGET_RE.match(statement)
    return match.group("table") if match else None


def _prepare_statement(statement: str) -> str:
    statement = _GENERATE_SERIES_RE.sub(lambda m: f"arrayJoin(range({m['lo']}, {int(m['hi']) + 1}))", statement.strip())
    if statement.upper().startswith(("UPDATE", "DELETE")) and is_unfiltered_update_or_delete(statement, "clickhouse"):
        return f"{statement} WHERE 1"
    create = _CREATE_TABLE_RE.match(statement)
    if create is not None:
        if not re.search(r"\bENGINE\s*=", statement, re.IGNORECASE):
            if create.group("rest").lstrip().startswith("("):
                statement = f"{statement}{_DEFAULT_ENGINE}"
            else:
                statement = f"{create.group('head')}{_DEFAULT_ENGINE}{statement[create.end('head') :]}"
        if merge_tree_create_target(statement) is not None:
            statement = _with_merge_tree_settings(statement)
    return statement


def prepare_clickhouse_statements(query: str) -> list[str]:
    if (
        ";" not in query
        and not query.lstrip().upper().startswith(_LEADING_KEYWORDS)
        and "generate_series" not in query.lower()
    ):
        return [query]
    return [_prepare_statement(statement) for statement in split_sql_statements(query, "clickhouse")]
