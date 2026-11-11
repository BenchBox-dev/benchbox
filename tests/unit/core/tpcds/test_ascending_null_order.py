from __future__ import annotations

from datetime import date

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _build(spec):
    duckdb = pytest.importorskip("duckdb")
    pl = pytest.importorskip("polars")
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    connection = duckdb.connect()
    ctx = PolarsDataFrameAdapter().create_context()
    for name, (columns, rows) in spec.items():
        connection.execute(f"CREATE TABLE {name} ({columns})")
        width = len(columns.split(","))
        if rows:
            connection.executemany(f"INSERT INTO {name} VALUES ({', '.join(['?'] * width)})", rows)
        ctx.register_table(name, pl.from_arrow(connection.execute(f"SELECT * FROM {name}").to_arrow_table()).lazy())
    return connection, ctx


def _check(monkeypatch, impl_name, params, spec, sql, *, null_key_position=None, family="expression"):
    from benchbox.core.equivalence.dataframe_surface import fetch_reference_rows, materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: params)
    connection, ctx = _build(spec)
    if family == "pandas":
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name in spec:
            ctx.register_table(name, connection.execute(f"SELECT * FROM {name}").to_arrow_table().to_pandas())
    expected = fetch_reference_rows(connection, sql)
    rows = materialize_rows(getattr(queries, impl_name)(ctx))
    assert rows == expected
    if null_key_position is not None:
        assert any(None in row for row in expected)
    return expected


def test_default_ascending_sort_puts_a_null_key_last():
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    _connection, ctx = _build({"t": ("a INTEGER, b INTEGER", [(None, 1), (2, 2), (1, 3), (None, 0)])})
    rows = materialize_rows(queries._sort_null_largest_expression(ctx, ctx.get_table("t"), ["a", "b"]))
    assert rows == [(1, 3), (2, 2), (None, 0), (None, 1)]


def test_q6_orders_a_null_state_after_the_other_states_with_the_same_count(monkeypatch):
    spec = {
        "customer_address": ("ca_address_sk INTEGER, ca_state VARCHAR", [(1, None), (2, "TX")]),
        "customer": (
            "c_customer_sk INTEGER, c_current_addr_sk INTEGER",
            [(sk, 1 if sk <= 10 else 2) for sk in range(1, 21)],
        ),
        "date_dim": ("d_date_sk INTEGER, d_year INTEGER, d_moy INTEGER", [(1, 2001, 1)]),
        "item": ("i_item_sk INTEGER, i_category VARCHAR, i_current_price DOUBLE", [(1, "c", 100.0), (2, "c", 1.0)]),
        "store_sales": (
            "ss_customer_sk INTEGER, ss_sold_date_sk INTEGER, ss_item_sk INTEGER",
            [(sk, 1, 1) for sk in range(1, 21)],
        ),
    }
    sql = """
        SELECT a.ca_state AS state, COUNT(*) AS cnt
        FROM customer_address a, customer c, store_sales s, date_dim d, item i
        WHERE a.ca_address_sk = c.c_current_addr_sk AND c.c_customer_sk = s.ss_customer_sk
          AND s.ss_sold_date_sk = d.d_date_sk AND s.ss_item_sk = i.i_item_sk
          AND d.d_year = 2001 AND d.d_moy = 1
          AND i.i_current_price > 1.2 * (SELECT AVG(j.i_current_price) FROM item j WHERE j.i_category = i.i_category)
        GROUP BY a.ca_state HAVING COUNT(*) >= 10
        ORDER BY cnt, a.ca_state LIMIT 100"""
    _check(monkeypatch, "q6_expression_impl", {}, spec, sql, null_key_position="last")


def test_q43_orders_a_null_store_name_last(monkeypatch):
    days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    spec = {
        "store": (
            "s_store_sk INTEGER, s_store_id VARCHAR, s_store_name VARCHAR, s_gmt_offset DOUBLE",
            [(1, "A", None, -5.0), (2, "B", "x", -5.0)],
        ),
        "date_dim": (
            "d_date_sk INTEGER, d_year INTEGER, d_day_name VARCHAR",
            [(index + 1, 1998, day) for index, day in enumerate(days)],
        ),
        "store_sales": (
            "ss_sold_date_sk INTEGER, ss_store_sk INTEGER, ss_sales_price DOUBLE",
            [(day, store, float(day + store)) for day in range(1, 8) for store in (1, 2)],
        ),
    }
    cases = ", ".join(
        f"SUM(CASE WHEN d_day_name = '{day}' THEN ss_sales_price ELSE NULL END) AS {day[:3].lower()}_sales"
        for day in days
    )
    sql = f"""
        SELECT s_store_name, s_store_id, {cases}
        FROM date_dim, store_sales, store
        WHERE d_date_sk = ss_sold_date_sk AND s_store_sk = ss_store_sk AND s_gmt_offset = -5 AND d_year = 1998
        GROUP BY s_store_name, s_store_id
        ORDER BY s_store_name, s_store_id, sun_sales, mon_sales, tue_sales, wed_sales, thu_sales, fri_sales, sat_sales
        LIMIT 100"""
    _check(monkeypatch, "q43_expression_impl", {}, spec, sql, null_key_position="last")


_DEMOGRAPHICS = (
    "cd_demo_sk INTEGER, cd_gender VARCHAR, cd_marital_status VARCHAR, cd_education_status VARCHAR, "
    "cd_purchase_estimate INTEGER, cd_credit_rating VARCHAR, cd_dep_count INTEGER, "
    "cd_dep_employed_count INTEGER, cd_dep_college_count INTEGER"
)
_DEMOGRAPHIC_ROWS = [(1, None, "M", "College", 500, "Good", 1, 1, 1), (2, "F", "M", "College", 500, "Good", 1, 1, 1)]


