from __future__ import annotations

from benchbox.core.tpcdi.etl.staging import INGEST_STEPS, render_step

from . import Case, frozen_legacy

AREA = "area_tpcdi_etl"

TPCDI_ETL_TARGETS = ("duckdb",)


def case_id(target: str, name: str) -> str:
    return f"tpcdi-etl:{target}:{name}"


def cases() -> list[Case]:
    return [
        Case(
            case_id(target, step.name),
            frozen_legacy(AREA, case_id(target, step.name)),
            lambda step=step, target=target: ";\n".join(render_step(step, target)),
        )
        for step in INGEST_STEPS
        for target in TPCDI_ETL_TARGETS
    ]
