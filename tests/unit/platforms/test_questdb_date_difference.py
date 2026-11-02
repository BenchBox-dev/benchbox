from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_with_modules

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def rewrite_query(sql: str) -> str:
    return rewrite_with_modules(sql, "date_difference")


def test_day_difference_cast_to_an_integer_keeps_the_fraction_for_the_cast_to_truncate():
    sql = "SELECT CAST(EXTRACT(epoch FROM CAST(a.d AS TIMESTAMP) - CAST(b.d AS TIMESTAMP)) / 86400 AS BIGINT) FROM a, b"
    assert rewrite_query(sql) == (
        "SELECT CAST((CAST((CAST(CAST(a.d AS TIMESTAMP) AS BIGINT) - CAST(CAST(b.d AS TIMESTAMP) AS BIGINT)) AS DOUBLE PRECISION) / 86400000000.0) AS BIGINT) FROM a CROSS JOIN b"
    )


def test_day_difference_keeps_its_fraction_when_it_is_not_cast_to_an_integer():
    sql = "SELECT EXTRACT(epoch FROM (a - b)) / 86400 FROM t"
    assert (
        rewrite_query(sql)
        == "SELECT (CAST((CAST(CAST(a AS TIMESTAMP) AS BIGINT) - CAST(CAST(b AS TIMESTAMP) AS BIGINT)) AS DOUBLE PRECISION) / 86400000000.0) FROM t"
    )


def test_current_date_becomes_the_start_of_today():
    assert rewrite_query("SELECT 1 FROM t WHERE d = CURRENT_DATE") == (
        "SELECT 1 FROM t WHERE d = CAST(DATE_TRUNC('day', NOW()) AS DATE)"
    )


def test_current_date_inside_a_day_difference_is_rewritten_too():
    sql = "SELECT EXTRACT(epoch FROM CAST(CURRENT_DATE AS TIMESTAMP) - CAST(MIN(x) AS TIMESTAMP)) / 86400 FROM t"
    assert rewrite_query(sql) == (
        "SELECT (CAST((CAST(CAST(CAST(DATE_TRUNC('day', NOW()) AS DATE) AS TIMESTAMP) AS BIGINT) - CAST(CAST(MIN(x) AS TIMESTAMP) AS BIGINT)) AS DOUBLE PRECISION) "
        "/ 86400000000.0) FROM t"
    )


def test_epoch_extraction_that_is_not_a_day_difference_is_left_alone():
    sql = "SELECT EXTRACT(epoch FROM ts) / 3600 FROM t"
    assert rewrite_query(sql) == sql


def test_epoch_of_a_timestamp_difference_keeps_fractional_seconds():
    sql = "SELECT EXTRACT(EPOCH FROM (dropoff - pickup)) FROM t"
    assert rewrite_query(sql) == (
        "SELECT (CAST((CAST(CAST(dropoff AS TIMESTAMP) AS BIGINT) - CAST(CAST(pickup AS TIMESTAMP) AS BIGINT)) AS DOUBLE PRECISION) / 1000000.0) FROM t"
    )


def test_epoch_of_a_timestamp_difference_inside_a_comparison_is_rewritten():
    sql = "SELECT 1 FROM t WHERE EXTRACT(EPOCH FROM (b - a)) > 60"
    assert rewrite_query(sql) == (
        "SELECT 1 FROM t WHERE (CAST((CAST(CAST(b AS TIMESTAMP) AS BIGINT) - CAST(CAST(a AS TIMESTAMP) AS BIGINT)) AS DOUBLE PRECISION) / 1000000.0) > 60"
    )


def test_epoch_of_a_timestamp_shifted_by_an_interval_is_left_alone():
    sql = "SELECT EXTRACT(EPOCH FROM (ts - INTERVAL '1 day')) FROM t"
    assert rewrite_query(sql) == sql
