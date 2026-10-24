# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_dataframe_side_is_obt_native() -> None:
    from benchbox.core.tpcds_obt.dataframe_queries import get_dataframe_queries

    queries = get_dataframe_queries()
    assert len(queries) == 17
    assert {str(q.query_id) for q in queries} == {f"Q{n}" for n in range(1, 18)}
    queries = sorted(queries, key=lambda q: int(str(q.query_id)[1:]))
    for query in queries:
        assert str(query.query_name).startswith("obt_"), query.query_id
        assert "tpcds_sales_returns_obt" in str(query.sql_equivalent), query.query_id
    assert queries[0].query_name == "obt_row_count"


def test_sql_side_is_tpcds_numbered() -> None:
    from benchbox.core.tpcds_obt.queries import CONVERTIBLE_QUERY_IDS

    assert len(CONVERTIBLE_QUERY_IDS) == 90
    assert max(CONVERTIBLE_QUERY_IDS) > 17
    assert set(range(1, 18)) - {8} <= set(CONVERTIBLE_QUERY_IDS)


def test_no_clean_correspondence() -> None:
    from benchbox.core.tpcds_obt.dataframe_queries import get_dataframe_queries
    from benchbox.core.tpcds_obt.queries import CONVERTIBLE_QUERY_IDS, TPCDSOBTQueryManager

    manager = TPCDSOBTQueryManager()
    df_by_id = {str(q.query_id): q for q in get_dataframe_queries()}
    overlap = sorted(set(df_by_id) & {f"Q{n}" for n in CONVERTIBLE_QUERY_IDS})
    assert overlap, "expected overlapping Qn labels to guard the verdict"
    for label in overlap:
        df_sql = " ".join(str(df_by_id[label].sql_equivalent).split())
        sql_n = " ".join(str(manager.get_template(int(label[1:]))).split())
        assert df_sql != sql_n, f"{label} matches TPC-DS query {label[1:]}: a correspondence exists"
