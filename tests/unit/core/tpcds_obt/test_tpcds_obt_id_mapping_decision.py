# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_dataframe_ids_are_the_obt_names() -> None:
    from benchbox.core.tpcds_obt.dataframe_queries import get_dataframe_queries

    queries = get_dataframe_queries()
    assert len(queries) == 17
    for query in queries:
        assert query.query_id == query.query_name, query.query_id
        assert str(query.query_id).startswith("obt_"), query.query_id
        assert "tpcds_sales_returns_obt" in str(query.sql_equivalent), query.query_id
    assert "obt_row_count" in {query.query_id for query in queries}


def test_sql_side_is_tpcds_numbered() -> None:
    from benchbox.core.tpcds_obt.queries import CONVERTIBLE_QUERY_IDS

    assert len(CONVERTIBLE_QUERY_IDS) == 90
    assert max(CONVERTIBLE_QUERY_IDS) > 17
    assert set(range(1, 18)) - {8} <= set(CONVERTIBLE_QUERY_IDS)


def test_dataframe_ids_never_collide_with_tpcds_numbered_ids() -> None:
    from benchbox.core.tpcds_obt.dataframe_queries import get_dataframe_queries
    from benchbox.core.tpcds_obt.queries import CONVERTIBLE_QUERY_IDS

    dataframe_ids = {str(query.query_id) for query in get_dataframe_queries()}
    sql_labels = {str(query_id) for query_id in CONVERTIBLE_QUERY_IDS} | {f"Q{n}" for n in CONVERTIBLE_QUERY_IDS}
    assert not dataframe_ids & sql_labels
