from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from benchbox.utils.dialect_utils import resolve_sql_dialect, split_sql_statements, translate_sql_query

APPENDIX_DIR = Path(__file__).resolve().parents[2] / "_binaries" / "tpc-di"

APPENDIX_FILES = {
    "audit": "tpcdi_audit.sql",
    "validation": "tpcdi_validation.sql",
    "visibility_1": "tpcdi_visibility_1.sql",
    "visibility_2": "tpcdi_visibility_2.sql",
}

SOURCE_DIALECT = "postgres"

DUMMY_TABLE_PLACEHOLDER = "FROM DUMMY TABLE"

_DUMMY_TABLE_PATTERN = re.compile(r"\bfrom\s+dummy\s+table\b", re.IGNORECASE)


@dataclass(frozen=True)
class PreStep:
    token: str
    replacement: str
    occurrences: int


@dataclass(frozen=True)
class AppendixSql:
    name: str
    platform: str
    source_dialect: str
    target_dialect: str
    sql: str
    pre_steps: tuple[PreStep, ...]
    stored_sha256: str

    def statements(self) -> list[str]:
        return split_sql_statements(self.sql, self.target_dialect)


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

    text, count = _DUMMY_TABLE_PATTERN.subn("", raw.decode("utf-8"))
    pre_steps = (PreStep(DUMMY_TABLE_PLACEHOLDER, "", count),) if count else ()

    sql = translate_sql_query(text, target, source_dialect=SOURCE_DIALECT, identify=False, strict=True)

    return AppendixSql(
        name=name,
        platform=platform.lower(),
        source_dialect=SOURCE_DIALECT,
        target_dialect=target,
        sql=sql,
        pre_steps=pre_steps,
        stored_sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_all_appendix_sql(platform: str = "duckdb") -> dict[str, AppendixSql]:
    return {name: load_appendix_sql(name, platform) for name in APPENDIX_FILES}
