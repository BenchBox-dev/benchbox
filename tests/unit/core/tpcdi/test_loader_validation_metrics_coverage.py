from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

import benchbox.utils.printing as printing
from benchbox.core.tpcdi.etl.results import ETLPhaseResult, ETLResult
from benchbox.core.tpcdi.loader import TPCDIDataLoader
from benchbox.core.tpcdi.metrics import BenchmarkMetrics, TPCDIMetrics
from benchbox.core.tpcdi.validation import DataQualityResult, TPCDIValidator, ValidationRule

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@dataclass
class _FetchOne:
    value: tuple[int, ...] | None

    def fetchone(self):
        return self.value


class _ExecConn:
    def __init__(self, fetch_value: tuple[int, ...] | None = (0,)):
        self.fetch_value = fetch_value
        self.executed: list[tuple[str, tuple | None]] = []
        self.executemany_calls: list[tuple[str, list[tuple]]] = []

    def execute(self, sql: str, params: tuple | None = None):
        self.executed.append((sql, params))
        return _FetchOne(self.fetch_value)

    def executemany(self, sql: str, values: list[tuple]):
        self.executemany_calls.append((sql, values))


class _ExecBySqlConn(_ExecConn):
    def execute(self, sql: str, params: tuple | None = None):
        self.executed.append((sql, params))
        value = (1,) if "select 1" in sql.lower() else (0,)
        return _FetchOne(value)


class _QueryConn:
    def __init__(self, value: tuple[int, ...] | None = (0,)):
        self.value = value
        self.queries: list[str] = []

    def query(self, sql: str):
        self.queries.append(sql)
        return _FetchOne(self.value)


def test_loader_normalize_and_load_dimension_success(monkeypatch: pytest.MonkeyPatch):
    conn = _ExecConn(fetch_value=(10,))
    loader = TPCDIDataLoader(conn, batch_size=2)
    records = [{"id": 1}, {"id": 2}]
    monkeypatch.setattr(loader, "_get_table_count", lambda _: 5)

    result = loader.load_dimension_data("DimCustomer", records)

    assert result.success is True
    assert result.records_loaded == 2
    assert result.pre_load_count == 5
    assert result.post_load_count == 5