def test_q10_orders_a_null_group_key_last(monkeypatch):
    spec = {
        "customer": (
            "c_customer_sk INTEGER, c_current_addr_sk INTEGER, c_current_cdemo_sk INTEGER",
            [(1, 1, 1), (2, 1, 2)],
        ),
        "customer_address": ("ca_address_sk INTEGER, ca_county VARCHAR", [(1, "Walker County")]),
        "customer_demographics": (_DEMOGRAPHICS, _DEMOGRAPHIC_ROWS),
        "date_dim": ("d_date_sk INTEGER, d_year INTEGER, d_moy INTEGER", [(1, 2002, 3)]),
        "store_sales": ("ss_customer_sk INTEGER, ss_sold_date_sk INTEGER", [(1, 1), (2, 1)]),
        "web_sales": ("ws_bill_customer_sk INTEGER, ws_sold_date_sk INTEGER", [(1, 1), (2, 1)]),
        "catalog_sales": ("cs_ship_customer_sk INTEGER, cs_sold_date_sk INTEGER", []),
    }
    params = {"year": 2002, "month": 2, "counties": ["Walker County"]}
    group = (
        "cd_gender, cd_marital_status, cd_education_status, cd_purchase_estimate, cd_credit_rating, "
        "cd_dep_count, cd_dep_employed_count, cd_dep_college_count"
    )
    sql = f"""
        SELECT cd_gender, cd_marital_status, cd_education_status, COUNT(*) AS cnt1, cd_purchase_estimate,
               COUNT(*) AS cnt2, cd_credit_rating, COUNT(*) AS cnt3, cd_dep_count, COUNT(*) AS cnt4,
               cd_dep_employed_count, COUNT(*) AS cnt5, cd_dep_college_count, COUNT(*) AS cnt6
        FROM customer c, customer_address ca, customer_demographics
        WHERE c.c_current_addr_sk = ca.ca_address_sk AND ca_county IN ('Walker County')
          AND cd_demo_sk = c.c_current_cdemo_sk
          AND EXISTS (SELECT * FROM store_sales, date_dim WHERE c.c_customer_sk = ss_customer_sk
                      AND ss_sold_date_sk = d_date_sk AND d_year = 2002 AND d_moy BETWEEN 2 AND 5)
          AND (EXISTS (SELECT * FROM web_sales, date_dim WHERE c.c_customer_sk = ws_bill_customer_sk
                       AND ws_sold_date_sk = d_date_sk AND d_year = 2002 AND d_moy BETWEEN 2 AND 5)
               OR EXISTS (SELECT * FROM catalog_sales, date_dim WHERE c.c_customer_sk = cs_ship_customer_sk
                          AND cs_sold_date_sk = d_date_sk AND d_year = 2002 AND d_moy BETWEEN 2 AND 5))
        GROUP BY {group} ORDER BY {group} LIMIT 100"""
    _check(monkeypatch, "q10_expression_impl", params, spec, sql, null_key_position="last")


def test_q69_orders_a_null_group_key_last(monkeypatch):
    spec = {
        "customer": (
            "c_customer_sk INTEGER, c_current_addr_sk INTEGER, c_current_cdemo_sk INTEGER",
            [(1, 1, 1), (2, 1, 2)],
        ),
        "customer_address": ("ca_address_sk INTEGER, ca_state VARCHAR", [(1, "KY")]),
        "customer_demographics": (_DEMOGRAPHICS, _DEMOGRAPHIC_ROWS),
        "date_dim": ("d_date_sk INTEGER, d_year INTEGER, d_moy INTEGER", [(1, 2001, 5)]),
        "store_sales": ("ss_customer_sk INTEGER, ss_sold_date_sk INTEGER", [(1, 1), (2, 1)]),
        "web_sales": ("ws_bill_customer_sk INTEGER, ws_sold_date_sk INTEGER", []),
        "catalog_sales": ("cs_ship_customer_sk INTEGER, cs_sold_date_sk INTEGER", []),
    }
    params = {"year": 2001, "month": 4, "states": ["KY", "GA", "NM"]}
    group = "cd_gender, cd_marital_status, cd_education_status, cd_purchase_estimate, cd_credit_rating"
    sql = f"""
        SELECT cd_gender, cd_marital_status, cd_education_status, COUNT(*) AS cnt1, cd_purchase_estimate,
               COUNT(*) AS cnt2, cd_credit_rating, COUNT(*) AS cnt3
        FROM customer c, customer_address ca, customer_demographics
        WHERE c.c_current_addr_sk = ca.ca_address_sk AND ca_state IN ('KY', 'GA', 'NM')
          AND cd_demo_sk = c.c_current_cdemo_sk
          AND EXISTS (SELECT * FROM store_sales, date_dim WHERE c.c_customer_sk = ss_customer_sk
                      AND ss_sold_date_sk = d_date_sk AND d_year = 2001 AND d_moy BETWEEN 4 AND 6)
          AND (NOT EXISTS (SELECT * FROM web_sales, date_dim WHERE c.c_customer_sk = ws_bill_customer_sk
                           AND ws_sold_date_sk = d_date_sk AND d_year = 2001 AND d_moy BETWEEN 4 AND 6)
               AND NOT EXISTS (SELECT * FROM catalog_sales, date_dim WHERE c.c_customer_sk = cs_ship_customer_sk
                               AND cs_sold_date_sk = d_date_sk AND d_year = 2001 AND d_moy BETWEEN 4 AND 6))
        GROUP BY {group} ORDER BY {group} LIMIT 100"""
    _check(monkeypatch, "q69_expression_impl", params, spec, sql, null_key_position="last")


