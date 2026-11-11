from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import benchbox.core.tpcdi.benchmark as benchmark_module
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.etl.dataframe_backend import DataFrameETLBackend
from benchbox.core.tpcdi.etl.finwire_processor import (
    CompanyFundamentalRecord,
    DailyMarketRecord,
    FinWireParser,
    SecurityMasterRecord,
)
from benchbox.core.tpcdi.etl.results import ETLPhaseResult, ETLResult
from benchbox.core.tpcdi.metrics import BenchmarkMetrics, BenchmarkReport
from benchbox.core.tpcdi.validation import DataQualityResult, ValidationResult

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_benchmark(tmp_path: Path) -> TPCDIBenchmark:
    return TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path, max_workers=1)


def _connect(engine: str) -> Any:
    if engine == "duckdb":
        return pytest.importorskip("duckdb").connect(":memory:")
    return sqlite3.connect(":memory:")


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def test_transform_accumulate_and_materialize_helpers(tmp_path: Path):
    benchmark = _make_benchmark(tmp_path)
    aggregate: dict[str, Any] = {"records_processed": 0, "transformations_applied": [], "staged_data_parts": {}}

    result_1 = {
        "records_processed": 2,
        "transformations": ["a"],
        "table_name": "DimCustomer",
        "dataframe": pd.DataFrame({"x": [1]}),
    }
    result_2 = {
        "records_processed": 3,
        "transformations": ["b"],
        "table_name": "DimCustomer",
        "dataframe": pd.DataFrame({"x": [2]}),
    }
    benchmark._accumulate_transformation_result(aggregate, result_1)
    benchmark._accumulate_transformation_result(aggregate, result_2)
    benchmark._materialize_staged_data(aggregate)

    assert aggregate["records_processed"] == 5
    assert aggregate["transformations_applied"] == ["a", "b"]
    assert list(aggregate["staged_data"]["DimCustomer"]["x"]) == [1, 2]


def test_table_name_mapping_and_validation_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    benchmark = _make_benchmark(tmp_path)
    assert benchmark._get_target_table_from_source_name("customer_2026.csv") == "DimCustomer"
    assert benchmark._get_target_table_from_source_name("unknown.csv") is None
    assert benchmark._get_target_table_from_source_name("customer.txt") == "DimCustomer"

    class _Backend:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def load_dataframes(self, dataframes: dict[str, pd.DataFrame], batch_type: str):
            return {"batch": batch_type, "tables": sorted(dataframes.keys())}

        def validate_results(self):
            return {"ok": True}

    backend_calls: list[dict[str, Any]] = []

    def _backend_factory(**kwargs):
        backend_calls.append(kwargs)
        return _Backend(**kwargs)

    monkeypatch.setattr(benchmark_module, "SQLETLBackend", _backend_factory)
    benchmark.query_manager = SimpleNamespace(get_queries_by_type=lambda _t: ["V1", "V2"])
    benchmark.get_create_tables_sql = lambda: "CREATE TABLE x(y INT)"
    benchmark.execute_query = lambda *_args, **_kwargs: []

    backend = benchmark._create_sql_etl_backend(connection=object())
    assert backend.kwargs["validation_query_ids"] == ["V1", "V2"]

    load_result = benchmark._load_warehouse_data(
        connection=object(),
        transformation_results={"staged_data": {"DimCustomer": pd.DataFrame({"id": [1]})}},
        batch_type="historical",
    )
    assert load_result == {"batch": "historical", "tables": ["DimCustomer"]}
    assert benchmark.validate_etl_results(connection=object()) == {"ok": True}
    assert len(backend_calls) == 3


