from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

duckdb = pytest.importorskip("duckdb")

DMS = 1200

_SALES = [
    (1, 1, "50.10"),
    (1, 2, "20.20"),
    (1, 3, "30.30"),
    (1, 4, "40.40"),
    (1, 5, "10.50"),
    (1, 6, "5.60"),
    (1, 7, "1.70"),
    (1, 8, "2.80"),
    (1, 9, "99.90"),
    (1, 10, "60.00"),
    (2, 6, "500.00"),
]
_DATES = [(1, DMS + 3), (2, DMS + 40)]
_STORES = [
    (1, "AL", "a1"),
    (2, "CA", "c1"),
    (3, "FL", "f1"),
    (4, "GA", "g1"),
    (5, "NY", "n1"),
    (6, "TX", "t1"),
    (7, "WA", "w1"),
    (8, "WA", "w2"),
    (9, None, "x1"),
    (10, "OR", None),
]

_Q70_SQL = f"""
select sum(ss_net_profit) as total_sum, s_state, s_county,
       grouping(s_state)+grouping(s_county) as lochierarchy,
       rank() over (
         partition by grouping(s_state)+grouping(s_county),
                      case when grouping(s_county) = 0 then s_state end
         order by sum(ss_net_profit) desc) as rank_within_parent
from store_sales, date_dim d1, store
where d1.d_month_seq between {DMS} and {DMS}+11
  and d1.d_date_sk = ss_sold_date_sk
  and s_store_sk = ss_store_sk
  and s_state in (
        select s_state
        from (select s_state as s_state,
                     rank() over (partition by s_state order by sum(ss_net_profit) desc) as ranking
              from store_sales, store, date_dim
              where d_month_seq between {DMS} and {DMS}+11
                and d_date_sk = ss_sold_date_sk
                and s_store_sk = ss_store_sk
              group by s_state) tmp1
        where ranking <= 5)
group by rollup(s_state, s_county)
order by lochierarchy desc, case when lochierarchy = 0 then s_state end, rank_within_parent
limit 100
"""

_INNER_SQL = f"""
select s_state, ranking
from (select s_state as s_state,
             rank() over (partition by s_state order by sum(ss_net_profit) desc) as ranking
      from store_sales, store, date_dim
      where d_month_seq between {DMS} and {DMS}+11
        and d_date_sk = ss_sold_date_sk
        and s_store_sk = ss_store_sk
      group by s_state) tmp1
"""


def _duckdb_connection():
    con = duckdb.connect()
    con.execute("create table store_sales(ss_sold_date_sk int, ss_store_sk int, ss_net_profit decimal(7,2))")
    con.execute("create table date_dim(d_date_sk int, d_month_seq int)")
    con.execute("create table store(s_store_sk int, s_state varchar, s_county varchar)")
    con.executemany("insert into store_sales values (?, ?, ?)", _SALES)
    con.executemany("insert into date_dim values (?, ?)", _DATES)
    con.executemany("insert into store values (?, ?, ?)", _STORES)
    return con


def _tables():
    return {
        "store_sales": {
            "ss_sold_date_sk": [r[0] for r in _SALES],
            "ss_store_sk": [r[1] for r in _SALES],
            "ss_net_profit": [float(r[2]) for r in _SALES],
        },
        "date_dim": {"d_date_sk": [r[0] for r in _DATES], "d_month_seq": [r[1] for r in _DATES]},
        "store": {
            "s_store_sk": [r[0] for r in _STORES],
            "s_state": [r[1] for r in _STORES],
            "s_county": [r[2] for r in _STORES],
        },
    }


def _normalize(rows):
    return [
        (round(float(total), 2), state, county, int(level), int(rank)) for total, state, county, level, rank in rows
    ]


def _reference_rows():
    return _normalize(_duckdb_connection().execute(_Q70_SQL).fetchall())


def test_inner_subquery_ranks_every_state_first():
    rows = _duckdb_connection().execute(_INNER_SQL).fetchall()
    states = {state for state, _ in rows}
    assert len(states - {None}) == 8
    assert {ranking for _, ranking in rows} == {1}


def test_reference_keeps_all_states_and_drops_null_state():
    states = {row[1] for row in _reference_rows() if row[3] == 0}
    assert states == {"AL", "CA", "FL", "GA", "NY", "TX", "WA", "OR"}


def test_q70_expression_matches_duckdb_sql(monkeypatch):
    pl = pytest.importorskip("polars")

    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"dms": DMS})
    ctx = PolarsDataFrameAdapter().create_context()
    for name, data in _tables().items():
        ctx.register_table(name, pl.DataFrame(data).lazy())

    assert _normalize(materialize_rows(queries.q70_expression_impl(ctx))) == _reference_rows()


def test_q70_pandas_matches_duckdb_sql(monkeypatch):
    pd = pytest.importorskip("pandas")

    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries
    from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"dms": DMS})
    ctx = PandasDataFrameAdapter().create_context()
    for name, data in _tables().items():
        ctx.register_table(name, pd.DataFrame(data))

    assert _normalize(materialize_rows(queries.q70_pandas_impl(ctx))) == _reference_rows()


