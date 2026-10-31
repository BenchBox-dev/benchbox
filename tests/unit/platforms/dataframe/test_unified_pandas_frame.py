# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.platforms.dataframe.unified_pandas_frame import (
    UnifiedPandasFrame,
    UnifiedPandasGroupBy,
    _is_dask_df,
    _is_dataframe,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestIsDaskDataFrame:
    def test_pandas_dataframe_returns_false(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3]})
        assert _is_dask_df(df) is False

    def test_dask_dataframe_returns_true(self):

        try:
            import dask.dataframe as dd

            df = dd.from_pandas(
                __import__("pandas").DataFrame({"a": [1, 2, 3]}),
                npartitions=1,
            )
            assert _is_dask_df(df) is True
        except ImportError:
            pytest.skip("Dask not installed")

    def test_mock_with_dask_module_returns_true(self):

        mock_df = Mock()
        mock_df.__class__.__module__ = "dask.dataframe.core"
        assert _is_dask_df(mock_df) is True

    def test_mock_with_pandas_module_returns_false(self):

        mock_df = Mock()
        mock_df.__class__.__module__ = "pandas.core.frame"
        assert _is_dask_df(mock_df) is False


class TestIsDataFrame:
    def test_pandas_dataframe_returns_true(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3]})
        assert _is_dataframe(df) is True

    def test_pandas_series_returns_false(self):

        import pandas as pd

        series = pd.Series([1, 2, 3])
        assert _is_dataframe(series) is False

    def test_dict_returns_false(self):

        assert _is_dataframe({"a": 1}) is False

    def test_list_returns_false(self):

        assert _is_dataframe([1, 2, 3]) is False


class TestUnifiedPandasFrameInit:
    def test_init_stores_df_and_adapter(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3]})
        adapter = Mock()

        wrapper = UnifiedPandasFrame(df, adapter)

        assert wrapper._df is df
        assert wrapper._adapter is adapter

    def test_native_property_returns_underlying_df(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3]})
        adapter = Mock()

        wrapper = UnifiedPandasFrame(df, adapter)

        assert wrapper.native is df


class TestUnifiedPandasFrameProperties:
    def test_columns_returns_df_columns(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1], "b": [2]})
        wrapper = UnifiedPandasFrame(df, Mock())

        assert list(wrapper.columns) == ["a", "b"]

    def test_dtypes_returns_df_dtypes(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1], "b": ["x"]})
        wrapper = UnifiedPandasFrame(df, Mock())

        dtypes = wrapper.dtypes
        assert "a" in dtypes.index
        assert "b" in dtypes.index

    def test_shape_returns_df_shape(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        wrapper = UnifiedPandasFrame(df, Mock())

        assert wrapper.shape == (3, 2)


class TestUnifiedPandasFrameItemAccess:
    def test_getitem_column_returns_series(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper["a"]
        assert list(result) == [1, 2, 3]

    def test_getitem_columns_returns_wrapper(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper[["a", "b"]]
        assert isinstance(result, UnifiedPandasFrame)
        assert list(result.columns) == ["a", "b"]

    def test_getitem_mask_returns_wrapper(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3]})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper[df["a"] > 1]
        assert isinstance(result, UnifiedPandasFrame)
        assert len(result) == 2

    def test_setitem_updates_column(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3]})
        wrapper = UnifiedPandasFrame(df, Mock())

        wrapper["b"] = [4, 5, 6]
        assert list(df["b"]) == [4, 5, 6]


class TestUnifiedPandasFrameCopy:
    def test_copy_returns_wrapper(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3]})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper.copy()
        assert isinstance(result, UnifiedPandasFrame)
        assert result._df is not df

    def test_copy_with_dask_uses_shallow(self):

        try:
            import dask.dataframe as dd
            import pandas as pd

            pandas_df = pd.DataFrame({"a": [1, 2, 3]})
            dask_df = dd.from_pandas(pandas_df, npartitions=1)
            adapter = Mock()

            wrapper = UnifiedPandasFrame(dask_df, adapter)
            result = wrapper.copy()

            assert isinstance(result, UnifiedPandasFrame)
        except ImportError:
            pytest.skip("Dask not installed")


