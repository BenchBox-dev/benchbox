from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.generator.data import TPCDIDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _ExecConn:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None):
        self.rows = rows or [(1,)]
        self.sql: list[str] = []

    def execute(self, sql: str):
        self.sql.append(sql)
        return SimpleNamespace(fetchall=lambda: self.rows)


class _CursorConn:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None):
        self.rows = rows or [(1,)]
        self.executed: list[str] = []

    def cursor(self):
        return SimpleNamespace(
            execute=lambda sql: self.executed.append(sql),
            fetchall=lambda: self.rows,
        )


def _make_benchmark(tmp_path: Path) -> TPCDIBenchmark:
    return TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path, max_workers=1)


def test_query_and_translation_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    benchmark = _make_benchmark(tmp_path)

    benchmark.query_manager = SimpleNamespace(
        _all_queries={"V1": "select 1", "A1": "select {limit_rows}"},
        get_query=lambda qid, params, dialect: f"Q:{qid}:{params}:{dialect}",
        get_all_queries=lambda: {"V1": "select 1"},
        get_execution_plan=lambda: [("V1", "validation", [])],
    )

    assert "VQ7" in benchmark.get_query(7, None, None)
    assert "AQ3" in benchmark.get_query("15", None, None)

    monkeypatch.setattr(benchmark, "translate_query_text", lambda sql, d: f"{d}:{sql}")
    queries = benchmark.get_queries(dialect="duckdb")
    assert queries["V1"].startswith("duckdb:")
    assert queries["A1"].startswith("duckdb:")

    assert benchmark.get_all_queries() == {"V1": "select 1"}
    assert benchmark.get_query_execution_plan() == [("V1", "validation", [])]


def test_execute_query_and_schema_helpers(tmp_path: Path):
    benchmark = _make_benchmark(tmp_path)
    benchmark.query_manager = SimpleNamespace(get_query=lambda *_args, **_kwargs: "SELECT 1")

    assert benchmark.execute_query("V1", _ExecConn(rows=[(1,), (2,)])) == [(1,), (2,)]
    assert benchmark.execute_query("V1", _CursorConn(rows=[("ok",)])) == [("ok",)]
    with pytest.raises(ValueError, match="Unsupported connection type"):
        benchmark.execute_query("V1", object())

    tuning = SimpleNamespace(
        primary_keys=SimpleNamespace(enabled=True),
        foreign_keys=SimpleNamespace(enabled=False),
    )
    schema_sql = benchmark.get_create_tables_sql(dialect="standard", tuning_config=tuning)
    assert "CREATE TABLE" in schema_sql
    assert "DimCustomer" in benchmark.get_schema()


def test_dataframe_capability_and_etl_result_helpers(tmp_path: Path):
    benchmark = _make_benchmark(tmp_path)
    missing = benchmark._build_dataframe_etl_capabilities("mock", None)
    partial = benchmark._build_dataframe_etl_capabilities(
        "mock",
        SimpleNamespace(
            get_capabilities=lambda: SimpleNamespace(
                supports_transactions=True,
                supports_insert=True,
                supports_update=False,
                notes="partial",
                transaction_isolation=SimpleNamespace(value="snapshot"),
            )
        ),
    )
    assert missing["contract_status"] == "missing"
    assert partial["contract_status"] == "partial"

    stage_results = [
        {"query_id": "TDI_HISTORICAL", "execution_time": 0.1, "status": "SUCCESS", "records_processed": 10},
        {
            "query_id": "TDI_INCREMENTAL",
            "execution_time": 0.05,
            "status": "SUCCESS",
            "first_row": {"records_processed": 5},
        },
        {"query_id": "TDI_SCD", "execution_time": 0.02, "status": "FAILED", "rows_returned": 3},
    ]
    etl_result = benchmark._build_etl_result_from_stage_results(
        stage_results,
        start_time=datetime(2026, 1, 1, 0, 0, 0),
        end_time=datetime(2026, 1, 1, 0, 0, 1),
    )

    assert etl_result.historical_load is not None
    assert etl_result.historical_load.total_records_processed == 10
    assert etl_result.incremental_loads[0].total_records_processed == 5
    assert etl_result.incremental_loads[1].total_records_processed == 3
    assert etl_result.success is False
    assert benchmark._extract_records_processed({"records_processed": 4}) == 4


