from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_epoch_day_difference_becomes_datediff():
    sql = "SELECT CAST(EXTRACT(epoch FROM CAST(a.d AS TIMESTAMP) - CAST(b.d AS TIMESTAMP)) / 86400 AS BIGINT) FROM a, b"
    assert rewrite_query(sql) == "SELECT CAST(DATEDIFF('d', b.d, a.d) AS BIGINT) FROM a CROSS JOIN b"


def test_current_date_becomes_the_start_of_today():
    assert rewrite_query("SELECT 1 FROM t WHERE d = CURRENT_DATE") == (
        "SELECT 1 FROM t WHERE d = CAST(DATE_TRUNC('day', NOW()) AS DATE)"
    )


def test_current_date_inside_a_day_difference_is_rewritten_too():
    sql = "SELECT EXTRACT(epoch FROM CAST(CURRENT_DATE AS TIMESTAMP) - CAST(MIN(x) AS TIMESTAMP)) / 86400 FROM t"
    assert rewrite_query(sql) == "SELECT DATEDIFF('d', MIN(x), CAST(DATE_TRUNC('day', NOW()) AS DATE)) FROM t"


def test_epoch_extraction_that_is_not_a_day_difference_is_left_alone():
    sql = "SELECT EXTRACT(epoch FROM ts) / 3600 FROM t"
    assert rewrite_query(sql) == sql
