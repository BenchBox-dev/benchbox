from __future__ import annotations

import re

from benchbox.utils.dialect_utils import split_sql_statements

_CURRENT_TIMESTAMP_RE = re.compile(r"\bCURRENT_TIMESTAMP\b(?!\s*\()", re.IGNORECASE)
_DELETE_RE = re.compile(
    r"^\s*DELETE\s+FROM\s+(?P<table>\w+)(?:\s+WHERE\s+(?P<where>.+?))?\s*$", re.IGNORECASE | re.DOTALL
)


def rewrite_questdb_operation_sql(sql: str) -> str:
    from benchbox.platforms.questdb_rewrites import rewrite_query

    statements = split_sql_statements(sql, "questdb")
    rewritten = [rewrite_query(_CURRENT_TIMESTAMP_RE.sub("now()", statement)) for statement in statements]
    if rewritten == statements:
        return sql
    if len(rewritten) == 1:
        return rewritten[0]
    return ";\n".join(rewritten) + ";"


def questdb_cleanup_statements(cleanup_sql: str) -> list[str]:
    statements: list[str] = []
    for statement in split_sql_statements(cleanup_sql, "questdb"):
        match = _DELETE_RE.match(statement)
        if match is None:
            statements.append(statement)
            continue
        table = match.group("table")
        where = match.group("where")
        if where is None:
            statements.append(f'TRUNCATE TABLE "{table}"')
            continue
        keep = f"{table}__keep"
        statements.extend(
            [
                f'DROP TABLE IF EXISTS "{keep}"',
                f'CREATE TABLE "{keep}" AS (SELECT * FROM "{table}" WHERE COALESCE(({where}), false) = false)',
                f'DROP TABLE "{table}"',
                f'RENAME TABLE "{keep}" TO "{table}"',
            ]
        )
    return statements
