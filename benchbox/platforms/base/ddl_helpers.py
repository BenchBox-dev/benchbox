from __future__ import annotations

import re
from typing import Any

_REF_ACTION = r"(?:NO\s+ACTION|RESTRICT|CASCADE|SET\s+(?:NULL|DEFAULT))"
_FK_CLAUSE_RE = re.compile(
    r",?\s*"
    r"(?:CONSTRAINT\s+[`\"\w]+\s+)?"
    r"FOREIGN\s+KEY\s*\([^)]*\)"
    r"\s*REFERENCES\s+"
    r"[`\"\w]+(?:\.[`\"\w]+)?"
    r"\s*\([^)]*\)"
    rf"(?:\s+ON\s+(?:DELETE|UPDATE)\s+{_REF_ACTION})*",
    re.IGNORECASE,
)
_INLINE_REFERENCES_RE = re.compile(
    r"\s+REFERENCES\s+"
    r"[`\"\w]+(?:\.[`\"\w]+)?"
    r"\s*(?:\([^)]*\))?"
    rf"(?:\s+ON\s+(?:DELETE|UPDATE)\s+{_REF_ACTION})*",
    re.IGNORECASE,
)

_TRAILING_COMMA_RE = re.compile(r",(\s*\))")


TPCDS_FOREIGN_KEY_DROP_REASON = (
    "TPC-DS generated data does not satisfy every declared foreign key, so the tables are created without them"
)


def is_tpcds_benchmark(benchmark: object) -> bool:
    name = getattr(benchmark, "_name", "") or type(benchmark).__name__
    return "TPC-DS" in str(name) or "TPCDS" in str(name)


def strip_tpcds_foreign_keys(adapter: Any, benchmark: object, schema_sql: str) -> str:
    if not is_tpcds_benchmark(benchmark):
        return schema_sql
    config = adapter.get_effective_tuning_configuration() if adapter.tuning_enabled else None
    ledger = getattr(adapter, "_applied_tuning_ledger", None)
    if config is not None and config.foreign_keys.enabled and ledger is not None:
        ledger.record_dropped("foreign_keys", TPCDS_FOREIGN_KEY_DROP_REASON)
    return strip_foreign_keys(schema_sql)


def strip_foreign_keys(stmt: str) -> str:
    stmt_upper = stmt.upper()
    if "FOREIGN KEY" not in stmt_upper and "REFERENCES" not in stmt_upper:
        return stmt

    cleaned = _FK_CLAUSE_RE.sub("", stmt)
    cleaned = _INLINE_REFERENCES_RE.sub("", cleaned)
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    return cleaned


_CONSTRAINT_PREFIX = r"(?:CONSTRAINT\s+[`\"\w]+\s+)?"
_TABLE_PK_RE = re.compile(rf",?\s*{_CONSTRAINT_PREFIX}PRIMARY\s+KEY\s*\(", re.IGNORECASE)
_INLINE_PK_RE = re.compile(rf"\s+{_CONSTRAINT_PREFIX}PRIMARY\s+KEY\b", re.IGNORECASE)
_LEADING_COMMA_RE = re.compile(r"(\()\s*,\s*")


def strip_primary_keys(stmt: str) -> str:
    if "PRIMARY KEY" not in stmt.upper():
        return stmt

    result = stmt

    while True:
        m = _TABLE_PK_RE.search(result)
        if m is None:
            break
        open_pos = m.end() - 1
        depth = 0
        close_pos = -1
        for i in range(open_pos, len(result)):
            if result[i] == "(":
                depth += 1
            elif result[i] == ")":
                depth -= 1
                if depth == 0:
                    close_pos = i
                    break
        if close_pos == -1:
            break
        result = result[: m.start()] + result[close_pos + 1 :]

    result = _INLINE_PK_RE.sub("", result)

    result = _TRAILING_COMMA_RE.sub(r"\1", result)
    result = _LEADING_COMMA_RE.sub(r"\1", result)

    return result


_WITH_KEYWORD_RE = re.compile(r"\s+WITH\s*\(", re.IGNORECASE)


def strip_with_properties(stmt: str) -> str:
    if "WITH" not in stmt.upper():
        return stmt

    result = stmt
    while True:
        m = _WITH_KEYWORD_RE.search(result)
        if m is None:
            break
        open_pos = m.end() - 1
        depth = 0
        close_pos = -1
        for i in range(open_pos, len(result)):
            if result[i] == "(":
                depth += 1
            elif result[i] == ")":
                depth -= 1
                if depth == 0:
                    close_pos = i
                    break
        if close_pos == -1:
            break
        result = result[: m.start()] + result[close_pos + 1 :]

    return result
