from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize(
    "customers,amounts",
    [([1, 2, 3], [13.0, 10.0, None]), ([1, 2, None], [13.0, 0.0, 100.0])],
)
def test_state_average_ignores_null_totals_and_keeps_null_customer_groups(family, customers, amounts, monkeypatch):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    tables = {
        "catalog_returns": {
            "cr_returned_date_sk": [1, 1, 1],
            "cr_returning_addr_sk": [1, 1, 1],
            "cr_returning_customer_sk": customers,
            "cr_return_amt_inc_tax": amounts,
        },
        "date_dim": {"d_date_sk": [1], "d_year": [1998]},
        "customer_address": {"ca_address_sk": [1], "ca_state": ["IL"]},
        "customer": {
            "c_customer_sk": [1, 2, 3],
            "c_customer_id": ["customer1", "customer2", "customer3"],
            "c_current_addr_sk": [1, 1, 1],
        },
    }
    if family == "expression":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, values in tables.items():
            ctx.register_table(name, pl.DataFrame(values).lazy())
        impl = queries._state_average_returns_expression_impl
    else:
        import pandas as pd

        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, values in tables.items():
            ctx.register_table(name, pd.DataFrame(values))
        impl = queries._state_average_returns_pandas_impl
    spec = dict(queries._QUERY_SPECS["state_average_returns"][1])
    spec.update(expression_select=["c_customer_id"], pandas_select=["c_customer_id"])
    monkeypatch.setattr(queries, "get_parameters", lambda _: {"year": 1998, "state": "IL"})

    assert materialize_rows(impl(ctx, spec)) == []


