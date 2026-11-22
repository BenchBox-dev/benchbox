from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

APPENDIX_DIR = Path(__file__).resolve().parents[2] / "_binaries" / "tpc-di"

APPENDIX_FILES = {
    "audit": "tpcdi_audit.sql",
    "validation": "tpcdi_validation.sql",
    "visibility_1": "tpcdi_visibility_1.sql",
    "visibility_2": "tpcdi_visibility_2.sql",
}

DUMMY_TABLE_TOKEN = "FROM DUMMY TABLE"
CONCATENATION_TOKEN = "||"
DATE_DURATION_TOKEN = "Date-1 day"

_DUMMY_TABLE_PATTERN = re.compile(r"\bfrom\s+dummy\s+table\b", re.IGNORECASE)
_CONCATENATION_PATTERN = re.compile(r"(?<!\[)\|\|(?!\])")
_DATE_DURATION_PATTERN = re.compile(r"\bDate-1 day\b")

_DUMMY_TABLE_CLAUSES = {
    "db2": "from sysibm.sysdummy1",
    "oracle": "from dual",
    "sqlserver": "",
}
_CONCATENATION_OPERATORS = {"sqlserver": "+"}
_DATE_DURATION_REPLACEMENTS = {"duckdb": "Date - INTERVAL 1 DAY"}


@dataclass(frozen=True)
class Substitution:
    token: str
    replacement: str
    occurrences: int
    in_specification_table: bool = True


@dataclass(frozen=True)
class AppendixSql:
    name: str
    platform: str
    sql: str
    substitutions: tuple[Substitution, ...]
    stored_sha256: str

    def statements(self) -> list[str]:
        return split_statements(self.sql)


def appendix_path(name: str) -> Path:
    if name not in APPENDIX_FILES:
        raise ValueError(f"Unknown appendix SQL: {name}")
    return APPENDIX_DIR / APPENDIX_FILES[name]


def stored_sha256(name: str) -> str:
    return hashlib.sha256(appendix_path(name).read_bytes()).hexdigest()


def load_appendix_sql(name: str, platform: str = "duckdb") -> AppendixSql:
    raw = appendix_path(name).read_bytes()
    sql = raw.decode("utf-8")
    key = platform.lower()
    dummy_clause = _DUMMY_TABLE_CLAUSES.get(key, "")
    substitutions: list[Substitution] = []

    sql, count = _DUMMY_TABLE_PATTERN.subn(lambda _: dummy_clause, sql)
    if count:
        substitutions.append(Substitution(DUMMY_TABLE_TOKEN, dummy_clause, count))

    operator = _CONCATENATION_OPERATORS.get(key)
    if operator is not None:
        sql, count = _CONCATENATION_PATTERN.subn(lambda _: operator, sql)
        if count:
            substitutions.append(Substitution(CONCATENATION_TOKEN, operator, count))

    duration = _DATE_DURATION_REPLACEMENTS.get(key)
    if duration is not None:
        sql, count = _DATE_DURATION_PATTERN.subn(lambda _: duration, sql)
        if count:
            substitutions.append(Substitution(DATE_DURATION_TOKEN, duration, count, in_specification_table=False))

    return AppendixSql(
        name=name,
        platform=key,
        sql=sql,
        substitutions=tuple(substitutions),
        stored_sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_all_appendix_sql(platform: str = "duckdb") -> dict[str, AppendixSql]:
    return {name: load_appendix_sql(name, platform) for name in APPENDIX_FILES}


def split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    start = 0
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        pair = sql[index : index + 2]
        if char == "'":
            index = _skip_quoted(sql, index)
            continue
        if pair == "--":
            newline = sql.find("\n", index)
            index = length if newline == -1 else newline + 1
            continue
        if pair == "/*":
            end = sql.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        if char == ";":
            _append_statement(statements, sql[start:index])
            start = index + 1
        index += 1
    _append_statement(statements, sql[start:])
    return statements


def _skip_quoted(sql: str, index: int) -> int:
    index += 1
    while index < len(sql):
        if sql[index] == "'":
            if sql[index + 1 : index + 2] == "'":
                index += 2
                continue
            return index + 1
        index += 1
    return index


def _append_statement(statements: list[str], text: str) -> None:
    without_comments = re.sub(r"/\*.*?\*/|--[^\n]*", "", text, flags=re.DOTALL)
    if without_comments.strip():
        statements.append(text.strip())
