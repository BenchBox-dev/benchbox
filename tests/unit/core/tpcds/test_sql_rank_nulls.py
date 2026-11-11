from __future__ import annotations

from collections import Counter

import pytest

from benchbox.core.tpcds.dataframe_queries import queries
from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS
from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


def _context(backend, tables):
    if backend == "datafusion":
        pytest.importorskip("datafusion")
        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        adapter = DataFusionDataFrameAdapter(target_partitions=4)
        ctx = adapter.create_context()
        for name, table in tables.items():
            ctx.register_table(name, adapter.session_ctx.from_arrow(table))
    elif backend == "polars":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, table in tables.items():
            ctx.register_table(name, pl.from_arrow(table).lazy())
    else:
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, table in tables.items():
            ctx.register_table(name, table.to_pandas())
    return ctx


def _rows(result, backend):
    from benchbox.core.equivalence.dataframe_surface import _normalize_value, materialize_rows

    if backend != "datafusion":
        return materialize_rows(result)
    table = result.collect()
    return [
        tuple(_normalize_value(value) for value in row)
        for row in zip(*(column.to_pylist() for column in table.columns))
    ]


@pytest.mark.parametrize("backend", ["polars", "datafusion"])
@pytest.mark.parametrize("descending", [False, True])
def test_sql_rank_helper_matches_translated_window_with_nulls_ties_and_null_partitions(backend, descending):
    import duckdb
    import pyarrow as pa

    tables = {
        "facts": pa.table(
            {
                "id": list(range(1, 11)),
                "grp": ["a"] * 5 + [None] * 5,
                "value": [None, 1.0, 1.0, 3.0, None, None, None, None, None, None],
            }
        )
    }
    direction = "DESC NULLS FIRST" if descending else "ASC NULLS LAST"
    with duckdb.connect() as connection:
        connection.register("facts", tables["facts"])
        expected = connection.execute(
            f"SELECT id, RANK() OVER (PARTITION BY grp ORDER BY value {direction}) FROM facts ORDER BY id"
        ).fetchall()
    ctx = _context(backend, tables)
    ranking = queries._sql_rank_expression(ctx, "value", ["grp"], descending=descending)
    result = ctx.get_table("facts").with_columns(ranking.alias("rank")).select("id", "rank").sort("id")
    assert _rows(result, backend) == expected


def _tables():
    import pyarrow as pa

    return {
        "store_sales": pa.table(
            {
                "ss_item_sk": [1, 2, 3, 4],
                "ss_sold_date_sk": [1] * 4,
                "ss_store_sk": [1, 2, 3, 4],
                "ss_net_profit": [2.0, 2.0, None, None],
                "ss_ext_sales_price": [10.0] * 4,
                "ss_sales_price": [10.0, None, None, None],
                "ss_quantity": [1] * 4,
            }
        ),
        "web_sales": pa.table(
            {"ws_item_sk": [1, 2, 3, 4], "ws_sold_date_sk": [1] * 4, "ws_net_paid": [5.0, 5.0, None, None]}
        ),
        "date_dim": pa.table({"d_date_sk": [1], "d_year": [2000], "d_month_seq": [1212], "d_qoy": [1], "d_moy": [1]}),
        "item": pa.table(
            {
                "i_item_sk": [1, 2, 3, 4],
                "i_category": ["A", "A", "A", "B"],
                "i_class": ["one", "two", "three", "one"],
                "i_brand": ["Brand"] * 4,
                "i_product_name": ["Product"] * 4,
            }
        ),
        "store": pa.table(
            {
                "s_store_sk": [1, 2, 3, 4],
                "s_state": ["TN"] * 4,
                "s_county": ["one", "two", "three", "four"],
                "s_store_id": ["Store"] * 4,
            }
        ),
    }


@pytest.fixture(scope="module")
def tpcds_benchmark(tmp_path_factory):
    from benchbox.tpcds import TPCDS

    return TPCDS(scale_factor=0.01, output_dir=tmp_path_factory.mktemp("null-rank-sql"))


