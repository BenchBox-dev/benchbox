from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from benchbox.core.tpcdi.appendix import load_appendix_sql
from benchbox.core.tpcdi.etl.historical import TransformStep, load_historical
from benchbox.core.tpcdi.etl.incremental import INCREMENTAL_INGEST_STEPS, INCREMENTAL_STEPS, load_incremental
from benchbox.core.tpcdi.etl.staging import (
    INGEST_STEPS,
    SOURCE_DIALECT,
    IngestStep,
    StepResult,
    execute_sql,
    load_batch,
)
from benchbox.core.tpcdi.schema import TPCDISchemaManager
from benchbox.core.tpcdi.sources.digen import verify_staging
from benchbox.utils.clock import mono_time
from benchbox.utils.run_deadline import enter_run_phase

INITIALIZATION_PHASE = "initialization"
HISTORICAL_PHASE = "historical_load"
INITIALIZATION_BATCH = 0
HISTORICAL_BATCH = 1
INCREMENTAL_PHASES = {2: "incremental_update_1", 3: "incremental_update_2"}

POWER_WITHOUT_INITIALIZATION_MESSAGE = (
    "TPC-DI times the historical load from the end of initialization. Run the load and power phases in one command."
)

LATEST_BATCH_SQL = "SELECT coalesce(max(BatchID), 0) FROM DImessages"
COMPLETION_RECORD_SQL = (
    "SELECT max(MessageDateAndTime) FROM DImessages WHERE MessageType = 'PCR' AND BatchID = $batch_id"
)


class PhaseOrderError(RuntimeError):
    pass


class StagingScaleError(RuntimeError):
    pass


@dataclass(frozen=True)
class PhaseRecord:
    name: str
    batch_id: int
    completed_at: datetime
    completed_mono: float
    steps: tuple[StepResult, ...] = ()

    @property
    def rows(self) -> int:
        return sum(result.rows for result in self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.name,
            "batch_id": self.batch_id,
            "completion_record_timestamp": self.completed_at.isoformat(),
            "completion_monotonic_seconds": self.completed_mono,
            "steps": [
                {"name": result.step.name, "table": result.step.table, "rows": result.rows, "seconds": result.seconds}
                for result in self.steps
            ],
        }


def latest_batch(connection: Any, platform: str = SOURCE_DIALECT) -> int:
    return int(execute_sql(connection, LATEST_BATCH_SQL, platform).fetchone()[0])


def complete_phase(
    connection: Any, name: str, batch_id: int, platform: str = SOURCE_DIALECT, steps: tuple[StepResult, ...] = ()
) -> PhaseRecord:
    latest = latest_batch(connection, platform)
    if latest != batch_id:
        raise PhaseOrderError(
            f"The {name} phase must leave BatchID {batch_id} as the latest in DImessages before Appendix B runs; "
            f"the latest is {latest}"
        )
    for statement in load_appendix_sql("validation", platform).statements():
        connection.execute(statement)
    completed_mono = mono_time()
    completed_at = execute_sql(connection, COMPLETION_RECORD_SQL, platform, {"batch_id": batch_id}).fetchone()[0]
    if completed_at is None:
        raise PhaseOrderError(f"Appendix B wrote no phase completion record for BatchID {batch_id}")
    return PhaseRecord(name, batch_id, completed_at, completed_mono, steps)


def check_staging(staging_dir: Path | str, *, scale_factor: float) -> None:
    staged_scale = verify_staging(staging_dir)["scale_factor"]
    if float(staged_scale) != float(scale_factor):
        raise StagingScaleError(
            f"The DIGen staging in {staging_dir} is scale factor {staged_scale}; this run is scale factor {scale_factor}"
        )


def run_initialization(connection: Any, *, platform: str = SOURCE_DIALECT) -> PhaseRecord:
    enter_run_phase(INITIALIZATION_PHASE)
    for step in (*INGEST_STEPS, *INCREMENTAL_INGEST_STEPS):
        if not step.loads_into_existing_table:
            execute_sql(connection, f"DROP TABLE IF EXISTS {step.table}", platform)
    schema = TPCDISchemaManager()
    schema.drop_schema(connection)
    schema.create_schema(connection, platform)
    return complete_phase(connection, INITIALIZATION_PHASE, INITIALIZATION_BATCH, platform)


def run_historical_load(
    connection: Any,
    staging_dir: Path | str,
    *,
    allow_third_party_extensions: bool,
    platform: str = SOURCE_DIALECT,
) -> PhaseRecord:
    enter_run_phase(HISTORICAL_PHASE)
    steps = (
        *load_batch(
            connection,
            staging_dir,
            HISTORICAL_BATCH,
            steps=INGEST_STEPS,
            allow_third_party_extensions=allow_third_party_extensions,
            platform=platform,
        ),
        *load_historical(connection, platform=platform),
    )
    return complete_phase(connection, HISTORICAL_PHASE, HISTORICAL_BATCH, platform, steps)


def run_incremental_update(
    connection: Any,
    staging_dir: Path | str,
    batch_id: int,
    *,
    platform: str = SOURCE_DIALECT,
    ingest_steps: Sequence[IngestStep] = INCREMENTAL_INGEST_STEPS,
    steps: Sequence[TransformStep] = INCREMENTAL_STEPS,
) -> PhaseRecord:
    name = INCREMENTAL_PHASES[batch_id]
    enter_run_phase(name)
    latest = latest_batch(connection, platform)
    if latest != batch_id - 1:
        raise PhaseOrderError(
            f"The {name} phase needs the completion record of BatchID {batch_id - 1} as the latest in DImessages; "
            f"the latest is {latest}"
        )
    results = (
        *load_batch(
            connection,
            staging_dir,
            batch_id,
            steps=ingest_steps,
            allow_third_party_extensions=False,
            platform=platform,
        ),
        *load_incremental(connection, batch_id, platform=platform, steps=steps),
    )
    return complete_phase(connection, name, batch_id, platform, results)
