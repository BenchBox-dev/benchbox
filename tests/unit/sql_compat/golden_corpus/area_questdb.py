from __future__ import annotations

from functools import lru_cache

from benchbox.utils.dialect_utils import translate_sql_query
from tests.unit.sql_compat.golden_corpus import Case, frozen_legacy

AREA = "area_questdb"

SOURCE_DIALECT = "netezza"
TPCH_SEED = 1

REWRITER_INPUTS: dict[str, str] = {
    "explicit_join": "SELECT l.x FROM lineitem AS l JOIN orders AS o ON l.id = o.id WHERE l.qty > 5",
    "single_table": "SELECT * FROM lineitem WHERE l_quantity > 10",
    "two_table_comma": "SELECT l.x, o.y FROM lineitem l, orders o WHERE l.l_orderkey = o.o_orderkey AND l.quantity > 10",
    "two_table_filter": "SELECT a.x, b.y FROM a, b WHERE a.x = b.x AND a.z > 1",
    "two_table_only_join_pred": "SELECT a.x FROM a, b WHERE a.id = b.id",
    "two_table_no_filter": "SELECT l.x, o.y FROM lineitem l, orders o WHERE l.l_orderkey = o.o_orderkey",
    "predicateless_cross": "SELECT * FROM a, b",
    "unconnected_cross": "SELECT * FROM a, b WHERE a.val > 5",
    "three_table_comma": (
        "SELECT l.l_orderkey, o.o_orderdate, c.c_name "
        "FROM lineitem l, orders o, customer c "
        "WHERE l.l_orderkey = o.o_orderkey AND o.o_custkey = c.c_custkey AND l.l_quantity > 10"
    ),
    "three_table_valid": (
        "SELECT l.x, o.y, c.z "
        "FROM lineitem l, orders o, customer c "
        "WHERE l.l_orderkey = o.o_orderkey AND o.o_custkey = c.c_custkey"
    ),
    "mixed_explicit_implicit": "SELECT a.x, b.y, c.z FROM a JOIN b ON a.bid = b.id, c WHERE b.cid = c.id AND a.val > 5",
    "subquery_in": "SELECT t.x FROM t WHERE t.id IN (SELECT a.id FROM a, b WHERE a.bid = b.id)",
    "subquery_exists": (
        "SELECT t.x FROM t WHERE EXISTS("
        "SELECT * FROM store_sales, date_dim "
        "WHERE ss_sold_date_sk = d_date_sk AND d_year = 2002)"
    ),
    "outer_and_inner_comma": (
        "SELECT p.p_name, s.s_name "
        "FROM part p, supplier s "
        "WHERE p.p_suppkey = s.s_suppkey "
        "  AND p.p_size > ("
        "    SELECT AVG(ps.ps_supplycost) FROM partsupp ps, supplier s2 "
        "    WHERE ps.ps_suppkey = s2.s_suppkey"
        "  )"
    ),
    "cte_comma_join": (
        "WITH ctr AS ("
        "  SELECT sr_customer_sk, SUM(sr_return_amt) AS ctr_total_return"
        "  FROM store_returns, date_dim"
        "  WHERE sr_returned_date_sk = d_date_sk AND d_year = 2000"
        "  GROUP BY sr_customer_sk"
        ")"
        " SELECT * FROM ctr WHERE ctr_total_return > 1000"
    ),
    "multiple_ctes": (
        "WITH cte1 AS ("
        "  SELECT a.x FROM t1 a, t2 b WHERE a.id = b.id"
        "),"
        " cte2 AS ("
        "  SELECT c.x FROM t3 c, t4 d WHERE c.id = d.id"
        ")"
        " SELECT * FROM cte1 JOIN cte2 ON cte1.x = cte2.x"
    ),
    "cte_and_outer": (
        "WITH cte AS (  SELECT a.x FROM t1 a, t2 b WHERE a.id = b.id) SELECT c.x FROM cte c, t3 e WHERE c.x = e.x"
    ),
    "tpch_q3_pattern": (
        "SELECT l.l_orderkey, SUM(l.l_extendedprice*(1-l.l_discount)) AS revenue "
        "FROM customer c, orders o, lineitem l "
        "WHERE c.c_mktsegment = 'BUILDING' "
        "  AND c.c_custkey = o.o_custkey "
        "  AND l.l_orderkey = o.o_orderkey "
        "  AND o.o_orderdate < DATE '1995-03-15' "
        "  AND l.l_shipdate > DATE '1995-03-15' "
        "GROUP BY l.l_orderkey, o.o_orderdate "
        "ORDER BY revenue DESC, o.o_orderdate LIMIT 10"
    ),
    "tpch_q5_pattern": (
        "SELECT n.n_name, SUM(l.l_extendedprice*(1-l.l_discount)) AS revenue "
        "FROM customer c, orders o, lineitem l, supplier s, nation n "
        "WHERE c.c_custkey = o.o_custkey "
        "  AND l.l_orderkey = o.o_orderkey "
        "  AND l.l_suppkey = s.s_suppkey "
        "  AND c.c_nationkey = s.s_nationkey "
        "  AND s.s_nationkey = n.n_nationkey "
        "  AND n.n_name = 'FRANCE' "
        "GROUP BY n.n_name ORDER BY revenue DESC"
    ),
    "interval_cast_minus_day": "SELECT CAST('1998-12-01' AS DATE) - INTERVAL '90' DAY AS d FROM x",
    "interval_cast_plus_day": "SELECT CAST('1995-03-15' AS DATE) + INTERVAL '30' DAY FROM x",
    "interval_plus_month_col": "SELECT col + INTERVAL '3' MONTH FROM x",
    "interval_minus_year_col": "SELECT col - INTERVAL '1' YEAR FROM x",
    "interval_inline_unit_form": "SELECT col + INTERVAL '3 MONTH' FROM x",
    "interval_inline_day_form": "SELECT col - INTERVAL '7 DAY' FROM x",
    "interval_week_unsupported_unit": "SELECT col + INTERVAL '1' WEEK FROM x",
    "interval_hour_unsupported_unit": "SELECT col + INTERVAL '2' HOUR FROM x",
    "no_interval": "SELECT * FROM x WHERE x.qty > 5",
    "tpch_q1_interval_pattern": (
        "SELECT l_returnflag FROM lineitem WHERE l_shipdate <= CAST('1998-12-01' AS DATE) - INTERVAL '90' DAY"
    ),
    "interval_ansi_date_literal_day": (
        "SELECT l_returnflag FROM lineitem WHERE l_shipdate <= date '1998-12-01' - interval '90' day"
    ),
    "interval_ansi_date_literal_year": (
        "SELECT sum(x) FROM lineitem WHERE l_shipdate < date '1994-01-01' + interval '1' year"
    ),
    "interval_ansi_date_literal_month": (
        "SELECT o FROM orders WHERE o_orderdate < date '1993-07-01' + interval '3' month"
    ),
    "interval_month_end_literal": "SELECT date '1995-01-31' + interval '1' month",
    "interval_leap_day_year": "SELECT date '1996-02-29' + interval '1' year",
    "interval_nested_expression": "SELECT (d + INTERVAL '1' DAY) + INTERVAL '2' DAY FROM x",
    "substring_from_for": "SELECT SUBSTRING(c_phone FROM 1 FOR 2) FROM customer",
    "substring_from_only": "SELECT SUBSTRING(c_phone FROM 3) FROM customer",
    "substring_comma_form": "SELECT SUBSTRING(c_phone, 1, 2) FROM customer",
    "substring_q22_pattern": (
        "SELECT SUBSTRING(c_phone FROM 1 FOR 2) AS cntrycode FROM customer "
        "WHERE SUBSTRING(c_phone FROM 1 FOR 2) IN ('13', '17')"
    ),
    "substring_expression_arg": "SELECT SUBSTRING(UPPER(c_name) FROM 2 FOR 5) FROM customer",
    "no_substring": "SELECT c_name FROM customer",
    "cte_column_list": (
        "WITH revenue (supplier_no, total) AS (SELECT s_suppkey, SUM(x) FROM s GROUP BY s_suppkey) SELECT * FROM revenue"
    ),
    "cte_no_column_list": "WITH revenue AS (SELECT s_suppkey FROM s) SELECT * FROM revenue",
    "cte_q15_pattern": (
        "WITH revenue0 (supplier_no, total_revenue) AS (SELECT l_suppkey, SUM(l_extendedprice) FROM lineitem "
        "GROUP BY l_suppkey) SELECT s_suppkey FROM supplier, revenue0 WHERE s_suppkey = revenue0.supplier_no"
    ),
    "cte_column_list_two_ctes": ("WITH a (x, y) AS (SELECT 1, 2), b (z) AS (SELECT 3) SELECT * FROM a, b"),
    "composition_plain": "SELECT l_orderkey FROM lineitem WHERE l_quantity > 10",
    "composition_interval": "SELECT CAST('1998-12-01' AS DATE) - INTERVAL '90' DAY",
    "composition_cte_column_list": (
        "WITH revenue0 (supplier_no, total_revenue) AS (SELECT s_suppkey, 1.0 FROM partsupp) SELECT * FROM revenue0"
    ),
    "composition_comma_join": (
        "SELECT l.l_orderkey FROM lineitem l, orders o WHERE l.l_orderkey = o.o_orderkey AND l.l_quantity > 5"
    ),
    "composition_multi_stage": (
        "WITH rev (sno, total) AS (SELECT s_suppkey, 1.0 FROM partsupp) "
        "SELECT SUBSTRING(c_phone FROM 1 FOR 2) FROM customer c, orders o "
        "WHERE c.c_custkey = o.o_custkey"
    ),
    "composition_interval_with_join": (
        "SELECT l.l_orderkey FROM lineitem l, orders o "
        "WHERE l.l_orderkey = o.o_orderkey AND o.o_orderdate > "
        "CAST('1998-12-01' AS DATE) - INTERVAL '90' DAY"
    ),
    "adapter_select_test": "SELECT * FROM test",
    "adapter_select_where": "SELECT * FROM test WHERE id > 5",
    "adapter_select_one": "SELECT 1",
    "adapter_select_lineitem": "SELECT * FROM lineitem",
    "string_literal_comma": "SELECT 'hello, world' FROM x WHERE x.id = 1",
}


@lru_cache(maxsize=1)
def tpch_queries() -> dict[int, str]:
    from benchbox.core.tpch.queries import TPCHQueries

    manager = TPCHQueries()
    return {number: manager.get_query(number, seed=TPCH_SEED, scale_factor=1.0) for number in range(1, 23)}


def fork_output(sql: str, identify: bool) -> str:
    return translate_sql_query(sql, "questdb", source_dialect=SOURCE_DIALECT, identify=identify)


def cases() -> list[Case]:
    inputs: list[tuple[str, str]] = [(f"rewriter.{name}", sql) for name, sql in REWRITER_INPUTS.items()]
    inputs += [(f"tpch.q{number}", sql) for number, sql in tpch_queries().items()]
    found: list[Case] = []
    for case_id, sql in inputs:
        for identify in (True, False):
            suffix = "identify" if identify else "plain"
            found.append(
                Case(
                    id=f"{case_id}.{suffix}",
                    legacy=frozen_legacy(AREA, f"{case_id}.{suffix}"),
                    fork=lambda sql=sql, identify=identify: fork_output(sql, identify),
                )
            )
    return found