def test_q40_orders_a_null_warehouse_state_last(monkeypatch):
    spec = {
        "catalog_sales": (
            "cs_order_number INTEGER, cs_item_sk INTEGER, cs_sold_date_sk INTEGER, cs_warehouse_sk INTEGER, "
            "cs_sales_price DOUBLE",
            [(1, 1, 1, 1, 10.0), (2, 1, 1, 2, 20.0)],
        ),
        "catalog_returns": (
            "cr_order_number INTEGER, cr_item_sk INTEGER, cr_refunded_cash DOUBLE",
            [(1, 1, 1.0)],
        ),
        "warehouse": ("w_warehouse_sk INTEGER, w_state VARCHAR", [(1, None), (2, "TX")]),
        "item": ("i_item_sk INTEGER, i_item_id VARCHAR, i_current_price DOUBLE", [(1, "I1", 1.0)]),
        "date_dim": ("d_date_sk INTEGER, d_date DATE", [(1, date(1998, 4, 10))]),
    }
    sql = """
        SELECT w_state, i_item_id,
               SUM(CASE WHEN d_date < DATE '1998-04-08' THEN cs_sales_price - COALESCE(cr_refunded_cash, 0)
                        ELSE 0 END) AS sales_before,
               SUM(CASE WHEN d_date >= DATE '1998-04-08' THEN cs_sales_price - COALESCE(cr_refunded_cash, 0)
                        ELSE 0 END) AS sales_after
        FROM catalog_sales LEFT OUTER JOIN catalog_returns
               ON (cs_order_number = cr_order_number AND cs_item_sk = cr_item_sk),
             warehouse, item, date_dim
        WHERE i_current_price BETWEEN 0.99 AND 1.49 AND i_item_sk = cs_item_sk
          AND cs_warehouse_sk = w_warehouse_sk AND cs_sold_date_sk = d_date_sk
          AND d_date BETWEEN DATE '1998-03-09' AND DATE '1998-05-08'
        GROUP BY w_state, i_item_id ORDER BY w_state, i_item_id LIMIT 100"""
    _check(monkeypatch, "q40_expression_impl", {"sales_date": "1998-04-08"}, spec, sql, null_key_position="last")


def test_q93_orders_a_null_customer_after_a_customer_with_the_same_sum(monkeypatch):
    spec = {
        "store_sales": (
            "ss_item_sk INTEGER, ss_ticket_number INTEGER, ss_customer_sk INTEGER, ss_quantity INTEGER, "
            "ss_sales_price DOUBLE",
            [(1, 1, None, 2, 5.0), (1, 2, 7, 2, 5.0)],
        ),
        "store_returns": (
            "sr_item_sk INTEGER, sr_ticket_number INTEGER, sr_reason_sk INTEGER, sr_return_quantity INTEGER",
            [(1, 1, 1, 1), (1, 2, 1, 1)],
        ),
        "reason": ("r_reason_sk INTEGER, r_reason_desc VARCHAR", [(1, "reason 28")]),
    }
    sql = """
        SELECT ss_customer_sk, SUM(act_sales) AS sumsales
        FROM (SELECT ss_item_sk, ss_ticket_number, ss_customer_sk,
                     CASE WHEN sr_return_quantity IS NOT NULL THEN (ss_quantity - sr_return_quantity) * ss_sales_price
                          ELSE ss_quantity * ss_sales_price END AS act_sales
              FROM store_sales LEFT OUTER JOIN store_returns
                     ON (sr_item_sk = ss_item_sk AND sr_ticket_number = ss_ticket_number), reason
              WHERE sr_reason_sk = r_reason_sk AND r_reason_desc = 'reason 28') AS t
        GROUP BY ss_customer_sk ORDER BY sumsales, ss_customer_sk LIMIT 100"""
    _check(monkeypatch, "q93_expression_impl", {"reason": "reason 28"}, spec, sql, null_key_position="last")


def test_q59_orders_a_null_store_name_last(monkeypatch):
    spec = {
        "store": (
            "s_store_sk INTEGER, s_store_id VARCHAR, s_store_name VARCHAR",
            [(1, "A", None), (2, "B", "x")],
        ),
        "date_dim": (
            "d_date_sk INTEGER, d_week_seq INTEGER, d_month_seq INTEGER, d_day_name VARCHAR",
            [(1, 100, 1212, "Sunday"), (2, 152, 1224, "Sunday")],
        ),
        "store_sales": (
            "ss_sold_date_sk INTEGER, ss_store_sk INTEGER, ss_sales_price DOUBLE",
            [(date_sk, store, 10.0 * store + date_sk) for date_sk in (1, 2) for store in (1, 2)],
        ),
    }
    days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    sums = ", ".join(
        f"SUM(CASE WHEN d_day_name = '{day}' THEN ss_sales_price ELSE NULL END) AS {day[:3].lower()}_sales"
        for day in days
    )
    ratios = ", ".join(f"{day[:3].lower()}_sales1 / {day[:3].lower()}_sales2" for day in days)
    first = ", ".join(f"{day[:3].lower()}_sales AS {day[:3].lower()}_sales1" for day in days)
    second = ", ".join(f"{day[:3].lower()}_sales AS {day[:3].lower()}_sales2" for day in days)
    sql = f"""
        WITH wss AS (SELECT d_week_seq, ss_store_sk, {sums}
                     FROM store_sales, date_dim WHERE d_date_sk = ss_sold_date_sk GROUP BY d_week_seq, ss_store_sk)
        SELECT s_store_name1, s_store_id1, d_week_seq1, {ratios}
        FROM (SELECT s_store_name AS s_store_name1, wss.d_week_seq AS d_week_seq1, s_store_id AS s_store_id1,
                     {first}
              FROM wss, store, date_dim d
              WHERE d.d_week_seq = wss.d_week_seq AND ss_store_sk = s_store_sk
                AND d_month_seq BETWEEN 1212 AND 1223) AS y,
             (SELECT s_store_name AS s_store_name2, wss.d_week_seq AS d_week_seq2, s_store_id AS s_store_id2,
                     {second}
              FROM wss, store, date_dim d
              WHERE d.d_week_seq = wss.d_week_seq AND ss_store_sk = s_store_sk
                AND d_month_seq BETWEEN 1224 AND 1235) AS x
        WHERE s_store_id1 = s_store_id2 AND d_week_seq1 = d_week_seq2 - 52
        ORDER BY s_store_name1, s_store_id1, d_week_seq1 LIMIT 100"""
    _check(monkeypatch, "q59_expression_impl", {"d_month_seq": 1212}, spec, sql, null_key_position="last")