def test_initialize_connection_dependent_systems_and_stats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    benchmark = _make_benchmark(tmp_path)
    benchmark.etl_stats = {"batches_processed": 3, "processing_time": 1.25, "errors": ["e1"]}
    benchmark.batch_status = {"historical": "ok"}

    created: list[str] = []

    monkeypatch.setattr(benchmark_module, "TPCDIValidator", lambda *_a, **_k: created.append("validator") or object())
    monkeypatch.setattr(benchmark_module, "TPCDIETLPipeline", lambda *_a, **_k: created.append("pipeline") or object())
    monkeypatch.setattr(benchmark_module, "TPCDIDataLoader", lambda *_a, **_k: created.append("loader") or object())
    monkeypatch.setattr(benchmark_module, "FinWireProcessor", lambda *_a, **_k: created.append("finwire") or object())
    monkeypatch.setattr(
        benchmark_module,
        "CustomerManagementProcessor",
        lambda *_a, **_k: created.append("customer_mgmt") or object(),
    )
    monkeypatch.setattr(
        benchmark_module,
        "EnhancedSCDType2Processor",
        lambda *_a, **_k: created.append("scd") or object(),
    )
    monkeypatch.setattr(
        benchmark_module,
        "IncrementalDataLoader",
        lambda *_a, **_k: created.append("incremental") or object(),
    )
    monkeypatch.setattr(benchmark_module, "DataQualityMonitor", lambda *_a, **_k: created.append("monitor") or object())
    monkeypatch.setattr(
        benchmark_module,
        "ErrorRecoveryManager",
        lambda *_a, **_k: created.append("recovery") or object(),
    )

    benchmark._initialize_connection_dependent_systems(connection=object(), dialect="duckdb")
    benchmark._initialize_connection_dependent_systems(connection=object(), dialect="duckdb")

    assert sorted(set(created)) == [
        "customer_mgmt",
        "finwire",
        "incremental",
        "loader",
        "monitor",
        "pipeline",
        "recovery",
        "scd",
        "validator",
    ]
    stats = benchmark._get_simple_stats()
    assert stats["batches_processed"] == 3
    assert stats["error_count"] == 1
    status = benchmark.get_etl_status()
    assert status["etl_mode_enabled"] is True
    assert status["simple_stats"]["batch_status"]["historical"] == "ok"


def test_run_etl_benchmark_and_extract_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    benchmark = _make_benchmark(tmp_path)
    monkeypatch.setattr(benchmark, "_initialize_connection_dependent_systems", lambda *_a, **_k: None)
    monkeypatch.setattr(benchmark, "_create_sql_etl_backend", lambda **_k: object())
    monkeypatch.setattr(
        benchmark,
        "_execute_etl_stage_queries",
        lambda **_k: [
            {"query_id": "TDI_HISTORICAL", "execution_time": 0.2, "status": "SUCCESS", "records_processed": 8},
            {"query_id": "TDI_INCREMENTAL", "execution_time": 0.1, "status": "SUCCESS", "rows_returned": 3},
            {"query_id": "TDI_SCD", "execution_time": 0.05, "status": "FAILED", "first_row": {"records_processed": 2}},
        ],
    )

    result = benchmark.run_etl_benchmark(connection=object(), dialect="duckdb")
    assert isinstance(result, ETLResult)
    assert result.total_records_processed == 13
    assert result.success is False
    assert benchmark._extract_records_processed({"records_processed": 9}) == 9
    assert benchmark._extract_records_processed({"first_row": {"records_processed": 4}}) == 4
    assert benchmark._extract_records_processed({"rows_returned": 2}) == 2


def test_validation_metrics_optimization_and_serializers(tmp_path: Path):
    benchmark = _make_benchmark(tmp_path)

    with pytest.raises(ValueError, match="Validator not initialized"):
        benchmark.run_data_validation(connection=object())

    v_item = ValidationResult(name="pk", description="d", sql="select 1", passed=True, status="passed")
    validation_result = DataQualityResult(
        validations=[v_item],
        total_validations=1,
        passed_validations=1,
        failed_validations=0,
        quality_score=1.0,
        error_count=0,
        warning_count=0,
        categories={"integrity": {"passed": 1}},
    )
    benchmark.validator = SimpleNamespace(run_all_validations=lambda: validation_result)
    assert benchmark.run_data_validation(connection=object()).quality_score == 1.0

    metrics = BenchmarkMetrics(overall_performance=7.0)
    benchmark.metrics_calculator = SimpleNamespace(calculate_detailed_metrics=lambda *_a, **_k: metrics)
    etl_result = ETLResult(
        historical_load=ETLPhaseResult(
            phase_name="Historical", total_execution_time=1.0, total_records_processed=5, success=True
        ),
        start_time=datetime.now(),
        end_time=datetime.now(),
        total_execution_time=1.0,
        total_records_processed=5,
        success=True,
    )
    assert benchmark.calculate_official_metrics(etl_result, validation_result).overall_performance == 7.0

    with pytest.raises(ValueError, match="Data loader not initialized"):
        benchmark.optimize_database(connection=object())
    benchmark.data_loader = SimpleNamespace(
        create_indexes=lambda _c: {"idx_a": True, "idx_b": True},
        optimize_tables=lambda _c: {"DimCustomer": True},
    )
    optimized = benchmark.optimize_database(connection=object())
    assert optimized["optimization_successful"] is True

    report = BenchmarkReport(
        metrics=metrics,
        summary={"ok": True},
        phase_details=[{"phase": "etl"}],
        validation_details={"score": 1.0},
        performance_breakdown={"total": 1.0},
    )
    serialized_etl = benchmark._serialize_etl_result(etl_result)
    serialized_validation = benchmark._serialize_validation_result(validation_result)
    serialized_report = benchmark._serialize_report(report)
    assert serialized_etl["success"] is True
    assert serialized_validation["total_validations"] == 1
    assert serialized_report["summary"] == {"ok": True}


