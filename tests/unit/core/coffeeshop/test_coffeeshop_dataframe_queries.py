# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.core.dataframe.query import QueryCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_QUERY_IDS = ["SA1", "SA2", "SA3", "SA4", "SA5", "PR1", "PR2", "TR1", "TM1", "QC1", "QC2"]


class TestCoffeeShopQueryRegistry:
    def test_registry_imports_successfully(self):
        from benchbox.core.coffeeshop.dataframe_queries import COFFEESHOP_DATAFRAME_QUERIES

        assert COFFEESHOP_DATAFRAME_QUERIES is not None

    def test_registry_has_11_queries(self):
        from benchbox.core.coffeeshop.dataframe_queries import COFFEESHOP_DATAFRAME_QUERIES

        queries = COFFEESHOP_DATAFRAME_QUERIES.get_all_queries()
        assert len(queries) == 11

    def test_registry_benchmark_name(self):
        from benchbox.core.coffeeshop.dataframe_queries import COFFEESHOP_DATAFRAME_QUERIES

        assert COFFEESHOP_DATAFRAME_QUERIES.benchmark == "coffeeshop"

    def test_get_query_by_id(self):
        from benchbox.core.coffeeshop.dataframe_queries import get_coffeeshop_query

        query = get_coffeeshop_query("SA1")
        assert query is not None
        assert query.query_id == "SA1"

    def test_get_nonexistent_query(self):
        from benchbox.core.coffeeshop.dataframe_queries import get_coffeeshop_query

        assert get_coffeeshop_query("X99") is None

    def test_list_queries(self):
        from benchbox.core.coffeeshop.dataframe_queries import list_coffeeshop_queries

        assert len(list_coffeeshop_queries()) == 11

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_registered(self, query_id):
        from benchbox.core.coffeeshop.dataframe_queries import get_coffeeshop_query

        assert get_coffeeshop_query(query_id) is not None, f"Query {query_id} should be registered"

    def test_queries_have_both_implementations(self):
        from benchbox.core.coffeeshop.dataframe_queries import COFFEESHOP_DATAFRAME_QUERIES

        for query in COFFEESHOP_DATAFRAME_QUERIES.get_all_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas impl"

    def test_all_queries_callable(self):
        from benchbox.core.coffeeshop.dataframe_queries import COFFEESHOP_DATAFRAME_QUERIES

        for query in COFFEESHOP_DATAFRAME_QUERIES.get_all_queries():
            assert callable(query.expression_impl), f"{query.query_id} expression_impl not callable"
            assert callable(query.pandas_impl), f"{query.query_id} pandas_impl not callable"

    def test_query_descriptions_not_empty(self):
        from benchbox.core.coffeeshop.dataframe_queries import COFFEESHOP_DATAFRAME_QUERIES

        for query in COFFEESHOP_DATAFRAME_QUERIES.get_all_queries():
            assert query.description, f"{query.query_id} missing description"
            assert len(query.description) > 10, f"{query.query_id} description too short"

    def test_query_names_not_empty(self):
        from benchbox.core.coffeeshop.dataframe_queries import COFFEESHOP_DATAFRAME_QUERIES

        for query in COFFEESHOP_DATAFRAME_QUERIES.get_all_queries():
            assert query.query_name, f"{query.query_id} missing query_name"


class TestCoffeeShopQueryCategories:
    def test_sales_queries_have_join(self):
        from benchbox.core.coffeeshop.dataframe_queries import get_coffeeshop_query

        for qid in ["SA1", "SA2", "SA4", "SA5"]:
            query = get_coffeeshop_query(qid)
            assert QueryCategory.JOIN in query.categories, f"{qid} should have JOIN"

    def test_window_query_tagged(self):
        from benchbox.core.coffeeshop.dataframe_queries import get_coffeeshop_query

        query = get_coffeeshop_query("SA4")
        assert QueryCategory.WINDOW in query.categories, "SA4 should have WINDOW"

    def test_analytical_queries_tagged(self):
        from benchbox.core.coffeeshop.dataframe_queries import get_coffeeshop_query

        for qid in ["SA3", "PR2", "TR1", "TM1", "QC2"]:
            query = get_coffeeshop_query(qid)
            assert QueryCategory.ANALYTICAL in query.categories, f"{qid} should have ANALYTICAL"


class TestCoffeeShopParameters:
    def test_all_queries_have_parameters(self):
        from benchbox.core.coffeeshop.dataframe_queries.parameters import COFFEESHOP_DEFAULT_PARAMS

        assert len(COFFEESHOP_DEFAULT_PARAMS) == 11

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_parameter_entries_exist(self, query_id):
        from benchbox.core.coffeeshop.dataframe_queries.parameters import COFFEESHOP_DEFAULT_PARAMS

        assert query_id in COFFEESHOP_DEFAULT_PARAMS, f"Missing parameters for {query_id}"

    def test_get_parameters_returns_container(self):
        from benchbox.core.coffeeshop.dataframe_queries.parameters import CoffeeShopParameters, get_parameters

        result = get_parameters("SA1")
        assert isinstance(result, CoffeeShopParameters)
        assert result.query_id == "SA1"

    def test_sa1_has_date_range(self):
        from benchbox.core.coffeeshop.dataframe_queries.parameters import get_parameters

        result = get_parameters("SA1")
        assert result.get("start_date") == "2023-01-01"
        assert result.get("end_date") == "2023-01-31"


class TestCoffeeShopBenchmarkRegistry:
    def test_coffeeshop_supports_dataframe(self):
        from benchbox.core.benchmark_registry import get_benchmark_metadata

        meta = get_benchmark_metadata("coffeeshop")
        assert meta is not None
        assert meta.get("supports_dataframe") is True


