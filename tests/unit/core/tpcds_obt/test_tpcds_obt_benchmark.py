import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.tpcds_obt.benchmark import TPCDSOBTBenchmark
from benchbox.core.tpcds_obt.schema import OBT_TABLE_NAME, get_obt_columns

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


def _write_existing_output(out_dir: Path, manifest: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tpcds_sales_returns_obt.parquet").write_text("parquet data\n")
    (out_dir / "tpcds_sales_returns_obt_manifest.json").write_text(json.dumps(manifest))


def _current_manifest() -> dict[str, Any]:
    return {
        "mode": "full",
        "channels": ["store", "web", "catalog"],
        "column_count": len(get_obt_columns("full")),
        "coverage": {"enforced": True},
    }


def test_existing_obt_is_reused_when_the_manifest_matches_the_model(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    _write_existing_output(out_dir, _current_manifest())

    benchmark = TPCDSOBTBenchmark(scale_factor=1.0, output_dir=out_dir)

    assert benchmark._existing_obt("parquet") is True
    assert benchmark.tables[OBT_TABLE_NAME] == out_dir / "tpcds_sales_returns_obt.parquet"


@pytest.mark.parametrize(
    "stale",
    [
        {"column_count": 518},
        {"coverage": None},
        {"mode": "minimal"},
        {"channels": ["store"]},
    ],
    ids=["old-width", "no-coverage-record", "other-mode", "other-channels"],
)
def test_existing_obt_is_regenerated_when_the_manifest_does_not_match_the_model(
    tmp_path: Path, stale: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    out_dir = tmp_path / "out"
    manifest = {**_current_manifest(), **stale}
    manifest = {key: value for key, value in manifest.items() if value is not None}
    _write_existing_output(out_dir, manifest)

    benchmark = TPCDSOBTBenchmark(scale_factor=1.0, output_dir=out_dir)

    with caplog.at_level(logging.INFO, logger="benchbox.core.tpcds_obt.benchmark"):
        assert benchmark._existing_obt("parquet") is False
    assert OBT_TABLE_NAME not in benchmark.tables
    assert any("does not match the current OBT model" in message for message in caplog.messages)


def test_existing_obt_is_regenerated_when_the_manifest_is_unreadable(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "tpcds_sales_returns_obt.parquet").write_text("parquet data\n")
    (out_dir / "tpcds_sales_returns_obt_manifest.json").write_text("{not json")

    assert TPCDSOBTBenchmark(scale_factor=1.0, output_dir=out_dir)._existing_obt("parquet") is False


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
        ids = sorted(q.query_id for q in benchmark.get_dataframe_queries())
        assert len(ids) == 17
        assert all(query_id.startswith("obt_") for query_id in ids)
        assert "obt_row_count" in ids

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


@pytest.mark.parametrize("query_id", [36, 70, 86])
def test_postgres_rollup_queries_inline_the_alias_used_in_an_order_by_expression(query_id: int) -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0)

    postgres = benchmark.get_query(query_id, dialect="postgres")
    duckdb = benchmark.get_query(query_id, dialect="duckdb")

    order_by = postgres.rsplit(" ORDER BY ", 1)[1]
    assert re.search(r"CASE WHEN lochierarchy\b", order_by) is None
    assert re.search(r"CASE WHEN \(?GROUPING\(", order_by)
    assert order_by.startswith("lochierarchy DESC")
    assert re.search(r"CASE WHEN lochierarchy\b", duckdb.rsplit("ORDER BY", 1)[1])
    assert benchmark.get_queries(dialect="postgres")[str(query_id)] == postgres


@pytest.mark.parametrize("query_id", [2, 78])
def test_postgres_round_queries_cast_the_rounded_double_to_decimal(query_id: int) -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0)

    postgres = benchmark.get_query(query_id, dialect="postgres")

    rounds = re.findall(r"ROUND\(CAST\(.*? AS DECIMAL\), \d\)", postgres)
    assert len(rounds) == postgres.count("ROUND(") > 0
    assert benchmark.get_queries(dialect="postgres")[str(query_id)] == postgres


def test_other_dialects_do_not_get_the_postgres_only_rewrites() -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0)

    for dialect in ("duckdb", "sqlite", "datafusion"):
        assert " AS DECIMAL), 2)" not in benchmark.get_query(2, dialect=dialect)
    assert re.search(
        r'CASE WHEN "?lochierarchy"? = 0', benchmark.get_query(36, dialect="sqlite").rsplit("ORDER BY", 1)[1]
    )


@pytest.mark.parametrize("query_id", [47, 57])
def test_clickhouse_monthly_queries_alias_qualified_projections(query_id: int) -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0)

    query = benchmark.get_query(query_id, dialect="clickhouse-local")

    assert "AVG(SUM(" in query.upper()
    assert "v1.d_year AS d_year" in query
    assert "v1.sum_sales AS sum_sales" in query


def test_clickhouse_q66_qualifies_alias_shadowed_columns() -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0)

    query = benchmark.get_query(66, dialect="clickhouse-local")

    assert "SUM(jan_sales)" not in query
    assert "SUM(jan_net)" not in query
    assert "SUM(x.jan_sales)" in query
    assert "SUM(x.jan_net)" in query
    assert re.search(r"\bw_warehouse_sq_ft\b", query) is None


@pytest.mark.parametrize("dialect", ["datafusion", "clickhouse", "spark"])
def test_get_queries_name_multi_statement_queries_like_tpcds(dialect: str) -> None:
    from benchbox.utils.dialect_utils import split_sql_statements

    queries = TPCDSOBTBenchmark(scale_factor=1.0).get_queries(dialect=dialect)

    assert {"14a", "14b", "23a", "23b", "24a", "24b"} <= set(queries)
    assert not {"14", "23", "24"} & set(queries)
    assert all(len(split_sql_statements(sql, dialect)) == 1 for sql in queries.values())


def test_get_query_translates_a_statement_variant() -> None:
    sql = TPCDSOBTBenchmark(scale_factor=1.0).get_query("14b", dialect="clickhouse")

    assert sql.count(";") == 0
    assert "tpcds_sales_returns_obt" in sql


def test_clickhouse_q95_has_no_correlated_in_subquery() -> None:
    query = TPCDSOBTBenchmark(scale_factor=1.0).get_queries(dialect="clickhouse")["95"]

    assert "ret_obt.sale_id = obt.sale_id" not in query
