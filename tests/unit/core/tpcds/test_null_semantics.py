from __future__ import annotations

from datetime import date

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


def _rows(result):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    return materialize_rows(result)


@pytest.mark.parametrize("family", FAMILIES)
def test_joined_aggregate_keeps_a_null_group_and_sorts_it_last(family):
    from benchbox.core.tpcds.dataframe_queries import queries

    spec = {
        "query_id": 15,
        "base": "t",
        "joins": [],
        "group_by": ["zip"],
        "aggs": [["total", "v", "sum"]],
        "sort_by": ["zip"],
    }
    ctx = _context(family, {"t": {"zip": ["b", None, "a", "b"], "v": [1.0, 5.0, 2.0, 3.0]}})
    engine = queries._joined_agg_expression_impl if family == "expression" else queries._joined_agg_pandas_impl

    assert _rows(engine(ctx, spec)) == [("a", 2.0), ("b", 4.0), (None, 5.0)]


@pytest.mark.parametrize("family", FAMILIES)
def test_joined_aggregate_orders_null_as_the_largest_value(family):
    from benchbox.core.tpcds.dataframe_queries import queries

    spec = {
        "query_id": 15,
        "base": "t",
        "joins": [],
        "group_by": ["g", "k"],
        "aggs": [["total", "v", "sum"]],
        "sort_by": ["g", "k"],
        "descending": [False, True],
    }
    data = {"g": ["x", "x", "x", "y", "y"], "k": ["b", None, "a", None, "c"], "v": [1.0, 2.0, 3.0, 4.0, 5.0]}
    engine = queries._joined_agg_expression_impl if family == "expression" else queries._joined_agg_pandas_impl

    assert _rows(engine(_context(family, {"t": data}), spec)) == [
        ("x", None, 2.0),
        ("x", "b", 1.0),
        ("x", "a", 3.0),
        ("y", None, 4.0),
        ("y", "c", 5.0),
    ]


@pytest.mark.parametrize("family", FAMILIES)
def test_q34_sorts_null_names_last_and_reports_them_as_none(family, monkeypatch):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"year": 1998})
    tickets = 15
    tables = {
        "store_sales": {
            "ss_sold_date_sk": [1] * (2 * tickets),
            "ss_store_sk": [1] * (2 * tickets),
            "ss_hdemo_sk": [1] * (2 * tickets),
            "ss_ticket_number": [100] * tickets + [200] * tickets,
            "ss_customer_sk": [1] * tickets + [2] * tickets,
        },
        "date_dim": {"d_date_sk": [1], "d_dom": [2], "d_year": [1998]},
        "store": {"s_store_sk": [1], "s_county": ["Williamson County"]},
        "household_demographics": {
            "hd_demo_sk": [1],
            "hd_buy_potential": [">10000"],
            "hd_vehicle_count": [1],
            "hd_dep_count": [2],
        },
        "customer": {
            "c_customer_sk": [1, 2],
            "c_last_name": ["Baker", None],
            "c_first_name": ["Andrew", None],
            "c_salutation": ["Dr.", None],
            "c_preferred_cust_flag": ["N", "Y"],
        },
    }
    ctx = _context(family, tables)
    impl = queries.q34_expression_impl if family == "expression" else queries.q34_pandas_impl

    assert _rows(impl(ctx)) == [
        ("Baker", "Andrew", "Dr.", "N", 100, tickets),
        (None, None, None, "Y", 200, tickets),
    ]


def _three_channel_tables(store, catalog, web):
    return {
        "store_sales": {"ss_sold_date_sk": [1] * len(store), "ss_customer_sk": store},
        "catalog_sales": {"cs_sold_date_sk": [1] * len(catalog), "cs_bill_customer_sk": catalog},
        "web_sales": {"ws_sold_date_sk": [1] * len(web), "ws_bill_customer_sk": web},
        "date_dim": {"d_date_sk": [1], "d_date": [date(2000, 3, 1)], "d_month_seq": [1200]},
        "customer": {"c_customer_sk": [1, 2], "c_last_name": [None, "Lee"], "c_first_name": [None, "Ann"]},
    }


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "query_id, impl_name, store, catalog, web, expected",
    [
        (38, "q38", [1, 2], [1], [1, 2], 1),
        (87, "q87", [1, 2], [1], [1], 1),
        (87, "q87", [1, 2], [99], [99], 2),
    ],
)
def test_set_operations_treat_null_names_as_equal(
    family, query_id, impl_name, store, catalog, web, expected, monkeypatch
):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"dms": 1200})
    ctx = _context(family, _three_channel_tables(store, catalog, web))
    impl = getattr(queries, f"{impl_name}_{family}_impl")

    assert _rows(impl(ctx)) == [(expected,)]