_MANUFACTURER_SQL = """
    SELECT * FROM (
        SELECT {key}, SUM(ss_sales_price) AS sum_sales,
               AVG(SUM(ss_sales_price)) OVER (PARTITION BY {key}) AS {avg}
        FROM item, store_sales, date_dim, store
        WHERE ss_item_sk = i_item_sk AND ss_sold_date_sk = d_date_sk AND ss_store_sk = s_store_sk
          AND d_month_seq IN (1212, 1213, 1214, 1215, 1216, 1217, 1218, 1219, 1220, 1221, 1222, 1223)
          AND ((i_category IN ('Books', 'Children', 'Electronics')
                AND i_class IN ('personal', 'portable', 'reference', 'self-help')
                AND i_brand IN ('scholaramalgamalg #14', 'scholaramalgamalg #7', 'exportiunivamalg #9',
                                'scholaramalgamalg #9'))
               OR (i_category IN ('Women', 'Music', 'Men')
                   AND i_class IN ('accessories', 'classical', 'fragrances', 'pants')
                   AND i_brand IN ('amalgimporto #1', 'edu packscholar #1', 'exportiimporto #1',
                                   'importoamalg #1')))
        GROUP BY {key}, {period}) AS tmp1
    WHERE CASE WHEN {avg} > 0 THEN ABS(sum_sales - {avg}) / {avg} ELSE NULL END > 0.1
    ORDER BY {order} LIMIT 100"""


def _manufacturer_spec():
    item_columns = (
        "i_item_sk INTEGER, i_manufact_id INTEGER, i_manager_id INTEGER, "
        "i_category VARCHAR, i_class VARCHAR, i_brand VARCHAR"
    )
    books = ("Books", "personal", "scholaramalgamalg #14")
    return {
        "item": (item_columns, [(1, None, None, *books), (2, 2, 2, *books)]),
        "date_dim": (
            "d_date_sk INTEGER, d_month_seq INTEGER, d_qoy INTEGER, d_moy INTEGER",
            [(1, 1212, 1, 1), (2, 1213, 2, 2)],
        ),
        "store": ("s_store_sk INTEGER", [(1,)]),
        "store_sales": (
            "ss_item_sk INTEGER, ss_sold_date_sk INTEGER, ss_store_sk INTEGER, ss_sales_price DOUBLE",
            [(item, date_sk, 1, price) for item in (1, 2) for date_sk, price in ((1, 10.0), (2, 30.0))],
        ),
    }


def test_q53_orders_a_null_manufacturer_after_equal_sums(monkeypatch):
    sql = _MANUFACTURER_SQL.format(
        key="i_manufact_id",
        avg="avg_quarterly_sales",
        period="d_qoy",
        order="avg_quarterly_sales, sum_sales, i_manufact_id",
    ).replace("SELECT * FROM (", "SELECT i_manufact_id, sum_sales, avg_quarterly_sales FROM (", 1)
    _check(monkeypatch, "q53_expression_impl", {}, _manufacturer_spec(), sql, null_key_position="last")


def test_q63_orders_a_null_manager_last(monkeypatch):
    sql = _MANUFACTURER_SQL.format(
        key="i_manager_id",
        avg="avg_monthly_sales",
        period="d_moy",
        order="i_manager_id, avg_monthly_sales, sum_sales",
    ).replace("SELECT * FROM (", "SELECT i_manager_id, sum_sales, avg_monthly_sales FROM (", 1)
    _check(monkeypatch, "q63_expression_impl", {}, _manufacturer_spec(), sql, null_key_position="last")


def _store_catalog_spec(date_row):
    combos = [(1, 1), (1, 2), (2, 1), (2, 2)]
    return {
        "item": (
            "i_item_sk INTEGER, i_item_id VARCHAR, i_item_desc VARCHAR",
            [(1, "A", None), (2, "A", "x")],
        ),
        "store": (
            "s_store_sk INTEGER, s_store_id VARCHAR, s_store_name VARCHAR, s_state VARCHAR",
            [(1, "S", None, None), (2, "S", "n", "TX")],
        ),
        "date_dim": ("d_date_sk INTEGER, d_year INTEGER, d_moy INTEGER, d_quarter_name VARCHAR", [date_row]),
        "store_sales": (
            "ss_item_sk INTEGER, ss_store_sk INTEGER, ss_sold_date_sk INTEGER, ss_customer_sk INTEGER, "
            "ss_ticket_number INTEGER, ss_quantity INTEGER, ss_net_profit DOUBLE",
            [(item, store, 1, number, number, 3, 4.0) for number, (item, store) in enumerate(combos, 1)],
        ),
        "store_returns": (
            "sr_item_sk INTEGER, sr_customer_sk INTEGER, sr_ticket_number INTEGER, sr_returned_date_sk INTEGER, "
            "sr_return_quantity INTEGER, sr_net_loss DOUBLE",
            [(item, number, number, 1, 1, 2.0) for number, (item, _store) in enumerate(combos, 1)],
        ),
        "catalog_sales": (
            "cs_bill_customer_sk INTEGER, cs_item_sk INTEGER, cs_sold_date_sk INTEGER, cs_quantity INTEGER, "
            "cs_net_profit DOUBLE",
            [(number, item, 1, 5, 6.0) for number, (item, _store) in enumerate(combos, 1)],
        ),
    }


