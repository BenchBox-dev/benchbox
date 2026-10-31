from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_global_flag_is_dropped_and_backreferences_use_dollar_syntax():
    sql = r"SELECT REGEXP_REPLACE(r, '^https?://(?:www\.)?([^/]+)/.*$', '\1', 'g') AS k FROM hits"
    assert rewrite_query(sql) == r"SELECT REGEXP_REPLACE(r, '^https?://(?:www\.)?([^/]+)/.*$', '$1') AS k FROM hits"


def test_multiple_backreferences_are_converted():
    assert rewrite_query(r"SELECT REGEXP_REPLACE(a, '(x)(y)', '\2-\1', 'g') FROM t") == (
        "SELECT REGEXP_REPLACE(a, '(x)(y)', '$2-$1') FROM t"
    )


def test_call_without_global_flag_is_left_unchanged():
    sql = r"SELECT REGEXP_REPLACE(a, '(x)', '\1') FROM t"
    assert rewrite_query(sql) == sql


def test_other_flag_sets_are_left_unchanged():
    for flags in ("i", "gi", "ig"):
        sql = rf"SELECT REGEXP_REPLACE(a, '(x)', '\1', '{flags}') FROM t"
        assert rewrite_query(sql) == sql
