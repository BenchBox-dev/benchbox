from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_cast_to_double_precision_inside_case_is_emitted_as_double():
    sql = "SELECT CASE WHEN a > 0 THEN CAST(b AS DOUBLE PRECISION) / a END FROM t"
    assert rewrite_query(sql) == "SELECT CASE WHEN a > 0 THEN CAST(b AS DOUBLE) / a END FROM t"


def test_plain_double_cast_is_emitted_as_double():
    assert rewrite_query("SELECT CAST(b AS DOUBLE) FROM t") == "SELECT CAST(b AS DOUBLE) FROM t"


def test_float_cast_is_emitted_as_double():
    assert rewrite_query("SELECT CAST(b AS FLOAT) FROM t") == "SELECT CAST(b AS DOUBLE) FROM t"


def test_decimal_cast_keeps_its_type():
    sql = "SELECT CAST(b AS DECIMAL(10, 2)) FROM t"
    assert rewrite_query(sql) == sql
