from __future__ import annotations

from typing import Any

import pytest

FAMILIES = ["pandas", "dask"]


def build_context(family: str, tables: dict[str, Any], npartitions: int = 2) -> Any:
    import pandas as pd

    if family == "pandas":
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, frame in tables.items():
            ctx.register_table(name, frame.copy())
        return ctx
    dd = pytest.importorskip("dask.dataframe")
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    ctx = DaskDataFrameAdapter(execution_engine="threaded").create_context()
    for name, frame in tables.items():
        assert isinstance(frame, pd.DataFrame)
        ctx.register_table(name, dd.from_pandas(frame, npartitions=npartitions))
    return ctx


def result_rows(result: Any) -> list[tuple[Any, ...]]:
    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    if hasattr(result, "compute"):
        result = result.compute()
    return materialize_rows(result)
