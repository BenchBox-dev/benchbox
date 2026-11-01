from __future__ import annotations

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]

DORIS_VARIANT_IDS = (
    "window_moving_frame",
    "fulltext_simple_search",
    "fulltext_boolean_search",
    "fulltext_phrase_search",
    "asof_join_basic",
)


@pytest.fixture(scope="module")
def doris_queries():
    return ReadPrimitivesBenchmark(scale_factor=0.01).get_queries(dialect="doris")


@pytest.mark.parametrize("query_id", DORIS_VARIANT_IDS)
def test_doris_uses_a_hand_written_variant(doris_queries, query_id):
    query = doris_queries[query_id]

    assert "AGAINST" not in query
    assert "ASOF JOIN" not in query.upper()
    assert "RANGE BETWEEN INTERVAL" not in query


def test_doris_fulltext_variants_use_match_predicates(doris_queries):
    assert "MATCH_ANY" in doris_queries["fulltext_simple_search"]
    assert "MATCH_ANY" in doris_queries["fulltext_boolean_search"]
    assert "MATCH_PHRASE" in doris_queries["fulltext_phrase_search"]
