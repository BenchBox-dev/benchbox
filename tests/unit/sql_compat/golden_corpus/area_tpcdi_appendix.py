from __future__ import annotations

from benchbox.core.tpcdi.appendix import APPENDIX_FILES, load_appendix_sql

from . import Case, frozen_legacy

AREA = "area_tpcdi_appendix"

TPCDI_APPENDIX_TARGETS = ("duckdb", "postgresql", "snowflake", "clickhouse", "datafusion", "trino")


def case_id(target: str, name: str) -> str:
    return f"tpcdi-appendix:{target}:{name}"


def cases() -> list[Case]:
    return [
        Case(
            case_id(target, name),
            frozen_legacy(AREA, case_id(target, name)),
            lambda name=name, target=target: load_appendix_sql(name, target).sql,
        )
        for name in APPENDIX_FILES
        for target in TPCDI_APPENDIX_TARGETS
    ]
