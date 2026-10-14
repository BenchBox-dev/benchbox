from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_benchmark(tmp_path: Path) -> TPCDIBenchmark:
    return TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path, max_workers=1)


def test_sql_and_dataframe_share_stage_execution_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    benchmark = _make_benchmark(tmp_path)
    fake_ops = SimpleNamespace(
        get_capabilities=lambda: SimpleNamespace(
            supports_transactions=True,
            supports_insert=True,
            supports_update=True,
            notes="",
            transaction_isolation=SimpleNamespace(value="snapshot"),
        ),
        insert_rows=lambda *args, **kwargs: SimpleNamespace(success=True, rows_affected=1, error_message=None),
        update_rows=lambda *args, **kwargs: SimpleNamespace(success=True, rows_affected=1, error_message=None),
        merge_rows=lambda *args, **kwargs: SimpleNamespace(success=True, rows_affected=1, error_message=None),
    )
    monkeypatch.setattr(
        "benchbox.core.tpcdi.benchmark.get_maintenance_operations_for_platform", lambda _p, _engine: fake_ops
    )

    def fake_run_etl_pipeline(
        *,
        connection: Any | None = None,
        backend: Any | None = None,
        batch_type: str,
        validate_data: bool = True,
    ) -> dict[str, Any]:
        _ = (validate_data,)
        assert (connection is None) ^ (backend is None)
        stage_payloads = {
            "historical": {"duration": 0.10, "processed": 100, "loaded": 90, "quality": 0.99},
            "incremental": {"duration": 0.07, "processed": 40, "loaded": 35, "quality": 0.96},
            "scd": {"duration": 0.05, "processed": 20, "loaded": 18, "quality": 0.95},
        }
        payload = stage_payloads[batch_type]
        return {
            "success": True,
            "total_duration": payload["duration"],
            "phases": {
                "transform": {"records_processed": payload["processed"]},
                "load": {"records_loaded": payload["loaded"]},
            },
            "validation_results": {"data_quality_score": payload["quality"]},
        }

    monkeypatch.setattr(benchmark, "run_etl_pipeline", fake_run_etl_pipeline)
    monkeypatch.setattr(benchmark, "_initialize_connection_dependent_systems", lambda *_args, **_kwargs: None)

    sql_result = benchmark.run_etl_benchmark(connection=object(), dialect="duckdb")
    dataframe_result = benchmark.execute_dataframe_workload(
        ctx=None,
        adapter=SimpleNamespace(platform_name="mock"),
        benchmark_config=SimpleNamespace(options={}),
        query_filter=None,
        monitor=None,
        run_options=None,
    )

    df_stages = {row["query_id"]: row for row in dataframe_result if row["query_id"].startswith("TDI_")}
    assert set(df_stages) == {"TDI_CAPABILITIES", "TDI_HISTORICAL", "TDI_INCREMENTAL", "TDI_SCD"}

    assert sql_result.historical_load is not None
    assert (
        sql_result.historical_load.total_records_processed
        == df_stages["TDI_HISTORICAL"]["first_row"]["records_processed"]
    )
    assert sql_result.historical_load.success == (df_stages["TDI_HISTORICAL"]["status"] == "SUCCESS")

    inc_load, scd_load = sql_result.incremental_loads
    assert inc_load.total_records_processed == df_stages["TDI_INCREMENTAL"]["first_row"]["records_processed"]
    assert scd_load.total_records_processed == df_stages["TDI_SCD"]["first_row"]["records_processed"]
    assert sql_result.total_execution_time > 0.0
