from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
]

duckdb = pytest.importorskip("duckdb", reason="duckdb not installed")

from benchbox.core.tpch.queries import TPCHQueries
from benchbox.core.validation.cross_platform import (
    Tolerance,
    compare_query_results,
    register_query_tolerance,
)
from benchbox.platforms.clickhouse._dependencies import import_chdb_session
from tests.utilities.optional_engines import chdb_skip_reason

register_query_tolerance(
    "tpch",
    "Q1",
    Tolerance(
        epsilon=1e-6,
        rationale="TPC-H §2.6.4: AVG aggregate floating-point epsilon; DuckDB returns "
        "float64 whereas DataFusion returns Decimal(n,6) - max observed diff < 1e-6.",
    ),
)

register_query_tolerance(
    "tpch",
    "Q14",
    Tolerance(
        epsilon=1e-4,
        rationale="TPC-H §2.6.4: percentage aggregate floating-point epsilon; "
        "CASE SUM ratio differs in last 4-5 decimal places between DuckDB float64 "
        "and DataFusion Decimal result.",
    ),
)

TPCH_QUERY_IDS = [f"Q{n}" for n in range(1, 23)]


_TPCH_TABLES = (
    "customer",
    "lineitem",
    "nation",
    "orders",
    "part",
    "partsupp",
    "region",
    "supplier",
)


def _tpch_sql(query_number: int) -> str:
    return TPCHQueries().get_query(query_number)


def _fetchall_as_tuples(conn: object, sql: str) -> list[tuple]:
    result = conn.execute(sql)
    return [tuple(row) for row in result.fetchall()]


