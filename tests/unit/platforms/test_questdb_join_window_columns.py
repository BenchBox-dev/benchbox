from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query
from benchbox.utils.dialect_utils import translate_sql_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SCHEMA = {
    "customer": frozenset({"c_custkey", "c_name", "c_mktsegment"}),
    "orders": frozenset({"o_orderkey", "o_custkey", "o_orderdate", "o_totalprice"}),
}

QUALIFY_ROW_NUMBER = """
SELECT c_custkey, c_name, o_orderkey, o_orderdate, o_totalprice,
    ROW_NUMBER() OVER (PARTITION BY c_custkey ORDER BY o_totalprice DESC) as order_rank
FROM customer c
JOIN orders o ON c.c_custkey = o.o_custkey
WHERE o_orderdate >= DATE '1995-01-01'
QUALIFY ROW_NUMBER() OVER (PARTITION BY c_custkey ORDER BY o_totalprice DESC) <= 3
ORDER BY c_custkey, order_rank;
"""

QUALIFY_NTILE = """
SELECT c_mktsegment, o_orderkey, o_totalprice,
    NTILE(4) OVER (PARTITION BY c_mktsegment ORDER BY o_totalprice, o_orderkey) as quartile
FROM orders o
JOIN customer c ON o.o_custkey = c.c_custkey
WHERE o_orderdate >= DATE '1995-01-01'
QUALIFY NTILE(4) OVER (PARTITION BY c_mktsegment ORDER BY o_totalprice, o_orderkey) = 4
ORDER BY c_mktsegment, o_totalprice DESC, o_orderkey DESC;
"""

QUALIFY_LAG = """
SELECT c_custkey, c_name, o_orderkey, o_orderdate, o_totalprice,
    LAG(o_totalprice) OVER (PARTITION BY c_custkey ORDER BY o_orderdate, o_orderkey) as prev_order_price
FROM customer c
JOIN orders o ON c.c_custkey = o.o_custkey
WHERE o_orderdate >= DATE '1995-01-01'
QUALIFY o_totalprice > LAG(o_totalprice) OVER (PARTITION BY c_custkey ORDER BY o_orderdate, o_orderkey)
ORDER BY c_custkey, o_orderdate, o_orderkey;
"""


def questdb_sql(query: str) -> str:
    return rewrite_query(translate_sql_query(query, "questdb"), SCHEMA.get)


def test_row_number_window_columns_are_qualified_after_qualify_elimination():
    sql = questdb_sql(QUALIFY_ROW_NUMBER)
    assert "PARTITION BY c.c_custkey ORDER BY o.o_totalprice DESC" in sql
    assert "PARTITION BY c_custkey" not in sql
    assert sql.rstrip().endswith(
        "ORDER BY CASE WHEN c_custkey IS NULL THEN 1 ELSE 0 END, c_custkey, CASE WHEN order_rank IS NULL THEN 1 ELSE 0 END, order_rank"
    )


def test_ntile_window_columns_are_qualified_after_qualify_elimination():
    sql = questdb_sql(QUALIFY_NTILE)
    assert "NTILE(4) OVER (PARTITION BY c.c_mktsegment ORDER BY o.o_totalprice, o.o_orderkey)" in sql
    assert "PARTITION BY c_mktsegment" not in sql


def test_lag_window_columns_are_qualified_after_qualify_elimination():
    sql = questdb_sql(QUALIFY_LAG)
    assert "LAG(o.o_totalprice) OVER (PARTITION BY c.c_custkey ORDER BY o.o_orderdate, o.o_orderkey)" in sql
    assert "PARTITION BY c_custkey" not in sql


def test_window_columns_are_left_alone_without_a_join():
    sql = rewrite_query(
        "SELECT ROW_NUMBER() OVER (PARTITION BY c_custkey ORDER BY c_name) AS r FROM customer", SCHEMA.get
    )
    assert "PARTITION BY c_custkey ORDER BY c_name" in sql


def test_ambiguous_window_columns_are_not_guessed():
    schema = {"a": frozenset({"k", "x"}), "b": frozenset({"k", "y"})}
    sql = rewrite_query(
        "SELECT ROW_NUMBER() OVER (PARTITION BY k ORDER BY x) AS r FROM a JOIN b ON a.k = b.k", schema.get
    )
    assert "PARTITION BY k ORDER BY a.x" in sql


def test_columns_of_nothing_known_leaves_the_query_unchanged():
    sql = rewrite_query(
        "SELECT ROW_NUMBER() OVER (PARTITION BY k ORDER BY x) AS r FROM a JOIN b ON a.k = b.k", lambda _: None
    )
    assert "PARTITION BY k ORDER BY x" in sql


def test_unaliased_derived_table_source_is_not_given_an_empty_qualifier():
    schema = {"b": frozenset({"k", "y"})}
    sql = rewrite_query(
        "SELECT ROW_NUMBER() OVER (PARTITION BY x ORDER BY y) AS r FROM (SELECT 1 AS x) JOIN b ON b.k = x", schema.get
    )
    assert "PARTITION BY x ORDER BY b.y" in sql
    assert '""' not in sql
    assert ". " not in sql
