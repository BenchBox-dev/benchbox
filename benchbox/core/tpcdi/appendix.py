from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from benchbox.utils.dialect_utils import resolve_sql_dialect, translate_sql_query

APPENDIX_DIR = Path(__file__).resolve().parents[2] / "_binaries" / "tpc-di"

APPENDIX_FILES = {
    "audit": "tpcdi_audit.sql",
    "validation": "tpcdi_validation.sql",
    "visibility_1": "tpcdi_visibility_1.sql",
    "visibility_2": "tpcdi_visibility_2.sql",
}

SOURCE_DIALECT = "postgres"
TRANSLATOR = "sqlglot"

DUMMY_TABLE_PLACEHOLDER = "FROM DUMMY TABLE"
DATE_DURATION_TOKEN = "Date-1 day"

_DUMMY_TABLE_PATTERN = re.compile(r"\bfrom\s+dummy\s+table\b", re.IGNORECASE)
_DATE_DURATION_PATTERN = re.compile(r"\bDate-1 day\b")

DATE_DURATION_NOTE = (
    "The translator reads the labelled duration 'Date-1 day' as the expression 'Date - 1' with the alias 'day'; "
    "this is not a rule in the specification's header table. Where the target subtracts an integer from a date as one "
    "day (verified on DuckDB), the value is the same."
)


@dataclass(frozen=True)
class PreStep:
    token: str
    replacement: str
    occurrences: int
    header_table_row: str


@dataclass(frozen=True)
class AppendixSql:
    name: str
    platform: str
    source_dialect: str
    target_dialect: str
    translator: str
    sql: str
    pre_steps: tuple[PreStep, ...]
    notes: tuple[str, ...]
    stored_sha256: str

    def statements(self) -> list[str]:
        return split_statements(self.sql)

    def translation_record(self) -> dict[str, object]:
        return {
            "translator": self.translator,
            "source_dialect": self.source_dialect,
            "target_dialect": self.target_dialect,
            "pre_steps": [
                {
                    "token": step.token,
                    "replacement": step.replacement,
                    "occurrences": step.occurrences,
                    "header_table_row": step.header_table_row,
                }
                for step in self.pre_steps
            ],
            "notes": list(self.notes),
            "stored_sha256": self.stored_sha256,
        }


def appendix_path(name: str) -> Path:
    if name not in APPENDIX_FILES:
        raise ValueError(f"Unknown appendix SQL: {name}")
    return APPENDIX_DIR / APPENDIX_FILES[name]


def stored_sha256(name: str) -> str:
    return hashlib.sha256(appendix_path(name).read_bytes()).hexdigest()


def load_appendix_sql(name: str, platform: str = "duckdb") -> AppendixSql:
    path = appendix_path(name)
    target = resolve_sql_dialect(platform)
    raw = path.read_bytes()
    text = raw.decode("utf-8")

    pre_steps: list[PreStep] = []
    text, count = _DUMMY_TABLE_PATTERN.subn("", text)
    if count:
        pre_steps.append(PreStep(DUMMY_TABLE_PLACEHOLDER, "", count, "SQLSERVER <blank>"))

    notes: list[str] = []
    if _DATE_DURATION_PATTERN.search(text):
        notes.append(DATE_DURATION_NOTE)

    sql = translate_sql_query(text, target, source_dialect=SOURCE_DIALECT, identify=False, strict=True)

    return AppendixSql(
        name=name,
        platform=platform.lower(),
        source_dialect=SOURCE_DIALECT,
        target_dialect=target,
        translator=TRANSLATOR,
        sql=sql,
        pre_steps=tuple(pre_steps),
        notes=tuple(notes),
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
