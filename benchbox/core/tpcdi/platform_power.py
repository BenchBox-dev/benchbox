from __future__ import annotations

from typing import Any

from benchbox.core.tpcdi.phases import (
    HISTORICAL_PHASE,
    POWER_WITHOUT_INITIALIZATION_MESSAGE,
    PhaseOrderError,
    run_historical_load,
)

TPCDI_BENCHMARK_ID = "tpcdi"


def execute_tpcdi_power_test(
    adapter: Any, benchmark: Any, connection: Any, run_config: dict[str, Any], *, console: Any
) -> list[dict[str, Any]]:
    initialization = benchmark.take_initialization_record()
    if initialization is None:
        raise PhaseOrderError(POWER_WITHOUT_INITIALIZATION_MESSAGE)
    platform = adapter.get_target_dialect()
    staging_dir = benchmark.digen_staging_dir
    console.print(f"[green]Running the TPC-DI historical load from {staging_dir}[/green]")
    historical = run_historical_load(
        connection,
        staging_dir,
        allow_third_party_extensions=bool(getattr(adapter, "allow_third_party_extensions", False)),
        platform=platform,
    )
    benchmark.record_measured_phases(initialization, (historical,))
    return [
        {
            "query_id": HISTORICAL_PHASE,
            "execution_time_seconds": historical.completed_mono - initialization.completed_mono,
            "status": "SUCCESS",
            "rows_returned": historical.rows,
            "test_type": "power",
            "stream_id": run_config.get("stream_id", 0),
            "position": 1,
            "iteration": 1,
            "run_type": "measurement",
        }
    ]
