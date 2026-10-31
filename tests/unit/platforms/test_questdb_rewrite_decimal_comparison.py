from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_decimal_division_compared_with_fractional_literal_uses_decimal_literal():
    sql = "SELECT 1 FROM t WHERE CAST(a AS DECIMAL(17, 2)) / CAST(b AS DECIMAL(17, 2)) < 0.9"
    assert rewrite_query(sql) == ("SELECT 1 FROM t WHERE CAST(a AS DECIMAL(17, 2)) / CAST(b AS DECIMAL(17, 2)) < 0.9m")


def test_literal_on_the_left_and_negative_literals_are_converted():
    assert rewrite_query(
        "SELECT 1 FROM t WHERE 0.5 <= CAST(a AS DECIMAL(7, 2)) AND CAST(a AS DECIMAL(7, 2)) > -0.25"
    ) == ("SELECT 1 FROM t WHERE 0.5m <= CAST(a AS DECIMAL(7, 2)) AND CAST(a AS DECIMAL(7, 2)) > -0.25m")


def test_non_decimal_comparisons_are_left_unchanged():
    for sql in (
        "SELECT 1 FROM t WHERE c < 0.5",
        "SELECT 1 FROM t WHERE CAST(a AS DECIMAL(7, 2)) < 5",
        "SELECT 1 FROM t WHERE CAST(a AS DOUBLE) < 0.5",
        "SELECT 1 FROM t WHERE CAST(a AS DECIMAL(7, 2)) / b < 0.5",
    ):
        assert rewrite_query(sql) == sql
