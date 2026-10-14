from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_benchmark(tmp_path: Path) -> TPCDIBenchmark:
    return TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path, max_workers=1)


class _FakeMaintenanceOps:
    def __init__(self) -> None:
        self.insert_calls: list[tuple[str, Any]] = []

    def get_capabilities(self) -> Any:
        return SimpleNamespace(
            supports_transactions=True,
            supports_insert=True,
            supports_update=True,
            notes="",
            transaction_isolation=SimpleNamespace(value="snapshot"),
        )

    def insert_rows(self, table_path: str, dataframe: Any, partition_columns=None, mode: str = "append") -> Any:
        _ = (partition_columns, mode)
        self.insert_calls.append((table_path, dataframe))
        row_count = len(dataframe) if hasattr(dataframe, "__len__") else 0
        return SimpleNamespace(success=True, rows_affected=row_count, error_message=None)

    def update_rows(self, table_path: str, condition: Any, updates: dict[str, Any]) -> Any:
        _ = (table_path, condition, updates)
        return SimpleNamespace(success=True, rows_affected=1, error_message=None)

    def merge_rows(
        self,
        table_path: str,
        source_dataframe: Any,
        merge_condition: Any,
        when_matched: dict[str, Any] | None = None,
        when_not_matched: dict[str, Any] | None = None,
    ) -> Any:
        _ = (table_path, source_dataframe, merge_condition, when_matched, when_not_matched)
        return SimpleNamespace(success=True, rows_affected=1, error_message=None)


def test_tpcdi_supports_dataframe_mode(tmp_path: Path) -> None:
    benchmark = _make_benchmark(tmp_path)
    assert benchmark.supports_dataframe_mode() is True
    assert benchmark.skip_dataframe_data_loading() is True


def test_execute_dataframe_workload_runs_all_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    benchmark = _make_benchmark(tmp_path)
    fake_ops = _FakeMaintenanceOps()
    seen_batches: list[str] = []

    def fake_run_etl_pipeline(
        *,
        connection: Any | None = None,
        backend: Any | None = None,
        batch_type: str,
        validate_data: bool = True,
    ) -> dict[str, Any]:
        _ = (connection, validate_data)
        assert backend is not None
        seen_batches.append(batch_type)
        return {
            "success": True,
            "total_duration": 0.01,
            "phases": {"transform": {"records_processed": 5}, "load": {"records_loaded": 3}},
            "validation_results": {"data_quality_score": 1.0},
        }

    monkeypatch.setattr(benchmark, "run_etl_pipeline", fake_run_etl_pipeline)
    monkeypatch.setattr(
        "benchbox.core.tpcdi.benchmark.get_maintenance_operations_for_platform", lambda _p, _engine: fake_ops
    )

    results = benchmark.execute_dataframe_workload(
        ctx=None,
        adapter=SimpleNamespace(platform_name="mock"),
        benchmark_config=None,
        query_filter=None,
        monitor=None,
        run_options=None,
    )

    assert seen_batches == ["historical", "incremental", "scd"]
    assert [r["query_id"] for r in results] == ["TDI_CAPABILITIES", "TDI_HISTORICAL", "TDI_INCREMENTAL", "TDI_SCD"]
    assert all(r["status"] == "SUCCESS" for r in results)


def test_execute_dataframe_workload_respects_query_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    benchmark = _make_benchmark(tmp_path)
    fake_ops = _FakeMaintenanceOps()
    seen_batches: list[str] = []

    def fake_run_etl_pipeline(
        *,
        connection: Any | None = None,
        backend: Any | None = None,
        batch_type: str,
        validate_data: bool = True,
    ) -> dict[str, Any]:
        _ = (connection, backend, validate_data)
        seen_batches.append(batch_type)
        return {"success": True, "total_duration": 0.01, "phases": {}, "validation_results": {}}

    monkeypatch.setattr(benchmark, "run_etl_pipeline", fake_run_etl_pipeline)
    monkeypatch.setattr(
        "benchbox.core.tpcdi.benchmark.get_maintenance_operations_for_platform", lambda _p, _engine: fake_ops
    )

    results = benchmark.execute_dataframe_workload(
        ctx=None,
        adapter=SimpleNamespace(platform_name="mock"),
        benchmark_config=None,
        query_filter={"TDI_SCD"},
        monitor=None,
        run_options=None,
    )

    assert seen_batches == ["scd"]
    assert len(results) == 2
    assert results[0]["query_id"] == "TDI_CAPABILITIES"
    assert results[1]["query_id"] == "TDI_SCD"


