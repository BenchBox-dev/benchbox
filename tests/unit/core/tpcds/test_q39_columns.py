from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

FAMILIES = ["expression", "pandas"]


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


def _tables():

    return {
        "inventory": {
            "inv_item_sk": [1] * 6,
            "inv_warehouse_sk": [1] * 6,
            "inv_date_sk": [1, 2, 3, 4, 5, 6],
            "inv_quantity_on_hand": [1, 1, 100, 1, 1, 100],
        },
        "item": {"i_item_sk": [1]},
        "warehouse": {"w_warehouse_sk": [1], "w_warehouse_name": ["Main"]},
        "date_dim": {"d_date_sk": [1, 2, 3, 4, 5, 6], "d_year": [2001] * 6, "d_moy": [1, 1, 1, 2, 2, 2]},
    }


@pytest.mark.parametrize("family", FAMILIES)
def test_q39_returns_the_key_columns_of_both_months(family, monkeypatch):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"year": 2001, "months": [1, 2]})
    impl = queries.q39_expression_impl if family == "expression" else queries.q39_pandas_impl

    (row,) = materialize_rows(impl(_context(family, _tables())))

    assert len(row) == 10
    assert row[0:3] == (1, 1, 1)
    assert row[5:8] == (1, 1, 2)
    assert row[3] == pytest.approx(34.0)
    assert row[8] == pytest.approx(34.0)
    assert row[4] == pytest.approx(row[9])


