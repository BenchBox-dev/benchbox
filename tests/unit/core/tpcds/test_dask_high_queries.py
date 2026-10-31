from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

FAMILIES = ["pandas", "dask"]


def _context(family, tables):
    import pandas as pd

    if family == "dask":
        dd = pytest.importorskip("dask.dataframe")
        from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

        ctx = DaskDataFrameAdapter(execution_engine="threaded").create_context()
        for name, data in tables.items():
            ctx.register_table(name, dd.from_pandas(pd.DataFrame(data), npartitions=2))
    else:
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pd.DataFrame(data))
    return ctx


def _rows(result):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    if hasattr(result, "compute"):
        result = result.compute()
    return materialize_rows(result)


def _impl(query_id):
    from benchbox.core.tpcds.dataframe_queries.registry import get_tpcds_query

    return get_tpcds_query(query_id).get_impl_for_family("pandas")


DATE_DIM = {
    "d_date_sk": [1, 2, 3],
    "d_date": ["2000-01-01", "2000-01-02", "2000-01-03"],
    "d_month_seq": [1212, 1212, 1213],
    "d_week_seq": [1, 1, 1],
    "d_year": [2000, 2000, 2000],
    "d_moy": [1, 1, 1],
}


@pytest.mark.parametrize("family", FAMILIES)
def test_grouped_sum_or_null_keeps_all_null_groups_null(family):
    from benchbox.core.tpcds.dataframe_queries import queries

    ctx = _context(family, {"t": {"k": ["a", "a", "b", "c"], "v": [1.0, None, None, 4.0]}})
    result = queries._grouped_sum_or_null_pandas(ctx.get_table("t"), ["k"], {"total": "v"})

    rows = sorted(_rows(result), key=lambda row: row[0])

    assert [row[0] for row in rows] == ["a", "b", "c"]
    assert rows[0][1] == 1.0
    assert rows[1][1] is None or rows[1][1] != rows[1][1]
    assert rows[2][1] == 4.0


@pytest.mark.parametrize("family", FAMILIES)
def test_q97_counts_store_and_catalog_pairs(family):
    tables = {
        "date_dim": DATE_DIM,
        "store_sales": {
            "ss_sold_date_sk": [1, 1, 2, 3],
            "ss_customer_sk": [1, 1, 2, 3],
            "ss_item_sk": [10, 10, 20, 30],
        },
        "catalog_sales": {
            "cs_sold_date_sk": [1, 2, 3],
            "cs_bill_customer_sk": [1, 4, 3],
            "cs_item_sk": [10, 40, 99],
        },
    }

    assert _rows(_impl("Q97")(_context(family, tables))) == [(2, 2, 1)]


@pytest.mark.parametrize("family", FAMILIES)
def test_q51_running_totals_match(family):
    tables = {
        "date_dim": DATE_DIM,
        "web_sales": {
            "ws_sold_date_sk": [1, 2, 3, 1],
            "ws_item_sk": [1, 1, 1, 2],
            "ws_sales_price": [5.0, None, 7.0, 1.0],
        },
        "store_sales": {
            "ss_sold_date_sk": [1, 2, 3, 1],
            "ss_item_sk": [1, 1, 1, 2],
            "ss_sales_price": [1.0, 1.0, 1.0, 9.0],
        },
    }

    expected = _rows(_impl("Q51")(_context("pandas", tables)))

    assert expected
    assert _rows(_impl("Q51")(_context(family, tables))) == expected


@pytest.mark.parametrize("family", FAMILIES)
def test_q61_promotion_share(family, monkeypatch):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(
        queries,
        "get_parameters",
        lambda query_id: {"year": 1998, "month": 11, "gmt_offset": -5.0, "category": "Jewelry"},
    )
    tables = {
        "date_dim": {"d_date_sk": [1], "d_year": [1998], "d_moy": [11]},
        "store": {"s_store_sk": [1], "s_gmt_offset": [-5.0]},
        "customer_address": {"ca_address_sk": [1], "ca_gmt_offset": [-5.0]},
        "customer": {"c_customer_sk": [1], "c_current_addr_sk": [1]},
        "item": {"i_item_sk": [1, 2], "i_category": ["Jewelry", "Jewelry"]},
        "promotion": {
            "p_promo_sk": [1, 2],
            "p_channel_dmail": ["Y", "N"],
            "p_channel_email": ["N", "N"],
            "p_channel_tv": ["N", "N"],
        },
        "store_sales": {
            "ss_sold_date_sk": [1, 1],
            "ss_store_sk": [1, 1],
            "ss_customer_sk": [1, 1],
            "ss_item_sk": [1, 2],
            "ss_promo_sk": [1, 2],
            "ss_ext_sales_price": [25.0, 75.0],
        },
    }

    assert _rows(_impl("Q61")(_context(family, tables))) == [(25.0, 100.0, 25.0)]


@pytest.mark.parametrize("family", FAMILIES)
def test_groupby_size_without_index_returns_a_frame(family):
    ctx = _context(family, {"t": {"k": ["a", "a", "b"], "v": [1, 2, 3]}})

    result = ctx.get_table("t").groupby(["k"], as_index=False).size()
    if hasattr(result, "compute"):
        result = result.compute()

    assert sorted(result.rename(columns={"size": "n"}).itertuples(index=False)) == [("a", 2), ("b", 1)]