@pytest.mark.parametrize("family", FAMILIES)
def test_q34_orders_a_null_preferred_flag_first_because_the_key_is_descending(family, monkeypatch):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"year": 1998})
    tickets = 15
    flags = [None, "Y", "N"]
    count = len(flags)
    tables = {
        "store_sales": {
            "ss_sold_date_sk": [1] * (count * tickets),
            "ss_store_sk": [1] * (count * tickets),
            "ss_hdemo_sk": [1] * (count * tickets),
            "ss_ticket_number": [100 * (i + 1) for i in range(count) for _ in range(tickets)],
            "ss_customer_sk": [i + 1 for i in range(count) for _ in range(tickets)],
        },
        "date_dim": {"d_date_sk": [1], "d_dom": [2], "d_year": [1998]},
        "store": {"s_store_sk": [1], "s_county": ["Williamson County"]},
        "household_demographics": {
            "hd_demo_sk": [1],
            "hd_buy_potential": [">10000"],
            "hd_vehicle_count": [1],
            "hd_dep_count": [2],
        },
        "customer": {
            "c_customer_sk": [1, 2, 3],
            "c_last_name": ["Baker"] * count,
            "c_first_name": ["Andrew"] * count,
            "c_salutation": ["Dr."] * count,
            "c_preferred_cust_flag": flags,
        },
    }
    impl = queries.q34_expression_impl if family == "expression" else queries.q34_pandas_impl

    assert [row[3] for row in _rows(impl(_context(family, tables)))] == [None, "Y", "N"]


def _dask_context(tables, npartitions=2):
    dd = pytest.importorskip("dask.dataframe")
    import pandas as pd

    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    ctx = DaskDataFrameAdapter(execution_engine="threaded").create_context()
    for name, data in tables.items():
        ctx.register_table(name, dd.from_pandas(pd.DataFrame(data), npartitions=npartitions))
    return ctx


@pytest.mark.parametrize("npartitions", [1, 3])
def test_mixed_direction_sort_places_nulls_per_key_on_dask(npartitions):
    from benchbox.core.tpcds.dataframe_queries import queries

    spec = {
        "query_id": 15,
        "base": "t",
        "joins": [],
        "group_by": ["g", "k"],
        "aggs": [["total", "v", "sum"]],
        "sort_by": ["g", "k"],
        "descending": [False, True],
    }
    data = {"g": ["x", "x", "x", "y", "y"], "k": ["b", None, "a", None, "c"], "v": [1.0, 2.0, 3.0, 4.0, 5.0]}

    assert _rows(queries._joined_agg_pandas_impl(_dask_context({"t": data}, npartitions), spec)) == [
        ("x", None, 2.0),
        ("x", "b", 1.0),
        ("x", "a", 3.0),
        ("y", None, 4.0),
        ("y", "c", 5.0),
    ]


@pytest.mark.parametrize("npartitions", [1, 4, 7])
def test_mixed_direction_sort_then_limit_matches_pandas_on_dask(npartitions):
    import numpy as np
    import pandas as pd

    from benchbox.core.tpcds.dataframe_queries import queries

    dd = pytest.importorskip("dask.dataframe")
    rng = np.random.default_rng(7)
    size = 300
    frame = pd.DataFrame(
        {
            "a": rng.choice(["x", "y", "z", None], size),
            "b": rng.choice([1.0, 2.0, 3.0, np.nan], size),
            "c": rng.integers(0, 5, size),
            "row": np.arange(size),
        }
    )
    keys, descending = ["a", "b", "c", "row"], [False, True, False, False]

    expected = queries._sort_null_largest_pandas(frame, keys, descending).head(40)
    ordered = queries._sort_null_largest_pandas(dd.from_pandas(frame, npartitions=npartitions), keys, descending)
    actual = ordered.head(40, npartitions=-1)

    assert list(actual.columns) == list(frame.columns)
    assert actual["row"].tolist() == expected["row"].tolist()


def _q71_tables():
    return {
        "date_dim": {"d_date_sk": [1], "d_year": [2000], "d_moy": [12]},
        "item": {
            "i_item_sk": [1, 2, 3],
            "i_manager_id": [1, 1, 1],
            "i_brand_id": [1, 2, 3],
            "i_brand": ["a", "b", "c"],
        },
        "time_dim": {"t_time_sk": [1], "t_meal_time": ["dinner"], "t_hour": [18], "t_minute": [26]},
        "store_sales": {
            "ss_sold_date_sk": [1, 1, 1],
            "ss_item_sk": [1, 2, 3],
            "ss_sold_time_sk": [1, 1, 1],
            "ss_ext_sales_price": [None, 5.0, 20.0],
        },
        "web_sales": {
            "ws_sold_date_sk": [1, 1],
            "ws_item_sk": [1, 2],
            "ws_sold_time_sk": [1, 1],
            "ws_ext_sales_price": [None, 2.0],
        },
        "catalog_sales": {
            "cs_sold_date_sk": [1, 1],
            "cs_item_sk": [1, 2],
            "cs_sold_time_sk": [1, 1],
            "cs_ext_sales_price": [None, 1.0],
        },
    }


