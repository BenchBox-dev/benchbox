from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("hours, expected", [([8, 9], None), ([8, 9, 19], 2.0), ([19], 0.0)])
def test_q90_ratio_preserves_sql_null_and_nonzero_division(family, hours, expected, monkeypatch):
    import pandas as pd

    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "_q90_params", lambda: (8, 19, 8, 5000, 5200))
    tables = {
        "web_sales": {
            "ws_sold_time_sk": list(range(len(hours))),
            "ws_ship_hdemo_sk": [1] * len(hours),
            "ws_web_page_sk": [1] * len(hours),
        },
        "time_dim": {"t_time_sk": list(range(len(hours))), "t_hour": hours},
        "household_demographics": {"hd_demo_sk": [1], "hd_dep_count": [8]},
        "web_page": {"wp_web_page_sk": [1], "wp_char_count": [5100]},
    }
    if family == "expression":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pl.DataFrame(data).lazy())
        result = queries.q90_expression_impl(ctx)
    else:
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pd.DataFrame(data))
        result = queries.q90_pandas_impl(ctx)

    assert materialize_rows(result) == [(expected,)]


