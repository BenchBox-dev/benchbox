from __future__ import annotations

from typing import Any

from benchbox.core.tpcdi.phases import (
    INCREMENTAL_PHASES,
    POWER_WITHOUT_INITIALIZATION_MESSAGE,
    PhaseOrderError,
    PhaseRecord,
    run_historical_load,
    run_incremental_update,
)
from benchbox.core.tpcdi.visibility import VisibilityRunner
from benchbox.utils.clock import elapsed_seconds

TPCDI_BENCHMARK_ID = "tpcdi"
HISTORICAL_ROW = "TDI_HISTORICAL"
INCREMENTAL_ROWS = {2: "TDI_INCREMENTAL_1", 3: "TDI_INCREMENTAL_2"}


def timed_row(
    query_id: str, start: PhaseRecord, end: PhaseRecord, position: int, run_config: dict[str, Any]
) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "execution_time_seconds": elapsed_seconds(start.completed_mono, end.completed_mono),
        "status": "SUCCESS",
        "rows_returned": end.rows,
        "test_type": "power",
        "stream_id": run_config.get("stream_id", 0),
        "position": position,
        "iteration": 1,
        "run_type": "measurement",
    }


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
    runner = VisibilityRunner(adapter.new_stream_connection(connection, benchmark_type=TPCDI_BENCHMARK_ID), platform)
    handed_over = False
    try:
        runner.start()
        console.print(
            f"[green]Running {INCREMENTAL_PHASES[2]} and {INCREMENTAL_PHASES[3]} with visibility queries[/green]"
        )
        first = run_incremental_update(connection, staging_dir, 2, platform=platform)
        runner.boundary()
        second = run_incremental_update(connection, staging_dir, 3, platform=platform)
        runner.stop()
        benchmark.record_measured_phases(initialization, (historical, first, second), runner)
        handed_over = True
    finally:
        if not handed_over:
            runner.stop()
            runner.close()
    return [
        timed_row(HISTORICAL_ROW, initialization, historical, 1, run_config),
        timed_row(INCREMENTAL_ROWS[2], historical, first, 2, run_config),
        timed_row(INCREMENTAL_ROWS[3], first, second, 3, run_config),
    ]
