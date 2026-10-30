from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_percentile_cont_within_group_becomes_approx_percentile():
    sql = "SELECT g, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY v) AS m FROM t GROUP BY g"
    assert rewrite_query(sql) == "SELECT g, APPROX_PERCENTILE(v, 0.5, 5) AS m FROM t GROUP BY g"


def test_percentile_cont_ignores_null_ordering_case_key():
    sql = "SELECT PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY CASE WHEN v IS NULL THEN 1 ELSE 0 END, v) FROM t"
    assert rewrite_query(sql) == "SELECT APPROX_PERCENTILE(v, 0.9, 5) FROM t"


def test_percentile_cont_descending_order_mirrors_fraction():
    sql = "SELECT PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY v DESC) FROM t"
    assert rewrite_query(sql) == "SELECT APPROX_PERCENTILE(v, 0.1, 5) FROM t"
