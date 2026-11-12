# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

duckdb = pytest.importorskip("duckdb", reason="duckdb not installed")
pl = pytest.importorskip("polars", reason="polars not installed")
pd = pytest.importorskip("pandas", reason="pandas not installed")

from benchbox.core.datavault.benchmark import DataVaultBenchmark
from benchbox.core.datavault.dataframe_queries import queries as dvq
from benchbox.core.datavault.schema import get_create_all_tables_sql
from benchbox.core.equivalence.dataframe_surface import materialize_rows
from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter
from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_EMPTY_TABLES = ("link_lineitem", "sat_lineitem", "sat_part")

_QUERY_IMPLS = {
    6: (dvq.q6_expression_impl, dvq.q6_pandas_impl),
    14: (dvq.q14_expression_impl, dvq.q14_pandas_impl),
    19: (dvq.q19_expression_impl, dvq.q19_pandas_impl),
}


def _empty_vault_connection():
    conn = duckdb.connect(":memory:")
    for statement in [part.strip() for part in get_create_all_tables_sql().split(";") if part.strip()]:
        conn.execute(statement)
    return conn


def _empty_contexts(conn):
    polars_ctx = PolarsDataFrameAdapter().create_context()
    pandas_ctx = PandasDataFrameAdapter().create_context()
    for table in _EMPTY_TABLES:
        arrow = conn.execute(f"SELECT * FROM {table} LIMIT 0").to_arrow_table()
        polars_ctx.register_table(table, pl.from_arrow(arrow))
        pandas_ctx.register_table(table, arrow.to_pandas())
    return polars_ctx, pandas_ctx


def _seeded_connection(seed_sql: str):
    conn = _empty_vault_connection()
    for statement in [part.strip() for part in seed_sql.split(";") if part.strip()]:
        conn.execute(statement)
    return conn


def _seeded_contexts(conn):
    polars_ctx = PolarsDataFrameAdapter().create_context()
    pandas_ctx = PandasDataFrameAdapter().create_context()
    for table in _EMPTY_TABLES:
        arrow = conn.execute(f"SELECT * FROM {table}").to_arrow_table()
        polars_ctx.register_table(table, pl.from_arrow(arrow))
        pandas_ctx.register_table(table, arrow.to_pandas())
    return polars_ctx, pandas_ctx


_HUB_SEED = "; ".join(
    [
        "INSERT INTO hub_lineitem VALUES ('LI1', 1, 1, now(), 'test')",
        "INSERT INTO hub_order VALUES ('O1', 1, now(), 'test')",
        "INSERT INTO hub_part VALUES ('P1', 1, now(), 'test')",
        "INSERT INTO hub_part VALUES ('P2', 2, now(), 'test')",
        "INSERT INTO hub_supplier VALUES ('S1', 1, now(), 'test')",
    ]
)


def _link_row(link: str, part: str) -> str:
    return f"INSERT INTO link_lineitem VALUES ('{link}', 'LI1', 'O1', '{part}', 'S1', now(), 'test')"


def _sat_part_row(part: str, p_type: str) -> str:
    return (
        "INSERT INTO sat_part VALUES "
        f"('{part}', now(), NULL, 'test', 'h', 'Part#1', 'Mfgr#1', 'Brand#12', "
        f"'{p_type}', 3, 'SM CASE', 100.0, 'comment')"
    )


def _sat_lineitem_row(link: str, shipdate: str) -> str:
    return (
        "INSERT INTO sat_lineitem VALUES "
        f"('{link}', now(), NULL, 'test', 'h', 10, 100.0, 0.06, 0.0, "
        f"'N', 'O', DATE '{shipdate}', DATE '{shipdate}', DATE '{shipdate}', "
        "'DELIVER IN PERSON', 'AIR', 'comment')"
    )


def _sat_lineitem_zero_revenue_row(link: str, shipdate: str) -> str:
    return (
        "INSERT INTO sat_lineitem VALUES "
        f"('{link}', now(), NULL, 'test', 'h', 10, 0.0, 0.06, 0.0, "
        f"'N', 'O', DATE '{shipdate}', DATE '{shipdate}', DATE '{shipdate}', "
        "'DELIVER IN PERSON', 'AIR', 'comment')"
    )


def _assert_single_value(rows, expected):
    assert len(rows) == 1, f"expected one row, got {rows}"
    assert len(rows[0]) == 1, f"expected one column, got {rows}"
    assert rows[0][0] == pytest.approx(expected)


