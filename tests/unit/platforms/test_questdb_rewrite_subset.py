from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query, rewrite_with_modules

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SQL = "SELECT DATE_TRUNC('DAY', ts) FROM t LIMIT 3 OFFSET 2"


def test_only_the_named_module_is_applied():
    assert rewrite_with_modules(SQL, "offset_to_limit") == "SELECT DATE_TRUNC('DAY', ts) FROM t LIMIT 2, 5"
    assert rewrite_with_modules(SQL, "date_trunc_unit") == "SELECT DATE_TRUNC('day', ts) FROM t LIMIT 3 OFFSET 2"


def test_naming_every_module_matches_the_full_pipeline():
    assert rewrite_with_modules(SQL, "offset_to_limit", "date_trunc_unit") == rewrite_query(SQL)


def test_no_modules_leaves_the_query_alone():
    assert rewrite_with_modules(SQL) == SQL


def test_unknown_module_name_is_rejected():
    with pytest.raises(ValueError, match="no_such_module"):
        rewrite_with_modules(SQL, "no_such_module")
