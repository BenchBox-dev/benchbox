from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES
from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS
from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


def _q14_tables():
    return {
        "store_sales": {
            "ss_item_sk": [1, 1, 1, 1],
            "ss_sold_date_sk": [1, 1, 2, 2],
            "ss_quantity": [1, 1, 1, 1],
            "ss_list_price": [100.0, 100.0, 200.0, 200.0],
        },
        "catalog_sales": {"cs_item_sk": [1], "cs_sold_date_sk": [1], "cs_quantity": [1], "cs_list_price": [1.0]},
        "web_sales": {"ws_item_sk": [1], "ws_sold_date_sk": [1], "ws_quantity": [1], "ws_list_price": [1.0]},
        "item": {"i_item_sk": [1], "i_brand_id": [7], "i_class_id": [8], "i_category_id": [9]},
        "date_dim": {
            "d_date_sk": [1, 2, 3, 4],
            "d_year": [1998, 1999, 1998, 1999],
            "d_moy": [12, 12, 12, 12],
            "d_dom": [16, 16, 20, 20],
            "d_week_seq": [1, 2, 3, 4],
        },
    }


def _q23_tables():
    return {
        "store_sales": {
            "ss_customer_sk": [1] * 5 + [2] * 5 + [3] * 5,
            "ss_item_sk": [1] * 15,
            "ss_sold_date_sk": [1] * 15,
            "ss_quantity": [1] * 15,
            "ss_sales_price": [100.0] * 15,
        },
        "catalog_sales": {
            "cs_bill_customer_sk": [1, 2, 3],
            "cs_item_sk": [1, 1, 1],
            "cs_sold_date_sk": [1, 1, 1],
            "cs_quantity": [1, 1, 1],
            "cs_list_price": [10.0, 20.0, None],
        },
        "web_sales": {
            "ws_bill_customer_sk": [1, 2, 3],
            "ws_item_sk": [1, 1, 1],
            "ws_sold_date_sk": [1, 1, 1],
            "ws_quantity": [1, 1, 1],
            "ws_list_price": [5.0, 7.0, 3.0],
        },
        "customer": {
            "c_customer_sk": [1, 2, 3],
            "c_last_name": ["Same", "Same", None],
            "c_first_name": ["Name", "Name", "Null"],
        },
        "item": {"i_item_sk": [1], "i_item_desc": ["frequent"]},
        "date_dim": {"d_date_sk": [1], "d_year": [1999], "d_moy": [1], "d_date": [date(1999, 1, 1)]},
    }


def _q24_tables():
    return {
        "store_sales": {
            "ss_ticket_number": [1, 2],
            "ss_item_sk": [1, 2],
            "ss_customer_sk": [1, 1],
            "ss_store_sk": [1, 1],
            "ss_sales_price": [10.0, 20.0],
            "ss_net_profit": [50.0, 60.0],
        },
        "store_returns": {"sr_ticket_number": [1, 2], "sr_item_sk": [1, 2]},
        "store": {
            "s_store_sk": [1],
            "s_store_name": ["Main"],
            "s_market_id": [7],
            "s_zip": ["12345"],
            "s_state": ["IL"],
        },
        "item": {
            "i_item_sk": [1, 2],
            "i_color": ["orchid", "chiffon"],
            "i_current_price": [10.0, 10.0],
            "i_manager_id": [1, 1],
            "i_units": ["each", "each"],
            "i_size": ["small", "small"],
        },
        "customer": {
            "c_customer_sk": [1],
            "c_last_name": ["Last"],
            "c_first_name": ["First"],
            "c_current_addr_sk": [1],
            "c_birth_country": ["CANADA"],
        },
        "customer_address": {"ca_address_sk": [1], "ca_state": ["IL"], "ca_zip": ["12345"], "ca_country": ["US"]},
    }


def _q39_tables():
    return {
        "inventory": {
            "inv_item_sk": [1] * 6 + [2] * 6,
            "inv_warehouse_sk": [1] * 12,
            "inv_date_sk": [1, 2, 3, 4, 5, 6] * 2,
            "inv_quantity_on_hand": [1, 1, 100, 1, 1, 100, 1, 1, 10, 1, 1, 10],
        },
        "item": {"i_item_sk": [1, 2]},
        "warehouse": {"w_warehouse_sk": [1], "w_warehouse_name": ["Main"]},
        "date_dim": {"d_date_sk": [1, 2, 3, 4, 5, 6], "d_year": [2001] * 6, "d_moy": [1, 1, 1, 2, 2, 2]},
    }


