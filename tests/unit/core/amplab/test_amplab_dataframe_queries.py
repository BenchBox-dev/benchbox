# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.core.dataframe.query import QueryCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_QUERY_IDS = ["Q1", "Q1a", "Q2", "Q2a", "Q3", "Q3a", "Q4", "Q5"]


class TestAMPLabQueryRegistry:
    def test_registry_imports_successfully(self):
        from benchbox.core.amplab.dataframe_queries import AMPLAB_DATAFRAME_QUERIES

        assert AMPLAB_DATAFRAME_QUERIES is not None

    def test_registry_has_8_queries(self):
        from benchbox.core.amplab.dataframe_queries import AMPLAB_DATAFRAME_QUERIES

        queries = AMPLAB_DATAFRAME_QUERIES.get_all_queries()
        assert len(queries) == 8

    def test_registry_benchmark_name(self):
        from benchbox.core.amplab.dataframe_queries import AMPLAB_DATAFRAME_QUERIES

        assert AMPLAB_DATAFRAME_QUERIES.benchmark == "amplab"

    def test_get_query_by_id(self):
        from benchbox.core.amplab.dataframe_queries import get_amplab_query

        query = get_amplab_query("Q1")
        assert query is not None
        assert query.query_id == "Q1"

    def test_get_nonexistent_query(self):
        from benchbox.core.amplab.dataframe_queries import get_amplab_query

        assert get_amplab_query("Q99") is None

    def test_list_queries(self):
        from benchbox.core.amplab.dataframe_queries import list_amplab_queries

        assert len(list_amplab_queries()) == 8

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_registered(self, query_id):
        from benchbox.core.amplab.dataframe_queries import get_amplab_query

        assert get_amplab_query(query_id) is not None, f"Query {query_id} should be registered"

    def test_queries_have_both_implementations(self):
        from benchbox.core.amplab.dataframe_queries import AMPLAB_DATAFRAME_QUERIES

        for query in AMPLAB_DATAFRAME_QUERIES.get_all_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas impl"

    def test_all_queries_callable(self):
        from benchbox.core.amplab.dataframe_queries import AMPLAB_DATAFRAME_QUERIES

        for query in AMPLAB_DATAFRAME_QUERIES.get_all_queries():
            assert callable(query.expression_impl), f"{query.query_id} expression_impl not callable"
            assert callable(query.pandas_impl), f"{query.query_id} pandas_impl not callable"

    def test_query_descriptions_not_empty(self):
        from benchbox.core.amplab.dataframe_queries import AMPLAB_DATAFRAME_QUERIES

        for query in AMPLAB_DATAFRAME_QUERIES.get_all_queries():
            assert query.description, f"{query.query_id} missing description"
            assert len(query.description) > 10, f"{query.query_id} description too short"

    def test_query_names_not_empty(self):
        from benchbox.core.amplab.dataframe_queries import AMPLAB_DATAFRAME_QUERIES

        for query in AMPLAB_DATAFRAME_QUERIES.get_all_queries():
            assert query.query_name, f"{query.query_id} missing query_name"


class TestAMPLabQueryCategories:
    def test_scan_queries_have_filter(self):
        from benchbox.core.amplab.dataframe_queries import get_amplab_query

        for qid in ["Q1", "Q1a"]:
            query = get_amplab_query(qid)
            assert QueryCategory.FILTER in query.categories or QueryCategory.SCAN in query.categories, (
                f"{qid} should have FILTER or SCAN"
            )

    def test_join_queries_have_join(self):
        from benchbox.core.amplab.dataframe_queries import get_amplab_query

        for qid in ["Q2", "Q2a", "Q5"]:
            query = get_amplab_query(qid)
            assert QueryCategory.JOIN in query.categories, f"{qid} should have JOIN"

    def test_analytical_queries_tagged(self):
        from benchbox.core.amplab.dataframe_queries import get_amplab_query

        for qid in ["Q3", "Q3a", "Q4", "Q5"]:
            query = get_amplab_query(qid)
            assert QueryCategory.ANALYTICAL in query.categories, f"{qid} should have ANALYTICAL"


class TestAMPLabParameters:
    def test_all_queries_have_parameters(self):
        from benchbox.core.amplab.dataframe_queries.parameters import AMPLAB_DEFAULT_PARAMS

        assert len(AMPLAB_DEFAULT_PARAMS) == 8

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_parameter_entries_exist(self, query_id):
        from benchbox.core.amplab.dataframe_queries.parameters import AMPLAB_DEFAULT_PARAMS

        assert query_id in AMPLAB_DEFAULT_PARAMS, f"Missing parameters for {query_id}"

    def test_get_parameters_returns_container(self):
        from benchbox.core.amplab.dataframe_queries.parameters import AMPLabParameters, get_parameters

        result = get_parameters("Q1")
        assert isinstance(result, AMPLabParameters)
        assert result.query_id == "Q1"

    def test_q1_has_pagerank_threshold(self):
        from benchbox.core.amplab.dataframe_queries.parameters import get_parameters

        result = get_parameters("Q1")
        assert result.get("pagerank_threshold") == 1000


class TestAMPLabBenchmarkRegistry:
    def test_amplab_supports_dataframe(self):
        from benchbox.core.benchmark_registry import get_benchmark_metadata

        meta = get_benchmark_metadata("amplab")
        assert meta is not None
        assert meta.get("supports_dataframe") is True