def test_run_full_benchmark_success_and_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    benchmark = _make_benchmark(tmp_path)
    etl_result = ETLResult(
        historical_load=ETLPhaseResult(
            phase_name="Historical", total_execution_time=0.2, total_records_processed=2, success=True
        ),
        start_time=datetime.now(),
        end_time=datetime.now(),
        total_execution_time=0.2,
        total_records_processed=2,
        success=True,
    )
    validation_result = DataQualityResult(total_validations=1, passed_validations=1, quality_score=1.0)
    metrics = BenchmarkMetrics(overall_performance=11.0)
    report = BenchmarkReport(metrics=metrics, summary={"status": "ok"})

    monkeypatch.setattr(benchmark, "_initialize_connection_dependent_systems", lambda *_a, **_k: None)
    monkeypatch.setattr(benchmark, "create_schema", lambda *_a, **_k: None)
    monkeypatch.setattr(benchmark, "run_etl_benchmark", lambda *_a, **_k: etl_result)
    monkeypatch.setattr(benchmark, "run_data_validation", lambda *_a, **_k: validation_result)
    benchmark.metrics_calculator = SimpleNamespace(
        calculate_detailed_metrics=lambda *_a, **_k: metrics,
        generate_official_report=lambda _m: report,
        print_official_results=lambda _m: None,
        export_metrics_json=lambda _m: {"overall_performance": _m.overall_performance},
    )

    success = benchmark.run_full_benchmark(connection=object(), dialect="duckdb")
    assert success["success"] is True
    assert success["metrics"]["overall_performance"] == 11.0
    assert success["report"]["summary"]["status"] == "ok"

    monkeypatch.setattr(benchmark, "create_schema", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    failure = benchmark.run_full_benchmark(connection=object(), dialect="duckdb")
    assert failure["success"] is False
    assert "boom" in failure["error"]


def test_generate_source_data_all_formats_and_invalid_format(tmp_path: Path):
    benchmark = _make_benchmark(tmp_path)

    generated = benchmark.generate_source_data(
        formats=["csv", "xml", "fixed_width", "json"],
        batch_types=["historical"],
    )

    assert sorted(generated.keys()) == ["csv", "fixed_width", "json", "xml"]
    assert len(generated["csv"]) == 2
    assert len(generated["xml"]) == 1
    assert len(generated["fixed_width"]) == 1
    assert len(generated["json"]) == 1
    assert all(Path(path).exists() for files in generated.values() for path in files)

    with pytest.raises(ValueError, match="Unsupported format"):
        benchmark.generate_source_data(formats=["bad"], batch_types=["historical"])


@pytest.mark.parametrize("engine", ["sqlite", "duckdb"])
def test_canonical_incremental_etl_keeps_one_current_customer_version_per_business_key(tmp_path: Path, engine: str):
    benchmark = _make_benchmark(tmp_path)
    with _connect(engine) as connection:
        benchmark.create_schema(connection, engine)

        historical = benchmark.run_etl_pipeline(connection, batch_type="historical", validate_data=False)
        first_incremental = benchmark.run_etl_pipeline(connection, batch_type="incremental", validate_data=False)
        recovered_benchmark = _make_benchmark(tmp_path)
        second_incremental = recovered_benchmark.run_etl_pipeline(
            connection, batch_type="incremental", validate_data=False
        )

        assert historical["success"] is True
        assert first_incremental["success"] is True
        assert second_incremental["success"] is True
        versions = connection.execute(
            """
            SELECT SK_CustomerID, IsCurrent, BatchID, EffectiveDate, EndDate
            FROM DimCustomer
            WHERE CustomerID = 100000000
            ORDER BY SK_CustomerID
            """
        ).fetchall()
        assert [row[0] for row in versions] == [1, 1_000_001, 1_000_002]
        assert [bool(row[1]) for row in versions] == [False, False, True]
        assert [row[2] for row in versions] == [1, 2, 3]
        assert _as_date(versions[0][4]) == _as_date(versions[1][3]) - timedelta(days=1)
        assert _as_date(versions[1][4]) == _as_date(versions[2][3]) - timedelta(days=1)
        assert _as_date(versions[2][3]) <= date.today()
        assert _as_date(versions[2][4]) == date(9999, 12, 31)
        assert connection.execute(
            "SELECT COUNT(*) FROM (SELECT CustomerID FROM DimCustomer WHERE IsCurrent = 1 "
            "GROUP BY CustomerID HAVING COUNT(*) != 1)"
        ).fetchone() == (0,)


@pytest.mark.parametrize("engine", ["sqlite", "duckdb"])
def test_canonical_incremental_etl_rolls_back_expiration_when_replacement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str
):
    benchmark = _make_benchmark(tmp_path)
    with _connect(engine) as connection:
        benchmark.create_schema(connection, engine)
        benchmark.run_etl_pipeline(connection, batch_type="historical", validate_data=False)
        backend = benchmark._create_sql_etl_backend(connection=connection)
        monkeypatch.setattr(
            backend,
            "_insert_table_dataframe",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("insert failed")),
        )

        with pytest.raises(RuntimeError, match="insert failed"):
            benchmark.run_etl_pipeline(backend=backend, batch_type="incremental", validate_data=False)

        (is_current,) = connection.execute("SELECT IsCurrent FROM DimCustomer WHERE CustomerID = 100000000").fetchone()
        assert bool(is_current) is True