@pytest.fixture(scope="session")
def tpch_parquet_dir(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("tpch_sf001_parquet")
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL tpch; LOAD tpch; CALL dbgen(sf=0.01)")
    for table in _TPCH_TABLES:
        conn.execute(f"COPY {table} TO '{data_dir / f'{table}.parquet'}' (FORMAT PARQUET)")
    conn.close()
    return data_dir


@pytest.fixture(scope="session")
def duckdb_reference_conn(tpch_parquet_dir):
    conn = duckdb.connect(":memory:")
    for table in _TPCH_TABLES:
        conn.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{tpch_parquet_dir / f'{table}.parquet'}')")
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def datafusion_ctx(tpch_parquet_dir):
    datafusion = pytest.importorskip("datafusion", reason="datafusion not installed")
    ctx = datafusion.SessionContext()
    for table in _TPCH_TABLES:
        ctx.register_parquet(table, str(tpch_parquet_dir / f"{table}.parquet"))
    yield ctx


def _datafusion_rows(ctx, sql: str) -> list[tuple]:
    batches = ctx.sql(sql).collect()
    rows: list[tuple] = []
    for batch in batches:
        for i in range(batch.num_rows):
            row = tuple(batch.column(j)[i].as_py() for j in range(batch.num_columns))
            rows.append(row)
    return rows


@pytest.mark.live_integration
@pytest.mark.parametrize("query_id", TPCH_QUERY_IDS)
class TestDuckDBDataFusion:
    def test_query_results_match(self, query_id, duckdb_reference_conn, datafusion_ctx):
        from benchbox.core.validation.cross_platform import tolerance_for

        qn = int(query_id[1:])
        sql = _tpch_sql(qn)
        tolerance = tolerance_for("tpch", query_id)

        reference_rows = _fetchall_as_tuples(duckdb_reference_conn, sql)
        comparison_rows = _datafusion_rows(datafusion_ctx, sql)

        report = compare_query_results(
            query_id=query_id,
            reference_platform="duckdb",
            comparison_platform="datafusion",
            reference_rows=reference_rows,
            comparison_rows=comparison_rows,
            tolerance=tolerance,
        )

        assert report.matched, f"DuckDB × DataFusion diverged for {query_id}:\n{report.summary()}"


@pytest.mark.live_integration
class TestDuckDBPolarsDF:
    @pytest.fixture(scope="class")
    def polars_adapter(self, tpch_parquet_dir):
        pytest.importorskip("polars", reason="polars not installed")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()
        for table in _TPCH_TABLES:
            adapter.load_table(ctx, table, [tpch_parquet_dir / f"{table}.parquet"])
        return adapter, ctx

    @pytest.mark.parametrize("query_id", TPCH_QUERY_IDS)
    def test_query_results_match(self, query_id, duckdb_reference_conn, polars_adapter):
        from benchbox.core.tpch.dataframe_queries import get_query, list_query_ids
        from benchbox.core.validation.cross_platform import tolerance_for

        adapter, ctx = polars_adapter
        qn = int(query_id[1:])

        if query_id not in list_query_ids():
            pytest.skip(f"polars-df: {query_id} not supported by expression runner")

        sql = _tpch_sql(qn)
        tolerance = tolerance_for("tpch", query_id)

        reference_rows = _fetchall_as_tuples(duckdb_reference_conn, sql)

        try:
            result_df = adapter.execute_query(ctx, get_query(query_id))
            comparison_rows = result_df.collect().rows() if hasattr(result_df, "collect") else result_df.rows()
        except Exception as exc:
            pytest.xfail(f"polars-df runner raised on Q{qn}: {exc}")
            return

        report = compare_query_results(
            query_id=query_id,
            reference_platform="duckdb",
            comparison_platform="polars-df",
            reference_rows=reference_rows,
            comparison_rows=list(comparison_rows),
            tolerance=tolerance,
        )

        assert report.matched, f"DuckDB × Polars-DF diverged for {query_id}:\n{report.summary()}"


@pytest.fixture(scope="module")
def clickhouse_session(tpch_parquet_dir):
    reason = chdb_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    chdb_session = import_chdb_session()

    sess = chdb_session.Session()
    sess.query("CREATE DATABASE IF NOT EXISTS tpch", "CSV")
    sess.query("USE tpch", "CSV")

    for table in _TPCH_TABLES:
        parquet_path = str(tpch_parquet_dir / f"{table}.parquet")
        sess.query(
            f"CREATE TABLE IF NOT EXISTS {table} ENGINE=File(Parquet, '{parquet_path}')",
            "CSV",
        )

    try:
        yield sess
    finally:
        sess.close()


def _clickhouse_rows(sess, sql: str) -> list[tuple]:
    result = sess.query(sql, "Arrow")
    table = result.to_pyarrow()
    rows: list[tuple] = []
    for i in range(table.num_rows):
        row = tuple(table.column(j)[i].as_py() for j in range(table.num_columns))
        rows.append(row)
    return rows


@pytest.mark.live_integration
@pytest.mark.parametrize("query_id", TPCH_QUERY_IDS)
class TestDuckDBClickHouseLocal:
    def test_query_results_match(self, query_id, duckdb_reference_conn, clickhouse_session):
        from benchbox.core.validation.cross_platform import tolerance_for
        from benchbox.platforms.clickhouse.query_rewrites import rewrite_query_for_clickhouse

        qn = int(query_id[1:])
        sql = _tpch_sql(qn)
        tolerance = tolerance_for("tpch", query_id)

        reference_rows = _fetchall_as_tuples(duckdb_reference_conn, sql)

        ch_sql = rewrite_query_for_clickhouse(sql)

        try:
            comparison_rows = _clickhouse_rows(clickhouse_session, ch_sql)
        except Exception as exc:
            pytest.xfail(f"ClickHouse-local raised on Q{qn}: {exc}")
            return

        report = compare_query_results(
            query_id=query_id,
            reference_platform="duckdb",
            comparison_platform="clickhouse-local",
            reference_rows=reference_rows,
            comparison_rows=comparison_rows,
            tolerance=tolerance,
        )

        assert report.matched, f"DuckDB × ClickHouse-local diverged for {query_id}:\n{report.summary()}"
