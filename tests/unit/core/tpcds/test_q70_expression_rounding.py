from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _q70_tables():

    return {
        "store_sales": {
            "ss_sold_date_sk": [1, 1, 1],
            "ss_store_sk": [1, 1, 2],
            "ss_net_profit": [0.1, 0.2, 0.7],
        },
        "date_dim": {"d_date_sk": [1], "d_month_seq": [1200]},
        "store": {"s_store_sk": [1, 2], "s_state": ["TN", "TN"], "s_county": ["A", "B"]},
    }


def test_q70_expression_totals_are_rounded_to_the_decimal_scale(monkeypatch):
    pl = pytest.importorskip("polars")

    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"dms": 1200})
    ctx = PolarsDataFrameAdapter().create_context()
    for name, data in _q70_tables().items():
        ctx.register_table(name, pl.DataFrame(data).lazy())

    rows = materialize_rows(queries.q70_expression_impl(ctx))

    totals = [row[0] for row in rows]
    assert totals, "Q70 returned no rows"
    assert all(total == round(total, 2) for total in totals), totals
    assert 0.3 in totals