@pytest.fixture(scope="module")
def tpcds_benchmark(tmp_path_factory):
    from benchbox.tpcds import TPCDS

    return TPCDS(scale_factor=0.01, output_dir=tmp_path_factory.mktemp("variant-sql"))


def _reference_rows(benchmark, query_id, logged, tables):
    import duckdb
    import pandas as pd

    number = int(query_id[:-1])
    impl = benchmark._impl
    raw = impl.query_manager.dsqgen.generate_with_parameters(query_id, logged, scale_factor=0.01)
    sql = impl._apply_target_dialect_overrides(number, impl.translate_query_text(raw, "netezza", "duckdb"), "duckdb")
    with duckdb.connect() as connection:
        for name, values in tables.items():
            connection.register(name, pd.DataFrame(values))
        return connection.execute(sql).fetchall()


def _dataframe_rows(family, query_id, logged, tables):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    if family == "expression":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, values in tables.items():
            dtypes = {column: pl.Float64 for column in values if column.endswith("_list_price")}
            ctx.register_table(name, pl.DataFrame(values, schema_overrides=dtypes).lazy())
    else:
        import pandas as pd

        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, values in tables.items():
            ctx.register_table(name, pd.DataFrame(values))
    number = int(query_id[:-1])
    with parameter_overrides({number: ADAPTERS[number](logged)}):
        implementation = TPCDS_DATAFRAME_QUERIES.get_or_raise(f"Q{query_id.removesuffix('a')}").get_impl_for_family(
            family
        )
        return materialize_rows(implementation(ctx))


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize(
    "query_id,logged,tables,expected_rows",
    [
        ("14b", {"YEAR.01": "1998", "DAY.01": "16"}, _q14_tables, 1),
        ("23b", {"YEAR.01": "1999", "MONTH.01": "1", "TOPPERCENT.01": "95"}, _q23_tables, 4),
        (
            "24b",
            {"MARKET.01": "7", "AMOUNTONE.01": "ss_sales_price", "COLOR.01": "orchid", "COLOR.02": "chiffon"},
            _q24_tables,
            1,
        ),
        ("39b", {"YEAR.01": "2001", "MONTH.01": "1"}, _q39_tables, 1),
    ],
)
def test_second_statement_matches_sql_on_nonempty_results(
    family, query_id, logged, tables, expected_rows, tpcds_benchmark
):
    values = tables()
    expected = _reference_rows(tpcds_benchmark, query_id, logged, values)
    assert len(expected) == expected_rows
    actual = _dataframe_rows(family, query_id, logged, values)
    assert len(actual) == len(expected)
    for actual_row, expected_row in zip(actual, expected):
        assert actual_row == pytest.approx(expected_row)


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q14b_follows_the_drawn_day(family, tpcds_benchmark):
    logged = {"YEAR.01": "1998", "DAY.01": "20"}
    tables = _q14_tables()
    assert _reference_rows(tpcds_benchmark, "14b", logged, tables) == []
    assert _dataframe_rows(family, "14b", logged, tables) == []


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q24b_uses_the_drawn_amount_column_and_second_color(family, tpcds_benchmark):
    logged = {"MARKET.01": "7", "AMOUNTONE.01": "ss_net_profit", "COLOR.01": "orchid", "COLOR.02": "chiffon"}
    tables = _q24_tables()
    expected = _reference_rows(tpcds_benchmark, "24b", logged, tables)
    assert expected == [("Last", "First", "Main", 60.0)]
    assert _dataframe_rows(family, "24b", logged, tables) == expected