class TestUnifiedPandasFrameMerge:
    def test_merge_returns_wrapper(self):

        import pandas as pd

        left = pd.DataFrame({"key": [1, 2], "a": ["x", "y"]})
        right = pd.DataFrame({"key": [1, 2], "b": ["p", "q"]})
        adapter = Mock()

        left_wrapper = UnifiedPandasFrame(left, adapter)
        right_wrapper = UnifiedPandasFrame(right, adapter)

        result = left_wrapper.merge(right_wrapper, on="key")

        assert isinstance(result, UnifiedPandasFrame)
        assert "a" in result.columns
        assert "b" in result.columns

    def test_merge_with_native_df(self):

        import pandas as pd

        left = pd.DataFrame({"key": [1, 2], "a": ["x", "y"]})
        right = pd.DataFrame({"key": [1, 2], "b": ["p", "q"]})
        adapter = Mock()

        left_wrapper = UnifiedPandasFrame(left, adapter)

        result = left_wrapper.merge(right, on="key")

        assert isinstance(result, UnifiedPandasFrame)
        assert "b" in result.columns

    def test_merge_with_left_on_right_on(self):

        import pandas as pd

        left = pd.DataFrame({"l_key": [1, 2], "a": ["x", "y"]})
        right = pd.DataFrame({"r_key": [1, 2], "b": ["p", "q"]})
        adapter = Mock()

        left_wrapper = UnifiedPandasFrame(left, adapter)

        result = left_wrapper.merge(right, left_on="l_key", right_on="r_key")

        assert isinstance(result, UnifiedPandasFrame)
        assert len(result) == 2


class TestUnifiedPandasFrameSort:
    def test_sort_values_returns_wrapper(self):

        import pandas as pd

        df = pd.DataFrame({"a": [3, 1, 2]})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper.sort_values("a")

        assert isinstance(result, UnifiedPandasFrame)
        assert list(result._df["a"]) == [1, 2, 3]

    def test_sort_values_descending(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3]})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper.sort_values("a", ascending=False)

        assert list(result._df["a"]) == [3, 2, 1]


class TestUnifiedPandasFrameHead:
    def test_head_returns_wrapper(self):

        import pandas as pd

        df = pd.DataFrame({"a": range(10)})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper.head(3)

        assert isinstance(result, UnifiedPandasFrame)
        assert len(result) == 3

    def test_dask_head_after_sort_and_projection_keeps_sort_column(self):
        import dask.dataframe as dd
        import pandas as pd

        pdf = pd.DataFrame({"a": [5, 3, 1, 4, 2, 6], "b": list("xyxyxy"), "c": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]})
        pdf["diff"] = pdf["c"] - pdf["a"]
        frame = UnifiedPandasFrame(dd.from_pandas(pdf, npartitions=2), Mock())

        result = frame.sort_values(["diff", "b"])[["a", "b"]].head(3).compute()

        expected = pdf.sort_values(["diff", "b"])[["a", "b"]].head(3)
        assert result.to_dict("records") == expected.to_dict("records")

    def test_tail_returns_wrapper(self):

        import pandas as pd

        df = pd.DataFrame({"a": range(10)})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper.tail(3)

        assert isinstance(result, UnifiedPandasFrame)
        assert len(result) == 3


class TestUnifiedPandasFrameRename:
    def test_rename_returns_wrapper(self):

        import pandas as pd

        df = pd.DataFrame({"old_name": [1, 2, 3]})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper.rename(columns={"old_name": "new_name"})

        assert isinstance(result, UnifiedPandasFrame)
        assert "new_name" in result.columns
        assert "old_name" not in result.columns


class TestUnifiedPandasFrameDrop:
    def test_drop_columns_returns_wrapper(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper.drop(columns=["b"])

        assert isinstance(result, UnifiedPandasFrame)
        assert "b" not in result.columns
        assert "a" in result.columns


class TestUnifiedPandasFrameCompute:
    def test_compute_on_pandas_returns_df(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 2, 3]})
        wrapper = UnifiedPandasFrame(df, Mock())

        result = wrapper.compute()

        assert result is df

    def test_compute_on_dask_materializes(self):

        try:
            import dask.dataframe as dd
            import pandas as pd

            pandas_df = pd.DataFrame({"a": [1, 2, 3]})
            dask_df = dd.from_pandas(pandas_df, npartitions=1)
            wrapper = UnifiedPandasFrame(dask_df, Mock())

            result = wrapper.compute()

            assert isinstance(result, pd.DataFrame)
            assert list(result["a"]) == [1, 2, 3]
        except ImportError:
            pytest.skip("Dask not installed")


class TestUnifiedPandasFrameLen:
    def test_len_returns_row_count(self):

        import pandas as pd

        df = pd.DataFrame({"a": range(5)})
        wrapper = UnifiedPandasFrame(df, Mock())

        assert len(wrapper) == 5


class TestUnifiedPandasFrameRepr:
    def test_repr_includes_type_name(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1]})
        wrapper = UnifiedPandasFrame(df, Mock())

        repr_str = repr(wrapper)

        assert "UnifiedPandasFrame" in repr_str
        assert "DataFrame" in repr_str


