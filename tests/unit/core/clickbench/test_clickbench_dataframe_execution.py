# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

try:
    import pandas as pd
    import polars as pl

    from benchbox.core.clickbench.dataframe_queries import list_clickbench_queries
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    DEPS_AVAILABLE = True
except ImportError:
    pd = None
    pl = None
    PolarsDataFrameAdapter = None
    list_clickbench_queries = None
    DEPS_AVAILABLE = False

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(not DEPS_AVAILABLE, reason="pandas/polars not installed"),
]

ALL_QUERY_IDS = [q.query_id for q in list_clickbench_queries()] if DEPS_AVAILABLE else []
assert not DEPS_AVAILABLE or len(ALL_QUERY_IDS) == 43

EXPECTED_EMPTY = frozenset({"Q28", "Q29", "Q39", "Q40", "Q41", "Q42", "Q43"})


class _PandasContext:
    def __init__(self, tables: dict[str, Any]) -> None:
        self._tables = tables

    def get_table(self, name: str) -> Any:
        return self._tables[name.lower()].copy()


def _make_hits() -> Any:
    base = datetime(2013, 7, 5, 12, 0, 0)
    rows = range(10)
    urls = [f"http://www.google.com/search?q={i}" if i % 2 == 0 else f"http://example.com/p{i}" for i in rows]
    urls[0] = "http://www.google-search.com/?q=0"
    user_ids = [1000 + (i % 5) for i in rows]
    user_ids[9] = 435090932899640449
    return pd.DataFrame(
        {
            "WatchID": [i + 1 for i in rows],
            "CounterID": [62] * 10,
            "EventDate": [date(2013, 7, 5)] * 10,
            "EventTime": [base.replace(hour=i, minute=i) for i in rows],
            "UserID": user_ids,
            "URL": urls,
            "Title": [f"Google result {i}" if i % 2 == 0 else f"Page {i}" for i in rows],
            "SearchPhrase": [f"phrase {i % 3}" if i % 2 == 0 else "" for i in rows],
            "SearchEngineID": [(i % 3) + 1 for i in rows],
            "AdvEngineID": [0 if i % 3 == 0 else (i % 4) + 1 for i in rows],
            "RegionID": [(i % 4) + 1 for i in rows],
            "MobilePhone": [1 if i % 4 == 0 else 0 for i in rows],
            "MobilePhoneModel": ["Pixel" if i % 4 == 0 else "" for i in rows],
            "ClientIP": [(1 << 24) + i for i in rows],
            "URLHash": [2868770270353813622 if i == 0 else 1000 + i for i in rows],
            "RefererHash": [3594120000172545465 if i == 1 else 2000 + i for i in rows],
            "TraficSourceID": [-1 if i % 3 == 0 else (6 if i % 3 == 1 else 1) for i in rows],
            "Referer": [f"http://referer{i}.com" for i in rows],
            "IsRefresh": [0] * 10,
            "IsLink": [1 if i % 2 == 0 else 0 for i in rows],
            "IsDownload": [0] * 10,
            "DontCountHits": [0] * 10,
            "ResolutionWidth": [1920 if i % 2 == 0 else 1366 for i in rows],
            "WindowClientWidth": [1920 - i for i in rows],
            "WindowClientHeight": [1080 - i for i in rows],
        }
    )


def _to_pandas_result(result: Any) -> Any:
    if hasattr(result, "collect"):
        result = result.collect()
    if isinstance(result, pl.DataFrame):
        return result.to_pandas()
    if isinstance(result, pd.DataFrame):
        return result.copy()
    raise TypeError(f"Unsupported result type: {type(result)!r}")


def _normalized(frame: Any) -> Any:
    frame = frame.copy()
    for column in frame.columns:
        values = frame[column]
        if pd.api.types.is_datetime64_any_dtype(values) or (
            len(frame) and pd.api.types.infer_dtype(values, skipna=True) in ("datetime", "date")
        ):
            frame[column] = values.astype(str)
    return frame.sort_values(by=list(frame.columns), na_position="first").reset_index(drop=True)


class TestClickBenchQueryExecution:
    @pytest.fixture(scope="class")
    @classmethod
    def hits(cls):
        return _make_hits()

    @pytest.fixture(scope="class")
    @classmethod
    def expr_ctx(cls, hits):
        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()
        ctx.register_table("hits", pl.from_pandas(hits).lazy())
        return ctx

    @pytest.fixture(scope="class")
    @classmethod
    def pandas_ctx(cls, hits):
        return _PandasContext({"hits": hits})

    def test_fixture_row_count_is_pinned(self, hits):
        assert len(hits) == 10

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_backends_agree(self, query_id, expr_ctx, pandas_ctx):
        from benchbox.core.clickbench.dataframe_queries import get_clickbench_query

        query = get_clickbench_query(query_id)
        assert query is not None

        expr_result = _normalized(_to_pandas_result(query.expression_impl(expr_ctx)))
        pandas_result = _normalized(_to_pandas_result(query.pandas_impl(pandas_ctx)))

        if query_id in EXPECTED_EMPTY:
            assert expr_result.empty and pandas_result.empty
            return
        assert len(expr_result) > 0 and len(pandas_result) > 0
        assert expr_result.columns.tolist() == pandas_result.columns.tolist()
        pd.testing.assert_frame_equal(expr_result, pandas_result, check_dtype=False)

    def test_q1_counts_all_rows(self, expr_ctx, pandas_ctx):
        from benchbox.core.clickbench.dataframe_queries import get_clickbench_query

        query = get_clickbench_query("Q1")
        assert _to_pandas_result(query.expression_impl(expr_ctx))["count"].tolist() == [10]
        assert _to_pandas_result(query.pandas_impl(pandas_ctx))["count"].tolist() == [10]

    def test_q4_average_user_id(self, expr_ctx, pandas_ctx):
        from benchbox.core.clickbench.dataframe_queries import get_clickbench_query

        query = get_clickbench_query("Q4")
        assert _to_pandas_result(query.expression_impl(expr_ctx))["avg_user_id"].tolist() == [4.350909328996494e16]
        assert _to_pandas_result(query.pandas_impl(pandas_ctx))["avg_user_id"].tolist() == [4.350909328996494e16]

    def test_q5_unique_users(self, expr_ctx, pandas_ctx):
        from benchbox.core.clickbench.dataframe_queries import get_clickbench_query

        query = get_clickbench_query("Q5")
        assert _to_pandas_result(query.expression_impl(expr_ctx))["uniq_users"].tolist() == [6]
        assert _to_pandas_result(query.pandas_impl(pandas_ctx))["uniq_users"].tolist() == [6]


