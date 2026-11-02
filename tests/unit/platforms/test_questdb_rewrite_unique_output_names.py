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


def test_query39_translated_final_projection_gets_unique_names():
    sql = (
        "SELECT inv1.w_warehouse_sk AS w_warehouse_sk, inv1.i_item_sk AS i_item_sk, inv1.d_moy AS d_moy, "
        "inv1.mean AS mean, inv1.cov AS cov, inv2.w_warehouse_sk AS w_warehouse_sk, inv2.i_item_sk AS i_item_sk, "
        "inv2.d_moy AS d_moy, inv2.mean AS mean, inv2.cov AS cov "
        "FROM inv AS inv1 JOIN inv AS inv2 ON inv1.i_item_sk = inv2.i_item_sk"
    )
    assert rewrite_query(sql) == (
        "SELECT inv1.w_warehouse_sk AS w_warehouse_sk, inv1.i_item_sk AS i_item_sk, inv1.d_moy AS d_moy, "
        "inv1.mean AS mean, inv1.cov AS cov, inv2.w_warehouse_sk AS w_warehouse_sk_2, inv2.i_item_sk AS i_item_sk_2, "
        "inv2.d_moy AS d_moy_2, inv2.mean AS mean_2, inv2.cov AS cov_2 "
        "FROM inv AS inv1 JOIN inv AS inv2 ON inv1.i_item_sk = inv2.i_item_sk"
    )


def test_query64_translated_final_projection_gets_unique_names():
    sql = (
        "SELECT cs1.syear AS syear, cs1.cnt AS cnt, cs1.s1 AS s11, cs2.s1 AS s12, cs2.syear AS syear, cs2.cnt AS cnt "
        "FROM cross_sales AS cs1 JOIN cross_sales AS cs2 ON cs1.item_sk = cs2.item_sk"
    )
    assert rewrite_query(sql) == (
        "SELECT cs1.syear AS syear, cs1.cnt AS cnt, cs1.s1 AS s11, cs2.s1 AS s12, cs2.syear AS syear_2, "
        "cs2.cnt AS cnt_2 FROM cross_sales AS cs1 JOIN cross_sales AS cs2 ON cs1.item_sk = cs2.item_sk"
    )


def test_unaliased_duplicate_columns_are_left_to_the_engine_which_deduplicates_them():
    sql = "SELECT inv1.w_warehouse_sk, inv1.d_moy, inv2.w_warehouse_sk, inv2.d_moy FROM inv AS inv1 JOIN inv AS inv2 ON inv1.k = inv2.k"
    assert rewrite_query(sql) == sql
