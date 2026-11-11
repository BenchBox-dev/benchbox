# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_sql_surface_numbers_queries_1_to_22() -> None:

    from benchbox.core.benchmark_loader import get_core_benchmark_class
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    cls = get_core_benchmark_class("tpch_skew")
    assert issubclass(cls, TPCHBenchmark)
    sql_ids = sorted((str(q) for q in cls(scale_factor=0.01).get_queries().keys()), key=int)
    assert sql_ids == [str(n) for n in range(1, 23)]


def test_dataframe_registry_numbers_queries_q1_to_q22() -> None:

    from benchbox.core.tpch.dataframe_queries import TPCH_DATAFRAME_QUERIES
    from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

    df_ids = sorted(
        (str(q) for q in TPCH_SKEW_DATAFRAME_QUERIES.get_query_ids()),
        key=lambda q: int(q.lstrip("Q")),
    )
    assert df_ids == [f"Q{n}" for n in range(1, 23)]
    tpch_ids = sorted(
        (str(q) for q in TPCH_DATAFRAME_QUERIES.get_query_ids()),
        key=lambda q: int(q.lstrip("Q")),
    )
    assert df_ids == tpch_ids, "skew registry must stay a 1:1 re-registration of TPC-H"


