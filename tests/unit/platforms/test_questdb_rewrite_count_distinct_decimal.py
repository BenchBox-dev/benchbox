from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_count_distinct_of_decimal_column_counts_distinct_text():
    assert rewrite_query("SELECT COUNT(DISTINCT ss.ss_list_price) AS c FROM store_sales AS ss") == (
        "SELECT COUNT_DISTINCT(CAST(ss.ss_list_price AS VARCHAR)) AS c FROM store_sales AS ss"
    )


def test_count_distinct_of_integer_column_is_left_unchanged():
    sql = "SELECT COUNT(DISTINCT ss_item_sk) FROM store_sales"
    assert rewrite_query(sql) == sql


def test_count_distinct_of_expression_is_left_unchanged():
    sql = "SELECT COUNT(DISTINCT ss_list_price + 1) FROM store_sales"
    assert rewrite_query(sql) == sql


def test_count_distinct_of_obt_decimal_column_counts_distinct_text():
    assert rewrite_query("SELECT COUNT(DISTINCT obt.list_price) FROM tpcds_obt AS obt") == (
        "SELECT COUNT_DISTINCT(CAST(obt.list_price AS VARCHAR)) FROM tpcds_obt AS obt"
    )
