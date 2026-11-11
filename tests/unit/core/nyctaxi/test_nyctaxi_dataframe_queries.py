# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import importlib
import sys
from datetime import datetime

import pytest

from benchbox.core.dataframe.query import QueryCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_QUERY_IDS = [f"Q{i}" for i in range(1, 26)]


def test_registry_import_does_not_require_pandas(monkeypatch):

    module_prefix = "benchbox.core.nyctaxi.dataframe_queries"
    for name in tuple(sys.modules):
        if name == module_prefix or name.startswith(f"{module_prefix}."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "pandas", None)

    module = importlib.import_module(module_prefix)

    assert len(module.NYCTAXI_DATAFRAME_QUERIES) == 25


class TestNYCTaxiQueryRegistry:
    def test_registry_imports_successfully(self):

        from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES

        assert len(NYCTAXI_DATAFRAME_QUERIES) > 0
        assert hasattr(NYCTAXI_DATAFRAME_QUERIES, "get_query_ids")

    def test_registry_has_25_queries(self):

        from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES

        queries = NYCTAXI_DATAFRAME_QUERIES.get_all_queries()
        assert len(queries) == 25

    def test_registry_benchmark_name(self):

        from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES

        assert NYCTAXI_DATAFRAME_QUERIES.benchmark == "nyctaxi"

    def test_get_query_by_id(self):

        from benchbox.core.nyctaxi.dataframe_queries import get_nyctaxi_query

        query = get_nyctaxi_query("Q1")
        assert query is not None
        assert query.query_id == "Q1"

    def test_get_nonexistent_query(self):

        from benchbox.core.nyctaxi.dataframe_queries import get_nyctaxi_query

        query = get_nyctaxi_query("Q99")
        assert query is None

    def test_list_queries(self):

        from benchbox.core.nyctaxi.dataframe_queries import list_nyctaxi_queries

        queries = list_nyctaxi_queries()
        assert len(queries) == 25

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_registered(self, query_id):

        from benchbox.core.nyctaxi.dataframe_queries import get_nyctaxi_query

        query = get_nyctaxi_query(query_id)
        assert query is not None, f"Query {query_id} should be registered"

    def test_queries_have_both_implementations(self):

        from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES

        for query in NYCTAXI_DATAFRAME_QUERIES.get_all_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas impl"

    def test_all_queries_callable(self):

        from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES

        for query in NYCTAXI_DATAFRAME_QUERIES.get_all_queries():
            assert callable(query.expression_impl), f"{query.query_id} expression_impl not callable"
            assert callable(query.pandas_impl), f"{query.query_id} pandas_impl not callable"

    def test_query_descriptions_not_empty(self):

        from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES

        for query in NYCTAXI_DATAFRAME_QUERIES.get_all_queries():
            assert query.description, f"{query.query_id} missing description"
            assert len(query.description) > 10, f"{query.query_id} description too short"

    def test_query_names_not_empty(self):

        from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES

        for query in NYCTAXI_DATAFRAME_QUERIES.get_all_queries():
            assert query.query_name, f"{query.query_id} missing query_name"


class TestNYCTaxiQueryCategories:
    def test_temporal_queries_have_analytical(self):

        from benchbox.core.nyctaxi.dataframe_queries import get_nyctaxi_query

        for i in range(1, 5):
            query = get_nyctaxi_query(f"Q{i}")
            assert QueryCategory.ANALYTICAL in query.categories, f"Q{i} should have ANALYTICAL"

    def test_geographic_queries_have_join(self):

        from benchbox.core.nyctaxi.dataframe_queries import get_nyctaxi_query

        for i in range(5, 9):
            query = get_nyctaxi_query(f"Q{i}")
            assert QueryCategory.JOIN in query.categories or QueryCategory.MULTI_JOIN in query.categories, (
                f"Q{i} should have JOIN or MULTI_JOIN"
            )

    def test_financial_queries_have_aggregate(self):

        from benchbox.core.nyctaxi.dataframe_queries import get_nyctaxi_query

        for i in range(9, 13):
            query = get_nyctaxi_query(f"Q{i}")
            assert QueryCategory.AGGREGATE in query.categories, f"Q{i} should have AGGREGATE"

    def test_complex_queries_have_analytical(self):

        from benchbox.core.nyctaxi.dataframe_queries import get_nyctaxi_query

        for i in range(19, 23):
            query = get_nyctaxi_query(f"Q{i}")
            assert QueryCategory.ANALYTICAL in query.categories, f"Q{i} should have ANALYTICAL"

    def test_point_queries_have_filter_and_aggregate(self):

        from benchbox.core.nyctaxi.dataframe_queries import get_nyctaxi_query

        for i in [23, 24]:
            query = get_nyctaxi_query(f"Q{i}")
            assert QueryCategory.FILTER in query.categories, f"Q{i} should have FILTER"
            assert QueryCategory.AGGREGATE in query.categories, f"Q{i} should have AGGREGATE"

    def test_baseline_scan_query(self):

        from benchbox.core.nyctaxi.dataframe_queries import get_nyctaxi_query

        query = get_nyctaxi_query("Q25")
        assert QueryCategory.SCAN in query.categories, "Q25 should have SCAN"

    def test_double_join_query(self):

        from benchbox.core.nyctaxi.dataframe_queries import get_nyctaxi_query

        query = get_nyctaxi_query("Q7")
        assert QueryCategory.MULTI_JOIN in query.categories, "Q7 should have MULTI_JOIN"


