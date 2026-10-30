from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_limit_offset_becomes_two_argument_limit():
    assert (
        rewrite_query("SELECT url FROM hits ORDER BY c DESC LIMIT 10 OFFSET 1000")
        == "SELECT url FROM hits ORDER BY c DESC LIMIT 1000, 1010"
    )


def test_offset_without_limit_gets_unbounded_upper():
    assert rewrite_query("SELECT a FROM t OFFSET 5") == "SELECT a FROM t LIMIT 5, 9223372036854775807"


def test_offset_zero_keeps_lower_bound():
    assert rewrite_query("SELECT a FROM t LIMIT 3 OFFSET 0") == "SELECT a FROM t LIMIT 0, 3"


def test_offset_in_nested_query_and_cte():
    sql = (
        "WITH c AS (SELECT a FROM t ORDER BY a LIMIT 2 OFFSET 4) SELECT * FROM (SELECT a FROM c LIMIT 1 OFFSET 1) AS s"
    )
    assert rewrite_query(sql) == (
        "WITH c AS (SELECT a FROM t ORDER BY a LIMIT 4, 6) SELECT * FROM (SELECT a FROM c LIMIT 1, 2) AS s"
    )


def test_non_literal_offset_is_left_alone():
    sql = "SELECT a FROM t LIMIT 3 OFFSET (SELECT 1)"
    assert rewrite_query(sql) == sql


def test_limit_without_offset_is_left_alone():
    sql = "SELECT a FROM t LIMIT 3"
    assert rewrite_query(sql) == sql
