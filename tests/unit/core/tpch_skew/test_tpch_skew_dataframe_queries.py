# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.core.dataframe.query import QueryCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_QUERY_IDS = [f"Q{i}" for i in range(1, 23)]


class TestTPCHSkewQueryRegistry:
    def test_registry_imports_successfully(self):
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        assert len(TPCH_SKEW_DATAFRAME_QUERIES) > 0
        assert hasattr(TPCH_SKEW_DATAFRAME_QUERIES, "get_query_ids")

    def test_registry_has_22_queries(self):
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        queries = TPCH_SKEW_DATAFRAME_QUERIES.get_all_queries()
        assert len(queries) == 22

    def test_registry_benchmark_name(self):
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        assert TPCH_SKEW_DATAFRAME_QUERIES.benchmark == "tpch_skew"

    def test_get_query_by_id(self):
        from benchbox.core.tpch_skew.dataframe_queries import get_tpch_skew_query

        query = get_tpch_skew_query("Q1")
        assert query is not None
        assert query.query_id == "Q1"

    def test_get_nonexistent_query(self):
        from benchbox.core.tpch_skew.dataframe_queries import get_tpch_skew_query

        assert get_tpch_skew_query("Q99") is None

    def test_list_queries(self):
        from benchbox.core.tpch_skew.dataframe_queries import list_tpch_skew_queries

        assert len(list_tpch_skew_queries()) == 22

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_registered(self, query_id):
        from benchbox.core.tpch_skew.dataframe_queries import get_tpch_skew_query

        assert get_tpch_skew_query(query_id) is not None, f"Query {query_id} should be registered"

    def test_queries_have_both_implementations(self):
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        for query in TPCH_SKEW_DATAFRAME_QUERIES.get_all_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas impl"

    def test_all_queries_callable(self):
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        for query in TPCH_SKEW_DATAFRAME_QUERIES.get_all_queries():
            assert callable(query.expression_impl), f"{query.query_id} expression_impl not callable"
            assert callable(query.pandas_impl), f"{query.query_id} pandas_impl not callable"

    def test_query_descriptions_not_empty(self):
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        for query in TPCH_SKEW_DATAFRAME_QUERIES.get_all_queries():
            assert query.description, f"{query.query_id} missing description"

    def test_query_names_not_empty(self):
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        for query in TPCH_SKEW_DATAFRAME_QUERIES.get_all_queries():
            assert query.query_name, f"{query.query_id} missing query_name"


class TestTPCHSkewMatchesTPCH:
    def test_same_query_count(self):
        from benchbox.core.tpch.dataframe_queries import TPCH_DATAFRAME_QUERIES
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        assert len(TPCH_SKEW_DATAFRAME_QUERIES) == len(TPCH_DATAFRAME_QUERIES)

    def test_same_query_ids(self):
        from benchbox.core.tpch.dataframe_queries import TPCH_DATAFRAME_QUERIES
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        tpch_ids = TPCH_DATAFRAME_QUERIES.get_query_ids()
        skew_ids = TPCH_SKEW_DATAFRAME_QUERIES.get_query_ids()
        assert tpch_ids == skew_ids

    def test_implementations_are_shared(self):

        from benchbox.core.tpch.dataframe_queries import TPCH_DATAFRAME_QUERIES
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        for query_id in TPCH_DATAFRAME_QUERIES.get_query_ids():
            tpch_q = TPCH_DATAFRAME_QUERIES.get(query_id)
            skew_q = TPCH_SKEW_DATAFRAME_QUERIES.get(query_id)
            assert tpch_q.expression_impl is skew_q.expression_impl, (
                f"{query_id} expression_impl should be same function object"
            )
            assert tpch_q.pandas_impl is skew_q.pandas_impl, f"{query_id} pandas_impl should be same function object"

    def test_separate_registries(self):

        from benchbox.core.tpch.dataframe_queries import TPCH_DATAFRAME_QUERIES
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        assert TPCH_SKEW_DATAFRAME_QUERIES is not TPCH_DATAFRAME_QUERIES


class TestTPCHSkewQueryCategories:
    def test_q1_has_aggregate(self):
        from benchbox.core.tpch_skew.dataframe_queries import get_tpch_skew_query

        query = get_tpch_skew_query("Q1")
        assert QueryCategory.AGGREGATE in query.categories

    def test_join_queries_have_join(self):
        from benchbox.core.tpch_skew.dataframe_queries import get_tpch_skew_query

        for qid in ["Q3", "Q5", "Q7", "Q8", "Q9", "Q10", "Q12"]:
            query = get_tpch_skew_query(qid)
            assert QueryCategory.JOIN in query.categories, f"{qid} should have JOIN"

    def test_subquery_queries(self):
        from benchbox.core.tpch_skew.dataframe_queries import get_tpch_skew_query

        for qid in ["Q2", "Q4", "Q11", "Q15", "Q16", "Q17", "Q18", "Q20", "Q21", "Q22"]:
            query = get_tpch_skew_query(qid)
            assert QueryCategory.SUBQUERY in query.categories, f"{qid} should have SUBQUERY"


class TestTPCHSkewBenchmarkRegistry:
    def test_tpch_skew_supports_dataframe(self):
        from benchbox.core.benchmark_registry import get_benchmark_metadata

        meta = get_benchmark_metadata("tpch_skew")
        assert meta is not None
        assert meta.get("supports_dataframe") is True


