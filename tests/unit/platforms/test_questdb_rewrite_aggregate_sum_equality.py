from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.variant_sets import Q15_VARIANTS
from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_sum_subquery_equal_to_max_of_sums_uses_tolerance():
    sql = (
        "SELECT s FROM t WHERE (SELECT SUM(a) FROM u WHERE u.k = t.k) = "
        "(SELECT MAX(r.v) FROM (SELECT k, SUM(a) AS v FROM u GROUP BY k) AS r)"
    )
    rewritten = rewrite_query(sql)
    assert "ABS((SELECT SUM(a)" in rewritten
    assert "<= 0.0001" in rewritten
    assert ") = (" not in rewritten


def test_equality_without_summed_subqueries_is_left_alone():
    sql = "SELECT s FROM t WHERE (SELECT MIN(a) FROM u) = (SELECT MAX(b) FROM (SELECT b FROM w) AS x)"
    assert "ABS" not in rewrite_query(sql)


def test_tpchavoc_q15_scalar_subquery_variant_has_no_float_equality():
    rewritten = rewrite_query(Q15_VARIANTS[1]._sql)
    assert "ABS(" in rewritten
    assert "<= 0.0001" in rewritten
