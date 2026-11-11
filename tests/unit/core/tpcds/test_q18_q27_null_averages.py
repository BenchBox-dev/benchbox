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
    elif family == "datafusion":
        pa = pytest.importorskip("pyarrow")
        pytest.importorskip("datafusion")
        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()
        for name, data in tables.items():
            columns = {
                key: pa.array(values, type=pa.float64() if None in values else None) for key, values in data.items()
            }
            adapter.session_ctx.register_record_batches(name, [pa.table(columns).to_batches()])
            ctx.register_table(name, adapter.session_ctx.sql(f"SELECT * FROM {name}"))
    else:
        import pandas as pd

        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pd.DataFrame(data))
    return ctx


def _run(family, number, parameters, tables):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries
    from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides

    impl_family = "expression" if family == "datafusion" else family
    with parameter_overrides({number: parameters}):
        result = getattr(queries, f"q{number}_{impl_family}_impl")(_context(family, tables))
    if family == "datafusion":
        table = result.collect()
        return list(zip(*(table.column(i).to_pylist() for i in range(table.num_columns))))
    return materialize_rows(result)


def _q18_tables():
    return {
        "catalog_sales": {
            "cs_sold_date_sk": [1],
            "cs_item_sk": [1],
            "cs_bill_cdemo_sk": [1],
            "cs_bill_customer_sk": [1],
            "cs_quantity": [5],
            "cs_list_price": [10.0],
            "cs_coupon_amt": [1.0],
            "cs_sales_price": [9.0],
            "cs_net_profit": [2.0],
        },
        "customer_demographics": {
            "cd_demo_sk": [1],
            "cd_gender": ["M"],
            "cd_education_status": ["College"],
            "cd_dep_count": [2],
        },
        "customer": {
            "c_customer_sk": [1],
            "c_current_cdemo_sk": [1],
            "c_current_addr_sk": [1],
            "c_birth_month": [9],
            "c_birth_year": [1970],
        },
        "customer_address": {
            "ca_address_sk": [1],
            "ca_country": ["United States"],
            "ca_state": ["ND"],
            "ca_county": ["Cass County"],
        },
        "date_dim": {"d_date_sk": [1], "d_year": [2001]},
        "item": {"i_item_sk": [1], "i_item_id": ["I1"]},
    }


def _q18_parameters(**overrides):
    return {
        "year": 2001,
        "states": ["ND"],
        "cd_gender": "M",
        "cd_education_status": "College",
        "birth_months": [9],
        **overrides,
    }


@pytest.mark.parametrize("family", FAMILIES)
def test_q18_filter_matching_nothing_returns_the_all_null_grand_total(family):
    rows = _run(family, 18, _q18_parameters(year=1900), _q18_tables())

    assert rows == [(None,) * 11]


@pytest.mark.parametrize("family", FAMILIES)
def test_q18_keeps_real_averages(family):
    rows = _run(family, 18, _q18_parameters(), _q18_tables())

    detail = ("I1", "United States", "ND", "Cass County", 5.0, 10.0, 1.0, 9.0, 2.0, 1970.0, 2.0)
    assert detail in rows
    assert (None, None, None, None, 5.0, 10.0, 1.0, 9.0, 2.0, 1970.0, 2.0) in rows


def _q27_tables():
    return {
        "store_sales": {
            "ss_sold_date_sk": [1, 1],
            "ss_item_sk": [1, 1],
            "ss_store_sk": [1, 1],
            "ss_cdemo_sk": [1, 1],
            "ss_quantity": [3, 5],
            "ss_list_price": [None, None],
            "ss_coupon_amt": [1.0, 3.0],
            "ss_sales_price": [2.0, 4.0],
        },
        "customer_demographics": {
            "cd_demo_sk": [1],
            "cd_gender": ["F"],
            "cd_marital_status": ["W"],
            "cd_education_status": ["Primary"],
        },
        "date_dim": {"d_date_sk": [1], "d_year": [1998]},
        "store": {"s_store_sk": [1], "s_state": ["TN"]},
        "item": {"i_item_sk": [1], "i_item_id": ["I1"]},
    }


Q27_PARAMETERS = {"year": 1998, "gender": "F", "marital_status": "W", "education": "Primary", "states": ["TN"]}


@pytest.mark.parametrize("family", FAMILIES)
def test_q27_average_over_only_nulls_is_none_and_the_grouping_flag_is_set_when_state_is_rolled_up(family):
    rows = _run(family, 27, Q27_PARAMETERS, _q27_tables())

    assert rows == [
        ("I1", "TN", 0, 4.0, None, 2.0, 3.0),
        ("I1", None, 1, 4.0, None, 2.0, 3.0),
        (None, None, 1, 4.0, None, 2.0, 3.0),
    ]


def test_q27_runs_on_datafusion_and_sets_the_grouping_flag():
    rows = _run("datafusion", 27, Q27_PARAMETERS, _q27_tables())

    assert {row[2] for row in rows} == {0, 1}
    assert len(rows) == 3