class TestEmptyAggregateNull:
    @pytest.mark.parametrize("query_id", [6, 14, 19])
    def test_sql_reference_returns_single_null_row(self, query_id):
        bench = DataVaultBenchmark(scale_factor=0.01)
        conn = _empty_vault_connection()
        try:
            assert conn.execute(bench.get_query(query_id)).fetchall() == [(None,)]
        finally:
            conn.close()

    @pytest.mark.parametrize("query_id", [6, 14, 19])
    def test_expression_backend_returns_single_null_row(self, query_id):
        conn = _empty_vault_connection()
        try:
            (expression_impl, _), (polars_ctx, _) = _QUERY_IMPLS[query_id], _empty_contexts(conn)
            assert materialize_rows(expression_impl(polars_ctx)) == [(None,)]
        finally:
            conn.close()

    @pytest.mark.parametrize("query_id", [6, 14, 19])
    def test_pandas_backend_returns_single_null_row(self, query_id):
        conn = _empty_vault_connection()
        try:
            (_, pandas_impl), (_, pandas_ctx) = _QUERY_IMPLS[query_id], _empty_contexts(conn)
            assert materialize_rows(pandas_impl(pandas_ctx)) == [(None,)]
        finally:
            conn.close()


class TestNonEmptyRegression:
    def test_q6_revenue(self):
        seed = "; ".join(
            [
                _HUB_SEED,
                _link_row("L1", "P1"),
                _sat_part_row("P1", "STANDARD POLISHED STEEL"),
                _sat_lineitem_row("L1", "1994-06-01"),
                _sat_lineitem_row("L1", "1994-07-01"),
            ]
        )
        conn = _seeded_connection(seed)
        try:
            bench = DataVaultBenchmark(scale_factor=0.01)
            expected = conn.execute(bench.get_query(6)).fetchall()
            polars_ctx, pandas_ctx = _seeded_contexts(conn)
            _assert_single_value(materialize_rows(dvq.q6_expression_impl(polars_ctx)), expected[0][0])
            _assert_single_value(materialize_rows(dvq.q6_pandas_impl(pandas_ctx)), expected[0][0])
            _assert_single_value(expected, 12.0)
        finally:
            conn.close()

    def test_q14_promo_percentage(self):
        seed = "; ".join(
            [
                _HUB_SEED,
                _link_row("L1", "P1"),
                _link_row("L2", "P2"),
                _sat_part_row("P1", "PROMO BRUSHED COPPER"),
                _sat_part_row("P2", "STANDARD POLISHED STEEL"),
                _sat_lineitem_row("L1", "1995-09-10"),
                _sat_lineitem_row("L2", "1995-09-11"),
            ]
        )
        conn = _seeded_connection(seed)
        try:
            bench = DataVaultBenchmark(scale_factor=0.01)
            expected = conn.execute(bench.get_query(14)).fetchall()
            polars_ctx, pandas_ctx = _seeded_contexts(conn)
            _assert_single_value(materialize_rows(dvq.q14_expression_impl(polars_ctx)), expected[0][0])
            _assert_single_value(materialize_rows(dvq.q14_pandas_impl(pandas_ctx)), expected[0][0])
            _assert_single_value(expected, 50.0)
        finally:
            conn.close()

    def test_q14_zero_revenue_yields_nan_not_error(self):
        import math

        seed = "; ".join(
            [
                _HUB_SEED,
                _link_row("L1", "P1"),
                _sat_part_row("P1", "PROMO BRUSHED COPPER"),
                _sat_lineitem_zero_revenue_row("L1", "1995-09-10"),
            ]
        )
        conn = _seeded_connection(seed)
        try:
            bench = DataVaultBenchmark(scale_factor=0.01)
            expected = conn.execute(bench.get_query(14)).fetchall()
            assert len(expected) == 1
            assert expected[0][0] is not None and math.isnan(expected[0][0])
            polars_ctx, pandas_ctx = _seeded_contexts(conn)
            for rows in (
                materialize_rows(dvq.q14_expression_impl(polars_ctx)),
                materialize_rows(dvq.q14_pandas_impl(pandas_ctx)),
            ):
                assert len(rows) == 1
                assert rows[0][0] is not None and math.isnan(rows[0][0]), f"expected NaN, got {rows}"
        finally:
            conn.close()

    def test_q19_revenue(self):
        seed = "; ".join(
            [
                _HUB_SEED,
                _link_row("L1", "P1"),
                _sat_part_row("P1", "STANDARD POLISHED STEEL"),
                _sat_lineitem_row("L1", "1994-06-01"),
            ]
        )
        conn = _seeded_connection(seed)
        try:
            bench = DataVaultBenchmark(scale_factor=0.01)
            expected = conn.execute(bench.get_query(19)).fetchall()
            polars_ctx, pandas_ctx = _seeded_contexts(conn)
            _assert_single_value(materialize_rows(dvq.q19_expression_impl(polars_ctx)), expected[0][0])
            _assert_single_value(materialize_rows(dvq.q19_pandas_impl(pandas_ctx)), expected[0][0])
            _assert_single_value(expected, 94.0)
        finally:
            conn.close()
