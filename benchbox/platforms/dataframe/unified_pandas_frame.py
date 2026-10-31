# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from benchbox.platforms.dataframe.pandas_family import PandasFamilyAdapter

logger = logging.getLogger(__name__)

DF = TypeVar("DF")


def _is_dask_df(df: Any) -> bool:
    type_module = type(df).__module__
    return "dask" in type_module


def _identity_partition(df: Any) -> Any:
    return df


def _is_dataframe(obj: Any) -> bool:
    if not hasattr(obj, "columns"):
        return False

    type_name = type(obj).__name__
    return "Series" not in type_name


class UnifiedPandasFrame(Generic[DF]):
    def __init__(self, df: DF, adapter: PandasFamilyAdapter[DF]) -> None:
        object.__setattr__(self, "_df", df)
        object.__setattr__(self, "_adapter", adapter)

    @property
    def native(self) -> DF:
        return self._df

    @property
    def columns(self) -> Any:
        return self._df.columns

    @property
    def dtypes(self) -> Any:
        return self._df.dtypes

    @property
    def shape(self) -> tuple[int, ...]:
        return self._df.shape

    @property
    def iloc(self) -> Any:
        return self._df.iloc

    @property
    def loc(self) -> Any:
        return self._df.loc

    @property
    def values(self) -> Any:
        return self._df.values

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._df, name)
        if callable(attr):
            return self._wrap_method(attr, name)
        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_df", "_adapter"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._df, name, value)

    def _wrap_method(self, method: Any, name: str) -> Any:

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = method(*args, **kwargs)
            if _is_dataframe(result):
                return UnifiedPandasFrame(result, self._adapter)
            return result

        return wrapped

    def __getitem__(self, key: Any) -> UnifiedPandasFrame[DF] | Any:
        result = self._df[key]
        if _is_dataframe(result):
            return UnifiedPandasFrame(result, self._adapter)
        return result

    def __setitem__(self, key: Any, value: Any) -> None:
        if isinstance(value, UnifiedPandasFrame):
            value = value._df
        self._df[key] = value

    def groupby(
        self,
        by: str | list[str],
        as_index: bool = True,
        **kwargs: Any,
    ) -> UnifiedPandasGroupBy[DF]:
        by_list = [by] if isinstance(by, str) else list(by)
        return UnifiedPandasGroupBy(
            self._df,
            by_list,
            self._adapter,
            as_index=as_index,
            kwargs=kwargs,
        )

    def merge(
        self,
        right: UnifiedPandasFrame[DF] | DF,
        on: str | list[str] | None = None,
        left_on: str | list[str] | None = None,
        right_on: str | list[str] | None = None,
        how: str = "inner",
        **kwargs: Any,
    ) -> UnifiedPandasFrame[DF]:
        right_df = right._df if isinstance(right, UnifiedPandasFrame) else right

        result = self._df.merge(
            right_df,
            on=on,
            left_on=left_on,
            right_on=right_on,
            how=how,
            **kwargs,
        )
        return UnifiedPandasFrame(result, self._adapter)

    def copy(self, deep: bool = True) -> UnifiedPandasFrame[DF]:
        result = self._df.copy(deep=False) if _is_dask_df(self._df) else self._df.copy(deep=deep)
        return UnifiedPandasFrame(result, self._adapter)

    def sort_values(
        self,
        by: str | list[str],
        ascending: bool | list[bool] = True,
        **kwargs: Any,
    ) -> UnifiedPandasFrame[DF]:
        result = self._df.sort_values(by=by, ascending=ascending, **kwargs)
        return UnifiedPandasFrame(result, self._adapter)

    def head(self, n: int = 5) -> UnifiedPandasFrame[DF]:
        if _is_dask_df(self._df):
            fenced = self._df.map_partitions(_identity_partition, meta=self._df._meta)
            result = fenced.head(n, npartitions=-1, compute=False)
        else:
            result = self._df.head(n)
        return UnifiedPandasFrame(result, self._adapter)

    def tail(self, n: int = 5) -> UnifiedPandasFrame[DF]:
        result = self._df.tail(n)
        return UnifiedPandasFrame(result, self._adapter)

    def rename(self, columns: dict[str, str] | None = None, **kwargs: Any) -> UnifiedPandasFrame[DF]:
        result = self._df.rename(columns=columns, **kwargs)
        return UnifiedPandasFrame(result, self._adapter)

    def drop(
        self,
        labels: str | list[str] | None = None,
        columns: str | list[str] | None = None,
        **kwargs: Any,
    ) -> UnifiedPandasFrame[DF]:
        result = self._df.drop(labels=labels, columns=columns, **kwargs)
        return UnifiedPandasFrame(result, self._adapter)

    def compute(self) -> Any:
        if hasattr(self._df, "compute"):
            return self._df.compute()
        return self._df

    def __len__(self) -> int:
        return len(self._df)

    def __repr__(self) -> str:
        return f"UnifiedPandasFrame({type(self._df).__name__})"


class UnifiedPandasGroupBy(Generic[DF]):
    def __init__(
        self,
        df: DF,
        by: list[str],
        adapter: PandasFamilyAdapter[DF],
        as_index: bool = True,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._df = df
        self._by = by
        self._adapter = adapter
        self._as_index = as_index
        self._kwargs = kwargs or {}

    def agg(self, *args: Any, **kwargs: Any) -> UnifiedPandasFrame[DF]:
        agg_spec = args[0] if args and isinstance(args[0], dict) else kwargs

        result = self._adapter.groupby_agg(
            self._df,
            self._by,
            agg_spec,
            as_index=self._as_index,
            **self._kwargs,
        )
        return UnifiedPandasFrame(result, self._adapter)

    def size(self) -> Any:
        result = self._native_groupby().size()
        if _is_dask_df(self._df) and not self._as_index:
            result = result.reset_index()
            count_col = next(column for column in result.columns if column not in self._by)
            if count_col != "size":
                result = result.rename(columns={count_col: "size"})
        return result

    def _native_groupby(self) -> Any:
        if _is_dask_df(self._df):
            return self._df.groupby(self._by, **self._kwargs)
        return self._df.groupby(self._by, as_index=self._as_index, **self._kwargs)

    def __getitem__(self, key: str | list[str]) -> Any:
        return self._native_groupby()[key]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._native_groupby(), name)
