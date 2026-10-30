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
