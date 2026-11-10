from __future__ import annotations

import pytest

from benchbox.core.dataframe.query import DataFrameQuery
from benchbox.core.results.timer_scope import TIMING_METHOD_LEGACY, TIMING_METHOD_QUERY_ONLY
from benchbox.platforms.dataframe._result_helpers import dataframe_result_bytes
from benchbox.platforms.dataframe.expression_family import ExpressionFamilyAdapter
from benchbox.platforms.dataframe.pandas_family import PandasFamilyAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestDataframeResultBytes:
    def test_arrow_table_uses_nbytes(self):
        pa = pytest.importorskip("pyarrow")
        table = pa.table({"a": [1, 2, 3]})
        assert dataframe_result_bytes(table) == table.nbytes

    def test_pandas_uses_deep_memory_usage(self):
        pd = pytest.importorskip("pandas")
        frame = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        assert dataframe_result_bytes(frame) == int(frame.memory_usage(deep=True).sum())

    def test_polars_uses_estimated_size(self):
        pl = pytest.importorskip("polars")
        frame = pl.DataFrame({"a": [1, 2, 3]})
        assert dataframe_result_bytes(frame) == frame.estimated_size()

    def test_unmeasurable_result_is_none(self):
        assert dataframe_result_bytes(object()) is None


class TestFamilyContract:
    def test_pandas_and_expression_families_declare_query_only(self):
        assert PandasFamilyAdapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert ExpressionFamilyAdapter.timing_method == TIMING_METHOD_QUERY_ONLY

    def test_pyspark_stays_legacy(self):
        from benchbox.platforms.dataframe.pyspark_df import PySparkDataFrameAdapter

        assert PySparkDataFrameAdapter.timing_method == TIMING_METHOD_LEGACY


class TestPandasExecuteQuery:
    @pytest.fixture
    def adapter(self):
        pytest.importorskip("pandas")
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        return PandasDataFrameAdapter()

    def test_success_reports_request_and_bytes(self, adapter):
        import pandas as pd

        frame = pd.DataFrame({"a": [1, 2, 3]})
        query = DataFrameQuery(query_id="Q1", query_name="q", description="q", pandas_impl=lambda ctx: frame)
        result = adapter.execute_query(adapter.create_context(), query)
        assert result["status"] == "SUCCESS"
        assert result["request_count"] == 1
        assert result["result_bytes"] == int(frame.memory_usage(deep=True).sum())

    def test_profiled_path_reports_the_same_fields(self, adapter):
        import pandas as pd

        frame = pd.DataFrame({"a": [1, 2, 3]})
        query = DataFrameQuery(query_id="Q1", query_name="q", description="q", pandas_impl=lambda ctx: frame)
        result, profile = adapter.execute_query_profiled(adapter.create_context(), query, track_memory=False)
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0
        assert result["execution_time_seconds"] == pytest.approx(profile.execution_time_ms / 1000.0)

    def test_failure_has_no_timing_fields(self, adapter):
        def boom(ctx):
            raise RuntimeError("nope")

        query = DataFrameQuery(query_id="Q1", query_name="q", description="q", pandas_impl=boom)
        result = adapter.execute_query(adapter.create_context(), query)
        assert result["status"] == "FAILED"
        assert "request_count" not in result


class TestPolarsExecuteQuery:
    def test_expression_family_reports_arrow_free_bytes(self):
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        adapter = PolarsDataFrameAdapter()
        frame = pl.DataFrame({"a": [1, 2, 3]}).lazy()
        query = DataFrameQuery(query_id="Q1", query_name="q", description="q", expression_impl=lambda ctx: frame)
        result = adapter.execute_query(adapter.create_context(), query)
        assert result["status"] == "SUCCESS"
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0
