from __future__ import annotations

from datetime import date
from typing import Any

import pytest

try:
    import dask
    import dask.dataframe as dd
    import pandas as pd

    DEPS_AVAILABLE = True
except ImportError:
    DEPS_AVAILABLE = False

from benchbox.core.read_primitives.dataframe_queries import approx_count_distinct_simple_pandas_impl

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(not DEPS_AVAILABLE, reason="Dask and/or Pandas not installed"),
]


class _DaskContext:
    platform = "Dask"

    def __init__(self, tables: dict[str, Any]) -> None:
        self._tables = tables

    def get_table(self, name: str) -> Any:
        return self._tables[name]

    def scalar_to_df(self, data: dict[str, Any]) -> Any:
        return pd.DataFrame({key: [value] for key, value in data.items()})


def test_approx_count_distinct_simple_uses_dask_hll(monkeypatch: pytest.MonkeyPatch) -> None:
    with dask.config.set({"dataframe.convert-string": False}):
        orders = dd.from_pandas(
            pd.DataFrame(
                {
                    "o_orderdate": [date(1995, 1, 2), date(1995, 1, 3), date(1994, 12, 31), date(1995, 1, 4)],
                    "o_custkey": [1, 2, 999, 2],
                }
            ),
            npartitions=2,
        )
    series_cls = type(orders["o_custkey"])
    original = series_cls.nunique_approx
    calls = {"count": 0}

    def spy_nunique_approx(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls["count"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(series_cls, "nunique_approx", spy_nunique_approx)

    result = approx_count_distinct_simple_pandas_impl(_DaskContext({"orders": orders}))

    assert calls["count"] == 1
    assert list(result.columns) == ["unique_customers"]
    assert result.loc[0, "unique_customers"] == pytest.approx(2, rel=0.01)


