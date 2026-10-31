from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_between_becomes_range_comparison():
    assert (
        rewrite_query("SELECT * FROM t WHERE dbl BETWEEN 0.05 AND 7.0")
        == "SELECT * FROM t WHERE (dbl >= 0.05 AND dbl <= 7.0)"
    )


def test_not_between_keeps_negation():
    assert (
        rewrite_query("SELECT * FROM t WHERE dbl NOT BETWEEN 1 AND 2")
        == "SELECT * FROM t WHERE NOT (dbl >= 1 AND dbl <= 2)"
    )


def test_between_operand_expression_is_repeated_per_comparison():
    assert (
        rewrite_query("SELECT * FROM t WHERE a + b BETWEEN 1 AND 2 AND c = 3")
        == "SELECT * FROM t WHERE (a + b >= 1 AND a + b <= 2) AND c = 3"
    )


def test_between_inside_or_and_subquery_precedence():
    assert (
        rewrite_query(
            "SELECT * FROM t WHERE x = 1 OR y BETWEEN lo AND hi AND w IN (SELECT q FROM s WHERE q BETWEEN 1 AND 2)"
        )
        == "SELECT * FROM t WHERE x = 1 OR (y >= lo AND y <= hi) AND w IN (SELECT q FROM s WHERE (q >= 1 AND q <= 2))"
    )


def test_timestamp_between_with_string_bounds():
    assert (
        rewrite_query("SELECT * FROM t WHERE ts BETWEEN '2020-01-01' AND '2020-02-01'")
        == "SELECT * FROM t WHERE (ts >= '2020-01-01' AND ts <= '2020-02-01')"
    )


def test_between_nested_in_operand_and_bounds_is_rewritten():
    rewritten = rewrite_query("SELECT * FROM t WHERE (a BETWEEN 1 AND 2) BETWEEN (b BETWEEN 3 AND 4) AND 5")
    assert "BETWEEN" not in rewritten
    assert "b >= 3 AND b <= 4" in rewritten


def test_between_in_scalar_subquery_bound_is_rewritten():
    sql = "SELECT * FROM t WHERE x BETWEEN (SELECT MIN(y) FROM s WHERE y BETWEEN 1 AND 2) AND 9"
    assert "BETWEEN" not in rewrite_query(sql)


def test_between_literal_bound_arithmetic_is_folded_exactly():
    assert (
        rewrite_query("SELECT SUM(x) FROM t WHERE d BETWEEN 0.06 - 0.01 AND 0.06 + 0.01 AND q < 24")
        == "SELECT SUM(x) FROM t WHERE (d >= 0.05 AND d <= 0.07) AND q < 24"
    )


def test_between_non_literal_bound_arithmetic_is_kept():
    assert rewrite_query("SELECT * FROM t WHERE d BETWEEN c - 1 AND c + 1") == (
        "SELECT * FROM t WHERE (d >= c - 1 AND d <= c + 1)"
    )


def test_between_negative_folded_bound():
    assert rewrite_query("SELECT * FROM t WHERE d BETWEEN 0.01 - 0.06 AND 1 + 2") == (
        "SELECT * FROM t WHERE (d >= -0.05 AND d <= 3)"
    )


def test_symmetric_between_accepts_either_bound_order():
    assert rewrite_query("SELECT 1 FROM t WHERE x BETWEEN SYMMETRIC 5 AND 1") == (
        "SELECT 1 FROM t WHERE ((x >= 5 AND x <= 1) OR (x >= 1 AND x <= 5))"
    )


def test_asymmetric_between_is_plain_range():
    assert rewrite_query("SELECT 1 FROM t WHERE x BETWEEN ASYMMETRIC 1 AND 5") == (
        "SELECT 1 FROM t WHERE (x >= 1 AND x <= 5)"
    )