def test_stream_resolution_selects_first_and_second_statement_implementations():
    from benchbox.core.dataframe.query_resolution import resolve_tpcds_stream_queries

    stream = [
        SimpleNamespace(query_id=number, variant=variant) for number in (14, 23, 24, 39) for variant in ("a", "b")
    ]
    resolved = resolve_tpcds_stream_queries(stream)
    assert [query.query_id for query in resolved] == [
        f"Q{number}{variant}" for number in (14, 23, 24, 39) for variant in ("a", "b")
    ]
    for number, first, second in zip((14, 23, 24, 39), resolved[::2], resolved[1::2]):
        assert first.expression_impl is TPCDS_DATAFRAME_QUERIES.get_or_raise(f"Q{number}").expression_impl
        assert second.expression_impl is TPCDS_DATAFRAME_QUERIES.get_or_raise(f"Q{number}b").expression_impl
        assert first.expression_impl is not second.expression_impl


def test_missing_second_statement_is_refused_by_default_and_explicit_fallback_warns(monkeypatch, caplog):
    from benchbox.core.dataframe.query_resolution import resolve_tpcds_stream_queries

    original_get = TPCDS_DATAFRAME_QUERIES.get
    monkeypatch.setattr(
        TPCDS_DATAFRAME_QUERIES,
        "get",
        lambda query_id: None if str(query_id).lower() == "q14b" else original_get(query_id),
    )
    stream = [SimpleNamespace(query_id=14, variant="b")]
    with pytest.raises(RuntimeError, match="missing variant DataFrame implementations.*Q14b"):
        resolve_tpcds_stream_queries(stream)
    (fallback,) = resolve_tpcds_stream_queries(stream, allow_variant_fallback=True)
    assert fallback.query_id == "Q14b"
    assert fallback.expression_impl is original_get("Q14").expression_impl
    assert "does not answer the SQL query Q14b" in caplog.text


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("query_id", ["23b", "24b", "39b"])
def test_variant_grouping_keeps_nullable_dimension_values(family, query_id, tpcds_benchmark):
    if query_id == "23b":
        logged = {"YEAR.01": "1999", "MONTH.01": "1", "TOPPERCENT.01": "95"}
        tables = _q23_tables()
        tables["item"] = {"i_item_sk": [1, 2], "i_item_desc": [None, "unused"]}
    elif query_id == "24b":
        logged = {"MARKET.01": "7", "AMOUNTONE.01": "ss_sales_price", "COLOR.01": "orchid", "COLOR.02": "chiffon"}
        tables = _q24_tables()
        tables["customer"] = {
            "c_customer_sk": [1, 2],
            "c_last_name": [None, "unused"],
            "c_first_name": ["First", "Other"],
            "c_current_addr_sk": [1, 1],
            "c_birth_country": ["CANADA", "CANADA"],
        }
    else:
        logged = {"YEAR.01": "2001", "MONTH.01": "1"}
        tables = _q39_tables()
        tables["warehouse"] = {"w_warehouse_sk": [1, 2], "w_warehouse_name": [None, "unused"]}
    expected = _reference_rows(tpcds_benchmark, query_id, logged, tables)
    assert expected
    actual = _dataframe_rows(family, query_id, logged, tables)
    assert len(actual) == len(expected)
    for actual_row, expected_row in zip(actual, expected):
        assert actual_row == pytest.approx(expected_row)


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q23b_null_maximum_does_not_select_customers_from_other_years(family, tpcds_benchmark):
    logged = {"YEAR.01": "1999", "MONTH.01": "1", "TOPPERCENT.01": "95"}
    tables = _q23_tables()
    tables["store_sales"]["ss_sales_price"] = [None] * 15
    for key, value in {
        "ss_customer_sk": 4,
        "ss_item_sk": 1,
        "ss_sold_date_sk": 2,
        "ss_quantity": 1,
        "ss_sales_price": 100.0,
    }.items():
        tables["store_sales"][key].append(value)
    for key, value in {"c_customer_sk": 4, "c_last_name": "Other", "c_first_name": "Year"}.items():
        tables["customer"][key].append(value)
    for key, value in {"d_date_sk": 2, "d_year": 2010, "d_moy": 1, "d_date": date(2010, 1, 1)}.items():
        tables["date_dim"][key].append(value)
    for key, value in {
        "cs_bill_customer_sk": 4,
        "cs_item_sk": 1,
        "cs_sold_date_sk": 1,
        "cs_quantity": 1,
        "cs_list_price": 99.0,
    }.items():
        tables["catalog_sales"][key].append(value)
    assert _reference_rows(tpcds_benchmark, "23b", logged, tables) == []
    assert _dataframe_rows(family, "23b", logged, tables) == []


