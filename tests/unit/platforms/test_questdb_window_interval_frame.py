from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_range_frame_interval_becomes_questdb_duration_syntax():
    sql = "SELECT SUM(x) OVER (ORDER BY d RANGE BETWEEN INTERVAL '30' DAY PRECEDING AND CURRENT ROW) AS s FROM t"
    assert (
        rewrite_query(sql)
        == "SELECT SUM(x) OVER (ORDER BY d RANGE BETWEEN '30' day PRECEDING AND CURRENT ROW) AS s FROM t"
    )


def test_translated_interval_string_form_is_rewritten():
    sql = "SELECT SUM(x) OVER (ORDER BY d RANGE BETWEEN INTERVAL '30 DAY' PRECEDING AND CURRENT ROW) AS s FROM t"
    assert (
        rewrite_query(sql)
        == "SELECT SUM(x) OVER (ORDER BY d RANGE BETWEEN '30' day PRECEDING AND CURRENT ROW) AS s FROM t"
    )


def test_following_interval_bound_is_rewritten():
    sql = "SELECT SUM(x) OVER (ORDER BY d RANGE BETWEEN CURRENT ROW AND INTERVAL '2' HOUR FOLLOWING) AS s FROM t"
    assert (
        rewrite_query(sql)
        == "SELECT SUM(x) OVER (ORDER BY d RANGE BETWEEN CURRENT ROW AND '2' hour FOLLOWING) AS s FROM t"
    )


def test_rows_frame_is_untouched():
    sql = "SELECT AVG(x) OVER (ORDER BY d ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS a FROM t"
    assert rewrite_query(sql) == sql


def test_numeric_range_frame_is_untouched():
    sql = "SELECT SUM(x) OVER (ORDER BY d RANGE BETWEEN 30 PRECEDING AND CURRENT ROW) AS s FROM t"
    assert rewrite_query(sql) == sql


def test_window_moving_frame_questdb_variant_orders_outside_the_range_window():
    from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark

    sql = ReadPrimitivesBenchmark(scale_factor=0.01).get_queries(dialect="questdb")["window_moving_frame"]
    rewritten = rewrite_query(sql)

    assert "RANGE BETWEEN '30' day PRECEDING AND CURRENT ROW" in rewritten
    assert rewritten.index("LIMIT 9223372036854775807") < rewritten.rindex("ORDER BY o_orderdate")