def test_q25_orders_a_null_item_description_and_store_name_last(monkeypatch):
    spec = _store_catalog_spec((1, 2000, 4, "2000Q2"))
    sql = """
        SELECT i_item_id, i_item_desc, s_store_id, s_store_name, SUM(ss_net_profit) AS store_sales_profit,
               SUM(sr_net_loss) AS store_returns_loss, SUM(cs_net_profit) AS catalog_sales_profit
        FROM store_sales, store_returns, catalog_sales, date_dim d1, date_dim d2, date_dim d3, store, item
        WHERE d1.d_moy = 4 AND d1.d_year = 2000 AND d1.d_date_sk = ss_sold_date_sk AND i_item_sk = ss_item_sk
          AND s_store_sk = ss_store_sk AND ss_customer_sk = sr_customer_sk AND ss_item_sk = sr_item_sk
          AND ss_ticket_number = sr_ticket_number AND sr_returned_date_sk = d2.d_date_sk
          AND d2.d_moy BETWEEN 4 AND 10 AND d2.d_year = 2000 AND sr_customer_sk = cs_bill_customer_sk
          AND sr_item_sk = cs_item_sk AND cs_sold_date_sk = d3.d_date_sk AND d3.d_moy BETWEEN 4 AND 10
          AND d3.d_year = 2000
        GROUP BY i_item_id, i_item_desc, s_store_id, s_store_name
        ORDER BY i_item_id, i_item_desc, s_store_id, s_store_name LIMIT 100"""
    _check(monkeypatch, "q25_expression_impl", {}, spec, sql, null_key_position="last")


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("null_quantity", [False, True])
def test_q29_orders_a_null_item_description_and_store_name_last(monkeypatch, family, null_quantity):
    spec = _store_catalog_spec((1, 1999, 1, "1999Q1"))
    if null_quantity:
        columns, rows = spec["store_sales"]
        spec["store_sales"] = (columns, [(*row[:5], None, row[6]) for row in rows])
    sql = """
        SELECT i_item_id, i_item_desc, s_store_id, s_store_name, SUM(ss_quantity) AS store_sales_quantity,
               SUM(sr_return_quantity) AS store_returns_quantity, SUM(cs_quantity) AS catalog_sales_quantity
        FROM store_sales, store_returns, catalog_sales, date_dim d1, date_dim d2, date_dim d3, store, item
        WHERE d1.d_moy = 1 AND d1.d_year = 1999 AND d1.d_date_sk = ss_sold_date_sk AND i_item_sk = ss_item_sk
          AND s_store_sk = ss_store_sk AND ss_customer_sk = sr_customer_sk AND ss_item_sk = sr_item_sk
          AND ss_ticket_number = sr_ticket_number AND sr_returned_date_sk = d2.d_date_sk
          AND d2.d_moy BETWEEN 1 AND 4 AND d2.d_year = 1999 AND sr_customer_sk = cs_bill_customer_sk
          AND sr_item_sk = cs_item_sk AND cs_sold_date_sk = d3.d_date_sk AND d3.d_year IN (1999, 2000, 2001)
        GROUP BY i_item_id, i_item_desc, s_store_id, s_store_name
        ORDER BY i_item_id, i_item_desc, s_store_id, s_store_name LIMIT 100"""
    _check(
        monkeypatch,
        f"q29_{family}_impl",
        {"year": 1999, "month": 1, "agg": "sum"},
        spec,
        sql,
        null_key_position="last",
        family=family,
    )


def test_q17_orders_a_null_item_description_and_state_last(monkeypatch):
    spec = _store_catalog_spec((1, 1998, 1, "1998Q1"))
    sql = """
        SELECT i_item_id, i_item_desc, s_state,
               COUNT(ss_quantity) AS store_sales_quantitycount, AVG(ss_quantity) AS store_sales_quantityave,
               STDDEV_SAMP(ss_quantity) AS store_sales_quantitystdev,
               STDDEV_SAMP(ss_quantity) / AVG(ss_quantity) AS store_sales_quantitycov,
               COUNT(sr_return_quantity) AS store_returns_quantitycount,
               AVG(sr_return_quantity) AS store_returns_quantityave,
               STDDEV_SAMP(sr_return_quantity) AS store_returns_quantitystdev,
               STDDEV_SAMP(sr_return_quantity) / AVG(sr_return_quantity) AS store_returns_quantitycov,
               COUNT(cs_quantity) AS catalog_sales_quantitycount, AVG(cs_quantity) AS catalog_sales_quantityave,
               STDDEV_SAMP(cs_quantity) AS catalog_sales_quantitystdev,
               STDDEV_SAMP(cs_quantity) / AVG(cs_quantity) AS catalog_sales_quantitycov
        FROM store_sales, store_returns, catalog_sales, date_dim d1, date_dim d2, date_dim d3, store, item
        WHERE d1.d_quarter_name = '1998Q1' AND d1.d_date_sk = ss_sold_date_sk AND i_item_sk = ss_item_sk
          AND s_store_sk = ss_store_sk AND ss_customer_sk = sr_customer_sk AND ss_item_sk = sr_item_sk
          AND ss_ticket_number = sr_ticket_number AND sr_returned_date_sk = d2.d_date_sk
          AND d2.d_quarter_name IN ('1998Q1', '1998Q2', '1998Q3') AND sr_customer_sk = cs_bill_customer_sk
          AND sr_item_sk = cs_item_sk AND cs_sold_date_sk = d3.d_date_sk
          AND d3.d_quarter_name IN ('1998Q1', '1998Q2', '1998Q3')
        GROUP BY i_item_id, i_item_desc, s_state
        ORDER BY i_item_id, i_item_desc, s_state LIMIT 100"""
    _check(monkeypatch, "q17_expression_impl", {"year": 1998, "quarter": 1}, spec, sql, null_key_position="last")