class TestNYCTaxiParameters:
    def test_all_queries_have_parameters(self):

        from benchbox.core.nyctaxi.dataframe_queries.parameters import NYCTAXI_DEFAULT_PARAMS

        assert len(NYCTAXI_DEFAULT_PARAMS) == 25

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_parameter_entries_exist(self, query_id):

        from benchbox.core.nyctaxi.dataframe_queries.parameters import NYCTAXI_DEFAULT_PARAMS

        assert query_id in NYCTAXI_DEFAULT_PARAMS, f"Missing parameters for {query_id}"

    def test_date_range_parameters(self):

        from benchbox.core.nyctaxi.dataframe_queries.parameters import NYCTAXI_DEFAULT_PARAMS

        for qid in [f"Q{i}" for i in range(1, 25)]:
            params = NYCTAXI_DEFAULT_PARAMS[qid]
            assert "start_date" in params, f"{qid} missing start_date"
            assert "end_date" in params, f"{qid} missing end_date"

    def test_q24_has_zone_id(self):

        from benchbox.core.nyctaxi.dataframe_queries.parameters import NYCTAXI_DEFAULT_PARAMS

        assert "zone_id" in NYCTAXI_DEFAULT_PARAMS["Q24"]
        assert NYCTAXI_DEFAULT_PARAMS["Q24"]["zone_id"] == 132

    def test_q25_has_no_parameters(self):

        from benchbox.core.nyctaxi.dataframe_queries.parameters import NYCTAXI_DEFAULT_PARAMS

        assert NYCTAXI_DEFAULT_PARAMS["Q25"] == {}

    def test_get_parameters_returns_container(self):

        from benchbox.core.nyctaxi.dataframe_queries.parameters import NYCTaxiParameters, get_parameters

        result = get_parameters("Q1")
        assert isinstance(result, NYCTaxiParameters)
        assert result.query_id == "Q1"

    def test_get_parameters_with_defaults(self):

        from benchbox.core.nyctaxi.dataframe_queries.parameters import get_parameters

        result = get_parameters("Q1")
        assert result.get("start_date") == datetime(2019, 1, 1)
        assert result.get("nonexistent", "fallback") == "fallback"

    def test_query_date_bounds_are_native_temporal_values(self):
        from benchbox.core.nyctaxi.dataframe_queries.queries import _date_bounds

        start, end = _date_bounds("Q1", "2000-01-01", "2000-01-02")

        assert start == datetime(2019, 1, 1)
        assert end == datetime(2019, 1, 31)

    @pytest.mark.parametrize("query_id", [f"Q{i}" for i in range(1, 25)])
    def test_date_parameters_are_native_datetimes(self, query_id):
        from benchbox.core.nyctaxi.dataframe_queries.parameters import get_parameters

        params = get_parameters(query_id)

        assert isinstance(params.get("start_date"), datetime)
        assert isinstance(params.get("end_date"), datetime)

    def test_production_date_bounds_filter_polars_timestamp_column(self):
        pl = pytest.importorskip("polars")

        from benchbox.core.nyctaxi.dataframe_queries.queries import _expression_window
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table(
            "trips",
            pl.DataFrame({"pickup_datetime": [datetime(2019, 1, 1), datetime(2020, 1, 1)]}),
        )

        result, _, _ = _expression_window(ctx, "Q1", "2000-01-01", "2000-01-02")

        assert result.collect().height == 1

    def test_production_date_bounds_filter_polars_raw_csv_timestamp_column(self, tmp_path):
        pl = pytest.importorskip("polars")

        from benchbox.core.nyctaxi.dataframe_queries.queries import _expression_window
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        class Benchmark:
            def get_schema(self):
                return {"trips": {"columns": {"pickup_datetime": {"type": "TIMESTAMP"}}}}

        csv_path = tmp_path / "trips.csv"
        csv_path.write_text("pickup_datetime\n2019-01-01 00:00:00\n2020-01-01 00:00:00\n")
        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()
        adapter.load_table(
            ctx,
            "trips",
            [csv_path],
            column_names=["pickup_datetime"],
            benchmark=Benchmark(),
        )

        result, _, _ = _expression_window(ctx, "Q1", "2000-01-01", "2000-01-02")

        assert ctx.get_table("trips").native.collect_schema()["pickup_datetime"] == pl.Datetime("us")
        assert result.collect().height == 1


class TestNYCTaxiBenchmarkRegistry:
    def test_nyctaxi_supports_dataframe(self):

        from benchbox.core.benchmark_registry import get_benchmark_metadata

        meta = get_benchmark_metadata("nyctaxi")
        assert meta is not None
        assert meta.get("supports_dataframe") is True