def test_production_resolution_refuses_missing_variants_without_an_option(monkeypatch):
    from benchbox.core.dataframe.query_resolution import get_tpcds_dataframe_queries

    original_get = TPCDS_DATAFRAME_QUERIES.get
    monkeypatch.setattr(
        TPCDS_DATAFRAME_QUERIES,
        "get",
        lambda query_id: None if str(query_id).lower() == "q14b" else original_get(query_id),
    )
    config = SimpleNamespace(name="tpcds", scale_factor=0.01, options={}, queries=None)
    manager = SimpleNamespace(get_query=lambda query_id, seed=None, variant=None: "SELECT 1")
    instance = SimpleNamespace(query_manager=manager)
    with pytest.raises(RuntimeError, match="missing variant DataFrame implementations.*Q14b"):
        get_tpcds_dataframe_queries(config, instance, stream_id=0, bind_parameters=False)


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q24b_threshold_excludes_all_null_amount_groups(family, tpcds_benchmark):
    logged = {"MARKET.01": "7", "AMOUNTONE.01": "ss_sales_price", "COLOR.01": "orchid", "COLOR.02": "chiffon"}
    tables = _q24_tables()
    tables["store_sales"]["ss_sales_price"] = [1000.0, 20.0]
    for key, value in {
        "ss_ticket_number": 3,
        "ss_item_sk": 3,
        "ss_customer_sk": 1,
        "ss_store_sk": 1,
        "ss_sales_price": None,
        "ss_net_profit": 1.0,
    }.items():
        tables["store_sales"][key].append(value)
    tables["store_returns"]["sr_ticket_number"].append(3)
    tables["store_returns"]["sr_item_sk"].append(3)
    for key, value in {
        "i_item_sk": 3,
        "i_color": "violet",
        "i_current_price": 10.0,
        "i_manager_id": 1,
        "i_units": "each",
        "i_size": "small",
    }.items():
        tables["item"][key].append(value)
    assert _reference_rows(tpcds_benchmark, "24b", logged, tables) == []
    assert _dataframe_rows(family, "24b", logged, tables) == []


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("column", ["c_birth_country", "ca_country", "s_zip", "ca_zip"])
def test_q24b_null_comparison_operands_do_not_pass_the_filter(family, column, tpcds_benchmark):
    logged = {"MARKET.01": "7", "AMOUNTONE.01": "ss_sales_price", "COLOR.01": "orchid", "COLOR.02": "chiffon"}
    tables = _q24_tables()
    table = {
        "c_birth_country": "customer",
        "ca_country": "customer_address",
        "s_zip": "store",
        "ca_zip": "customer_address",
    }[column]
    for key, values in tables[table].items():
        values.append(values[0])
    identifier = {"customer": "c_customer_sk", "customer_address": "ca_address_sk", "store": "s_store_sk"}[table]
    tables[table][identifier][1] = 2
    tables[table][column][0] = None
    assert _reference_rows(tpcds_benchmark, "24b", logged, tables) == []
    assert _dataframe_rows(family, "24b", logged, tables) == []


def test_first_statement_alias_requires_a_multi_statement_template():
    from benchbox.core.dataframe.query_resolution import resolve_tpcds_stream_queries

    with pytest.raises(RuntimeError, match="missing variant DataFrame implementations.*Q1a"):
        resolve_tpcds_stream_queries([SimpleNamespace(query_id=1, variant="a")])


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize(
    "catalog_prices,web_prices,expected",
    [
        ([None, None, None], [None, None, None], None),
        ([None, None, None], [0.0, None, None], 0.0),
        ([None, None, None], [5.0, None, None], 5.0),
    ],
)
def test_q23a_union_sum_preserves_null_and_zero(family, catalog_prices, web_prices, expected, tpcds_benchmark):
    logged = {"YEAR.01": "1999", "MONTH.01": "1", "TOPPERCENT.01": "95"}
    tables = _q23_tables()
    tables["catalog_sales"]["cs_list_price"] = catalog_prices
    tables["web_sales"]["ws_list_price"] = web_prices
    assert _reference_rows(tpcds_benchmark, "23a", logged, tables) == [(expected,)]
    assert _dataframe_rows(family, "23a", logged, tables) == [(expected,)]