def test_q8_orders_a_null_store_name_last(monkeypatch):
    spec = {
        "customer_address": (
            "ca_address_sk INTEGER, ca_zip VARCHAR",
            [(sk, "12345") for sk in range(1, 12)],
        ),
        "customer": (
            "c_current_addr_sk INTEGER, c_preferred_cust_flag VARCHAR",
            [(sk, "Y") for sk in range(1, 12)],
        ),
        "store": (
            "s_store_sk INTEGER, s_store_name VARCHAR, s_zip VARCHAR",
            [(1, None, "12000"), (2, "x", "12999")],
        ),
        "date_dim": ("d_date_sk INTEGER, d_qoy INTEGER, d_year INTEGER", [(1, 1, 1998)]),
        "store_sales": (
            "ss_sold_date_sk INTEGER, ss_store_sk INTEGER, ss_net_profit DOUBLE",
            [(1, 1, 5.0), (1, 2, 6.0)],
        ),
    }
    sql = """
        SELECT s_store_name, SUM(ss_net_profit)
        FROM store_sales, date_dim, store,
             (SELECT ca_zip FROM (
                  SELECT SUBSTRING(ca_zip, 1, 5) AS ca_zip FROM customer_address
                  WHERE SUBSTRING(ca_zip, 1, 5) IN ('12345')
                  INTERSECT
                  SELECT ca_zip FROM (
                      SELECT SUBSTRING(ca_zip, 1, 5) AS ca_zip, COUNT(*) AS cnt
                      FROM customer_address, customer
                      WHERE ca_address_sk = c_current_addr_sk AND c_preferred_cust_flag = 'Y'
                      GROUP BY ca_zip HAVING COUNT(*) > 10) AS A1) AS A2) AS V1
        WHERE ss_store_sk = s_store_sk AND ss_sold_date_sk = d_date_sk AND d_qoy = 1 AND d_year = 1998
          AND SUBSTRING(s_zip, 1, 2) = SUBSTRING(V1.ca_zip, 1, 2)
        GROUP BY s_store_name ORDER BY s_store_name LIMIT 100"""
    params = {"year": 1998, "qoy": 1, "zip_codes": ["12345"]}
    _check(monkeypatch, "q8_expression_impl", params, spec, sql, null_key_position="last")


def test_q24_orders_a_null_customer_name_and_store_name_last(monkeypatch):
    spec = {
        "store": (
            "s_store_sk INTEGER, s_store_name VARCHAR, s_state VARCHAR, s_zip VARCHAR, s_market_id INTEGER",
            [(1, None, "TX", "z", 7), (2, "n", "TX", "z", 7)],
        ),
        "item": (
            "i_item_sk INTEGER, i_color VARCHAR, i_current_price DOUBLE, i_manager_id INTEGER, "
            "i_units VARCHAR, i_size VARCHAR",
            [(1, "orchid", 1.0, 1, "u", "s")],
        ),
        "customer": (
            "c_customer_sk INTEGER, c_last_name VARCHAR, c_first_name VARCHAR, c_birth_country VARCHAR, "
            "c_current_addr_sk INTEGER",
            [(1, None, "F", "X", 1), (2, "L", "F", "X", 1), (3, "L", "F", "X", 1)],
        ),
        "customer_address": (
            "ca_address_sk INTEGER, ca_state VARCHAR, ca_country VARCHAR, ca_zip VARCHAR",
            [(1, "TX", "usa", "z")],
        ),
        "store_sales": (
            "ss_ticket_number INTEGER, ss_item_sk INTEGER, ss_customer_sk INTEGER, ss_store_sk INTEGER, "
            "ss_sales_price DOUBLE",
            [(1, 1, 1, 2, 10.0), (2, 1, 2, 1, 10.0), (3, 1, 3, 2, 10.0)],
        ),
        "store_returns": ("sr_ticket_number INTEGER, sr_item_sk INTEGER", [(1, 1), (2, 1), (3, 1)]),
    }
    sql = """
        WITH ssales AS (
            SELECT c_last_name, c_first_name, s_store_name, ca_state, s_state, i_color, i_current_price,
                   i_manager_id, i_units, i_size, SUM(ss_sales_price) AS netpaid
            FROM store_sales, store_returns, store, item, customer, customer_address
            WHERE ss_ticket_number = sr_ticket_number AND ss_item_sk = sr_item_sk AND ss_customer_sk = c_customer_sk
              AND ss_item_sk = i_item_sk AND ss_store_sk = s_store_sk AND c_current_addr_sk = ca_address_sk
              AND c_birth_country <> UPPER(ca_country) AND s_zip = ca_zip AND s_market_id = 7
            GROUP BY c_last_name, c_first_name, s_store_name, ca_state, s_state, i_color, i_current_price,
                     i_manager_id, i_units, i_size)
        SELECT c_last_name, c_first_name, s_store_name, SUM(netpaid) AS paid
        FROM ssales WHERE i_color = 'orchid'
        GROUP BY c_last_name, c_first_name, s_store_name
        HAVING SUM(netpaid) > (SELECT 0.05 * AVG(netpaid) FROM ssales)
        ORDER BY c_last_name, c_first_name, s_store_name"""
    _check(monkeypatch, "q24_expression_impl", {}, spec, sql, null_key_position="last")