class TestUnifiedPandasGroupByInit:
    def test_init_stores_parameters(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 1, 2], "b": [10, 20, 30]})
        adapter = Mock()
        adapter.groupby_agg = Mock(return_value=df)

        groupby = UnifiedPandasGroupBy(
            df,
            ["a"],
            adapter,
            as_index=False,
            kwargs={"sort": False},
        )

        assert groupby._df is df
        assert groupby._by == ["a"]
        assert groupby._adapter is adapter
        assert groupby._as_index is False
        assert groupby._kwargs == {"sort": False}


class TestUnifiedPandasGroupByAgg:
    def test_agg_routes_to_adapter(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 1, 2], "b": [10, 20, 30]})
        result_df = pd.DataFrame({"a": [1, 2], "sum_b": [30, 30]})

        adapter = Mock()
        adapter.groupby_agg = Mock(return_value=result_df)

        groupby = UnifiedPandasGroupBy(df, ["a"], adapter, as_index=False)
        result = groupby.agg(sum_b=("b", "sum"))

        adapter.groupby_agg.assert_called_once_with(
            df,
            ["a"],
            {"sum_b": ("b", "sum")},
            as_index=False,
        )
        assert isinstance(result, UnifiedPandasFrame)

    def test_agg_with_dict_argument(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 1, 2], "b": [10, 20, 30]})
        result_df = pd.DataFrame({"a": [1, 2], "sum_b": [30, 30]})

        adapter = Mock()
        adapter.groupby_agg = Mock(return_value=result_df)

        groupby = UnifiedPandasGroupBy(df, ["a"], adapter)
        result = groupby.agg({"sum_b": ("b", "sum")})

        adapter.groupby_agg.assert_called_once()
        assert isinstance(result, UnifiedPandasFrame)

    def test_agg_forwards_groupby_kwargs(self):
        import pandas as pd

        df = pd.DataFrame({"a": [1, 1, 2], "b": [10, 20, 30]})
        result_df = pd.DataFrame({"a": [1, 2], "sum_b": [30, 30]})

        adapter = Mock()
        adapter.groupby_agg = Mock(return_value=result_df)

        groupby = UnifiedPandasGroupBy(df, ["a"], adapter, as_index=False, kwargs={"dropna": False})
        groupby.agg(sum_b=("b", "sum"))

        adapter.groupby_agg.assert_called_once_with(
            df,
            ["a"],
            {"sum_b": ("b", "sum")},
            as_index=False,
            dropna=False,
        )

    def test_null_group_survives_production_wrapper_path(self):
        import pandas as pd

        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        df = pd.DataFrame({"g": ["x", None, "x"], "v": [1, 2, 3]})
        adapter = PandasDataFrameAdapter()
        result = UnifiedPandasFrame(df, adapter).groupby("g", as_index=False, dropna=False).agg(total=("v", "sum"))
        native = result.compute() if hasattr(result, "compute") else result
        frame = native._df if isinstance(native, UnifiedPandasFrame) else native
        assert len(frame) == 2
        assert frame["total"].sum() == 6


class TestUnifiedPandasGroupByGetattr:
    def test_getattr_proxies_to_native_groupby(self):

        import pandas as pd

        df = pd.DataFrame({"a": [1, 1, 2], "b": [10, 20, 30]})
        adapter = Mock()

        groupby = UnifiedPandasGroupBy(df, ["a"], adapter)

        result = groupby.size()

        assert len(result) == 2


class TestUnifiedPandasFrameIntegration:
    def test_chained_operations(self):

        import pandas as pd

        df = pd.DataFrame(
            {
                "group": ["A", "A", "B", "B"],
                "value": [1, 2, 3, 4],
            }
        )
        adapter = Mock()
        adapter.groupby_agg = lambda df, by, agg, as_index: (
            df.groupby(by, as_index=as_index).agg(**agg).reset_index()
            if not as_index
            else df.groupby(by, as_index=as_index).agg(**agg)
        )

        wrapper = UnifiedPandasFrame(df, adapter)

        result = wrapper[wrapper["value"] > 1].copy().sort_values("value", ascending=False).head(2)

        assert isinstance(result, UnifiedPandasFrame)
        assert len(result) == 2

    def test_groupby_with_pandas_adapter(self):

        import pandas as pd

        df = pd.DataFrame(
            {
                "group": ["A", "A", "B"],
                "value": [10, 20, 30],
            }
        )

        adapter = Mock()
        adapter.groupby_agg = lambda df, by, agg, as_index: df.groupby(by, as_index=False).agg(**agg)

        wrapper = UnifiedPandasFrame(df, adapter)
        result = wrapper.groupby("group", as_index=False).agg(total=("value", "sum"))

        assert isinstance(result, UnifiedPandasFrame)
        assert "total" in result.columns
        assert len(result) == 2
