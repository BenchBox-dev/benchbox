from __future__ import annotations

from datetime import date

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ITEM_COUNT = 150


def _tables(
    prefix: str, sales_table: str, prices: list[float | None] | None = None, classes: list[str | None] | None = None
):
    items = list(range(1, (len(prices) if prices else ITEM_COUNT) + 1))
    count = len(items)
    return {
        sales_table: {
            f"{prefix}_item_sk": items,
            f"{prefix}_sold_date_sk": [1] * count,
            f"{prefix}_ext_sales_price": prices if prices else [10.0 + i for i in items],
        },
        "item": {
            "i_item_sk": items,
            "i_item_id": [f"ITEM{i:04d}" for i in items],
            "i_item_desc": [f"desc {i}" for i in items],
            "i_category": ["Sports"] * count,
            "i_class": classes if classes else ["golf"] * count,
            "i_current_price": [1.0] * count,
        },
        "date_dim": {"d_date_sk": [1], "d_date": [date(2001, 1, 15)]},
    }


def _run(family, query_id, prefix, sales_table, prices=None, classes=None):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    tables = _tables(prefix, sales_table, prices, classes)
    if family == "expression":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pl.DataFrame(data).lazy())
        impl = getattr(queries, f"q{query_id}_expression_impl")
    else:
        import pandas as pd

        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pd.DataFrame(data))
        impl = getattr(queries, f"q{query_id}_pandas_impl")
    return materialize_rows(impl(ctx))


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize(
    "query_id, prefix, sales_table, expected_rows",
    [(98, "ss", "store_sales", ITEM_COUNT), (12, "ws", "web_sales", 100), (20, "cs", "catalog_sales", 100)],
)
def test_item_category_sales_limit_follows_the_sql(family, query_id, prefix, sales_table, expected_rows, monkeypatch):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"sales_date": "2001-01-12"})

    assert len(_run(family, query_id, prefix, sales_table)) == expected_rows


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_all_null_group_keeps_a_null_ratio_and_a_zero_group_stays_zero(family, monkeypatch):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"sales_date": "2001-01-12"})

    rows = _run(family, 98, "ss", "store_sales", prices=[None, 0.0, 5.0])

    by_item = {row[0]: (row[-2], row[-1]) for row in rows}
    assert by_item["ITEM0001"] == (None, None)
    assert by_item["ITEM0002"] == (0.0, 0.0)
    assert by_item["ITEM0003"] == (5.0, 100.0)


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_zero_total_class_keeps_nan_ratios(family, monkeypatch):
    import math

    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"sales_date": "2001-01-12"})

    rows = _run(family, 98, "ss", "store_sales", prices=[0.0, 0.0])

    assert [row[-2] for row in rows] == [0.0, 0.0]
    assert all(isinstance(row[-1], float) and math.isnan(row[-1]) for row in rows)


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_a_null_class_is_kept_sorts_last_and_stays_none(family, monkeypatch):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"sales_date": "2001-01-12"})

    rows = _run(family, 20, "cs", "catalog_sales", prices=[1.0, 2.0, 3.0], classes=[None, "golf", "golf"])

    assert [row[0] for row in rows] == ["ITEM0002", "ITEM0003", "ITEM0001"]
    assert rows[-1][3] is None


def test_none_for_null_converts_lazily_on_dask():
    dd = pytest.importorskip("dask.dataframe")
    import pandas as pd

    from benchbox.core.tpcds.dataframe_queries import queries

    frame = dd.from_pandas(pd.DataFrame({"k": ["a", None, "c"], "n": [1.0, None, 3.0], "i": [1, 2, 3]}), npartitions=2)

    rows = queries._none_for_null(frame, ["k", "n"]).compute().to_dict("records")

    assert rows == [{"k": "a", "n": 1.0, "i": 1}, {"k": None, "n": None, "i": 2}, {"k": "c", "n": 3.0, "i": 3}]