def test_q85_orders_a_null_reason_last(monkeypatch):
    spec = {
        "web_sales": (
            "ws_item_sk INTEGER, ws_order_number INTEGER, ws_web_page_sk INTEGER, ws_sold_date_sk INTEGER, "
            "ws_quantity INTEGER, ws_sales_price DOUBLE, ws_net_profit DOUBLE",
            [(1, 1, 1, 1, 3, 120.0, 150.0), (1, 2, 1, 1, 3, 120.0, 150.0)],
        ),
        "web_returns": (
            "wr_item_sk INTEGER, wr_order_number INTEGER, wr_refunded_cdemo_sk INTEGER, "
            "wr_returning_cdemo_sk INTEGER, wr_refunded_addr_sk INTEGER, wr_reason_sk INTEGER, "
            "wr_refunded_cash DOUBLE, wr_fee DOUBLE",
            [(1, 1, 1, 1, 1, 1, 7.0, 1.0), (1, 2, 1, 1, 1, 2, 7.0, 1.0)],
        ),
        "web_page": ("wp_web_page_sk INTEGER", [(1,)]),
        "customer_demographics": (
            "cd_demo_sk INTEGER, cd_marital_status VARCHAR, cd_education_status VARCHAR",
            [(1, "M", "4 yr Degree")],
        ),
        "customer_address": (
            "ca_address_sk INTEGER, ca_country VARCHAR, ca_state VARCHAR",
            [(1, "United States", "KY")],
        ),
        "date_dim": ("d_date_sk INTEGER, d_year INTEGER", [(1, 1998)]),
        "reason": ("r_reason_sk INTEGER, r_reason_desc VARCHAR", [(1, None), (2, "reason x")]),
    }
    sql = """
        SELECT SUBSTRING(r_reason_desc, 1, 20), AVG(ws_quantity), AVG(wr_refunded_cash), AVG(wr_fee)
        FROM web_sales, web_returns, web_page, customer_demographics cd1, customer_demographics cd2,
             customer_address, date_dim, reason
        WHERE ws_web_page_sk = wp_web_page_sk AND ws_item_sk = wr_item_sk AND ws_order_number = wr_order_number
          AND ws_sold_date_sk = d_date_sk AND d_year = 1998 AND cd1.cd_demo_sk = wr_refunded_cdemo_sk
          AND cd2.cd_demo_sk = wr_returning_cdemo_sk AND ca_address_sk = wr_refunded_addr_sk
          AND r_reason_sk = wr_reason_sk
          AND ((cd1.cd_marital_status = 'M' AND cd1.cd_marital_status = cd2.cd_marital_status
                AND cd1.cd_education_status = '4 yr Degree' AND cd1.cd_education_status = cd2.cd_education_status
                AND ws_sales_price BETWEEN 100.00 AND 150.00)
               OR (cd1.cd_marital_status = 'D' AND cd1.cd_marital_status = cd2.cd_marital_status
                   AND cd1.cd_education_status = 'Primary' AND cd1.cd_education_status = cd2.cd_education_status
                   AND ws_sales_price BETWEEN 50.00 AND 100.00)
               OR (cd1.cd_marital_status = 'U' AND cd1.cd_marital_status = cd2.cd_marital_status
                   AND cd1.cd_education_status = 'Advanced Degree'
                   AND cd1.cd_education_status = cd2.cd_education_status
                   AND ws_sales_price BETWEEN 150.00 AND 200.00))
          AND ((ca_country = 'United States' AND ca_state IN ('KY', 'GA', 'NM') AND ws_net_profit BETWEEN 100 AND 200)
               OR (ca_country = 'United States' AND ca_state IN ('MT', 'OR', 'IN')
                   AND ws_net_profit BETWEEN 150 AND 300)
               OR (ca_country = 'United States' AND ca_state IN ('WI', 'MO', 'WV')
                   AND ws_net_profit BETWEEN 50 AND 250))
        GROUP BY r_reason_desc
        ORDER BY SUBSTRING(r_reason_desc, 1, 20), AVG(ws_quantity), AVG(wr_refunded_cash), AVG(wr_fee) LIMIT 100"""
    _check(monkeypatch, "q85_expression_impl", {}, spec, sql, null_key_position="last")


