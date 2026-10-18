import logging
import re
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.tpcds_obt.benchmark import TPCDSOBTBenchmark
from benchbox.core.tpcds_obt.schema import OBT_TABLE_NAME

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class StubGenerator:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.called = False

    def generate(self) -> dict[str, Path]:
        self.called = True
        sample = self.base_dir / "store_sales.dat"
        sample.parent.mkdir(parents=True, exist_ok=True)
        sample.touch()
        return {"store_sales": sample}


class StubTransformer:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.calls: list[dict[str, Any]] = []

    def transform(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        fmt = kwargs.get("output_format", "parquet")
        table_path = self.base_dir / f"{OBT_TABLE_NAME}.{fmt}"
        manifest_path = self.base_dir / f"{OBT_TABLE_NAME}_manifest.json"
        table_path.parent.mkdir(parents=True, exist_ok=True)
        table_path.write_text("data\n")
        manifest_path.write_text('{"rows_total": 1}')
        return {"table": table_path, "manifest": manifest_path}


class StubCursor:
    def __init__(self) -> None:
        self.executed = None
        self.params = None

    def execute(self, query: str, params: dict[str, Any] | None = None) -> None:
        self.executed = query
        self.params = params

    def fetchall(self) -> list[tuple]:
        return [("ok",)]


def test_generate_data_invokes_generator_and_transformer(tmp_path: Path) -> None:
    benchmark = TPCDSOBTBenchmark(
        scale_factor=1.0,
        output_dir=tmp_path / "out",
        dimension_mode="minimal",
        channels=["store"],
        output_format="dat",
        force_regenerate=True,
    )
    stub_generator = StubGenerator(tmp_path)
    stub_transformer = StubTransformer(tmp_path)
    benchmark._data_generator = stub_generator
    benchmark._obt_transformer = stub_transformer

    result = benchmark.generate_data(output_format="dat")

    assert stub_generator.called is True
    assert len(stub_transformer.calls) == 1
    call = stub_transformer.calls[0]
    assert call["mode"] == "minimal"
    assert call["channels"] == ["store"]
    assert result["table"].exists()
    assert benchmark.tables["tpcds_sales_returns_obt"] == result["table"]
    assert benchmark.manifest == result["manifest"]


def test_generate_data_default_format_is_parquet(tmp_path: Path) -> None:
    benchmark = TPCDSOBTBenchmark(
        scale_factor=1.0,
        output_dir=tmp_path / "out",
        force_regenerate=True,
    )
    stub_generator = StubGenerator(tmp_path)
    stub_transformer = StubTransformer(tmp_path)
    benchmark._data_generator = stub_generator
    benchmark._obt_transformer = stub_transformer

    result = benchmark.generate_data()

    assert benchmark.output_format == "parquet"
    assert result["table"].suffix == ".parquet"
    assert result["table"].exists()


def test_existing_obt_logs_stale_dat_when_parquet_requested(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale_dat = out_dir / "tpcds_sales_returns_obt.dat"
    stale_dat.write_text("old data\n")

    benchmark = TPCDSOBTBenchmark(scale_factor=1.0, output_dir=out_dir)

    with caplog.at_level(logging.INFO, logger="benchbox.core.tpcds_obt.benchmark"):
        result = benchmark._existing_obt("parquet")

    assert result is False
    assert any("stale .dat" in m for m in caplog.messages)


def test_existing_obt_logs_when_dat_requested_but_parquet_exists(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing_parquet = out_dir / "tpcds_sales_returns_obt.parquet"
    existing_parquet.write_text("parquet data\n")

    benchmark = TPCDSOBTBenchmark(scale_factor=1.0, output_dir=out_dir)

    with caplog.at_level(logging.INFO, logger="benchbox.core.tpcds_obt.benchmark"):
        result = benchmark._existing_obt("dat")

    assert result is False
    assert any("Regenerating as dat" in m for m in caplog.messages)


class TestDataFrameMode:
    def test_supports_dataframe_mode(self) -> None:
        benchmark = TPCDSOBTBenchmark(scale_factor=1.0)
        assert benchmark.supports_dataframe_mode() is True

    def test_get_dataframe_queries_returns_all_17(self) -> None:
        benchmark = TPCDSOBTBenchmark(scale_factor=1.0)
        queries = benchmark.get_dataframe_queries()
        assert len(queries) == 17

    def test_dataframe_queries_have_both_implementations(self) -> None:
        benchmark = TPCDSOBTBenchmark(scale_factor=1.0)
        for query in benchmark.get_dataframe_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression_impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas_impl"

    def test_dataframe_query_ids(self) -> None:
        benchmark = TPCDSOBTBenchmark(scale_factor=1.0)
        ids = sorted(
            (q.query_id for q in benchmark.get_dataframe_queries()),
            key=lambda qid: int(qid[1:]),
        )
        assert ids == [f"Q{i}" for i in range(1, 18)]

    def test_normalize_does_not_confuse_obt_with_tpcds(self) -> None:
        from benchbox.core.results.builder import normalize_benchmark_id

        assert normalize_benchmark_id("tpcds_obt") == "tpcds_obt"


def test_get_data_source_benchmark_declares_tpcds() -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0)

    assert benchmark.get_data_source_benchmark() == "tpcds"


def test_get_create_tables_sql_includes_source_tables() -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0)

    ddl = benchmark.get_create_tables_sql()
    statements = [stmt.strip() for stmt in ddl.split(";") if stmt.strip()]

    assert len(statements) == 26
    lowered = ddl.lower()
    assert "create table if not exists call_center" in lowered
    assert lowered.count("create table if not exists") == 25
    assert f"create table {OBT_TABLE_NAME.lower()}" in lowered


def test_get_query_and_execute_query(tmp_path: Path) -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0, output_dir=tmp_path / "out", force_regenerate=True)

    sql = benchmark.get_query(3)
    assert OBT_TABLE_NAME in sql

    cursor = StubCursor()
    results = benchmark.execute_query(3, cursor)
    assert results == [("ok",)]
    assert cursor.executed is not None


def test_spark_dialect_translates_space_containing_aliases() -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0)

    query = benchmark.get_queries(dialect="spark")["16"]

    assert 'AS "order count"' not in query
    assert "AS `order count`" in query


@pytest.mark.parametrize("query_id", [47, 57])
def test_clickhouse_monthly_queries_materialize_nested_aggregates(query_id: int) -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0)

    query = benchmark.get_query(query_id, dialect="clickhouse-local")

    assert "AVG(SUM(" not in query.upper()
    assert "SELECT v2.* FROM v2 AS v2" in query


def test_clickhouse_q66_materializes_monthly_aliases() -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0)

    query = benchmark.get_query(66, dialect="clickhouse-local")

    assert "SUM(jan_sales)" not in query
    assert "SUM(jan_net)" not in query
    assert "WITH channel_sales AS (" in query
    assert re.search(r"\bw_warehouse_sq_ft\b", query) is None
