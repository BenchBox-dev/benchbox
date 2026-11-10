from __future__ import annotations

import pytest

from benchbox.utils.dialect_utils import translate_sql_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _singlestore(sql: str) -> str:
    return translate_sql_query(sql, "singlestore", source_dialect="duckdb")


def test_full_outer_join_stays_a_full_outer_join():
    sql = _singlestore("SELECT SUM(a.x) FROM a FULL OUTER JOIN b ON a.k = b.k")

    assert "FULL OUTER JOIN" in sql
    assert "UNION" not in sql


def test_aggregate_over_full_outer_join_stays_one_select():
    sql = _singlestore(
        "WITH s AS (SELECT k FROM a GROUP BY k), c AS (SELECT k FROM b GROUP BY k) "
        "SELECT SUM(CASE WHEN s.k IS NOT NULL AND c.k IS NULL THEN 1 ELSE 0 END) AS store_only, "
        "SUM(CASE WHEN s.k IS NULL AND c.k IS NOT NULL THEN 1 ELSE 0 END) AS catalog_only "
        "FROM s FULL OUTER JOIN c ON s.k = c.k"
    )

    assert sql.count("SELECT SUM(") == 1
    assert "FULL OUTER JOIN" in sql
    assert "UNION" not in sql
    assert "RIGHT" not in sql


def test_other_singlestore_rewrites_still_apply_after_the_full_outer_join_change():
    sql = _singlestore("SELECT x FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY k ORDER BY x) = 1")

    assert "QUALIFY" not in sql.upper()
    assert "ROW_NUMBER" in sql.upper()


def test_rollup_over_empty_input_still_emits_the_grand_total_row():
    sql = _singlestore(
        "WITH c AS (SELECT 1 AS a, 2 AS x) SELECT a, AVG(x) AS m FROM c GROUP BY ROLLUP (a) ORDER BY a LIMIT 5"
    )

    assert sql.startswith("WITH `c` AS")
    assert "NOT EXISTS" in sql
    assert "UNION ALL" in sql


def test_rollup_with_grouping_function_is_left_alone():
    sql = _singlestore("SELECT a, GROUPING(a) AS g, SUM(x) FROM t GROUP BY ROLLUP (a)")

    assert "UNION" not in sql
