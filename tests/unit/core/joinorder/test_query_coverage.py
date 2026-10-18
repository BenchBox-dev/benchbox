from __future__ import annotations

import re

import pytest

from benchbox.core.joinorder.queries import JoinOrderQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_all_113_canonical_queries_registered() -> None:
    manager = JoinOrderQueryManager()

    assert manager.get_query_count() == 113
    assert len(manager.get_query_ids()) == 113


def test_query_ids_match_canonical_shape() -> None:
    manager = JoinOrderQueryManager()

    assert all(re.fullmatch(r"\d+[a-z]", query_id) for query_id in manager.get_query_ids())


def test_query_15_avoids_reserved_alias_for_local_engines() -> None:
    manager = JoinOrderQueryManager()
    query = manager.get_query("15a")

    assert "AS at1" in query
    assert "at." not in query


@pytest.mark.parametrize("query_id", JoinOrderQueryManager().get_query_ids())
def test_canonical_queries_parse_with_sqlglot(query_id: str) -> None:
    from benchbox._vendor import sqlglot

    sqlglot.parse_one(JoinOrderQueryManager().get_query(query_id), read="postgres")