def _q64_fixture():
    sales = [
        (item, item * 10 + date_sk, 1, 1, 1, 1, 1, 1, date_sk, 2.0, 3.0, 1.0) for item in (1, 2) for date_sk in (1, 2)
    ]
    spec = {
        "store_sales": (
            "ss_item_sk INTEGER, ss_ticket_number INTEGER, ss_store_sk INTEGER, ss_customer_sk INTEGER, "
            "ss_cdemo_sk INTEGER, ss_hdemo_sk INTEGER, ss_addr_sk INTEGER, ss_promo_sk INTEGER, "
            "ss_sold_date_sk INTEGER, ss_wholesale_cost DOUBLE, ss_list_price DOUBLE, ss_coupon_amt DOUBLE",
            sales,
        ),
        "store_returns": (
            "sr_item_sk INTEGER, sr_ticket_number INTEGER",
            [(row[0], row[1]) for row in sales],
        ),
        "catalog_sales": (
            "cs_item_sk INTEGER, cs_order_number INTEGER, cs_ext_list_price DOUBLE",
            [(1, 1, 100.0), (2, 2, 100.0)],
        ),
        "catalog_returns": (
            "cr_item_sk INTEGER, cr_order_number INTEGER, cr_refunded_cash DOUBLE, "
            "cr_reversed_charge DOUBLE, cr_store_credit DOUBLE",
            [(1, 1, 1.0, 1.0, 1.0), (2, 2, 1.0, 1.0, 1.0)],
        ),
        "date_dim": ("d_date_sk INTEGER, d_year INTEGER", [(1, 1999), (2, 2000)]),
        "store": ("s_store_sk INTEGER, s_store_name VARCHAR, s_zip VARCHAR", [(1, "st", "z")]),
        "customer": (
            "c_customer_sk INTEGER, c_current_cdemo_sk INTEGER, c_current_hdemo_sk INTEGER, "
            "c_current_addr_sk INTEGER, c_first_sales_date_sk INTEGER, c_first_shipto_date_sk INTEGER",
            [(1, 2, 1, 1, 1, 1)],
        ),
        "customer_demographics": (
            "cd_demo_sk INTEGER, cd_marital_status VARCHAR",
            [(1, "M"), (2, "S")],
        ),
        "promotion": ("p_promo_sk INTEGER", [(1,)]),
        "household_demographics": ("hd_demo_sk INTEGER, hd_income_band_sk INTEGER", [(1, 1)]),
        "customer_address": (
            "ca_address_sk INTEGER, ca_street_number VARCHAR, ca_street_name VARCHAR, ca_city VARCHAR, ca_zip VARCHAR",
            [(1, "1", "Main", "Town", "z")],
        ),
        "income_band": ("ib_income_band_sk INTEGER", [(1,)]),
        "item": (
            "i_item_sk INTEGER, i_product_name VARCHAR, i_color VARCHAR, i_current_price DOUBLE",
            [(1, None, "slate", 5.0), (2, "p", "slate", 5.0)],
        ),
    }
    sql = """
        WITH cs_ui AS (
            SELECT cs_item_sk, SUM(cs_ext_list_price) AS sale,
                   SUM(cr_refunded_cash + cr_reversed_charge + cr_store_credit) AS refund
            FROM catalog_sales, catalog_returns
            WHERE cs_item_sk = cr_item_sk AND cs_order_number = cr_order_number
            GROUP BY cs_item_sk
            HAVING SUM(cs_ext_list_price) > 2 * SUM(cr_refunded_cash + cr_reversed_charge + cr_store_credit)),
        cross_sales AS (
            SELECT i_product_name AS product_name, i_item_sk AS item_sk, s_store_name AS store_name,
                   s_zip AS store_zip, ad1.ca_street_number AS b_street_number, ad1.ca_street_name AS b_street_name,
                   ad1.ca_city AS b_city, ad1.ca_zip AS b_zip, ad2.ca_street_number AS c_street_number,
                   ad2.ca_street_name AS c_street_name, ad2.ca_city AS c_city, ad2.ca_zip AS c_zip,
                   d1.d_year AS syear, d2.d_year AS fsyear, d3.d_year AS s2year, COUNT(*) AS cnt,
                   SUM(ss_wholesale_cost) AS s1, SUM(ss_list_price) AS s2, SUM(ss_coupon_amt) AS s3
            FROM store_sales, store_returns, cs_ui, date_dim d1, date_dim d2, date_dim d3, store, customer,
                 customer_demographics cd1, customer_demographics cd2, promotion, household_demographics hd1,
                 household_demographics hd2, customer_address ad1, customer_address ad2, income_band ib1,
                 income_band ib2, item
            WHERE ss_store_sk = s_store_sk AND ss_sold_date_sk = d1.d_date_sk AND ss_customer_sk = c_customer_sk
              AND ss_cdemo_sk = cd1.cd_demo_sk AND ss_hdemo_sk = hd1.hd_demo_sk AND ss_addr_sk = ad1.ca_address_sk
              AND ss_item_sk = i_item_sk AND ss_item_sk = sr_item_sk AND ss_ticket_number = sr_ticket_number
              AND ss_item_sk = cs_ui.cs_item_sk AND c_current_cdemo_sk = cd2.cd_demo_sk
              AND c_current_hdemo_sk = hd2.hd_demo_sk AND c_current_addr_sk = ad2.ca_address_sk
              AND c_first_sales_date_sk = d2.d_date_sk AND c_first_shipto_date_sk = d3.d_date_sk
              AND ss_promo_sk = p_promo_sk AND hd1.hd_income_band_sk = ib1.ib_income_band_sk
              AND hd2.hd_income_band_sk = ib2.ib_income_band_sk AND cd1.cd_marital_status <> cd2.cd_marital_status
              AND i_color IN ('slate') AND i_current_price BETWEEN 0 AND 10 AND i_current_price BETWEEN 1 AND 15
            GROUP BY i_product_name, i_item_sk, s_store_name, s_zip, ad1.ca_street_number, ad1.ca_street_name,
                     ad1.ca_city, ad1.ca_zip, ad2.ca_street_number, ad2.ca_street_name, ad2.ca_city, ad2.ca_zip,
                     d1.d_year, d2.d_year, d3.d_year)
        SELECT cs1.product_name, cs1.store_name, cs1.store_zip, cs1.b_street_number, cs1.b_street_name,
               cs1.b_city, cs1.b_zip, cs1.c_street_number, cs1.c_street_name, cs1.c_city, cs1.c_zip, cs1.syear,
               cs1.cnt, cs1.s1 AS s11, cs1.s2 AS s21, cs1.s3 AS s31, cs2.s1 AS s12, cs2.s2 AS s22, cs2.s3 AS s32,
               cs2.syear, cs2.cnt
        FROM cross_sales cs1, cross_sales cs2
        WHERE cs1.item_sk = cs2.item_sk AND cs1.syear = 1999 AND cs2.syear = 2000 AND cs2.cnt <= cs1.cnt
          AND cs1.store_name = cs2.store_name AND cs1.store_zip = cs2.store_zip
        ORDER BY cs1.product_name, cs1.store_name, cs2.cnt, cs1.s1, cs2.s1"""
    params = {"year": 1999, "colors": ["slate"], "price_min": 0}
    return spec, sql, params


def test_q64_orders_a_null_product_name_last(monkeypatch):
    spec, sql, params = _q64_fixture()
    _check(monkeypatch, "q64_expression_impl", params, spec, sql, null_key_position="last")


