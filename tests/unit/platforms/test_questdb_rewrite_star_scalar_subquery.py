from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_star_column_of_cte_scalar_subquery_resolves_to_the_cte_projection():
    sql = (
        "WITH m AS (SELECT MAX(x) AS cmax FROM t) SELECT a FROM s "
        'CROSS JOIN (SELECT * FROM m AS m) AS _u_0 WHERE b > 2 * _u_0."*"'
    )
    assert rewrite_query(sql) == (
        "WITH m AS (SELECT MAX(x) AS cmax FROM t) SELECT a FROM s "
        "CROSS JOIN (SELECT * FROM m AS m) AS _u_0 WHERE b > 2 * _u_0.cmax"
    )


def test_star_over_multi_column_cte_is_left_unchanged():
    sql = (
        "WITH m AS (SELECT MAX(x) AS cmax, MIN(x) AS cmin FROM t) SELECT a FROM s "
        'CROSS JOIN (SELECT * FROM m AS m) AS _u_0 WHERE b > _u_0."*"'
    )
    assert rewrite_query(sql) == sql
