# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory
from benchbox.core.tpch.dataframe_queries import (
    TPCH_DATAFRAME_QUERIES,
    get_query,
    get_tpch_dataframe_queries,
    list_query_ids,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTPCHQueryRegistry:
    def test_registry_exists(self):

        assert len(TPCH_DATAFRAME_QUERIES) > 0

    def test_registry_name(self):

        assert TPCH_DATAFRAME_QUERIES.benchmark == "TPC-H DataFrame"

    def test_get_tpch_dataframe_queries_returns_same_registry(self):

        registry = get_tpch_dataframe_queries()
        assert registry is TPCH_DATAFRAME_QUERIES

    def test_list_query_ids(self):

        query_ids = list_query_ids()

        assert isinstance(query_ids, list)
        assert len(query_ids) > 0
        assert "Q1" in query_ids
        assert "Q6" in query_ids


class TestRegisteredQueries:
    @pytest.mark.parametrize(
        "query_id",
        [
            "Q1",
            "Q2",
            "Q3",
            "Q4",
            "Q5",
            "Q6",
            "Q7",
            "Q8",
            "Q9",
            "Q10",
            "Q11",
            "Q12",
            "Q13",
            "Q14",
            "Q15",
            "Q16",
            "Q17",
            "Q18",
            "Q19",
            "Q20",
            "Q21",
            "Q22",
        ],
    )
    def test_query_exists(self, query_id: str):

        query = get_query(query_id)
        assert isinstance(query, DataFrameQuery)

    def test_get_nonexistent_query_raises(self):

        with pytest.raises(KeyError):
            get_query("Q99")


class TestQ1PricingSummary:
    def test_q1_query_metadata(self):

        query = get_query("Q1")

        assert query.query_id == "Q1"
        assert query.query_name == "Pricing Summary Report"
        assert QueryCategory.AGGREGATE in query.categories
        assert QueryCategory.GROUP_BY in query.categories
        assert query.expected_row_count == 4

    def test_q1_has_expression_impl(self):

        query = get_query("Q1")
        assert query.has_expression_impl()

    def test_q1_has_pandas_impl(self):

        query = get_query("Q1")
        assert query.has_pandas_impl()


class TestQ3ShippingPriority:
    def test_q3_query_metadata(self):

        query = get_query("Q3")

        assert query.query_id == "Q3"
        assert query.query_name == "Shipping Priority"
        assert QueryCategory.JOIN in query.categories
        assert query.expected_row_count == 10

    def test_q3_has_expression_impl(self):

        query = get_query("Q3")
        assert query.has_expression_impl()


class TestQ4OrderPriority:
    def test_q4_query_metadata(self):

        query = get_query("Q4")

        assert query.query_id == "Q4"
        assert QueryCategory.SUBQUERY in query.categories

    def test_q4_has_expression_impl(self):

        query = get_query("Q4")
        assert query.has_expression_impl()


class TestQ5LocalSupplier:
    def test_q5_query_metadata(self):

        query = get_query("Q5")

        assert query.query_id == "Q5"
        assert QueryCategory.JOIN in query.categories

    def test_q5_has_expression_impl(self):

        query = get_query("Q5")
        assert query.has_expression_impl()


class TestQ6ForecastingRevenue:
    def test_q6_query_metadata(self):

        query = get_query("Q6")

        assert query.query_id == "Q6"
        assert query.query_name == "Forecasting Revenue Change"
        assert QueryCategory.FILTER in query.categories
        assert query.expected_row_count == 1

    def test_q6_has_expression_impl(self):

        query = get_query("Q6")
        assert query.has_expression_impl()

    def test_q6_has_pandas_impl(self):

        query = get_query("Q6")
        assert query.has_pandas_impl()


class TestQ10ReturnedItems:
    def test_q10_query_metadata(self):

        query = get_query("Q10")

        assert query.query_id == "Q10"
        assert QueryCategory.JOIN in query.categories
        assert query.expected_row_count == 20

    def test_q10_has_expression_impl(self):

        query = get_query("Q10")
        assert query.has_expression_impl()


class TestQ12ShippingModes:
    def test_q12_query_metadata(self):

        query = get_query("Q12")

        assert query.query_id == "Q12"
        assert QueryCategory.FILTER in query.categories
        assert query.expected_row_count == 2

    def test_q12_has_expression_impl(self):

        query = get_query("Q12")
        assert query.has_expression_impl()


class TestQ14PromotionEffect:
    def test_q14_query_metadata(self):

        query = get_query("Q14")

        assert query.query_id == "Q14"
        assert QueryCategory.AGGREGATE in query.categories
        assert query.expected_row_count == 1

    def test_q14_has_expression_impl(self):

        query = get_query("Q14")
        assert query.has_expression_impl()


class TestQ7VolumeShipping:
    def test_q7_query_metadata(self):

        query = get_query("Q7")

        assert query.query_id == "Q7"
        assert query.query_name == "Volume Shipping"
        assert QueryCategory.JOIN in query.categories

    def test_q7_has_expression_impl(self):

        query = get_query("Q7")
        assert query.has_expression_impl()


class TestQ8NationalMarketShare:
    def test_q8_query_metadata(self):

        query = get_query("Q8")

        assert query.query_id == "Q8"
        assert query.query_name == "National Market Share"
        assert QueryCategory.JOIN in query.categories

    def test_q8_has_expression_impl(self):

        query = get_query("Q8")
        assert query.has_expression_impl()


class TestQ9ProductTypeProfit:
    def test_q9_query_metadata(self):

        query = get_query("Q9")

        assert query.query_id == "Q9"
        assert query.query_name == "Product Type Profit Measure"
        assert QueryCategory.JOIN in query.categories

    def test_q9_has_expression_impl(self):

        query = get_query("Q9")
        assert query.has_expression_impl()


class TestQ13CustomerDistribution:
    def test_q13_query_metadata(self):

        query = get_query("Q13")

        assert query.query_id == "Q13"
        assert query.query_name == "Customer Distribution"
        assert QueryCategory.SUBQUERY in query.categories

    def test_q13_has_expression_impl(self):

        query = get_query("Q13")
        assert query.has_expression_impl()


class TestQ18LargeVolumeCustomer:
    def test_q18_query_metadata(self):

        query = get_query("Q18")

        assert query.query_id == "Q18"
        assert query.query_name == "Large Volume Customer"
        assert QueryCategory.SUBQUERY in query.categories
        assert query.expected_row_count == 100

    def test_q18_has_expression_impl(self):

        query = get_query("Q18")
        assert query.has_expression_impl()


class TestQ19DiscountedRevenue:
    def test_q19_query_metadata(self):

        query = get_query("Q19")

        assert query.query_id == "Q19"
        assert query.query_name == "Discounted Revenue"
        assert QueryCategory.FILTER in query.categories
        assert query.expected_row_count == 1

    def test_q19_has_expression_impl(self):

        query = get_query("Q19")
        assert query.has_expression_impl()


class TestQ2MinimumCostSupplier:
    def test_q2_query_metadata(self):

        query = get_query("Q2")

        assert query.query_id == "Q2"
        assert query.query_name == "Minimum Cost Supplier"
        assert QueryCategory.SUBQUERY in query.categories
        assert query.expected_row_count == 100

    def test_q2_has_expression_impl(self):

        query = get_query("Q2")
        assert query.has_expression_impl()


class TestQ11ImportantStock:
    def test_q11_query_metadata(self):

        query = get_query("Q11")

        assert query.query_id == "Q11"
        assert query.query_name == "Important Stock Identification"
        assert QueryCategory.SUBQUERY in query.categories

    def test_q11_has_expression_impl(self):

        query = get_query("Q11")
        assert query.has_expression_impl()


class TestQ15TopSupplier:
    def test_q15_query_metadata(self):

        query = get_query("Q15")

        assert query.query_id == "Q15"
        assert query.query_name == "Top Supplier"
        assert QueryCategory.SUBQUERY in query.categories

    def test_q15_has_expression_impl(self):

        query = get_query("Q15")
        assert query.has_expression_impl()


class TestQ16PartsSupplierRelationship:
    def test_q16_query_metadata(self):

        query = get_query("Q16")

        assert query.query_id == "Q16"
        assert query.query_name == "Parts/Supplier Relationship"
        assert QueryCategory.SUBQUERY in query.categories

    def test_q16_has_expression_impl(self):

        query = get_query("Q16")
        assert query.has_expression_impl()


class TestQ17SmallQuantityOrderRevenue:
    def test_q17_query_metadata(self):

        query = get_query("Q17")

        assert query.query_id == "Q17"
        assert query.query_name == "Small-Quantity-Order Revenue"
        assert QueryCategory.SUBQUERY in query.categories
        assert query.expected_row_count == 1

    def test_q17_has_expression_impl(self):

        query = get_query("Q17")
        assert query.has_expression_impl()


class TestQ20PotentialPartPromotion:
    def test_q20_query_metadata(self):

        query = get_query("Q20")

        assert query.query_id == "Q20"
        assert query.query_name == "Potential Part Promotion"
        assert QueryCategory.SUBQUERY in query.categories

    def test_q20_has_expression_impl(self):

        query = get_query("Q20")
        assert query.has_expression_impl()


class TestQ21SuppliersKeptOrdersWaiting:
    def test_q21_query_metadata(self):

        query = get_query("Q21")

        assert query.query_id == "Q21"
        assert query.query_name == "Suppliers Who Kept Orders Waiting"
        assert QueryCategory.SUBQUERY in query.categories
        assert query.expected_row_count == 100

    def test_q21_has_expression_impl(self):

        query = get_query("Q21")
        assert query.has_expression_impl()


class TestQ22GlobalSalesOpportunity:
    def test_q22_query_metadata(self):

        query = get_query("Q22")

        assert query.query_id == "Q22"
        assert query.query_name == "Global Sales Opportunity"
        assert QueryCategory.SUBQUERY in query.categories

    def test_q22_has_expression_impl(self):

        query = get_query("Q22")
        assert query.has_expression_impl()


class TestQueryFamilySupport:
    def test_all_queries_have_expression_impl(self):

        for query_id in list_query_ids():
            query = get_query(query_id)
            assert query.has_expression_impl(), f"{query_id} missing expression_impl"

    def test_q1_supports_both_families(self):

        query = get_query("Q1")

        assert query.has_pandas_impl()
        assert query.has_expression_impl()

    def test_q6_supports_both_families(self):

        query = get_query("Q6")

        assert query.has_pandas_impl()
        assert query.has_expression_impl()

    def test_q3_supports_both_families(self):

        query = get_query("Q3")

        assert query.has_pandas_impl()
        assert query.has_expression_impl()

    def test_q4_supports_both_families(self):

        query = get_query("Q4")

        assert query.has_pandas_impl()
        assert query.has_expression_impl()

    def test_q5_supports_both_families(self):

        query = get_query("Q5")

        assert query.has_pandas_impl()
        assert query.has_expression_impl()

    def test_q10_supports_both_families(self):

        query = get_query("Q10")

        assert query.has_pandas_impl()
        assert query.has_expression_impl()


class TestQueryCategories:
    def test_aggregate_queries_have_aggregate_category(self):

        for query_id in ["Q1", "Q6"]:
            query = get_query(query_id)
            assert QueryCategory.AGGREGATE in query.categories, f"{query_id} should have AGGREGATE"

    def test_join_queries_have_join_category(self):

        for query_id in [
            "Q2",
            "Q3",
            "Q5",
            "Q7",
            "Q8",
            "Q9",
            "Q10",
            "Q11",
            "Q12",
            "Q13",
            "Q14",
            "Q15",
            "Q16",
            "Q17",
            "Q18",
            "Q19",
            "Q20",
            "Q21",
        ]:
            query = get_query(query_id)
            assert QueryCategory.JOIN in query.categories, f"{query_id} should have JOIN"

    def test_subquery_queries_have_subquery_category(self):

        for query_id in ["Q2", "Q4", "Q11", "Q13", "Q15", "Q16", "Q17", "Q18", "Q20", "Q21", "Q22"]:
            query = get_query(query_id)
            assert QueryCategory.SUBQUERY in query.categories, f"{query_id} should have SUBQUERY"