def test_execute_dataframe_workload_rejects_backend_overrides(tmp_path: Path) -> None:
    benchmark = _make_benchmark(tmp_path)
    config = SimpleNamespace(options={"tpcdi_dataframe_backend": "duckdb"})

    results = benchmark.execute_dataframe_workload(
        ctx=None,
        adapter=None,
        benchmark_config=config,
        query_filter=None,
        monitor=None,
        run_options=None,
    )

    assert len(results) == 1
    assert results[0]["query_id"] == "TDI_WORKLOAD"
    assert results[0]["status"] == "FAILED"
    assert "no longer supports backend overrides" in str(results[0].get("error"))
    assert "tpcdi_dataframe_backend" in str(results[0].get("error"))


def test_execute_dataframe_workload_never_uses_sql_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    benchmark = _make_benchmark(tmp_path)
    fake_ops = _FakeMaintenanceOps()

    class _AdapterWithSQL:
        platform_name = "mock"

        def get_sql_connection(self) -> Any:
            raise AssertionError("execute_dataframe_workload must not call get_sql_connection()")

    monkeypatch.setattr(
        "benchbox.core.tpcdi.benchmark.get_maintenance_operations_for_platform", lambda _p, _engine: fake_ops
    )
    monkeypatch.setattr(
        benchmark,
        "run_etl_pipeline",
        lambda **_kwargs: {"success": True, "total_duration": 0.01, "phases": {}, "validation_results": {}},
    )

    results = benchmark.execute_dataframe_workload(
        ctx=SimpleNamespace(get_sql_connection=lambda: object()),
        adapter=_AdapterWithSQL(),
        benchmark_config=SimpleNamespace(options={}),
        query_filter={"TDI_HISTORICAL"},
        monitor=None,
        run_options=None,
    )

    assert len(results) == 2
    assert results[1]["query_id"] == "TDI_HISTORICAL"
    assert results[1]["status"] == "SUCCESS"


def test_execute_dataframe_workload_fails_transactional_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    benchmark = _make_benchmark(tmp_path)

    class _NonTransactionalOps(_FakeMaintenanceOps):
        def get_capabilities(self) -> Any:
            return SimpleNamespace(
                supports_transactions=False,
                supports_insert=False,
                supports_update=False,
                notes="",
                transaction_isolation=SimpleNamespace(value="none"),
            )

    monkeypatch.setattr(
        "benchbox.core.tpcdi.benchmark.get_maintenance_operations_for_platform",
        lambda _p, _engine: _NonTransactionalOps(),
    )

    results = benchmark.execute_dataframe_workload(
        ctx=None,
        adapter=SimpleNamespace(platform_name="mock"),
        benchmark_config=SimpleNamespace(options={"tpcdi_require_transactional_capabilities": True}),
        query_filter=None,
        monitor=None,
        run_options=None,
    )

    assert [r["query_id"] for r in results] == ["TDI_CAPABILITIES", "TDI_TRANSACTIONAL_CONTRACT"]
    assert results[1]["status"] == "FAILED"


def test_execute_dataframe_workload_stage_parity_and_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    benchmark = _make_benchmark(tmp_path)
    fake_ops = _FakeMaintenanceOps()

    def fake_run_etl_pipeline(
        *,
        connection: Any | None = None,
        backend: Any | None = None,
        batch_type: str,
        validate_data: bool = True,
    ) -> dict[str, Any]:
        _ = (connection, backend, validate_data)
        if batch_type == "historical":
            return {
                "success": True,
                "total_duration": 0.11,
                "phases": {"transform": {"records_processed": 100}, "load": {"records_loaded": 90}},
                "validation_results": {"data_quality_score": 0.98},
            }
        if batch_type == "incremental":
            return {
                "success": True,
                "total_duration": 0.07,
                "phases": {"transform": {"records_processed": 40}, "load": {"records_loaded": 35}},
                "validation_results": {"data_quality_score": 0.96},
            }
        return {
            "success": True,
            "total_duration": 0.05,
            "phases": {"transform": {"records_processed": 20}, "load": {"records_loaded": 18}},
            "validation_results": {"data_quality_score": 0.95},
        }

    monkeypatch.setattr(benchmark, "run_etl_pipeline", fake_run_etl_pipeline)
    monkeypatch.setattr(
        "benchbox.core.tpcdi.benchmark.get_maintenance_operations_for_platform", lambda _p, _engine: fake_ops
    )

    results = benchmark.execute_dataframe_workload(
        ctx=None,
        adapter=SimpleNamespace(platform_name="mock"),
        benchmark_config=None,
        query_filter=None,
        monitor=None,
        run_options=None,
    )

    stage_results = {
        row["query_id"]: row
        for row in results
        if row["query_id"].startswith("TDI_") and row["query_id"] != "TDI_CAPABILITIES"
    }
    assert set(stage_results) == {"TDI_HISTORICAL", "TDI_INCREMENTAL", "TDI_SCD"}
    assert stage_results["TDI_HISTORICAL"]["first_row"]["records_processed"] == 100
    assert stage_results["TDI_INCREMENTAL"]["first_row"]["records_loaded"] == 35
    assert stage_results["TDI_SCD"]["first_row"]["data_quality_score"] == pytest.approx(0.95)


