from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_case_mixing_dateadd_and_date_casts_the_date_branch_to_timestamp():
    sql = "SELECT CASE WHEN a = 1 THEN DATEADD('d', -1, CAST(x AS DATE)) ELSE CAST(y AS DATE) END FROM t"
    assert rewrite_query(sql) == (
        "SELECT CASE WHEN a = 1 THEN DATEADD('d', -1, CAST(x AS DATE)) ELSE CAST(CAST(y AS DATE) AS TIMESTAMP) END FROM t"
    )


def test_coalesce_mixing_dateadd_and_date_casts_the_date_argument_to_timestamp():
    sql = "SELECT COALESCE(DATEADD('d', -1, CAST(x AS DATE)), CAST(y AS DATE)) FROM t"
    assert rewrite_query(sql) == (
        "SELECT COALESCE(DATEADD('d', -1, CAST(x AS DATE)), CAST(CAST(y AS DATE) AS TIMESTAMP)) FROM t"
    )


def test_case_without_dateadd_is_untouched():
    sql = "SELECT CASE WHEN a = 1 THEN CAST(x AS DATE) ELSE CAST(y AS DATE) END FROM t"
    assert rewrite_query(sql) == sql
