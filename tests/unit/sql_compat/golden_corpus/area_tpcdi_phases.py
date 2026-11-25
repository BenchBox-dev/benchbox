from __future__ import annotations

from benchbox.core.tpcdi.audit import AUDIT_SQL
from benchbox.core.tpcdi.etl.staging import WEBBED_LOADED_SQL, render_sql
from benchbox.core.tpcdi.phases import COMPLETION_RECORD_SQL, LATEST_BATCH_SQL

from . import Case, frozen_legacy

AREA = "area_tpcdi_phases"

TPCDI_PHASE_TARGETS = ("duckdb",)

STATEMENTS = {
    **AUDIT_SQL,
    "latest_batch": LATEST_BATCH_SQL,
    "completion_record": COMPLETION_RECORD_SQL,
    "webbed_loaded": WEBBED_LOADED_SQL,
}


def case_id(target: str, name: str) -> str:
    return f"tpcdi-phases:{target}:{name}"


def cases() -> list[Case]:
    return [
        Case(
            case_id(target, name),
            frozen_legacy(AREA, case_id(target, name)),
            lambda sql=sql, target=target: ";\n".join(render_sql(sql, target)),
        )
        for name, sql in STATEMENTS.items()
        for target in TPCDI_PHASE_TARGETS
    ]
