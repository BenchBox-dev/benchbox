from __future__ import annotations

import pytest

from benchbox.core.tpcds.dataframe_queries import queries

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

duckdb = pytest.importorskip("duckdb")
pa = pytest.importorskip("pyarrow")

DMS = 1200

_ITEMS = [
    (1, "Books", "arts"),
    (2, "Books", "fiction"),
    (3, "Children", "toys"),
    (4, "Music", "pop"),
    (5, "Music", "rock"),
    (6, None, "loose"),
]
_SALES = [
    (1, 1, "10.00", "4.00"),
    (2, 1, "5.00", "1.00"),
    (3, 1, "40.00", "2.00"),
    (4, 1, "70.00", "35.00"),
    (5, 1, "30.00", "3.00"),
    (6, 1, "20.00", "8.00"),
    (4, 2, "999.00", "999.00"),
]
_DATES = [(1, DMS + 3, 2000), (2, DMS + 40, 2003)]

_Q86_SQL = f"""
select sum(ws_net_paid) as total_sum, i_category, i_class,
       grouping(i_category)+grouping(i_class) as lochierarchy,
       rank() over (
         partition by grouping(i_category)+grouping(i_class),
                      case when grouping(i_class) = 0 then i_category end
         order by sum(ws_net_paid) desc) as rank_within_parent
from web_sales, date_dim d1, item
where d1.d_month_seq between {DMS} and {DMS}+11
  and d1.d_date_sk = ws_sold_date_sk
  and i_item_sk = ws_item_sk
group by rollup(i_category, i_class)
order by lochierarchy desc, case when lochierarchy = 0 then i_category end, rank_within_parent
limit 100
"""

_Q36_SQL = """
select sum(ss_net_profit)/sum(ss_ext_sales_price) as gross_margin, i_category, i_class,
       grouping(i_category)+grouping(i_class) as lochierarchy,
       rank() over (
         partition by grouping(i_category)+grouping(i_class),
                      case when grouping(i_class) = 0 then i_category end
         order by sum(ss_net_profit)/sum(ss_ext_sales_price) asc) as rank_within_parent
from store_sales, date_dim d1, item, store
where d1.d_year = 2000
  and d1.d_date_sk = ss_sold_date_sk
  and i_item_sk = ss_item_sk
  and s_store_sk = ss_store_sk
  and s_state in ('TN')
group by rollup(i_category, i_class)
order by lochierarchy desc, case when lochierarchy = 0 then i_category end, rank_within_parent
limit 100
"""


def _tables() -> dict[str, pa.Table]:
    items = {
        "i_item_sk": [row[0] for row in _ITEMS],
        "i_category": [row[1] for row in _ITEMS],
        "i_class": [row[2] for row in _ITEMS],
    }
    dates = {
        "d_date_sk": [row[0] for row in _DATES],
        "d_month_seq": [row[1] for row in _DATES],
        "d_year": [row[2] for row in _DATES],
    }
    return {
        "item": pa.table(items),
        "date_dim": pa.table(dates),
        "web_sales": pa.table(
            {
                "ws_item_sk": [row[0] for row in _SALES],
                "ws_sold_date_sk": [row[1] for row in _SALES],
                "ws_net_paid": [float(row[2]) for row in _SALES],
            }
        ),
        "store_sales": pa.table(
            {
                "ss_item_sk": [row[0] for row in _SALES],
                "ss_sold_date_sk": [row[1] for row in _SALES],
                "ss_store_sk": [1] * len(_SALES),
                "ss_ext_sales_price": [float(row[2]) for row in _SALES],
                "ss_net_profit": [float(row[3]) for row in _SALES],
            }
        ),
        "store": pa.table({"s_store_sk": [1], "s_state": ["TN"]}),
    }


def _reference(sql: str) -> list[tuple]:
    con = duckdb.connect()
    for name, table in _tables().items():
        con.register(name, table)
    return _normalize(con.execute(sql).fetchall())


def _normalize(rows) -> list[tuple]:
    return [
        (round(float(value), 6), category, klass, int(level), int(rank)) for value, category, klass, level, rank in rows
    ]


def _context(backend: str):
    tables = _tables()
    if backend == "datafusion":
        pytest.importorskip("datafusion")
        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        adapter = DataFusionDataFrameAdapter(target_partitions=4)
        ctx = adapter.create_context()
        for name, table in tables.items():
            adapter.session_ctx.register_record_batches(name, [table.to_batches()])
            ctx.register_table(name, adapter.session_ctx.table(name))
    elif backend == "polars":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, table in tables.items():
            ctx.register_table(name, pl.from_arrow(table).lazy())
    else:
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, table in tables.items():
            ctx.register_table(name, table.to_pandas())
    return ctx


def _run(implementation, backend: str) -> list[tuple]:
    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    return _normalize(materialize_rows(implementation(_context(backend))))


def test_reference_orders_subtotals_by_rank_not_category():
    subtotals = [row[1] for row in _reference(_Q86_SQL) if row[3] == 1]
    assert subtotals == ["Music", "Children", None, "Books"]


@pytest.mark.parametrize("backend", ["datafusion", "polars", "pandas"])
def test_q86_matches_sql_row_order(backend, monkeypatch):
    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"dms": DMS})
    implementation = queries.q86_pandas_impl if backend == "pandas" else queries.q86_expression_impl
    assert _run(implementation, backend) == _reference(_Q86_SQL)


@pytest.mark.parametrize("backend", ["datafusion", "polars", "pandas"])
def test_q36_matches_sql_row_order(backend, monkeypatch):
    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"year": 2000, "states": ["TN"]})
    implementation = queries.q36_pandas_impl if backend == "pandas" else queries.q36_expression_impl
    assert _run(implementation, backend) == _reference(_Q36_SQL)


