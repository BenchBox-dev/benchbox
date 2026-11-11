# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.core.dataframe.query import QueryCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_QUERY_IDS = [f"Q{i}" for i in range(1, 44)]


class TestClickBenchQueryRegistry:
    def test_registry_imports_successfully(self):

        from benchbox.core.clickbench.dataframe_queries import CLICKBENCH_DATAFRAME_QUERIES

        assert CLICKBENCH_DATAFRAME_QUERIES is not None

    def test_registry_has_43_queries(self):

        from benchbox.core.clickbench.dataframe_queries import CLICKBENCH_DATAFRAME_QUERIES

        queries = CLICKBENCH_DATAFRAME_QUERIES.get_all_queries()
        assert len(queries) == 43

    def test_registry_benchmark_name(self):

        from benchbox.core.clickbench.dataframe_queries import CLICKBENCH_DATAFRAME_QUERIES

        assert CLICKBENCH_DATAFRAME_QUERIES.benchmark == "clickbench"

    def test_get_query_by_id(self):

        from benchbox.core.clickbench.dataframe_queries import get_clickbench_query

        query = get_clickbench_query("Q1")
        assert query is not None
        assert query.query_id == "Q1"

    def test_get_nonexistent_query(self):

        from benchbox.core.clickbench.dataframe_queries import get_clickbench_query

        query = get_clickbench_query("Q99")
        assert query is None

    def test_list_queries(self):

        from benchbox.core.clickbench.dataframe_queries import list_clickbench_queries

        queries = list_clickbench_queries()
        assert len(queries) == 43

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_registered(self, query_id):

        from benchbox.core.clickbench.dataframe_queries import get_clickbench_query

        query = get_clickbench_query(query_id)
        assert query is not None, f"Query {query_id} should be registered"

    def test_queries_have_both_implementations(self):

        from benchbox.core.clickbench.dataframe_queries import CLICKBENCH_DATAFRAME_QUERIES

        for query in CLICKBENCH_DATAFRAME_QUERIES.get_all_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas impl"

    def test_all_queries_callable(self):

        from benchbox.core.clickbench.dataframe_queries import CLICKBENCH_DATAFRAME_QUERIES

        for query in CLICKBENCH_DATAFRAME_QUERIES.get_all_queries():
            assert callable(query.expression_impl), f"{query.query_id} expression_impl not callable"
            assert callable(query.pandas_impl), f"{query.query_id} pandas_impl not callable"

    def test_query_descriptions_not_empty(self):

        from benchbox.core.clickbench.dataframe_queries import CLICKBENCH_DATAFRAME_QUERIES

        for query in CLICKBENCH_DATAFRAME_QUERIES.get_all_queries():
            assert query.description, f"{query.query_id} missing description"
            assert len(query.description) > 10, f"{query.query_id} description too short"

    def test_query_names_not_empty(self):

        from benchbox.core.clickbench.dataframe_queries import CLICKBENCH_DATAFRAME_QUERIES

        for query in CLICKBENCH_DATAFRAME_QUERIES.get_all_queries():
            assert query.query_name, f"{query.query_id} missing query_name"


class TestClickBenchQueryCategories:
    def test_basic_aggregation_queries_have_aggregate(self):

        from benchbox.core.clickbench.dataframe_queries import get_clickbench_query

        for i in range(1, 8):
            query = get_clickbench_query(f"Q{i}")
            assert QueryCategory.AGGREGATE in query.categories, f"Q{i} should have AGGREGATE"

    def test_grouping_queries_have_group_by(self):

        from benchbox.core.clickbench.dataframe_queries import get_clickbench_query

        for i in range(8, 16):
            query = get_clickbench_query(f"Q{i}")
            assert QueryCategory.GROUP_BY in query.categories or QueryCategory.FILTER in query.categories, (
                f"Q{i} should have GROUP_BY or FILTER"
            )

    def test_analytical_queries_tagged(self):

        from benchbox.core.clickbench.dataframe_queries import get_clickbench_query

        analytical_ids = ["Q19", "Q23", "Q28", "Q29", "Q35", "Q36", "Q40", "Q43"]
        for qid in analytical_ids:
            query = get_clickbench_query(qid)
            assert QueryCategory.ANALYTICAL in query.categories, f"{qid} should have ANALYTICAL"


class TestClickBenchBenchmarkRegistry:
    def test_clickbench_supports_dataframe(self):

        from benchbox.core.benchmark_registry import get_benchmark_metadata

        meta = get_benchmark_metadata("clickbench")
        assert meta is not None
        assert meta.get("supports_dataframe") is True


