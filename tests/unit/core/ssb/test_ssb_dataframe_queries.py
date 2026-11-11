# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from typing import Any

import pytest

from benchbox.core.dataframe.query import QueryCategory

try:
    import pandas as pd
    import polars as pl

    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter
    from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame
    from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

    DEPS_AVAILABLE = True
except ImportError:
    pd = None
    pl = None
    PolarsDataFrameAdapter = None
    UnifiedLazyFrame = Any
    UnifiedPandasFrame = Any
    DEPS_AVAILABLE = False

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_QUERY_IDS = [
    "Q1.1",
    "Q1.2",
    "Q1.3",
    "Q2.1",
    "Q2.2",
    "Q2.3",
    "Q3.1",
    "Q3.2",
    "Q3.3",
    "Q3.4",
    "Q4.1",
    "Q4.2",
    "Q4.3",
]

QUERY_RESULT_COLUMNS = {
    "Q1.1": ["revenue"],
    "Q1.2": ["revenue"],
    "Q1.3": ["revenue"],
    "Q2.1": ["d_year", "p_brand1", "revenue"],
    "Q2.2": ["d_year", "p_brand1", "revenue"],
    "Q2.3": ["d_year", "p_brand1", "revenue"],
    "Q3.1": ["c_nation", "s_nation", "d_year", "revenue"],
    "Q3.2": ["c_city", "s_city", "d_year", "revenue"],
    "Q3.3": ["c_city", "s_city", "d_year", "revenue"],
    "Q3.4": ["c_city", "s_city", "d_year", "revenue"],
    "Q4.1": ["d_year", "c_nation", "profit"],
    "Q4.2": ["d_year", "s_nation", "p_category", "profit"],
    "Q4.3": ["d_year", "s_city", "p_brand1", "profit"],
}


def _to_pandas_result(result: Any) -> pd.DataFrame:
    if isinstance(result, UnifiedLazyFrame):
        result = result.collect()
    elif isinstance(result, UnifiedPandasFrame):
        result = result.native

    if isinstance(result, pl.LazyFrame):
        result = result.collect()

    if isinstance(result, pl.DataFrame):
        return result.to_pandas()
    if isinstance(result, pd.DataFrame):
        return result.copy()

    raise TypeError(f"Unsupported result type: {type(result)!r}")


class _PandasContext:
    def __init__(self, tables: dict[str, pd.DataFrame]) -> None:
        self._tables = tables

    def get_table(self, name: str) -> pd.DataFrame:
        return self._tables[name.lower()]


class TestSSBQueryRegistry:
    def test_registry_imports_successfully(self):

        from benchbox.core.ssb.dataframe_queries import SSB_DATAFRAME_QUERIES

        assert SSB_DATAFRAME_QUERIES is not None

    def test_registry_has_13_queries(self):
        from benchbox.core.ssb.dataframe_queries import SSB_DATAFRAME_QUERIES

        queries = SSB_DATAFRAME_QUERIES.get_all_queries()
        assert len(queries) == 13

    def test_registry_benchmark_name(self):

        from benchbox.core.ssb.dataframe_queries import SSB_DATAFRAME_QUERIES

        assert SSB_DATAFRAME_QUERIES.benchmark == "ssb"

    def test_get_query_by_id(self):

        from benchbox.core.ssb.dataframe_queries import get_ssb_query

        query = get_ssb_query("Q1.1")
        assert query is not None
        assert query.query_id == "Q1.1"

    def test_get_nonexistent_query(self):

        from benchbox.core.ssb.dataframe_queries import get_ssb_query

        query = get_ssb_query("Q99")
        assert query is None

    def test_list_queries(self):

        from benchbox.core.ssb.dataframe_queries import list_ssb_queries

        queries = list_ssb_queries()
        assert len(queries) == 13

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_registered(self, query_id):

        from benchbox.core.ssb.dataframe_queries import get_ssb_query

        query = get_ssb_query(query_id)
        assert query is not None, f"Query {query_id} should be registered"

    def test_queries_have_both_implementations(self):

        from benchbox.core.ssb.dataframe_queries import SSB_DATAFRAME_QUERIES

        for query in SSB_DATAFRAME_QUERIES.get_all_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas impl"

    def test_all_queries_callable(self):

        from benchbox.core.ssb.dataframe_queries import SSB_DATAFRAME_QUERIES

        for query in SSB_DATAFRAME_QUERIES.get_all_queries():
            assert callable(query.expression_impl), f"{query.query_id} expression_impl not callable"
            assert callable(query.pandas_impl), f"{query.query_id} pandas_impl not callable"

    def test_query_descriptions_not_empty(self):

        from benchbox.core.ssb.dataframe_queries import SSB_DATAFRAME_QUERIES

        for query in SSB_DATAFRAME_QUERIES.get_all_queries():
            assert query.description, f"{query.query_id} missing description"
            assert len(query.description) > 10, f"{query.query_id} description too short"

    def test_query_names_not_empty(self):

        from benchbox.core.ssb.dataframe_queries import SSB_DATAFRAME_QUERIES

        for query in SSB_DATAFRAME_QUERIES.get_all_queries():
            assert query.query_name, f"{query.query_id} missing query_name"


