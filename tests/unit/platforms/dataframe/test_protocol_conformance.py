from __future__ import annotations

import pytest

from benchbox.platforms.dataframe.protocol import LazyFrameLike

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_polars_lazyframe_satisfies_protocol() -> None:
    pl = pytest.importorskip("polars")
    lf = pl.LazyFrame({"a": [1, 2], "b": [3, 4]})
    assert isinstance(lf, LazyFrameLike)
    assert list(lf.columns) == ["a", "b"]


def test_polars_dataframe_satisfies_protocol() -> None:
    pl = pytest.importorskip("polars")
    df = pl.DataFrame({"a": [1, 2], "b": [3, 4]})
    assert isinstance(df, LazyFrameLike)
    assert list(df.columns) == ["a", "b"]


def test_pandas_dataframe_satisfies_protocol() -> None:
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    assert isinstance(df, LazyFrameLike)
    assert list(df.columns) == ["a", "b"]


def test_pyarrow_table_satisfies_protocol() -> None:
    pa = pytest.importorskip("pyarrow")
    table = pa.table({"a": [1, 2], "b": [3, 4]})

    assert isinstance(table, LazyFrameLike)


def test_protocol_rejects_non_dataframe() -> None:
    assert not isinstance({"a": [1, 2]}, LazyFrameLike)
    assert not isinstance([[1, 2], [3, 4]], LazyFrameLike)


def test_protocol_accepts_minimal_duck() -> None:

    class _Duck:
        @property
        def columns(self) -> list[str]:
            return ["x", "y"]

    assert isinstance(_Duck(), LazyFrameLike)


