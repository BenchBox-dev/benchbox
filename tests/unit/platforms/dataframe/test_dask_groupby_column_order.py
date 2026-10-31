from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_nunique_mixed_with_other_aggregates_keeps_requested_column_order():
    dd = pytest.importorskip("dask.dataframe")
    import pandas as pd

    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    frame = pd.DataFrame({"k": [1, 1, 2, 2, 2], "x": [1, 1, 2, 3, 3], "y": [1.0, 2.0, 3.0, 4.0, 5.0]})
    adapter = DaskDataFrameAdapter(execution_engine="threaded")

    result = adapter.groupby_agg(
        dd.from_pandas(frame, npartitions=2),
        by="k",
        agg_spec={"sum_y": ("y", "sum"), "n_x": ("x", "nunique"), "cnt": ("y", "count")},
        as_index=False,
    ).compute()

    assert list(result.columns) == ["k", "sum_y", "n_x", "cnt"]
    assert result.sort_values("k").values.tolist() == [[1, 3.0, 1, 2], [2, 12.0, 2, 3]]


def test_nunique_with_as_index_keeps_group_keys_as_index():
    dd = pytest.importorskip("dask.dataframe")
    import pandas as pd

    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    frame = pd.DataFrame({"k": [10, 10, 20, 20, 30], "x": [1, 2, 3, 3, 4]})
    adapter = DaskDataFrameAdapter(execution_engine="threaded")

    result = adapter.groupby_agg(
        dd.from_pandas(frame, npartitions=2),
        by="k",
        agg_spec={"n_x": ("x", "nunique")},
        as_index=True,
    ).compute()

    assert result.index.name == "k"
    assert result[result["n_x"] > 1].index.tolist() == [10]


def test_nunique_with_as_index_and_composite_key_keeps_key_columns():
    dd = pytest.importorskip("dask.dataframe")
    import pandas as pd

    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    frame = pd.DataFrame({"a": [1, 1, 2], "b": [1, 2, 3], "x": [1, 2, 3]})
    adapter = DaskDataFrameAdapter(execution_engine="threaded")

    result = adapter.groupby_agg(
        dd.from_pandas(frame, npartitions=2),
        by=["a", "b"],
        agg_spec={"n_x": ("x", "nunique")},
        as_index=True,
    ).compute()

    assert {"a", "b", "n_x"} <= set(result.columns)
