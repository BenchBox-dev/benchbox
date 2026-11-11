from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS
from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]
BACKENDS = ("polars", "pandas", "datafusion")
QUALIFICATION = json.loads(
    (Path(__file__).parents[4] / "benchbox/core/tpcds/dataframe_queries/qualification_values.json").read_text()
)["values"]


@pytest.fixture(scope="module")
def sql_generator():
    from benchbox.core.tpcds.c_tools import DSQGenBinary
    from benchbox.tpcds import TPCDS

    generator = DSQGenBinary()
    benchmark = TPCDS(scale_factor=1.0)

    def render(number, values):
        raw = generator.generate_with_parameters(number, values, scale_factor=1.0, seed=1)
        translated = benchmark._impl.translate_query_text(raw, "netezza", "duckdb")
        return benchmark._impl._apply_target_dialect_overrides(number, translated, "duckdb")

    return render


def _check(backend, number, values, spec, sql):
    duckdb = pytest.importorskip("duckdb")
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    pl = None
    if backend == "polars":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        adapter = PolarsDataFrameAdapter()
    elif backend == "pandas":
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        adapter = PandasDataFrameAdapter()
    else:
        pytest.importorskip("datafusion")
        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        adapter = DataFusionDataFrameAdapter()
    ctx = adapter.create_context()
    with duckdb.connect() as connection:
        for name, (columns, rows) in spec.items():
            connection.execute(f"CREATE TABLE {name} ({columns})")
            connection.executemany(f"INSERT INTO {name} VALUES ({','.join('?' for _ in rows[0])})", rows)
            table = connection.execute(f"SELECT * FROM {name}").to_arrow_table()
            if backend == "polars":
                assert pl is not None
                frame = pl.from_arrow(table).lazy()
            elif backend == "pandas":
                frame = table.to_pandas()
            else:
                adapter.session_ctx.register_record_batches(name, [table.to_batches()])
                frame = adapter.session_ctx.table(name)
            ctx.register_table(name, frame)
        expected = connection.execute(sql).fetchall()
        family = "pandas" if backend == "pandas" else "expression"
        with parameter_overrides({number: ADAPTERS[number](values)}):
            actual = materialize_rows(getattr(queries, f"q{number}_{family}_impl")(ctx))
    assert len(expected) >= 2
    assert actual == expected
    return actual


def _rolling_spec(number):
    store = number == 47
    table, prefix = ("store_sales", "ss") if store else ("catalog_sales", "cs")
    channel, channel_key, source_key = (
        ("store", "s_store_sk", "ss_store_sk") if store else ("call_center", "cc_call_center_sk", "cs_call_center_sk")
    )
    names = ["s_store_name", "s_company_name"] if store else ["cc_name"]
    return {
        "item": ("i_item_sk INTEGER,i_category VARCHAR,i_brand VARCHAR", [(1, "Books", "Brand")]),
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER,d_moy INTEGER", [(1, 1999, 1), (2, 1999, 2), (3, 1999, 3)]),
        channel: (
            f"{channel_key} INTEGER,{','.join(name + ' VARCHAR' for name in names)}",
            [(1, *["A" for _ in names]), (2, *["Z" for _ in names])],
        ),
        table: (
            f"{prefix}_item_sk INTEGER,{prefix}_sold_date_sk INTEGER,{source_key} INTEGER,{prefix}_sales_price DOUBLE",
            [
                (1, month, key, amount)
                for key, amounts in ((1, (10.0, 60.0, 20.0)), (2, (20.0, 60.0, 10.0)))
                for month, amount in enumerate(amounts, 1)
            ],
        ),
    }


def _q74_spec():
    spec = {
        "customer": (
            "c_customer_sk INTEGER,c_customer_id VARCHAR,c_first_name VARCHAR,c_last_name VARCHAR",
            [(1, "B", "First", "X"), (2, "A", "Z", "Last"), (3, "C", None, None)],
        ),
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER", [(1, 2001), (2, 2002)]),
    }
    for table, customer, date_key, amount, current in (
        ("store_sales", "ss_customer_sk", "ss_sold_date_sk", "ss_net_paid", 110.0),
        ("web_sales", "ws_bill_customer_sk", "ws_sold_date_sk", "ws_net_paid", 200.0),
    ):
        spec[table] = (
            f"{customer} INTEGER,{date_key} INTEGER,{amount} DOUBLE",
            [(key, year_key, 100.0 if year_key == 1 else current) for key in (1, 2, 3) for year_key in (1, 2)],
        )
    return spec


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("number", [47, 57, 74])
def test_qualification_choices_execute_unchanged_generated_sql(backend, number, sql_generator):
    values = QUALIFICATION[str(number)]
    spec = _q74_spec() if number == 74 else _rolling_spec(number)
    actual = _check(backend, number, values, spec, sql_generator(number, values))
    if number == 74:
        assert [row[0] for row in actual] == ["A", "B", "C"]
    else:
        assert [row[2] for row in actual] == ["A", "Z"]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("number", [47, 57])
def test_rolling_measure_order_remains_drawn(backend, number, sql_generator):
    values = {**QUALIFICATION[str(number)], "ORDERBY.01": "nsum"}
    actual = _check(backend, number, values, _rolling_spec(number), sql_generator(number, values))
    assert [row[2] for row in actual] == ["Z", "A"]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("positions", tuple(itertools.product((1, 2, 3), repeat=3)))
def test_q74_all_three_position_choices_match_generated_sql(backend, positions, sql_generator):
    values = {**QUALIFICATION["74"], **{f"ORDERC.{i:02}": str(position) for i, position in enumerate(positions, 1)}}
    _check(backend, 74, values, _q74_spec(), sql_generator(74, values))


@pytest.mark.parametrize("number,unsupported", [(47, "cc_name"), (57, "s_store_name"), (47, "sum_sales DESC")])
def test_rolling_unsupported_order_refuses(number, unsupported):
    from benchbox.core.tpcds.dataframe_queries.queries import _rolling_average_parameters

    keys = ["i_category", "i_brand", *(["s_store_name", "s_company_name"] if number == 47 else ["cc_name"])]
    with pytest.raises(ValueError, match="ORDERBY"):
        _rolling_average_parameters({"order_by": unsupported}, keys, "v1.i_category")


@pytest.mark.parametrize(
    "positions",
    [[], [1], [1, 2], [1, 2, 3, 1], [0, 1, 2], [1, 2, 4], [1, 2, "x"], [1.5, 2, 3], [True, 2, 3], None, "123"],
)
def test_q74_malformed_positions_refuse(positions):
    from benchbox.core.tpcds.dataframe_queries.queries import _q74_parameters

    with pytest.raises(ValueError):
        _q74_parameters({"order_by": positions})