def test_loader_load_dimension_handles_empty_and_errors(monkeypatch: pytest.MonkeyPatch):
    conn = _ExecConn()
    loader = TPCDIDataLoader(conn)
    assert loader.load_dimension_data("DimCustomer", []).success is True

    monkeypatch.setattr(loader, "_get_table_count", lambda _: 0)
    monkeypatch.setattr(loader, "_bulk_insert", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
    failed = loader.load_dimension_data("DimCustomer", [{"id": 1}])
    assert failed.success is False
    assert "boom" in (failed.error_message or "")


def test_loader_csv_and_table_helpers(tmp_path: Path):
    conn = _ExecConn(fetch_value=(3,))
    loader = TPCDIDataLoader(conn, dialect="duckdb")
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("id,name\n1,a\n2,b\n", encoding="utf-8")

    loaded = loader.load_csv_file("DimCustomer", csv_path)
    missing = loader.load_csv_file("DimCustomer", tmp_path / "missing.csv")
    info = loader.get_table_info("DimCustomer")

    assert loaded.success is True
    assert loaded.records_loaded == 2
    assert missing.success is False
    assert info["exists"] is True
    assert info["record_count"] == 3


def test_loader_index_optimize_truncate_paths(monkeypatch: pytest.MonkeyPatch):
    conn = _ExecConn(fetch_value=(0,))
    loader = TPCDIDataLoader(conn, dialect="duckdb")

    monkeypatch.setattr("benchbox._vendor.sqlglot.transpile", lambda *args, **kwargs: [args[0]])
    index_results = loader.create_indexes(conn)
    assert any(index_results.values())

    pg_results = TPCDIDataLoader(conn, dialect="postgres").optimize_tables(conn)
    query_results = TPCDIDataLoader(_QueryConn(), dialect="mysql").optimize_tables(_QueryConn())
    assert sum(pg_results.values()) >= 1
    assert sum(query_results.values()) >= 1

    assert loader.truncate_table("DimCustomer") is True
    assert TPCDIDataLoader(object()).truncate_table("DimCustomer") is False


def test_loader_bulk_insert_and_count_fallbacks():
    class ExecuteOnlyConn:
        def __init__(self):
            self.calls: list[tuple[str, tuple | None]] = []

        def execute(self, sql: str, params: tuple | None = None):
            self.calls.append((sql, params))
            return _FetchOne((7,))

    conn = ExecuteOnlyConn()
    loader = TPCDIDataLoader(conn, batch_size=1)
    inserted = loader._bulk_insert("DimAccount", [{"id": 1}, {"id": 2}])
    assert inserted == 2
    assert loader._get_table_count("DimAccount") == 7

    assert loader._normalize_data_format(pd.DataFrame([{"id": 1}])) == [{"id": 1}]
    assert loader._normalize_data_format({"id": 1} for _ in range(1)) == [{"id": 1}]
    assert loader._normalize_data_format(123) == []


def test_validator_run_paths_and_aggregate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(printing, "_QUIET", False)
    monkeypatch.setattr(printing, "_STD_CONSOLE", None)
    validator = TPCDIValidator(_ExecBySqlConn(fetch_value=(0,)), dialect="duckdb")
    validator.validation_rules = [
        ValidationRule("ok", "select 0", 0, "ok desc", category="integrity"),
        ValidationRule("warn", "select 1", 0, "warn desc", category="completeness", severity="warning"),
    ]
    monkeypatch.setattr("benchbox._vendor.sqlglot.transpile", lambda *args, **kwargs: [args[0]])

    ok = validator.run_validation(validator.validation_rules[0])
    assert ok.passed is True
    assert ok.status == "PASSED"

    query_validator = TPCDIValidator(_QueryConn((1,)), dialect="standard")
    query_validator.validation_rules = [ValidationRule("q", "select 1", 1, "q desc", category="foreign_key")]
    qres = query_validator.validate_foreign_keys()
    assert qres[0].passed is True

    bad_conn = TPCDIValidator(object(), dialect="duckdb")
    err = bad_conn.run_validation(ValidationRule("bad", "select 1", 1, "bad desc"))
    assert err.passed is False
    assert err.error is not None

    agg = validator.run_all_validations()
    assert agg.total_validations == 2
    assert agg.failed_validations == 1
    assert agg.warning_count == 1

    empty = validator.run_category_validations("missing")
    assert empty.total_validations == 0

    validator.print_validation_summary(agg)
    assert "TPC-DI DATA QUALITY VALIDATION RESULTS" in capsys.readouterr().out


def test_metrics_calculations_and_exports(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(printing, "_QUIET", False)
    monkeypatch.setattr(printing, "_STD_CONSOLE", None)
    start = datetime(2026, 2, 1, 10, 0, 0)
    end = start + timedelta(seconds=30)
    historical = ETLPhaseResult(
        phase_name="historical",
        total_execution_time=10.0,
        total_records_processed=500,
        success=True,
    )
    incremental = ETLPhaseResult(
        phase_name="incremental",
        total_execution_time=5.0,
        total_records_processed=250,
        success=True,
    )
    etl_result = ETLResult(historical_load=historical, incremental_loads=[incremental], success=True)
    validation_result = DataQualityResult(
        total_validations=2,
        passed_validations=2,
        failed_validations=0,
        error_count=0,
        warning_count=0,
    )
    validation_result.quality_score = 1.0

    metrics_calc = TPCDIMetrics(scale_factor=1.0)
    metrics = metrics_calc.calculate_detailed_metrics(etl_result, validation_result, start, end)
    report = metrics_calc.generate_official_report(metrics)
    exported = metrics_calc.export_metrics_json(metrics)
    metrics_calc.print_official_results(metrics)

    assert metrics.etl_throughput > 0
    assert metrics.tpc_di_compliant is True
    assert report.summary["tpc_di_compliant"] == "YES"
    assert exported["primary_metrics"]["data_quality_score"] == 1.0
    assert "OFFICIAL TPC-DI BENCHMARK RESULTS" in capsys.readouterr().out

    comparison = metrics_calc.compare_benchmarks(metrics, metrics)
    assert comparison["improvement"] is False
    assert metrics_calc._calculate_percentage_change(0, 5) == float("inf")

    assert metrics_calc.calculate_overall_performance(0, 1.0) == 0.0
    assert metrics_calc.calculate_data_quality_score(DataQualityResult(total_validations=0)) == 0.0
    assert metrics_calc._format_duration(30) == "30.00s"
    assert metrics_calc._format_duration(120) == "2.0m"
    assert metrics_calc._format_duration(7200) == "2.00h"