class TestSSBQueryCategories:
    def test_flight1_queries_have_filter_aggregate(self):
        from benchbox.core.ssb.dataframe_queries import get_ssb_query

        for qid in ["Q1.1", "Q1.2", "Q1.3"]:
            query = get_ssb_query(qid)
            assert QueryCategory.FILTER in query.categories, f"{qid} should have FILTER"
            assert QueryCategory.AGGREGATE in query.categories, f"{qid} should have AGGREGATE"

    def test_flight2_queries_have_multi_join(self):
        from benchbox.core.ssb.dataframe_queries import get_ssb_query

        for qid in ["Q2.1", "Q2.2", "Q2.3"]:
            query = get_ssb_query(qid)
            assert QueryCategory.MULTI_JOIN in query.categories, f"{qid} should have MULTI_JOIN"

    def test_flight3_queries_have_multi_join(self):
        from benchbox.core.ssb.dataframe_queries import get_ssb_query

        for qid in ["Q3.1", "Q3.2", "Q3.3", "Q3.4"]:
            query = get_ssb_query(qid)
            assert QueryCategory.MULTI_JOIN in query.categories, f"{qid} should have MULTI_JOIN"

    def test_flight4_queries_have_multi_join(self):
        from benchbox.core.ssb.dataframe_queries import get_ssb_query

        for qid in ["Q4.1", "Q4.2", "Q4.3"]:
            query = get_ssb_query(qid)
            assert QueryCategory.MULTI_JOIN in query.categories, f"{qid} should have MULTI_JOIN"


class TestSSBParameters:
    def test_get_parameters_q1_1(self):
        from benchbox.core.ssb.dataframe_queries.parameters import get_parameters

        params = get_parameters("Q1.1")
        assert params.query_id == "Q1.1"
        assert params.get("year") == 1993
        assert params.get("discount_min") == 1
        assert params.get("discount_max") == 3
        assert params.get("quantity") == 25

    def test_get_parameters_q3_3(self):
        from benchbox.core.ssb.dataframe_queries.parameters import get_parameters

        params = get_parameters("Q3.3")
        assert params.get("c_city1") == "UNITED KI1"
        assert params.get("c_city2") == "UNITED KI5"
        assert params.get("s_city1") == "UNITED KI1"
        assert params.get("s_city2") == "UNITED KI5"

    def test_get_parameters_q4_2(self):
        from benchbox.core.ssb.dataframe_queries.parameters import get_parameters

        params = get_parameters("Q4.2")
        assert params.get("region") == "AMERICA"
        assert params.get("year1") == 1997
        assert params.get("year2") == 1998
        assert params.get("mfgr1") == "MFGR#1"
        assert params.get("mfgr2") == "MFGR#2"

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_have_parameters(self, query_id):

        from benchbox.core.ssb.dataframe_queries.parameters import get_parameters

        params = get_parameters(query_id)
        assert params.query_id == query_id
        assert len(params.params) > 0, f"{query_id} should have at least one parameter"

    def test_parameter_get_with_default(self):

        from benchbox.core.ssb.dataframe_queries.parameters import get_parameters

        params = get_parameters("Q1.1")
        assert params.get("nonexistent", "default") == "default"


class TestSSBBenchmarkRegistry:
    def test_ssb_supports_dataframe(self):

        from benchbox.core.benchmark_registry import get_benchmark_metadata

        meta = get_benchmark_metadata("ssb")
        assert meta is not None
        assert meta.get("supports_dataframe") is True


