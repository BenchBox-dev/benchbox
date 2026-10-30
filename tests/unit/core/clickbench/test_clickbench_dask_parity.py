from __future__ import annotations

from datetime import date

import pytest

from tests.fixtures.dataframe_parity import FAMILIES, build_context, result_rows

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _hits():
    import pandas as pd
    import pyarrow as pa

    dates = [date(2013, 7, 3), date(2013, 7, 1), None, date(2013, 7, 20), date(2013, 7, 9), date(2013, 7, 1)]
    return pd.DataFrame(
        {
            "EventDate": pd.array(dates, dtype=pd.ArrowDtype(pa.date32())),
            "URL": ["a", "b", "b", "c", "c", "c"],
            "SearchEngineID": [0, 0, 1, 0, 0, 0],
            "AdvEngineID": [0, 0, 0, 0, 0, 0],
            "TraficSourceID": [1, 1, 1, 1, 1, 1],
            "Referer": ["x", "x", "x", "y", "y", "y"],
        }
    )


def _rows(family, impl):
    return result_rows(impl(build_context(family, {"hits": _hits()})))


@pytest.mark.parametrize("family", FAMILIES)
def test_date_min_max_matches_pandas(family):
    from benchbox.core.clickbench.dataframe_queries import queries

    assert _rows(family, queries.q7_pandas_impl) == [(date(2013, 7, 1), date(2013, 7, 20))]


@pytest.mark.parametrize("family", FAMILIES)
def test_group_offset_slices_after_sorting(family):
    from benchbox.core.clickbench.dataframe_queries.queries import _make_group_pandas

    impl = _make_group_pandas("", "", "URL", "PageViews=URL:count", "PageViews:desc", "3", "1")

    assert _rows(family, impl) == [("b", 2), ("a", 1)]


@pytest.mark.parametrize("family", FAMILIES)
def test_derived_source_group_with_offset(family):
    from benchbox.core.clickbench.dataframe_queries.queries import _make_group_pandas

    impl = _make_group_pandas("", "source", "SearchEngineID,Src", "PageViews=URL:count", "PageViews:desc", "5", "1")

    assert _rows(family, impl) == [(0, "x", 2), (1, "", 1)]