def _q76_tables():
    def channel(prefix, prices, customer_key):
        return {
            f"{prefix}_sold_date_sk": [1, 1],
            f"{prefix}_item_sk": [1, 2],
            customer_key: [None, None],
            f"{prefix}_ext_sales_price": prices,
        }

    return {
        "date_dim": {"d_date_sk": [1], "d_year": [2001], "d_qoy": [3]},
        "item": {"i_item_sk": [1, 2], "i_category": [None, "Books"]},
        "store_sales": channel("ss", [None, 2.0], "ss_customer_sk"),
        "web_sales": channel("ws", [3.0, 4.0], "ws_bill_customer_sk"),
        "catalog_sales": channel("cs", [5.0, 6.0], "cs_bill_customer_sk"),
    }


@pytest.mark.parametrize("family", FAMILIES)
def test_q71_sums_only_nulls_to_null_and_sorts_it_first_because_the_key_is_descending(family, monkeypatch):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"year": 2000, "month": 12})
    tables = _q71_tables()
    impl = queries.q71_expression_impl if family == "expression" else queries.q71_pandas_impl

    assert _rows(impl(_context(family, tables))) == [
        (1, "a", 18, 26, None),
        (3, "c", 18, 26, 20.0),
        (2, "b", 18, 26, 8.0),
    ]


@pytest.mark.parametrize("family", FAMILIES)
def test_q76_sorts_a_null_category_last_and_reports_it_as_none(family, monkeypatch):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {})

    tables = _q76_tables()
    impl = queries.q76_expression_impl if family == "expression" else queries.q76_pandas_impl

    assert _rows(impl(_context(family, tables))) == [
        ("catalog", "cs_bill_customer_sk", 2001, 3, "Books", 1, 6.0),
        ("catalog", "cs_bill_customer_sk", 2001, 3, None, 1, 5.0),
        ("store", "ss_customer_sk", 2001, 3, "Books", 1, 2.0),
        ("store", "ss_customer_sk", 2001, 3, None, 1, None),
        ("web", "ws_bill_customer_sk", 2001, 3, "Books", 1, 4.0),
        ("web", "ws_bill_customer_sk", 2001, 3, None, 1, 3.0),
    ]


def test_q71_and_q76_run_on_dask_and_keep_a_null_total():
    dd = pytest.importorskip("dask.dataframe")
    import pandas as pd

    from benchbox.core.tpcds.dataframe_queries import queries
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    def run(impl, tables):
        ctx = DaskDataFrameAdapter(execution_engine="threaded").create_context()
        for name, data in tables.items():
            ctx.register_table(name, dd.from_pandas(pd.DataFrame(data), npartitions=2))
        return impl(ctx).compute().astype(object).where(lambda frame: frame.notna(), None)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(queries, "get_parameters", lambda query_id: {"year": 2000, "month": 12} if query_id == 71 else {})
        q71 = run(queries.q71_pandas_impl, _q71_tables())
        q76 = run(queries.q76_pandas_impl, _q76_tables())

    assert sorted(q71.itertuples(index=False, name=None), key=str) == sorted(
        [(1, "a", 18, 26, None), (3, "c", 18, 26, 20.0), (2, "b", 18, 26, 8.0)], key=str
    )
    assert list(q76.itertuples(index=False, name=None))[3] == ("store", "ss_customer_sk", 2001, 3, None, 1, None)


def test_joined_aggregate_runs_on_dask_and_keeps_a_null_group_last():
    dd = pytest.importorskip("dask.dataframe")
    import pandas as pd

    from benchbox.core.tpcds.dataframe_queries import queries
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    ctx = DaskDataFrameAdapter(execution_engine="threaded").create_context()
    data = {"g": ["x", "x", "x", "y", "y"], "k": ["b", None, "a", None, "c"], "v": [1.0, 2.0, 3.0, 4.0, 5.0]}
    ctx.register_table("t", dd.from_pandas(pd.DataFrame(data), npartitions=2))
    spec = {
        "query_id": 15,
        "base": "t",
        "joins": [],
        "group_by": ["g", "k"],
        "aggs": [["total", "v", "sum"]],
        "sort_by": ["g", "k"],
    }

    assert _rows(queries._joined_agg_pandas_impl(ctx, spec)) == [
        ("x", "a", 3.0),
        ("x", "b", 1.0),
        ("x", None, 2.0),
        ("y", "c", 5.0),
        ("y", None, 4.0),
    ]