def test_etl_status_and_generator_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    benchmark = _make_benchmark(tmp_path)
    status = benchmark.get_etl_status()
    assert status["etl_mode_enabled"] is True
    assert status["simple_stats"]["batches_processed"] == 0
    assert "csv" in status["supported_formats"]

    generator = TPCDIDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
    monkeypatch.setattr(generator, "_estimate_memory_requirements", lambda: 1.2)
    monkeypatch.setattr(generator, "_estimate_disk_requirements", lambda: 3.4)
    cfg = generator.get_generation_config()
    assert cfg["enable_progress"] is False
    assert cfg["estimated_memory_gb"] == 1.2
    assert cfg["estimated_disk_gb"] == 3.4


def test_generate_data_validates_inputs_and_returns_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    benchmark = _make_benchmark(tmp_path)
    monkeypatch.setattr("benchbox.core.tpcdi.benchmark.TABLES", {"Fake": {"columns": []}})
    monkeypatch.setattr(benchmark, "data_generator", SimpleNamespace(generate_data=lambda tables: {"Fake": "p.csv"}))

    assert benchmark.generate_data() == ["p.csv"]
    assert benchmark.generate_data(["Fake"]) == ["p.csv"]
    with pytest.raises(ValueError, match="Unsupported output format"):
        benchmark.generate_data(output_format="parquet")
    with pytest.raises(ValueError, match="Invalid table names"):
        benchmark.generate_data(["Missing"])


def test_load_data_to_database_executes_batch_and_cursor_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    benchmark = _make_benchmark(tmp_path)
    csv_path = tmp_path / "fake.csv"
    csv_path.write_text("1|x\n|y\n", encoding="utf-8")
    benchmark.tables = {"Fake": csv_path}
    monkeypatch.setattr(
        "benchbox.core.tpcdi.benchmark.TABLES",
        {"Fake": {"columns": [{"name": "a", "type": "INT"}, {"name": "b", "type": "TEXT"}]}},
    )
    monkeypatch.setattr(benchmark, "get_create_tables_sql", lambda: "CREATE TABLE Fake(a INT,b TEXT);")

    class _ConnMany:
        def __init__(self) -> None:
            self.executed: list[str] = []
            self.batches: list[list[list[Any]]] = []
            self.committed = False

        def executescript(self, sql: str) -> None:
            self.executed.append(sql)

        def executemany(self, sql: str, batch: list[list[Any]]) -> None:
            self.executed.append(sql)
            self.batches.append(batch)

        def commit(self) -> None:
            self.committed = True

    conn_many = _ConnMany()
    benchmark.load_data_to_database(conn_many)
    assert conn_many.committed is True
    assert conn_many.batches and conn_many.batches[0][1][0] is None

    class _Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[Any] | None]] = []

        def execute(self, sql: str, params: list[Any] | None = None) -> None:
            self.calls.append((sql, params))

    class _ConnCursor:
        def __init__(self) -> None:
            self._cursor = _Cursor()
            self.committed = False

        def cursor(self) -> _Cursor:
            return self._cursor

        def commit(self) -> None:
            self.committed = True

    conn_cursor = _ConnCursor()
    benchmark.load_data_to_database(conn_cursor)
    assert any(call[0].startswith("INSERT INTO Fake") for call in conn_cursor._cursor.calls)
    assert conn_cursor.committed is True

    benchmark.tables = {}
    with pytest.raises(ValueError, match="No data generated"):
        benchmark.load_data_to_database(conn_many)


def test_run_benchmark_tracks_success_and_error_iterations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    benchmark = _make_benchmark(tmp_path)
    benchmark.query_manager = SimpleNamespace(
        get_all_queries=lambda: {"Q1": "select 1"},
        get_query_statistics=lambda: {"total": 1},
    )
    monkeypatch.setattr(benchmark, "get_query", lambda _qid: "SELECT 1")
    state = {"count": 0}

    def _execute(_qid: str, _conn: Any):
        state["count"] += 1
        if state["count"] == 2:
            raise RuntimeError("boom")
        return [("ok",)]

    monkeypatch.setattr(benchmark, "execute_query", _execute)
    out = benchmark.run_benchmark(connection=object(), queries=["Q1"], iterations=2)
    q = out["queries"]["Q1"]
    assert out["total_queries"] == 1
    assert len(q["iterations"]) == 2
    assert any(not item["success"] for item in q["iterations"])


