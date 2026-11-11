from __future__ import annotations

import pytest
from test_ascending_null_order import _MANUFACTURER_SQL, _manufacturer_spec, _q64_fixture, _store_catalog_spec

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _check(monkeypatch, family, query_id, params, spec, sql, *, float_money=False):
    duckdb = pytest.importorskip("duckdb")
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: params)
    pl = None
    if family == "expression":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
    else:
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
    with duckdb.connect() as connection:
        for name, (columns, rows) in spec.items():
            connection.execute(f"CREATE TABLE {name} ({columns})")
            if rows:
                placeholders = ", ".join(["?"] * len(rows[0]))
                connection.executemany(f"INSERT INTO {name} VALUES ({placeholders})", rows)
            arrow = connection.execute(f"SELECT * FROM {name}").to_arrow_table()
            if float_money:
                import pyarrow as pa

                arrow = arrow.cast(
                    pa.schema(
                        [
                            pa.field(field.name, pa.float64() if pa.types.is_decimal(field.type) else field.type)
                            for field in arrow.schema
                        ]
                    )
                )
            if family == "expression":
                assert pl is not None
                frame = pl.from_arrow(arrow).lazy()
            else:
                frame = arrow.to_pandas()
            ctx.register_table(name, frame)
        expected = connection.execute(sql).fetchall()
        actual = materialize_rows(getattr(queries, f"q{query_id}_{family}_impl")(ctx))
    assert actual == expected
    return expected


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("query_id", [33, 56, 60])
def test_three_channel_sums_preserve_null_and_zero(monkeypatch, family, query_id):
    spec = {
        "item": (
            "i_item_sk INTEGER, i_item_id VARCHAR, i_manufact_id INTEGER, i_category VARCHAR, i_color VARCHAR",
            [
                (1, "A", 1, "Books", "slate"),
                (2, "B", 2, "Books", "slate"),
                (3, "C", 3, "Books", "slate"),
                (4, None, None, "Books", "slate"),
            ],
        ),
        "date_dim": ("d_date_sk INTEGER, d_year INTEGER, d_moy INTEGER", [(1, 1999, 3)]),
        "customer_address": ("ca_address_sk INTEGER, ca_gmt_offset INTEGER", [(1, -5)]),
    }
    branches = []
    key = "i_manufact_id" if query_id == 33 else "i_item_id"
    for table, prefix, addr, price in (
        ("store_sales", "ss", "addr", 5.0),
        ("catalog_sales", "cs", "bill_addr", 3.0),
        ("web_sales", "ws", "bill_addr", 4.0),
    ):
        spec[table] = (
            f"{prefix}_item_sk INTEGER, {prefix}_sold_date_sk INTEGER, "
            f"{prefix}_{addr}_sk INTEGER, {prefix}_ext_sales_price DOUBLE",
            [(1, 1, 1, None), (2, 1, 1, price), (3, 1, 1, 0.0 if prefix == "ss" else None), (4, 1, 1, 9.0)],
        )
        branches.append(
            f"SELECT {key}, SUM({prefix}_ext_sales_price) AS total_sales FROM {table} "
            f"JOIN item ON {prefix}_item_sk=i_item_sk "
            f"JOIN date_dim ON {prefix}_sold_date_sk=d_date_sk "
            f"JOIN customer_address ON {prefix}_{addr}_sk=ca_address_sk "
            f"WHERE d_year=1999 AND d_moy=3 AND ca_gmt_offset=-5 "
            f"AND {key} IN (SELECT {key} FROM item WHERE i_category='Books' AND i_color='slate') GROUP BY {key}"
        )
    ordering = f"{key}, total_sales" if query_id == 60 else f"total_sales, {key}"
    sql = f"SELECT {key}, SUM(total_sales) AS total_sales FROM ({' UNION ALL '.join(branches)}) GROUP BY {key} ORDER BY {ordering}"
    params = {"year": 1999, "month": 3, "gmt_offset": -5, "category": "Books", "colors": ["slate"]}
    expected = _check(monkeypatch, family, query_id, params, spec, sql)
    assert len(expected) == 3
    assert sorted(row[1] for row in expected if row[1] is not None) == [0.0, 12.0]
    assert sum(row[1] is None for row in expected) == 1


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q43_conditional_sums_preserve_missing_days(monkeypatch, family):
    spec = {
        "date_dim": (
            "d_date_sk INTEGER, d_year INTEGER, d_day_name VARCHAR",
            [(1, 1998, "Sunday"), (2, 1998, "Monday"), (3, 1998, "Tuesday")],
        ),
        "store": (
            "s_store_sk INTEGER, s_store_name VARCHAR, s_store_id VARCHAR, s_gmt_offset DOUBLE",
            [(1, "s", "S", -5.0)],
        ),
        "store_sales": (
            "ss_sold_date_sk INTEGER, ss_store_sk INTEGER, ss_sales_price DOUBLE",
            [(1, 1, None), (2, 1, 0.0), (3, 1, 7.0)],
        ),
    }
    days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    aggregates = ", ".join(f"SUM(CASE WHEN d_day_name='{day}' THEN ss_sales_price END)" for day in days)
    sql = (
        f"SELECT s_store_name, s_store_id, {aggregates} FROM store_sales "
        "JOIN date_dim ON ss_sold_date_sk=d_date_sk JOIN store ON ss_store_sk=s_store_sk "
        "WHERE s_gmt_offset=-5 AND d_year=1998 GROUP BY s_store_name,s_store_id"
    )
    assert _check(monkeypatch, family, 43, {}, spec, sql) == [("s", "S", None, 0.0, 7.0, None, None, None, None)]


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("query_id", [53, 63])
def test_manufacturer_window_retains_null_groups_and_ignores_null_totals(monkeypatch, family, query_id):
    spec = _manufacturer_spec()
    columns, dates = spec["date_dim"]
    spec["date_dim"] = columns, [*dates, (3, 1214, 3, 3)]
    columns, sales = spec["store_sales"]
    spec["store_sales"] = columns, [*sales, (1, 3, 1, None), (2, 3, 1, None)]
    key, period, avg = (
        ("i_manufact_id", "d_qoy", "avg_quarterly_sales")
        if query_id == 53
        else ("i_manager_id", "d_moy", "avg_monthly_sales")
    )
    order = f"{avg},sum_sales,{key}" if query_id == 53 else f"{key},{avg},sum_sales"
    sql = _MANUFACTURER_SQL.format(key=key, avg=avg, period=period, order=order).replace(
        "SELECT * FROM (", f"SELECT {key}, sum_sales, {avg} FROM (", 1
    )
    expected = _check(monkeypatch, family, query_id, {}, spec, sql)
    assert len(expected) == 4
    assert sum(row[0] is None for row in expected) == 2
    assert all(row[2] == 20.0 for row in expected)


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("aggregate", ["stddev_samp", "sum"])
def test_q25_null_aggregate_returns_null(monkeypatch, family, aggregate):
    spec = _store_catalog_spec((1, 2000, 4, "2000Q2"))
    if aggregate == "sum":
        for table in ("store_sales", "store_returns", "catalog_sales"):
            columns, rows = spec[table]
            spec[table] = columns, [(*row[:-1], None) for row in rows]
    sql = """
        SELECT i_item_id,i_item_desc,s_store_id,s_store_name,
               STDDEV_SAMP(ss_net_profit),STDDEV_SAMP(sr_net_loss),STDDEV_SAMP(cs_net_profit)
        FROM store_sales JOIN item ON ss_item_sk=i_item_sk JOIN store ON ss_store_sk=s_store_sk
        JOIN store_returns ON ss_customer_sk=sr_customer_sk AND ss_item_sk=sr_item_sk AND ss_ticket_number=sr_ticket_number
        JOIN catalog_sales ON sr_customer_sk=cs_bill_customer_sk AND sr_item_sk=cs_item_sk
        GROUP BY i_item_id,i_item_desc,s_store_id,s_store_name
        ORDER BY i_item_id,i_item_desc,s_store_id,s_store_name
    """
    sql = sql.replace("STDDEV_SAMP", aggregate.upper())
    expected = _check(monkeypatch, family, 25, {"year": 2000, "agg": aggregate}, spec, sql)
    assert len(expected) == 4
    assert all(row[-3:] == (None, None, None) for row in expected)


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("query_id", [47, 57])
def test_rolling_average_ignores_null_month_and_keeps_null_neighbor(monkeypatch, family, query_id):
    store_channel = query_id == 47
    table, prefix = ("store_sales", "ss") if store_channel else ("catalog_sales", "cs")
    channel, channel_key, source_key = (
        ("store", "s_store_sk", "ss_store_sk")
        if store_channel
        else ("call_center", "cc_call_center_sk", "cs_call_center_sk")
    )
    channel_names = ["s_store_name", "s_company_name"] if store_channel else ["cc_name"]
    spec = {
        "item": ("i_item_sk INTEGER, i_category VARCHAR, i_brand VARCHAR", [(1, "Books", "b")]),
        "date_dim": ("d_date_sk INTEGER, d_year INTEGER, d_moy INTEGER", [(1, 2000, 1), (2, 2000, 2), (3, 2000, 3)]),
        channel: (
            f"{channel_key} INTEGER, {', '.join(name + ' VARCHAR' for name in channel_names)}",
            [(1, *["s" for _ in channel_names])],
        ),
        table: (
            f"{prefix}_item_sk INTEGER, {prefix}_sold_date_sk INTEGER, {source_key} INTEGER, {prefix}_sales_price DOUBLE",
            [(1, 1, 1, None), (1, 2, 1, 30.0), (1, 3, 1, 10.0)],
        ),
    }
    keys = ["i_category", "i_brand", *channel_names]
    partition = ",".join(keys)
    selected = "i_category,i_brand," if store_channel else "cc_name,"
    sql = f"""
        WITH base AS (
            SELECT {partition},d_year,d_moy,SUM({prefix}_sales_price) AS sum_sales
            FROM {table} JOIN item ON {prefix}_item_sk=i_item_sk
            JOIN date_dim ON {prefix}_sold_date_sk=d_date_sk JOIN {channel} ON {source_key}={channel_key}
            GROUP BY {partition},d_year,d_moy
        ), v AS (
            SELECT *,AVG(sum_sales) OVER(PARTITION BY {partition},d_year) AS avg_monthly_sales,
                   LAG(sum_sales) OVER(PARTITION BY {partition} ORDER BY d_year,d_moy) AS psum,
                   LEAD(sum_sales) OVER(PARTITION BY {partition} ORDER BY d_year,d_moy) AS nsum,
                   ROW_NUMBER() OVER(PARTITION BY {partition} ORDER BY d_year,d_moy) AS rn
            FROM base
        ) SELECT {selected}d_year,d_moy,avg_monthly_sales,sum_sales,psum,nsum
          FROM v WHERE rn=2 AND ABS(sum_sales-avg_monthly_sales)/avg_monthly_sales>0.1
    """
    expected = _check(monkeypatch, family, query_id, {"year": 2000}, spec, sql)
    assert len(expected) == 1
    assert expected[0][-4:] == (20.0, 30.0, None, 10.0)


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("null_refund", [False, True])
def test_q64_null_sums_control_refund_filter_and_result(monkeypatch, family, null_refund):
    spec, sql, params = _q64_fixture()
    columns, sales = spec["store_sales"]
    spec["store_sales"] = columns, [(*row[:-3], None, row[-2], None) for row in sales]
    if null_refund:
        columns, refunds = spec["catalog_returns"]
        spec["catalog_returns"] = columns, [(*row[:-1], None) for row in refunds]
    expected = _check(monkeypatch, family, 64, params, spec, sql)
    if null_refund:
        assert expected == []
    else:
        assert len(expected) == 2
        assert all(row[13] is None and row[15] is None and row[16] is None and row[18] is None for row in expected)


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("store_row", [(1, None, "z"), (1, "s", None)])
def test_q64_grouping_nulls_do_not_match_year_join_keys(monkeypatch, family, store_row):
    spec, sql, params = _q64_fixture()
    columns, _rows = spec["store"]
    spec["store"] = columns, [store_row]

    assert _check(monkeypatch, family, 64, params, spec, sql) == []


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q54_null_revenue_remains_a_counted_null_segment(monkeypatch, family):
    spec = {
        "item": ("i_item_sk INTEGER,i_category VARCHAR,i_class VARCHAR", [(1, "Women", "maternity")]),
        "date_dim": (
            "d_date_sk INTEGER,d_year INTEGER,d_moy INTEGER,d_month_seq INTEGER",
            [(1, 1998, 12, 12), (2, 1999, 1, 13)],
        ),
        "catalog_sales": (
            "cs_sold_date_sk INTEGER,cs_bill_customer_sk INTEGER,cs_item_sk INTEGER",
            [(1, key, 1) for key in (1, 2, 3, 4)],
        ),
        "web_sales": ("ws_sold_date_sk INTEGER,ws_bill_customer_sk INTEGER,ws_item_sk INTEGER", []),
        "customer": ("c_customer_sk INTEGER,c_current_addr_sk INTEGER", [(key, 1) for key in (1, 2, 3, 4)]),
        "customer_address": ("ca_address_sk INTEGER,ca_county VARCHAR,ca_state VARCHAR", [(1, "c", "s")]),
        "store": ("s_store_sk INTEGER,s_county VARCHAR,s_state VARCHAR", [(1, "c", "s")]),
        "store_sales": (
            "ss_customer_sk INTEGER,ss_sold_date_sk INTEGER,ss_ext_sales_price DOUBLE",
            [(1, 2, None), (2, 2, 0.0), (3, 2, 125.0), (4, 2, -125.0)],
        ),
    }
    sql = """
        WITH customers AS (
            SELECT DISTINCT c_customer_sk,c_current_addr_sk FROM catalog_sales
            JOIN item ON cs_item_sk=i_item_sk JOIN date_dim ON cs_sold_date_sk=d_date_sk
            JOIN customer ON cs_bill_customer_sk=c_customer_sk
            WHERE i_category='Women' AND i_class='maternity' AND d_year=1998 AND d_moy=12
        ), revenue AS (
            SELECT c_customer_sk,CAST(SUM(ss_ext_sales_price)/50 AS BIGINT) AS segment
            FROM customers JOIN customer_address ON c_current_addr_sk=ca_address_sk
            JOIN store ON ca_county=s_county AND ca_state=s_state
            JOIN store_sales ON c_customer_sk=ss_customer_sk JOIN date_dim ON ss_sold_date_sk=d_date_sk
            WHERE d_month_seq BETWEEN 13 AND 15 GROUP BY c_customer_sk
        ) SELECT segment,COUNT(*),segment*50 FROM revenue GROUP BY segment ORDER BY segment
    """
    assert _check(monkeypatch, family, 54, {}, spec, sql) == [(-2, 1, -100), (0, 1, 0), (2, 1, 100), (None, 1, None)]


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q75_null_amount_and_quantity_remain_null(monkeypatch, family):
    spec = {
        "item": (
            "i_item_sk INTEGER,i_brand_id INTEGER,i_class_id INTEGER,i_category_id INTEGER,i_manufact_id INTEGER,i_category VARCHAR",
            [(key, key, 1, 1, 1, "Books") for key in (1, 2, 3)],
        ),
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER", [(1, 2000), (2, 2001)]),
    }
    branches = []
    for table, returns, prefix, rp, order, amount in (
        ("catalog_sales", "catalog_returns", "cs", "cr", "order_number", "return_amount"),
        ("store_sales", "store_returns", "ss", "sr", "ticket_number", "return_amt"),
        ("web_sales", "web_returns", "ws", "wr", "order_number", "return_amt"),
    ):
        rows = []
        for key in (1, 2, 3):
            rows.extend(
                [
                    (key, 1, key * 10, 10, 0.0 if key == 2 else 100.0),
                    (key, 2, key * 10 + 1, None if key == 3 else 5, 0.0 if key == 2 else None),
                ]
            )
        spec[table] = (
            f"{prefix}_item_sk INTEGER,{prefix}_sold_date_sk INTEGER,{prefix}_{order} INTEGER,{prefix}_quantity INTEGER,{prefix}_ext_sales_price DOUBLE",
            rows,
        )
        spec[returns] = (
            f"{rp}_item_sk INTEGER,{rp}_{order} INTEGER,{rp}_return_quantity INTEGER,{rp}_{amount} DOUBLE",
            [],
        )
        branches.append(
            f"SELECT d_year,i_brand_id,i_class_id,i_category_id,i_manufact_id,"
            f"{prefix}_quantity-COALESCE({rp}_return_quantity,0) AS sales_cnt,"
            f"{prefix}_ext_sales_price-COALESCE({rp}_{amount},0) AS sales_amt FROM {table} "
            f"JOIN item ON {prefix}_item_sk=i_item_sk JOIN date_dim ON {prefix}_sold_date_sk=d_date_sk "
            f"LEFT JOIN {returns} ON {prefix}_item_sk={rp}_item_sk AND {prefix}_{order}={rp}_{order}"
        )
    keys = "i_brand_id,i_class_id,i_category_id,i_manufact_id"
    sql = f"""
        WITH all_sales AS (
            SELECT d_year,{keys},SUM(sales_cnt) AS sales_cnt,SUM(sales_amt) AS sales_amt
            FROM ({" UNION ".join(branches)}) GROUP BY d_year,{keys}
        ) SELECT p.d_year,c.d_year,c.i_brand_id,c.i_class_id,c.i_category_id,c.i_manufact_id,
                 p.sales_cnt,c.sales_cnt,c.sales_cnt-p.sales_cnt AS count_diff,c.sales_amt-p.sales_amt AS amount_diff
          FROM all_sales c JOIN all_sales p USING ({keys})
          WHERE c.d_year=2001 AND p.d_year=2000 AND c.sales_cnt/p.sales_cnt<0.9
          ORDER BY count_diff,amount_diff
    """
    assert _check(monkeypatch, family, 75, {}, spec, sql) == [
        (2000, 2001, 2, 1, 1, 1, 10, 5, -5, 0.0),
        (2000, 2001, 1, 1, 1, 1, 10, 5, -5, None),
    ]


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q93_keeps_null_sums_and_customer_groups(monkeypatch, family):
    spec = {
        "store_sales": (
            "ss_item_sk INTEGER, ss_ticket_number INTEGER, ss_customer_sk INTEGER, "
            "ss_quantity INTEGER, ss_sales_price DOUBLE",
            [
                (1, 1, 1, None, 2.0),
                (2, 2, 2, 4, 2.0),
                (3, 3, None, 5, 2.0),
                (4, 4, 3, 1, 1.0),
                (5, 5, 4, 1, 1.0),
                (None, 6, 5, 100, 1.0),
            ],
        ),
        "store_returns": (
            "sr_item_sk INTEGER, sr_ticket_number INTEGER, sr_return_quantity INTEGER, sr_reason_sk INTEGER",
            [(1, 1, 1, 1), (2, 2, 4, 1), (3, 3, None, 1), (4, 4, 2, 1), (None, 6, 1, 1)],
        ),
        "reason": ("r_reason_sk INTEGER, r_reason_desc VARCHAR", [(1, "selected")]),
    }
    sql = """
        SELECT ss_customer_sk, SUM(act_sales) sumsales FROM (
            SELECT ss_customer_sk,
                   CASE WHEN sr_return_quantity IS NOT NULL
                        THEN (ss_quantity-sr_return_quantity)*ss_sales_price
                        ELSE ss_quantity*ss_sales_price END act_sales
            FROM store_sales LEFT JOIN store_returns
              ON sr_item_sk=ss_item_sk AND sr_ticket_number=ss_ticket_number, reason
            WHERE sr_reason_sk=r_reason_sk AND r_reason_desc='selected'
        ) GROUP BY ss_customer_sk ORDER BY sumsales,ss_customer_sk LIMIT 100
    """
    expected = _check(monkeypatch, family, 93, {"reason": "selected"}, spec, sql)
    assert expected == [(3, -1.0), (2, 0.0), (None, 10.0), (1, None)]


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize(
    "previous,current,expected_difference",
    [
        (("0.30", "0.10", "0.20"), ("0.80", "0.10", "0.70"), 0.5),
        (("0.20", "0.30", "-0.10"), ("0.20", "0.80", "-0.60"), -0.5),
        (
            ("9999999999999.99", "9999999999999.79", "0.20"),
            ("9999999999999.99", "9999999999999.29", "0.70"),
            0.5,
        ),
    ],
)
def test_q75_union_uses_decimal_amount_equality(monkeypatch, family, previous, current, expected_difference):
    from decimal import Decimal

    spec = {
        "item": (
            "i_item_sk INTEGER,i_brand_id INTEGER,i_class_id INTEGER,i_category_id INTEGER,i_manufact_id INTEGER,i_category VARCHAR",
            [(1, 1, 1, 1, 1, "Books")],
        ),
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER", [(1, 2000), (2, 2001)]),
    }
    branches = []
    for channel_index, (table, returns, prefix, rp, order, amount) in enumerate(
        (
            ("catalog_sales", "catalog_returns", "cs", "cr", "order_number", "return_amount"),
            ("store_sales", "store_returns", "ss", "sr", "ticket_number", "return_amt"),
            ("web_sales", "web_returns", "ws", "wr", "order_number", "return_amt"),
        )
    ):
        sales_rows = []
        return_rows = []
        for date_key, quantity, amounts in ((1, 10, previous), (2, 5, current)):
            price, refund = amounts[:2] if channel_index == 0 else (amounts[2], "0.00")
            sales_rows.append((1, date_key, date_key, quantity, Decimal(price)))
            return_rows.append((1, date_key, 0, Decimal(refund)))
        spec[table] = (
            f"{prefix}_item_sk INTEGER,{prefix}_sold_date_sk INTEGER,{prefix}_{order} INTEGER,"
            f"{prefix}_quantity INTEGER,{prefix}_ext_sales_price DECIMAL(15,2)",
            sales_rows,
        )
        spec[returns] = (
            f"{rp}_item_sk INTEGER,{rp}_{order} INTEGER,{rp}_return_quantity INTEGER,{rp}_{amount} DECIMAL(15,2)",
            return_rows,
        )
        branches.append(
            f"SELECT d_year,i_brand_id,i_class_id,i_category_id,i_manufact_id,"
            f"{prefix}_quantity-COALESCE({rp}_return_quantity,0) AS sales_cnt,"
            f"{prefix}_ext_sales_price-COALESCE({rp}_{amount},0) AS sales_amt FROM {table} "
            f"JOIN item ON {prefix}_item_sk=i_item_sk JOIN date_dim ON {prefix}_sold_date_sk=d_date_sk "
            f"LEFT JOIN {returns} ON {prefix}_item_sk={rp}_item_sk AND {prefix}_{order}={rp}_{order}"
        )
    keys = "i_brand_id,i_class_id,i_category_id,i_manufact_id"
    sql = f"""
        WITH all_sales AS (
            SELECT d_year,{keys},SUM(sales_cnt) AS sales_cnt,SUM(sales_amt) AS sales_amt
            FROM ({" UNION ".join(branches)}) GROUP BY d_year,{keys}
        ) SELECT p.d_year,c.d_year,c.i_brand_id,c.i_class_id,c.i_category_id,c.i_manufact_id,
                 p.sales_cnt,c.sales_cnt,c.sales_cnt-p.sales_cnt AS count_diff,c.sales_amt-p.sales_amt AS amount_diff
          FROM all_sales c JOIN all_sales p USING ({keys})
          WHERE c.d_year=2001 AND p.d_year=2000 AND c.sales_cnt/p.sales_cnt<0.9
          ORDER BY count_diff,amount_diff
    """
    assert _check(monkeypatch, family, 75, {}, spec, sql, float_money=True) == [
        (2000, 2001, 1, 1, 1, 1, 10, 5, -5, expected_difference)
    ]


