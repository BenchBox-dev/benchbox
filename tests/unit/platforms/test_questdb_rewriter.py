# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file for details.

from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewriter import (
    _build_and,
    _flatten_and,
    _has_comma_join,
    rewrite,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestHasCommaJoin:
    def test_detects_simple_comma_join(self):
        assert _has_comma_join("SELECT * FROM a, b WHERE a.x = b.x")

    def test_no_false_negative_with_aliases(self):
        assert _has_comma_join("SELECT * FROM lineitem l, orders o WHERE l.x = o.x")

    def test_ignores_commas_in_string_literals(self):
        sql = "SELECT 'hello, world' FROM x WHERE x.id = 1"
        _has_comma_join(sql)

    def test_no_match_for_explicit_join(self):
        _has_comma_join("SELECT * FROM lineitem AS l JOIN orders AS o ON l.id = o.id")


class TestPassthrough:
    def test_explicit_join_unchanged(self):
        sql = "SELECT l.x FROM lineitem AS l JOIN orders AS o ON l.id = o.id WHERE l.qty > 5"
        assert rewrite(sql) == sql

    def test_single_table_unchanged(self):
        sql = "SELECT * FROM lineitem WHERE l_quantity > 10"
        assert rewrite(sql) == sql

    def test_idempotent(self):
        sql = "SELECT l.x, o.y FROM lineitem l, orders o WHERE l.l_orderkey = o.o_orderkey AND l.qty > 1"
        once = rewrite(sql)
        twice = rewrite(once)
        assert once == twice


class TestTwoTableCommaJoin:
    def test_basic_aliased(self):
        sql = "SELECT l.x, o.y FROM lineitem l, orders o WHERE l.l_orderkey = o.o_orderkey AND l.quantity > 10"
        result = rewrite(sql)
        assert "JOIN" in result.upper()
        assert ", orders" not in result and ", lineitem" not in result
        assert "l.l_orderkey = o.o_orderkey" in result or "ON" in result.upper()
        assert "WHERE" in result.upper()

    def test_filter_separated_from_join_predicate(self):
        sql = "SELECT a.x, b.y FROM a, b WHERE a.x = b.x AND a.z > 1"
        result = rewrite(sql)
        assert "JOIN" in result.upper()
        assert "a.z > 1" in result or "WHERE" in result.upper()

    def test_join_predicate_removed_from_where(self):
        sql = "SELECT a.x, b.y FROM a, b WHERE a.x = b.x AND a.z > 1"
        result = rewrite(sql)
        on_idx = result.upper().index("ON")
        where_idx = result.upper().index("WHERE") if "WHERE" in result.upper() else len(result)
        assert on_idx < where_idx

    def test_only_join_predicate_no_filter(self):
        sql = "SELECT a.x FROM a, b WHERE a.id = b.id"
        result = rewrite(sql)
        assert "JOIN" in result.upper()
        assert "WHERE" not in result.upper()

    def test_result_is_valid_sql(self):
        from benchbox._vendor import sqlglot

        sql = "SELECT l.x, o.y FROM lineitem l, orders o WHERE l.l_orderkey = o.o_orderkey"
        result = rewrite(sql)
        sqlglot.parse_one(result, dialect="postgres")

    def test_predicateless_comma_join_becomes_cross_join(self):
        sql = "SELECT * FROM a, b"
        result = rewrite(sql)
        assert "," not in result.partition("FROM")[2]
        assert "CROSS JOIN" in result.upper()


class TestMultiTableCommaJoin:
    def test_three_table_comma_join(self):
        sql = (
            "SELECT l.l_orderkey, o.o_orderdate, c.c_name "
            "FROM lineitem l, orders o, customer c "
            "WHERE l.l_orderkey = o.o_orderkey AND o.o_custkey = c.c_custkey AND l.l_quantity > 10"
        )
        result = rewrite(sql)
        assert ", orders" not in result
        assert ", customer" not in result
        assert result.upper().count("JOIN") >= 2
        assert "l.l_quantity > 10" in result or "WHERE" in result.upper()

    def test_three_table_result_is_valid_sql(self):
        from benchbox._vendor import sqlglot

        sql = (
            "SELECT l.x, o.y, c.z "
            "FROM lineitem l, orders o, customer c "
            "WHERE l.l_orderkey = o.o_orderkey AND o.o_custkey = c.c_custkey"
        )
        result = rewrite(sql)
        sqlglot.parse_one(result, dialect="postgres")

    def test_mixed_explicit_and_implicit_join(self):
        sql = "SELECT a.x, b.y, c.z FROM a JOIN b ON a.bid = b.id, c WHERE b.cid = c.id AND a.val > 5"
        result = rewrite(sql)
        from_idx = result.upper().index("FROM")
        from_clause = result[from_idx:]
        assert ", c " not in from_clause and not from_clause.upper().startswith("FROM a,")
        assert "WHERE" in result.upper()

    def test_unconnected_comma_join_falls_back_to_cross_join(self):
        sql = "SELECT * FROM a, b WHERE a.val > 5"
        result = rewrite(sql)
        assert "CROSS JOIN" in result.upper()
        assert "a.val > 5" in result


class TestSubqueryCommaJoin:
    def test_subquery_comma_join_rewritten(self):
        sql = "SELECT t.x FROM t WHERE t.id IN (SELECT a.id FROM a, b WHERE a.bid = b.id)"
        result = rewrite(sql)
        assert ", b" not in result

    def test_exists_subquery_comma_join_rewritten(self):
        sql = (
            "SELECT t.x FROM t WHERE EXISTS("
            "SELECT * FROM store_sales, date_dim "
            "WHERE ss_sold_date_sk = d_date_sk AND d_year = 2002)"
        )
        result = rewrite(sql)
        assert "store_sales, date_dim" not in result
        assert "JOIN" in result.upper()

    def test_outer_and_inner_comma_joins(self):
        sql = (
            "SELECT p.p_name, s.s_name "
            "FROM part p, supplier s "
            "WHERE p.p_suppkey = s.s_suppkey "
            "  AND p.p_size > ("
            "    SELECT AVG(ps.ps_supplycost) FROM partsupp ps, supplier s2 "
            "    WHERE ps.ps_suppkey = s2.s_suppkey"
            "  )"
        )
        result = rewrite(sql)
        assert ", supplier" not in result
        assert ", s2" not in result or "JOIN" in result.upper()


class TestCteCommaJoin:
    def test_comma_join_inside_cte_body(self):
        sql = (
            "WITH ctr AS ("
            "  SELECT sr_customer_sk, SUM(sr_return_amt) AS ctr_total_return"
            "  FROM store_returns, date_dim"
            "  WHERE sr_returned_date_sk = d_date_sk AND d_year = 2000"
            "  GROUP BY sr_customer_sk"
            ")"
            " SELECT * FROM ctr WHERE ctr_total_return > 1000"
        )
        result = rewrite(sql)
        assert "store_returns, date_dim" not in result
        assert "JOIN" in result.upper()
        assert "sr_returned_date_sk = d_date_sk" in result or "ON" in result.upper()

    def test_multiple_ctes_each_rewritten(self):
        sql = (
            "WITH cte1 AS ("
            "  SELECT a.x FROM t1 a, t2 b WHERE a.id = b.id"
            "),"
            " cte2 AS ("
            "  SELECT c.x FROM t3 c, t4 d WHERE c.id = d.id"
            ")"
            " SELECT * FROM cte1 JOIN cte2 ON cte1.x = cte2.x"
        )
        result = rewrite(sql)
        assert "t1 a, t2 b" not in result
        assert "t3 c, t4 d" not in result

    def test_cte_and_outer_query_both_rewritten(self):
        sql = "WITH cte AS (  SELECT a.x FROM t1 a, t2 b WHERE a.id = b.id) SELECT c.x FROM cte c, t3 e WHERE c.x = e.x"
        result = rewrite(sql)
        assert "t1 a, t2 b" not in result
        assert "cte c, t3 e" not in result


class TestTpchPatterns:
    def test_q3_customer_orders_lineitem(self):
        sql = (
            "SELECT l.l_orderkey, SUM(l.l_extendedprice*(1-l.l_discount)) AS revenue "
            "FROM customer c, orders o, lineitem l "
            "WHERE c.c_mktsegment = 'BUILDING' "
            "  AND c.c_custkey = o.o_custkey "
            "  AND l.l_orderkey = o.o_orderkey "
            "  AND o.o_orderdate < DATE '1995-03-15' "
            "  AND l.l_shipdate > DATE '1995-03-15' "
            "GROUP BY l.l_orderkey, o.o_orderdate "
            "ORDER BY revenue DESC, o.o_orderdate LIMIT 10"
        )
        result = rewrite(sql)
        assert ", orders" not in result
        assert ", lineitem" not in result
        assert result.upper().count("JOIN") >= 2

    def test_q5_five_table_comma_join(self):
        sql = (
            "SELECT n.n_name, SUM(l.l_extendedprice*(1-l.l_discount)) AS revenue "
            "FROM customer c, orders o, lineitem l, supplier s, nation n "
            "WHERE c.c_custkey = o.o_custkey "
            "  AND l.l_orderkey = o.o_orderkey "
            "  AND l.l_suppkey = s.s_suppkey "
            "  AND c.c_nationkey = s.s_nationkey "
            "  AND s.s_nationkey = n.n_nationkey "
            "  AND n.n_name = 'FRANCE' "
            "GROUP BY n.n_name ORDER BY revenue DESC"
        )
        result = rewrite(sql)
        assert ", orders" not in result
        assert ", lineitem" not in result
        assert ", supplier" not in result
        assert ", nation" not in result


class TestIntervalRewrite:
    def test_cast_date_minus_interval_day(self):
        sql = "SELECT CAST('1998-12-01' AS DATE) - INTERVAL '90' DAY AS d FROM x"
        result = rewrite(sql)
        assert "INTERVAL" not in result.upper()
        assert "dateadd('d', -90," in result

    def test_cast_date_plus_interval_day(self):
        sql = "SELECT CAST('1995-03-15' AS DATE) + INTERVAL '30' DAY FROM x"
        result = rewrite(sql)
        assert "INTERVAL" not in result.upper()
        assert "dateadd('d', 30," in result

    def test_interval_month(self):
        sql = "SELECT col + INTERVAL '3' MONTH FROM x"
        result = rewrite(sql)
        assert "INTERVAL" not in result.upper()
        assert "dateadd('M', 3," in result

    def test_interval_year(self):
        sql = "SELECT col - INTERVAL '1' YEAR FROM x"
        result = rewrite(sql)
        assert "INTERVAL" not in result.upper()
        assert "dateadd('y', -1," in result

    def test_no_interval_unchanged(self):
        sql = "SELECT * FROM x WHERE x.qty > 5"
        assert rewrite(sql) == sql

    def test_tpch_q1_pattern(self):
        sql = "SELECT l_returnflag FROM lineitem WHERE l_shipdate <= CAST('1998-12-01' AS DATE) - INTERVAL '90' DAY"
        result = rewrite(sql)
        assert "INTERVAL" not in result.upper()
        assert "dateadd(" in result


class TestSubstringRewrite:
    def test_substring_from_for(self):
        sql = "SELECT SUBSTRING(c_phone FROM 1 FOR 2) FROM customer"
        result = rewrite(sql)
        assert "SUBSTRING" not in result.upper() or "substring(c_phone, 1, 2)" in result
        assert "substring(c_phone, 1, 2)" in result

    def test_substring_from_only(self):
        sql = "SELECT SUBSTRING(c_phone FROM 3) FROM customer"
        result = rewrite(sql)
        assert "substring(c_phone, 3)" in result

    def test_tpch_q22_pattern(self):
        sql = "SELECT SUBSTRING(c_phone FROM 1 FOR 2) AS cntrycode FROM customer WHERE SUBSTRING(c_phone FROM 1 FOR 2) IN ('13', '17')"
        result = rewrite(sql)
        assert " FROM " not in result or "FROM customer" in result
        assert result.count("substring(c_phone, 1, 2)") == 2

    def test_no_substring_unchanged(self):
        sql = "SELECT c_name FROM customer"
        assert rewrite(sql) == sql


class TestCteColumnListRewrite:
    def test_cte_with_column_list_removed(self):
        sql = "WITH revenue (supplier_no, total) AS (SELECT s_suppkey, SUM(x) FROM s GROUP BY s_suppkey) SELECT * FROM revenue"
        result = rewrite(sql)
        assert "(supplier_no, total)" not in result
        assert "WITH revenue AS (" in result

    def test_cte_without_column_list_unchanged(self):
        sql = "WITH revenue AS (SELECT s_suppkey FROM s) SELECT * FROM revenue"
        assert rewrite(sql) == sql

    def test_tpch_q15_pattern(self):
        sql = "WITH revenue0 (supplier_no, total_revenue) AS (SELECT l_suppkey, SUM(l_extendedprice) FROM lineitem GROUP BY l_suppkey) SELECT s_suppkey FROM supplier, revenue0 WHERE s_suppkey = revenue0.supplier_no"
        result = rewrite(sql)
        assert "(supplier_no, total_revenue)" not in result
        assert "WITH revenue0 AS (" in result


class TestInternalHelpers:
    def test_flatten_and_single(self):
        from benchbox._vendor import sqlglot
        from benchbox._vendor.sqlglot import exp

        cond = sqlglot.parse_one("a.x = b.x", dialect="postgres")
        parts = _flatten_and(cond)
        assert len(parts) == 1

    def test_flatten_and_two(self):
        from benchbox._vendor import sqlglot
        from benchbox._vendor.sqlglot import exp

        tree = sqlglot.parse_one("SELECT * FROM t WHERE a.x = b.x AND a.z > 1", dialect="postgres")
        where = tree.args["where"]
        parts = _flatten_and(where.this)
        assert len(parts) == 2

    def test_flatten_and_three(self):
        from benchbox._vendor import sqlglot

        tree = sqlglot.parse_one("SELECT * FROM t WHERE a.x = b.x AND b.y = c.y AND a.z > 1", dialect="postgres")
        where = tree.args["where"]
        parts = _flatten_and(where.this)
        assert len(parts) == 3

    def test_build_and_none_for_empty(self):
        assert _build_and([]) is None

    def test_build_and_single(self):
        from benchbox._vendor import sqlglot

        node = sqlglot.parse_one("a.x = b.x", dialect="postgres")
        result = _build_and([node])
        assert result is node

    def test_build_and_two(self):
        from benchbox._vendor import sqlglot
        from benchbox._vendor.sqlglot import exp

        n1 = sqlglot.parse_one("a.x = b.x", dialect="postgres")
        n2 = sqlglot.parse_one("a.z > 1", dialect="postgres")
        result = _build_and([n1, n2])
        assert isinstance(result, exp.And)