@pytest.mark.skipif(not DEPS_AVAILABLE, reason="pandas and/or polars not installed")
class TestSSBQueryExecutionBehavior:
    @pytest.fixture(scope="session")
    def ssb_tables(self):
        return {
            "date": pd.DataFrame(
                {
                    "d_datekey": [19930210, 19940115, 19950704, 19961211, 19971215, 19980120],
                    "d_year": [1993, 1994, 1995, 1996, 1997, 1998],
                    "d_yearmonthnum": [199302, 199401, 199507, 199612, 199712, 199801],
                    "d_yearmonth": ["Feb1993", "Jan1994", "Jul1995", "Dec1996", "Dec1997", "Jan1998"],
                    "d_weeknuminyear": [6, 3, 27, 50, 51, 4],
                }
            ),
            "customer": pd.DataFrame(
                {
                    "c_custkey": [1, 2, 3, 4, 5],
                    "c_city": ["EUROPE1", "UNITED ST1", "ASIA1", "UNITED KI1", "UNITED KI5"],
                    "c_nation": ["FRANCE", "UNITED STATES", "CHINA", "UNITED KINGDOM", "UNITED KINGDOM"],
                    "c_region": ["EUROPE", "AMERICA", "ASIA", "EUROPE", "EUROPE"],
                }
            ),
            "supplier": pd.DataFrame(
                {
                    "s_suppkey": [1, 2, 3, 4, 5],
                    "s_city": ["EUROPE2", "UNITED ST2", "ASIA2", "UNITED KI5", "UNITED ST3"],
                    "s_nation": ["GERMANY", "UNITED STATES", "CHINA", "UNITED KINGDOM", "UNITED STATES"],
                    "s_region": ["EUROPE", "AMERICA", "ASIA", "EUROPE", "AMERICA"],
                }
            ),
            "part": pd.DataFrame(
                {
                    "p_partkey": [1, 2, 3, 4],
                    "p_mfgr": ["MFGR#9", "MFGR#1", "MFGR#1", "MFGR#2"],
                    "p_category": ["OTHER", "MFGR#12", "MFGR#12", "MFGR#22"],
                    "p_brand1": ["OTHER#1", "MFGR#1210", "MFGR#1211", "MFGR#2221"],
                }
            ),
            "lineorder": pd.DataFrame(
                {
                    "lo_orderkey": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                    "lo_custkey": [1, 1, 1, 2, 1, 3, 2, 4, 4, 2],
                    "lo_partkey": [1, 1, 1, 2, 4, 1, 1, 1, 1, 3],
                    "lo_suppkey": [1, 1, 1, 2, 2, 3, 2, 4, 4, 5],
                    "lo_orderdate": [
                        19930210,
                        19930210,
                        19940115,
                        19971215,
                        19980120,
                        19950704,
                        19961211,
                        19961211,
                        19971215,
                        19980120,
                    ],
                    "lo_quantity": [20, 30, 30, 10, 12, 5, 8, 9, 11, 13],
                    "lo_discount": [2, 2, 2, 1, 1, 1, 1, 1, 1, 1],
                    "lo_extendedprice": [100, 120, 150, 500, 650, 200, 210, 220, 230, 240],
                    "lo_revenue": [1000, 1100, 1200, 500, 650, 700, 800, 900, 1000, 700],
                    "lo_supplycost": [400, 500, 600, 100, 250, 300, 500, 550, 600, 250],
                }
            ),
        }

    @pytest.fixture(scope="session")
    def expr_ctx(self, ssb_tables):
        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()
        for name, table in ssb_tables.items():
            ctx.register_table(name, pl.from_pandas(table).lazy())
        return ctx

    @pytest.fixture(scope="session")
    def pandas_ctx(self, ssb_tables):
        return _PandasContext(ssb_tables)

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_return_expected_columns_and_match_families(self, query_id, expr_ctx, pandas_ctx):
        from benchbox.core.ssb.dataframe_queries import get_ssb_query

        query = get_ssb_query(query_id)
        expected_columns = QUERY_RESULT_COLUMNS[query_id]

        expr_result = _to_pandas_result(query.expression_impl(expr_ctx))
        pandas_result = _to_pandas_result(query.pandas_impl(pandas_ctx))

        assert expr_result.columns.tolist() == expected_columns
        assert pandas_result.columns.tolist() == expected_columns
        assert not expr_result.empty
        assert not pandas_result.empty
        pd.testing.assert_frame_equal(
            expr_result.reset_index(drop=True), pandas_result.reset_index(drop=True), check_dtype=False
        )

    def test_q1_1_known_revenue_value(self, expr_ctx, pandas_ctx):
        from benchbox.core.ssb.dataframe_queries import get_ssb_query

        query = get_ssb_query("Q1.1")
        expr_result = _to_pandas_result(query.expression_impl(expr_ctx))
        pandas_result = _to_pandas_result(query.pandas_impl(pandas_ctx))

        assert expr_result["revenue"].tolist() == [200]
        pd.testing.assert_frame_equal(
            expr_result.reset_index(drop=True), pandas_result.reset_index(drop=True), check_dtype=False
        )

    def test_q4_3_known_profit_breakdown(self, expr_ctx, pandas_ctx):
        from benchbox.core.ssb.dataframe_queries import get_ssb_query

        query = get_ssb_query("Q4.3")
        expected = pd.DataFrame(
            {
                "d_year": [1997, 1998],
                "s_city": ["UNITED ST2", "UNITED ST3"],
                "p_brand1": ["MFGR#1210", "MFGR#1211"],
                "profit": [400, 450],
            }
        )

        expr_result = _to_pandas_result(query.expression_impl(expr_ctx))
        pandas_result = _to_pandas_result(query.pandas_impl(pandas_ctx))

        pd.testing.assert_frame_equal(expr_result.reset_index(drop=True), expected, check_dtype=False)
        pd.testing.assert_frame_equal(pandas_result.reset_index(drop=True), expected, check_dtype=False)