def test_dataframe_backend_rejects_repeated_incremental_customer_batches_without_atomic_support(tmp_path: Path):
    class _DataFrameMaintenanceOps:
        def __init__(self) -> None:
            self.inserted: list[pd.DataFrame] = []

        def insert_rows(self, _table: Path, dataframe: pd.DataFrame, **_kwargs: Any) -> SimpleNamespace:
            self.inserted.append(dataframe.copy())
            return SimpleNamespace(success=True, rows_affected=len(dataframe))

    benchmark = _make_benchmark(tmp_path)
    maintenance_ops = _DataFrameMaintenanceOps()
    backend = DataFrameETLBackend(maintenance_ops=maintenance_ops, platform_name="test")

    historical = benchmark.run_etl_pipeline(backend=backend, batch_type="historical", validate_data=False)
    assert historical["success"] is True

    for _ in range(2):
        with pytest.raises(RuntimeError, match="durable atomic SCD2 batch support"):
            benchmark.run_etl_pipeline(backend=backend, batch_type="incremental", validate_data=False)

    assert len(maintenance_ops.inserted) == 1


def test_generated_finwire_records_match_the_parser_layout(tmp_path: Path):
    benchmark = _make_benchmark(tmp_path)
    path = benchmark._generate_finwire_data_files()[0]
    parser = FinWireParser()

    records = list(parser.parse_file(path))

    assert not parser.errors
    assert sum(isinstance(record, CompanyFundamentalRecord) for record in records) == 1
    assert sum(isinstance(record, SecurityMasterRecord) for record in records) == 5
    assert sum(isinstance(record, DailyMarketRecord) for record in records) == 2
    assert records[0].company_id == "0000000001"
    assert records[0].company_name == "Company_0001"
    assert records[0].ceo == "CEO_1"
    security = next(record for record in records if isinstance(record, SecurityMasterRecord))
    assert security.symbol == "SEC0001"
    assert security.shares_outstanding == 1000000


