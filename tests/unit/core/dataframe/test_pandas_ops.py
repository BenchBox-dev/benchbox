from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")
dd = pytest.importorskip("dask.dataframe")

from benchbox.core.dataframe.pandas_ops import select, where

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def frames():
    pdf = pd.DataFrame({"a": ["x", "y", None, "x"], "b": [1, 2, 3, 4]})
    return pdf, dd.from_pandas(pdf, npartitions=2)


def test_where_matches_numpy_on_pandas(frames):
    pdf, _ = frames

    result = where(pdf["b"] > 2, pdf["b"] * 10, 0)

    assert list(result) == [0, 0, 30, 40]


def test_where_is_lazy_and_aligned_on_dask(frames):
    pdf, ddf = frames

    lazy = where(ddf["a"].str.contains("x", na=False), ddf["b"], -1)

    assert hasattr(lazy, "compute")
    computed = lazy.compute()
    assert list(computed) == [1, -1, -1, 4]
    assert list(computed.index) == list(pdf.index)


def test_where_assigns_back_to_dask_frame(frames):
    _, ddf = frames

    ddf["flag"] = where(ddf["b"] != 2, ddf["b"] / 2, float("nan"))

    assert ddf.compute()["flag"].isna().tolist() == [False, True, False, False]


def test_select_first_matching_condition_wins_on_dask(frames):
    _, ddf = frames

    lazy = select([ddf["b"] < 2, ddf["b"] < 4], ["low", "mid"], "high")

    assert list(lazy.compute()) == ["low", "mid", "mid", "high"]


def test_select_matches_numpy_on_pandas(frames):
    pdf, _ = frames

    result = select([pdf["b"] < 2, pdf["b"] < 4], ["low", "mid"], "high")

    assert list(result) == ["low", "mid", "mid", "high"]


def test_joinorder_null_mask_applies_to_dask_series(frames):
    import operator

    from benchbox.core.joinorder.dataframe_queries import _pandas_sql_compare

    pdf = pd.DataFrame({"a": [1.0, None, 3.0], "b": [2.0, 2.0, 3.0]})
    ddf = dd.from_pandas(pdf, npartitions=2)

    pandas_result = _pandas_sql_compare(pdf["a"], pdf["b"], operator.ne)
    dask_result = _pandas_sql_compare(ddf["a"], ddf["b"], operator.ne)

    assert list(dask_result.compute()) == list(pandas_result) == [True, False, False]
