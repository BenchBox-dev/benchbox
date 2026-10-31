from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_duplicate_explicit_alias_is_renamed_and_first_is_kept():
    assert rewrite_query("SELECT a.x AS x, b.x AS x FROM a JOIN b ON a.k = b.k") == (
        "SELECT a.x AS x, b.x AS x_2 FROM a JOIN b ON a.k = b.k"
    )


def test_duplicates_are_case_insensitive_and_avoid_existing_names():
    assert rewrite_query("SELECT a AS n, b AS N, c AS n_2, d AS n FROM t") == (
        "SELECT a AS n, b AS N_3, c AS n_2, d AS n_4 FROM t"
    )


def test_duplicates_inside_a_cte_are_renamed():
    assert rewrite_query("WITH c AS (SELECT 1 AS a, 2 AS a) SELECT * FROM c") == (
        "WITH c AS (SELECT 1 AS a, 2 AS a_2) SELECT * FROM c"
    )


def test_distinct_aliases_are_left_unchanged():
    sql = "SELECT a.x AS x, b.x AS y FROM a JOIN b ON a.k=b.k"
    assert rewrite_query(sql) == sql
