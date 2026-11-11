# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.core.dataframe.query import QueryCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_QUERY_IDS = [f"Q{i}" for i in range(1, 11)]


class TestH2ODBQueryRegistry:
    def test_registry_imports_successfully(self):
        from benchbox.core.h2odb.dataframe_queries import H2ODB_DATAFRAME_QUERIES

        assert len(H2ODB_DATAFRAME_QUERIES) > 0
        assert hasattr(H2ODB_DATAFRAME_QUERIES, "get_query_ids")

    def test_registry_has_10_queries(self):
        from benchbox.core.h2odb.dataframe_queries import H2ODB_DATAFRAME_QUERIES

        queries = H2ODB_DATAFRAME_QUERIES.get_all_queries()
        assert len(queries) == 10

    def test_registry_benchmark_name(self):
        from benchbox.core.h2odb.dataframe_queries import H2ODB_DATAFRAME_QUERIES

        assert H2ODB_DATAFRAME_QUERIES.benchmark == "h2odb"

    def test_get_query_by_id(self):
        from benchbox.core.h2odb.dataframe_queries import get_h2odb_query

        query = get_h2odb_query("Q1")
        assert query is not None
        assert query.query_id == "Q1"

    def test_get_nonexistent_query(self):
        from benchbox.core.h2odb.dataframe_queries import get_h2odb_query

        assert get_h2odb_query("Q99") is None

    def test_list_queries(self):
        from benchbox.core.h2odb.dataframe_queries import list_h2odb_queries

        assert len(list_h2odb_queries()) == 10

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_registered(self, query_id):
        from benchbox.core.h2odb.dataframe_queries import get_h2odb_query

        assert get_h2odb_query(query_id) is not None, f"Query {query_id} should be registered"

    def test_queries_have_both_implementations(self):
        from benchbox.core.h2odb.dataframe_queries import H2ODB_DATAFRAME_QUERIES

        for query in H2ODB_DATAFRAME_QUERIES.get_all_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas impl"

    def test_all_queries_callable(self):
        from benchbox.core.h2odb.dataframe_queries import H2ODB_DATAFRAME_QUERIES

        for query in H2ODB_DATAFRAME_QUERIES.get_all_queries():
            assert callable(query.expression_impl), f"{query.query_id} expression_impl not callable"
            assert callable(query.pandas_impl), f"{query.query_id} pandas_impl not callable"

    def test_query_descriptions_not_empty(self):
        from benchbox.core.h2odb.dataframe_queries import H2ODB_DATAFRAME_QUERIES

        for query in H2ODB_DATAFRAME_QUERIES.get_all_queries():
            assert query.description, f"{query.query_id} missing description"
            assert len(query.description) > 10, f"{query.query_id} description too short"

    def test_query_names_not_empty(self):
        from benchbox.core.h2odb.dataframe_queries import H2ODB_DATAFRAME_QUERIES

        for query in H2ODB_DATAFRAME_QUERIES.get_all_queries():
            assert query.query_name, f"{query.query_id} missing query_name"


class TestH2ODBQueryCategories:
    def test_basic_queries_have_aggregate(self):
        from benchbox.core.h2odb.dataframe_queries import get_h2odb_query

        for qid in ["Q1", "Q2"]:
            query = get_h2odb_query(qid)
            assert QueryCategory.AGGREGATE in query.categories, f"{qid} should have AGGREGATE"

    def test_groupby_queries_have_group_by(self):
        from benchbox.core.h2odb.dataframe_queries import get_h2odb_query

        for i in range(3, 10):
            query = get_h2odb_query(f"Q{i}")
            assert QueryCategory.GROUP_BY in query.categories, f"Q{i} should have GROUP_BY"

    def test_temporal_queries_have_analytical(self):
        from benchbox.core.h2odb.dataframe_queries import get_h2odb_query

        for qid in ["Q7", "Q8", "Q9"]:
            query = get_h2odb_query(qid)
            assert QueryCategory.ANALYTICAL in query.categories, f"{qid} should have ANALYTICAL"


class TestH2ODBBenchmarkRegistry:
    def test_h2odb_supports_dataframe(self):
        from benchbox.core.benchmark_registry import get_benchmark_metadata

        meta = get_benchmark_metadata("h2odb")
        assert meta is not None
        assert meta.get("supports_dataframe") is True


