from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

FAMILIES = ["expression", "pandas"]


def _duckdb_rows(tables, sql):
    duckdb = pytest.importorskip("duckdb")
    import pandas as pd

    connection = duckdb.connect()
    for name, data in tables.items():
        connection.register(name, pd.DataFrame(data))
    return [tuple(row) for row in connection.execute(sql).fetchall()]


def _context(family, tables):
    if family == "expression":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pl.DataFrame(data).lazy())
    else:
        import pandas as pd

        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pd.DataFrame(data))
    return ctx


def _normalize(rows):
    return [tuple(None if value != value else value for value in row) for row in rows]


@pytest.mark.parametrize("family", FAMILIES)
def test_shared_sort_puts_a_null_descending_key_first_and_a_null_ascending_key_last(family):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    data = {
        "total": [5.0, None, 9.0, 5.0, None],
        "name": ["b", "x", "c", None, None],
        "tag": [1, 2, 3, 4, 5],
    }
    tables = {"t": data}
    expected = _duckdb_rows(tables, "SELECT total, name, tag FROM t ORDER BY total DESC NULLS FIRST, name")
    ctx = _context(family, tables)
    frame = ctx.get_table("t")
    if family == "expression":
        result = queries._sort_null_largest_expression(ctx, frame, ["total", "name"], [True, False])
    else:
        result = queries._sort_null_largest_pandas(frame, ["total", "name"], [True, False])

    assert _normalize(materialize_rows(result)) == _normalize(expected)
    assert expected[0][0] is None


_DATE_ITEM_QUERIES = {
    3: (
        ["d_year", "i_brand_id", "i_brand"],
        "sum_agg",
        "i_manufact_id = 436 AND d_moy = 12",
        "d_year, sum_agg DESC NULLS FIRST, i_brand_id",
    ),
    42: (
        ["d_year", "i_category_id", "i_category"],
        "sum_sales",
        "i_manager_id = 1 AND d_moy = 12 AND d_year = 1998",
        "sum_sales DESC NULLS FIRST, d_year, i_category_id, i_category",
    ),
    52: (
        ["d_year", "i_brand_id", "i_brand"],
        "ext_price",
        "i_manager_id = 1 AND d_moy = 12 AND d_year = 1998",
        "d_year, ext_price DESC NULLS FIRST, i_brand_id",
    ),
    55: (
        ["i_brand_id", "i_brand"],
        "ext_price",
        "i_manager_id = 1 AND d_moy = 12 AND d_year = 1998",
        "ext_price DESC NULLS FIRST, i_brand_id",
    ),
}
_PARAMS = {"manufact_id": 436, "month": 12, "year": 1998, "manager_id": 1}


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("query_id", sorted(_DATE_ITEM_QUERIES))
def test_date_item_queries_match_sql_for_null_keys_null_sums_and_ties(query_id, family, monkeypatch):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    group_cols, alias, where, order_by = _DATE_ITEM_QUERIES[query_id]
    keys = [None, 2, 3, 4]
    tables = {
        "date_dim": {"d_date_sk": [1], "d_year": [1998], "d_moy": [12]},
        "store_sales": {
            "ss_sold_date_sk": [1, 1, 1, 1],
            "ss_item_sk": [1, 2, 3, 4],
            "ss_ext_sales_price": [5.0, 5.0, 9.0, None],
        },
        "item": {
            "i_item_sk": [1, 2, 3, 4],
            "i_brand_id": keys,
            "i_brand": ["brand_0", "brand_1", "brand_2", "brand_3"],
            "i_category_id": keys,
            "i_category": ["category_0", "category_1", "category_2", "category_3"],
            "i_manufact_id": [436] * 4,
            "i_manager_id": [1] * 4,
        },
    }
    expected = _duckdb_rows(
        tables,
        f"SELECT {', '.join(group_cols)}, SUM(ss_ext_sales_price) AS {alias} "
        "FROM date_dim JOIN store_sales ON d_date_sk = ss_sold_date_sk JOIN item ON ss_item_sk = i_item_sk "
        f"WHERE {where} GROUP BY {', '.join(group_cols)} ORDER BY {order_by} LIMIT 100",
    )

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: _PARAMS)
    impl = getattr(queries, f"q{query_id}_{family}_impl")
    rows = materialize_rows(impl(_context(family, tables)))

    assert rows == expected
    assert expected[0][-1] is None
    assert any(None in row[:-1] for row in expected)