@pytest.mark.parametrize("backend", ["polars", "pandas"])
@pytest.mark.parametrize(
    "query_id,empty",
    [(query, empty) for query in [36, 67, 70, 86] for empty in [False, True]]
    + [(36, "nan"), (36, "zero"), (36, "negative_zero")],
)
def test_rollup_ranks_match_generated_sql_on_nullable_sales(backend, query_id, empty, tpcds_benchmark):
    import duckdb

    from benchbox.core.equivalence.dataframe_surface import fetch_reference_rows

    logged = {"DMS.01": "1212"}
    if query_id == 36:
        logged = {"YEAR.01": "2000", **{f"STATE_{letter}.01": "TN" for letter in "ABCDEFGH"}}
    tables = _tables()
    if empty in ("nan", "zero", "negative_zero"):
        import pyarrow as pa

        tables["item"] = tables["item"].set_column(1, "i_category", pa.array(["A"] * 4))
        tables["item"] = tables["item"].set_column(2, "i_class", pa.array(["one", "two", "three", "four"]))
        tables["store_sales"] = tables["store_sales"].set_column(
            3, "ss_net_profit", pa.array([2.0, -2.0 if empty == "negative_zero" else 2.0, 0.0, None])
        )
        tables["store_sales"] = tables["store_sales"].set_column(
            4, "ss_ext_sales_price", pa.array([10.0, 10.0 if empty == "nan" else 0.0, 0.0, 10.0])
        )
    elif empty:
        tables["store_sales"] = tables["store_sales"].slice(0, 0)
        tables["web_sales"] = tables["web_sales"].slice(0, 0)
    implementation = tpcds_benchmark._impl
    raw = implementation.query_manager.dsqgen.generate_with_parameters(query_id, logged, scale_factor=0.01)
    sql = implementation.translate_query_text(raw, "netezza", "duckdb")
    with duckdb.connect() as connection:
        for name, table in tables.items():
            connection.register(name, table)
        expected = fetch_reference_rows(connection, sql)
    assert expected
    if empty is True:
        assert len(expected) == 1
    if query_id != 67 or empty:
        assert any(row[0] is None for row in expected)
    ctx = _context(backend, tables)
    family = "pandas" if backend == "pandas" else "expression"
    with parameter_overrides({query_id: ADAPTERS[query_id](logged)}):
        actual = _rows(getattr(queries, f"q{query_id}_{family}_impl")(ctx), backend)
    if empty in ("nan", "zero", "negative_zero"):
        import math

        actual = {(row[1], row[2], row[3]): (row[0], row[4]) for row in actual}
        expected = {(row[1], row[2], row[3]): (row[0], row[4]) for row in expected}
        assert actual.keys() == expected.keys()
        for key, (value, rank) in expected.items():
            assert actual[key][1] == rank
            if isinstance(value, float) and math.isnan(value):
                assert math.isnan(actual[key][0])
            else:
                assert actual[key][0] == value
    else:
        assert Counter(actual) == Counter(expected)


@pytest.mark.parametrize("backend", ["polars", "pandas", "datafusion", "datafusion_decimal"])
@pytest.mark.parametrize("scenario", ["tie", "quantity", "empty"])
def test_q67_ranks_decimal_sales_ties_before_display_conversion(backend, scenario, tpcds_benchmark):
    from decimal import Decimal

    import duckdb
    import pyarrow as pa

    from benchbox.core.equivalence.dataframe_surface import fetch_reference_rows

    prices = [Decimal("0.10"), Decimal("0.20"), Decimal("0.30"), None, Decimal("0.90")]
    quantities = [1, 1, 1, 1, None]
    if scenario == "quantity":
        prices[2] = Decimal("0.70")
        quantities[:2] = [3, 2]
    tables = {
        "store_sales": pa.table(
            {
                "ss_item_sk": [1, 1, 2, 1, 2],
                "ss_sold_date_sk": [1] * 5,
                "ss_store_sk": [1] * 5,
                "ss_sales_price": pa.array(prices, type=pa.decimal128(7, 2)),
                "ss_quantity": pa.array(quantities, type=pa.int32()),
            }
        ),
        "date_dim": pa.table({"d_date_sk": [1], "d_year": [2000], "d_month_seq": [1176], "d_qoy": [1], "d_moy": [1]}),
        "item": pa.table(
            {
                "i_item_sk": [1, 2],
                "i_category": ["A", "A"],
                "i_class": ["C", "C"],
                "i_brand": ["B1", "B2"],
                "i_product_name": ["P1", "P2"],
            }
        ),
        "store": pa.table({"s_store_sk": [1], "s_store_id": ["S"]}),
    }
    if scenario == "empty":
        tables["store_sales"] = tables["store_sales"].slice(0, 0)
    logged = {"DMS.01": "1176"}
    implementation = tpcds_benchmark._impl
    raw = implementation.query_manager.dsqgen.generate_with_parameters(67, logged, scale_factor=0.01)
    sql = implementation.translate_query_text(raw, "netezza", "duckdb")
    with duckdb.connect() as connection:
        for name, table in tables.items():
            connection.register(name, table)
        expected = fetch_reference_rows(connection, sql)
    if scenario == "empty":
        assert len(expected) == 1
        assert expected[0][8:] == (None, 1)
    else:
        detail = [row for row in expected if row[7] is not None]
        assert len(detail) == 2
        assert {row[9] for row in detail} == {3}
    if backend != "datafusion_decimal":
        sales = tables["store_sales"]
        tables["store_sales"] = sales.set_column(3, "ss_sales_price", sales.column("ss_sales_price").cast(pa.float64()))
    execution_backend = "datafusion" if backend == "datafusion_decimal" else backend
    ctx = _context(execution_backend, tables)
    family = "pandas" if backend == "pandas" else "expression"
    with parameter_overrides({67: ADAPTERS[67](logged)}):
        actual = _rows(getattr(queries, f"q67_{family}_impl")(ctx), execution_backend)
    assert [row[:8] + row[9:] for row in actual] == [row[:8] + row[9:] for row in expected]
    from benchbox.core.equivalence.cross_surface import get_gate

    get_gate("tpcds").build_validator().validate_results_exact(expected, actual, "Q67", 0, order_aware=True)


