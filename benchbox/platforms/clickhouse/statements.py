from __future__ import annotations

import re

from benchbox.utils.dialect_utils import split_sql_statements

_LEADING_KEYWORDS = ("UPDATE", "DELETE", "CREATE")
_UPDATE_TARGET_RE = re.compile(r"^\s*UPDATE\s+(?P<table>[A-Za-z_]\w*)\s+SET\b", re.IGNORECASE)
_CREATE_TABLE_RE = re.compile(
    r"^(?P<head>\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)(?P<rest>\s*[(]|\s+AS\b)",
    re.IGNORECASE,
)
_DEFAULT_ENGINE = " ENGINE = MergeTree ORDER BY tuple()"
_GENERATE_SERIES_RE = re.compile(
    r"\barrayJoin\(\s*GENERATE_SERIES\(\s*(?P<lo>-?\d+)\s*,\s*(?P<hi>-?\d+)\s*\)\s*\)", re.IGNORECASE
)


def lightweight_update_target(statement: str) -> str | None:
    match = _UPDATE_TARGET_RE.match(statement)
    return match.group("table") if match else None


def _requires_where(statement: str) -> bool:
    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp

    try:
        tree = sqlglot.parse_one(statement, read="clickhouse")
    except Exception:
        return False
    return isinstance(tree, (exp.Update, exp.Delete)) and not tree.args.get("where")


def _prepare_statement(statement: str) -> str:
    statement = _GENERATE_SERIES_RE.sub(lambda m: f"arrayJoin(range({m['lo']}, {int(m['hi']) + 1}))", statement.strip())
    if statement.upper().startswith(("UPDATE", "DELETE")) and _requires_where(statement):
        return f"{statement} WHERE 1"
    create = _CREATE_TABLE_RE.match(statement)
    if create is not None and not re.search(r"\bENGINE\s*=", statement, re.IGNORECASE):
        if create.group("rest").lstrip().startswith("("):
            return f"{statement}{_DEFAULT_ENGINE}"
        return f"{create.group('head')}{_DEFAULT_ENGINE}{statement[create.end('head') :]}"
    return statement


def prepare_clickhouse_statements(query: str) -> list[str]:
    if (
        ";" not in query
        and not query.lstrip().upper().startswith(_LEADING_KEYWORDS)
        and "generate_series" not in query.lower()
    ):
        return [query]
    return [_prepare_statement(statement) for statement in split_sql_statements(query, "clickhouse")]