def test_execute_dataframe_workload_fails_without_maintenance_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    benchmark = _make_benchmark(tmp_path)
    monkeypatch.setattr(
        "benchbox.core.tpcdi.benchmark.get_maintenance_operations_for_platform", lambda _p, _engine: None
    )

    results = benchmark.execute_dataframe_workload(
        ctx=None,
        adapter=SimpleNamespace(platform_name="mock"),
        benchmark_config=None,
        query_filter=None,
        monitor=None,
        run_options=None,
    )

    assert len(results) == 2
    assert results[1]["query_id"] == "TDI_WORKLOAD"
    assert results[1]["status"] == "FAILED"
    assert "requires DataFrame maintenance operations" in str(results[1].get("error"))


def test_get_dataframe_etl_capabilities_reports_missing_interface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    benchmark = _make_benchmark(tmp_path)
    monkeypatch.setattr(
        "benchbox.core.tpcdi.benchmark.get_maintenance_operations_for_platform", lambda _platform, _engine: None
    )

    caps = benchmark.get_dataframe_etl_capabilities("pandas-df")

    assert caps["has_maintenance_interface"] is False
    assert caps["supports_transactions"] is False
    assert caps["contract_status"] == "missing"


def test_execute_dataframe_workload_resolves_maintenance_ops_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    benchmark = _make_benchmark(tmp_path)
    fake_ops = _FakeMaintenanceOps()
    calls = {"count": 0}

    def _resolve(_platform: str, _engine: str) -> Any:
        calls["count"] += 1
        return fake_ops

    monkeypatch.setattr("benchbox.core.tpcdi.benchmark.get_maintenance_operations_for_platform", _resolve)
    monkeypatch.setattr(
        benchmark,
        "run_etl_pipeline",
        lambda **_kwargs: {"success": True, "total_duration": 0.01, "phases": {}, "validation_results": {}},
    )

    results = benchmark.execute_dataframe_workload(
        ctx=None,
        adapter=SimpleNamespace(platform_name="mock"),
        benchmark_config=SimpleNamespace(options={}),
        query_filter={"TDI_HISTORICAL"},
        monitor=None,
        run_options=None,
    )

    assert len(results) == 2
    assert calls["count"] == 1


def test_accumulate_transformation_result_materializes_once(tmp_path: Path) -> None:
    benchmark = _make_benchmark(tmp_path)
    aggregate = {
        "records_processed": 0,
        "transformations_applied": [],
        "staged_data": {},
        "staged_data_parts": {},
    }
    first = pd.DataFrame({"SK_CustomerID": [1, 2]})
    second = pd.DataFrame({"SK_CustomerID": [3]})

    benchmark._accumulate_transformation_result(
        aggregate,
        {
            "records_processed": 2,
            "transformations": ["csv_to_staging"],
            "table_name": "DimCustomer",
            "dataframe": first,
        },
    )
    benchmark._accumulate_transformation_result(
        aggregate,
        {
            "records_processed": 1,
            "transformations": ["csv_to_staging"],
            "table_name": "DimCustomer",
            "dataframe": second,
        },
    )
    benchmark._materialize_staged_data(aggregate)

    staged = aggregate["staged_data"]["DimCustomer"]
    assert aggregate["records_processed"] == 3
    assert list(staged["SK_CustomerID"]) == [1, 2, 3]
