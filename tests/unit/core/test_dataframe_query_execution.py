from __future__ import annotations

import time
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


try:
    import polars as pl

    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    HAS_POLARS = True
except ImportError:
    HAS_POLARS = False

needs_polars = pytest.mark.skipif(not HAS_POLARS, reason="polars not installed")


def _make_ctx(tables: dict[str, Any]) -> Any:
    adapter = PolarsDataFrameAdapter()
    ctx = adapter.create_context()
    for name, df in tables.items():
        lf = df.lazy() if hasattr(df, "lazy") else df
        ctx.register_table(name, lf)
    return ctx


@needs_polars
class TestAMPLabDataFrameQueriesExecution:
    @pytest.fixture()
    def ctx(self):
        rankings = pl.DataFrame(
            {
                "pageURL": ["http://a.com/1", "http://b.com/2", "http://c.com/3"],
                "pageRank": [1500, 500, 2000],
                "avgDuration": [120, 60, 300],
            }
        )
        uservisits = pl.DataFrame(
            {
                "sourceIP": ["1.2.3.4", "5.6.7.8", "9.10.11.12"],
                "destURL": ["http://a.com/1", "http://b.com/2", "http://c.com/3"],
                "visitDate": [date(2000, 1, 15), date(2000, 2, 10), date(2000, 3, 20)],
                "adRevenue": [1.50, 0.00, 3.20],
                "userAgent": ["Mozilla/5.0", "Mozilla/5.0", "Chrome"],
                "countryCode": ["USA", "CHN", "USA"],
                "languageCode": ["en-US", "zh-CN", "en-US"],
                "searchWord": ["data", "", "query"],
                "duration": [300, 120, 600],
            }
        )
        documents = pl.DataFrame(
            {
                "url": ["http://a.com/1", "http://b.com/2"],
                "contents": ["web data system information", "database query engine"],
            }
        )
        return _make_ctx({"rankings": rankings, "uservisits": uservisits, "documents": documents})

    def test_q1_expression(self, ctx):
        from benchbox.core.amplab.dataframe_queries.queries import q1_expression_impl

        result = q1_expression_impl(ctx)
        assert result.columns == ["pageURL", "pageRank"]

    def test_q1a_expression(self, ctx):
        from benchbox.core.amplab.dataframe_queries.queries import q1a_expression_impl

        result = q1a_expression_impl(ctx)
        assert result.columns == ["total_pages", "avg_pagerank", "max_pagerank"]

    def test_q2_expression(self, ctx):
        from benchbox.core.amplab.dataframe_queries.queries import q2_expression_impl

        result = q2_expression_impl(ctx)
        assert result.columns == ["sourceIP", "totalRevenue", "avgPageRank"]

    @pytest.mark.parametrize("visit_dtype", ["date", "string"])
    def test_q2_expression_filters_visit_dates_of_either_dtype(self, visit_dtype):
        from benchbox.core.amplab.dataframe_queries.queries import q2_expression_impl

        visits = pl.DataFrame(
            {
                "sourceIP": ["1.1.1.1", "2.2.2.2", "3.3.3.3"],
                "destURL": ["u1", "u2", "u1"],
                "visitDate": [date(2000, 1, 2), date(2000, 1, 2), date(2000, 2, 1)],
                "adRevenue": [1.5, 2.5, 9.0],
            }
        )
        if visit_dtype == "string":
            visits = visits.with_columns(pl.col("visitDate").cast(pl.String))
        rankings = pl.DataFrame({"pageURL": ["u1", "u2"], "pageRank": [10, 20]})

        result = q2_expression_impl(_make_ctx({"uservisits": visits, "rankings": rankings}))

        assert sorted(result.collect()["sourceIP"].to_list()) == ["1.1.1.1", "2.2.2.2"]

    def test_q2a_expression(self, ctx):
        from benchbox.core.amplab.dataframe_queries.queries import q2a_expression_impl

        result = q2a_expression_impl(ctx)
        assert result.columns == ["destURL", "visitDate", "adRevenue", "pageRank", "avgDuration"]

    def test_q3_expression(self, ctx):
        from benchbox.core.amplab.dataframe_queries.queries import q3_expression_impl

        result = q3_expression_impl(ctx)
        assert result.columns == ["sourceIP", "visit_count", "total_revenue", "avg_duration"]

    def test_q3a_expression(self, ctx):
        from benchbox.core.amplab.dataframe_queries.queries import q3a_expression_impl

        result = q3a_expression_impl(ctx)
        assert result.columns == ["url", "content_length", "has_keyword1", "has_keyword2"]

    def test_q4_expression(self, ctx):
        from benchbox.core.amplab.dataframe_queries.queries import q4_expression_impl

        result = q4_expression_impl(ctx)
        assert result.columns == [
            "countryCode",
            "languageCode",
            "visit_count",
            "total_revenue",
            "avg_duration",
            "unique_visitors",
        ]

    def test_q5_expression(self, ctx):
        from benchbox.core.amplab.dataframe_queries.queries import q5_expression_impl

        result = q5_expression_impl(ctx)
        assert result.columns == [
            "countryCode",
            "unique_pages",
            "total_visits",
            "total_revenue",
            "avg_pagerank",
            "avg_duration",
        ]

    def test_q1_pandas(self, ctx):
        import pandas as pd

        from benchbox.core.amplab.dataframe_queries.queries import q1_pandas_impl

        rankings = pd.DataFrame(
            {"pageURL": ["http://a.com/1", "http://b.com/2"], "pageRank": [1500, 500], "avgDuration": [120, 60]}
        )

        class PdCtx:
            def get_table(self, name: str) -> Any:
                return {"rankings": rankings}[name.lower()]

        result = q1_pandas_impl(PdCtx())
        assert hasattr(result, "columns"), "Q1 pandas should return a DataFrame"

    def test_q1a_pandas(self, ctx):
        import pandas as pd

        from benchbox.core.amplab.dataframe_queries.queries import q1a_pandas_impl

        rankings = pd.DataFrame({"pageURL": ["http://a.com/1"], "pageRank": [1500], "avgDuration": [120]})

        class PdCtx:
            def get_table(self, name: str) -> Any:
                return {"rankings": rankings}[name.lower()]

        result = q1a_pandas_impl(PdCtx())
        assert hasattr(result, "columns"), "Q1a pandas should return a DataFrame"


@needs_polars
class TestSSBDataFrameQueriesExecution:
    @pytest.fixture()
    def ctx(self):
        date_dim = pl.DataFrame(
            {
                "d_datekey": [19920101, 19930615, 19940301],
                "d_date": ["January 1, 1992", "June 15, 1993", "March 1, 1994"],
                "d_dayofweek": ["Wednesday", "Tuesday", "Tuesday"],
                "d_month": ["January", "June", "March"],
                "d_year": [1992, 1993, 1994],
                "d_yearmonthnum": [199201, 199306, 199403],
                "d_yearmonth": ["Jan1992", "Jun1993", "Mar1994"],
                "d_daynuminweek": [4, 3, 3],
                "d_daynuminmonth": [1, 15, 1],
                "d_daynuminyear": [1, 166, 60],
                "d_monthnuminyear": [1, 6, 3],
                "d_weeknuminyear": [1, 24, 9],
                "d_sellingseason": ["Winter", "Summer", "Spring"],
                "d_lastdayinweekfl": [0, 0, 0],
                "d_lastdayinmonthfl": [0, 0, 0],
                "d_holidayfl": [0, 0, 0],
                "d_weekdayfl": [1, 1, 1],
            }
        )
        customer = pl.DataFrame(
            {
                "c_custkey": [1, 2],
                "c_name": ["Customer#1", "Customer#2"],
                "c_address": ["addr1", "addr2"],
                "c_city": ["UNITED KI1", "UNITED ST2"],
                "c_nation": ["UNITED KINGDOM", "UNITED STATES"],
                "c_region": ["EUROPE", "AMERICA"],
                "c_phone": ["111", "222"],
                "c_mktsegment": ["BUILDING", "AUTOMOBILE"],
            }
        )
        supplier = pl.DataFrame(
            {
                "s_suppkey": [1, 2],
                "s_name": ["Supplier#1", "Supplier#2"],
                "s_address": ["saddr1", "saddr2"],
                "s_city": ["UNITED KI1", "UNITED ST2"],
                "s_nation": ["UNITED KINGDOM", "UNITED STATES"],
                "s_region": ["EUROPE", "AMERICA"],
                "s_phone": ["333", "444"],
            }
        )
        part = pl.DataFrame(
            {
                "p_partkey": [1, 2],
                "p_name": ["Part#1", "Part#2"],
                "p_mfgr": ["MFGR#1", "MFGR#2"],
                "p_category": ["MFGR#11", "MFGR#21"],
                "p_brand1": ["MFGR#1101", "MFGR#2101"],
                "p_color": ["red", "blue"],
                "p_type": ["PROMO BURNISHED COPPER", "LARGE BRUSHED BRASS"],
                "p_size": [7, 15],
                "p_container": ["JUMBO CASE", "SM PKG"],
            }
        )
        lineorder = pl.DataFrame(
            {
                "lo_orderkey": [1, 2],
                "lo_linenumber": [1, 1],
                "lo_custkey": [1, 2],
                "lo_partkey": [1, 2],
                "lo_suppkey": [1, 2],
                "lo_orderdate": [19920101, 19930615],
                "lo_orderpriority": ["1-URGENT", "5-LOW"],
                "lo_shippriority": [0, 0],
                "lo_quantity": [17, 36],
                "lo_extendedprice": [2116823, 3632190],
                "lo_ordtotalprice": [4598316, 7236920],
                "lo_discount": [4, 9],
                "lo_revenue": [2032150, 3305613],
                "lo_supplycost": [68476, 83458],
                "lo_tax": [2, 6],
                "lo_commitdate": [19920301, 19930801],
                "lo_shipmode": ["TRUCK", "MAIL"],
            }
        )
        return _make_ctx(
            {
                "date": date_dim,
                "customer": customer,
                "supplier": supplier,
                "part": part,
                "lineorder": lineorder,
            }
        )

    def test_q1_1_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q1_1_expression_impl

        result = q1_1_expression_impl(ctx)
        assert result.columns == ["revenue"]

    def test_q1_2_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q1_2_expression_impl

        result = q1_2_expression_impl(ctx)
        assert result.columns == ["revenue"]

    def test_q1_3_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q1_3_expression_impl

        result = q1_3_expression_impl(ctx)
        assert result.columns == ["revenue"]

    def test_q2_1_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q2_1_expression_impl

        result = q2_1_expression_impl(ctx)
        assert result.columns == ["d_year", "p_brand1", "revenue"]

    def test_q2_2_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q2_2_expression_impl

        result = q2_2_expression_impl(ctx)
        assert result.columns == ["d_year", "p_brand1", "revenue"]

    def test_q2_3_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q2_3_expression_impl

        result = q2_3_expression_impl(ctx)
        assert result.columns == ["d_year", "p_brand1", "revenue"]

    def test_q3_1_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q3_1_expression_impl

        result = q3_1_expression_impl(ctx)
        assert result.columns == ["c_nation", "s_nation", "d_year", "revenue"]

    def test_q3_2_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q3_2_expression_impl

        result = q3_2_expression_impl(ctx)
        assert result.columns == ["c_city", "s_city", "d_year", "revenue"]

    def test_q3_3_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q3_3_expression_impl

        result = q3_3_expression_impl(ctx)
        assert result.columns == ["c_city", "s_city", "d_year", "revenue"]

    def test_q3_4_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q3_4_expression_impl

        result = q3_4_expression_impl(ctx)
        assert result.columns == ["c_city", "s_city", "d_year", "revenue"]

    def test_q4_1_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q4_1_expression_impl

        result = q4_1_expression_impl(ctx)
        assert result.columns == ["d_year", "c_nation", "profit"]

    def test_q4_2_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q4_2_expression_impl

        result = q4_2_expression_impl(ctx)
        assert result.columns == ["d_year", "s_nation", "p_category", "profit"]

    def test_q4_3_expression(self, ctx):
        from benchbox.core.ssb.dataframe_queries.queries import q4_3_expression_impl

        result = q4_3_expression_impl(ctx)
        assert result.columns == ["d_year", "s_city", "p_brand1", "profit"]


@needs_polars
class TestCoffeeShopDataFrameQueriesExecution:
    @pytest.fixture()
    def ctx(self):
        order_lines = pl.DataFrame(
            {
                "order_id": [1, 2, 3],
                "order_date": [date(2023, 1, 15), date(2023, 6, 20), date(2023, 9, 10)],
                "order_time": [
                    datetime(2023, 1, 15, 8, 30),
                    datetime(2023, 6, 20, 12, 15),
                    datetime(2023, 9, 10, 17, 45),
                ],
                "product_id": [101, 102, 101],
                "product_record_id": [1, 2, 1],
                "location_id": [1, 2, 1],
                "location_record_id": [1, 2, 1],
                "quantity": [2, 1, 3],
                "unit_price": [4.50, 3.75, 4.50],
                "total_price": [9.00, 3.75, 13.50],
                "payment_method": ["credit_card", "cash", "mobile"],
                "customer_type": ["regular", "new", "regular"],
            }
        )
        dim_locations = pl.DataFrame(
            {
                "record_id": [1, 2],
                "location_name": ["Downtown", "Uptown"],
                "city": ["NYC", "NYC"],
                "state": ["NY", "NY"],
                "region": ["South", "South"],
            }
        )
        dim_products = pl.DataFrame(
            {
                "record_id": [1, 2],
                "product_id": [101, 102],
                "name": ["Latte", "Espresso"],
                "category": ["Hot Drinks", "Hot Drinks"],
                "subcategory": ["Coffee", "Coffee"],
                "cost": [1.50, 1.00],
                "from_date": [date(2022, 1, 1), date(2022, 1, 1)],
                "to_date": [date(2024, 12, 31), date(2024, 12, 31)],
            }
        )
        return _make_ctx({"order_lines": order_lines, "dim_locations": dim_locations, "dim_products": dim_products})

    def test_sa1_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import sa1_expression_impl

        result = sa1_expression_impl(ctx)
        assert result.columns == ["order_date", "region", "order_count", "gross_revenue", "avg_order_value"]

    def test_sa2_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import sa2_expression_impl

        result = sa2_expression_impl(ctx)
        assert result.columns == ["subcategory", "product_name", "total_quantity", "total_revenue"]

    def test_sa3_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import sa3_expression_impl

        result = sa3_expression_impl(ctx)
        assert result.columns == [
            "year",
            "month",
            "orders",
            "items_sold",
            "revenue",
            "avg_order_value",
            "avg_items_per_order",
        ]

    def test_sa4_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import sa4_expression_impl

        result = sa4_expression_impl(ctx)
        assert result.columns == ["region", "orders", "revenue", "revenue_share"]

    def test_sa5_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import sa5_expression_impl

        result = sa5_expression_impl(ctx)
        assert result.columns == ["location_id", "city", "state", "region", "orders", "revenue", "avg_line_value"]

    def test_pr1_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import pr1_expression_impl

        result = pr1_expression_impl(ctx)
        assert result.columns == ["subcategory", "active_products", "quantity_sold", "revenue"]

    def test_pr2_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import pr2_expression_impl

        result = pr2_expression_impl(ctx)
        assert result.columns == ["price_band", "line_count", "revenue"]

    def test_tr1_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import tr1_expression_impl

        result = tr1_expression_impl(ctx)
        assert result.columns == ["year", "quarter", "orders", "revenue"]

    def test_tm1_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import tm1_expression_impl

        result = tm1_expression_impl(ctx)
        assert result.columns == ["day_part", "orders", "revenue", "avg_line_value"]

    def test_qc1_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import qc1_expression_impl

        result = qc1_expression_impl(ctx)
        assert result.columns == ["avg_lines_per_order", "avg_items_per_order"]

    def test_qc2_expression(self, ctx):
        from benchbox.core.coffeeshop.dataframe_queries.queries import qc2_expression_impl

        result = qc2_expression_impl(ctx)
        assert result.columns == ["region", "season", "revenue", "orders"]


class TestAIPrimitivesDataFrameOperations:
    def test_operation_type_enum(self):
        from benchbox.core.ai_primitives.dataframe_operations import AIOperationType

        assert AIOperationType.EMBEDDING_SINGLE.value == "embedding_single"
        assert AIOperationType.SENTIMENT_SINGLE.value == "sentiment_single"
        assert AIOperationType.COSINE_SIMILARITY.value == "cosine_similarity"

    def test_skip_list(self):
        from benchbox.core.ai_primitives.dataframe_operations import SKIP_FOR_DATAFRAME

        assert isinstance(SKIP_FOR_DATAFRAME, list)
        assert "generative_complete_simple" in SKIP_FOR_DATAFRAME
        assert len(SKIP_FOR_DATAFRAME) == 8

    def test_ai_model_capabilities_direct(self):
        from benchbox.core.ai_primitives.dataframe_operations import AIModelCapabilities

        caps = AIModelCapabilities(
            has_sentence_transformers=False,
            has_textblob=True,
            has_spacy=False,
            has_numpy=True,
            has_torch=False,
            missing_packages=["sentence-transformers"],
        )
        assert caps.can_run_embeddings() is False
        assert caps.can_run_sentiment() is True
        assert caps.can_run_entity_extraction() is True
        assert caps.has_numpy is True

    def test_ai_model_capabilities_detect(self):
        from benchbox.core.ai_primitives.dataframe_operations import AIModelCapabilities

        caps = AIModelCapabilities.detect()
        assert isinstance(caps.has_numpy, bool)
        assert isinstance(caps.missing_packages, list)

    def test_ai_model_capabilities_get_missing(self):
        from benchbox.core.ai_primitives.dataframe_operations import AIModelCapabilities, AIOperationType

        caps = AIModelCapabilities(has_numpy=False)
        missing = caps.get_missing_for_operation(AIOperationType.COSINE_SIMILARITY)
        assert any("numpy" in m for m in missing)

    def test_dataframe_ai_capabilities(self):
        from benchbox.core.ai_primitives.dataframe_operations import (
            AIModelCapabilities,
            AIOperationType,
            DataFrameAICapabilities,
        )

        model_caps = AIModelCapabilities(has_textblob=True)
        caps = DataFrameAICapabilities(platform_name="polars-df", model_caps=model_caps)
        assert caps.platform_name == "polars-df"
        assert caps.supports_operation(AIOperationType.SENTIMENT_SINGLE) is True
        unsup = caps.get_unsupported_operations()
        assert isinstance(unsup, list)

    def test_dataframe_ai_result(self):
        from benchbox.core.ai_primitives.dataframe_operations import AIOperationType, DataFrameAIResult

        result = DataFrameAIResult(
            operation_type=AIOperationType.EMBEDDING_SINGLE,
            success=True,
            start_time=time.time(),
            end_time=time.time(),
            duration_ms=10.5,
            rows_processed=5,
            model_name="test-model",
        )
        assert result.success is True
        assert result.rows_processed == 5

    def test_dataframe_ai_result_failure(self):
        from benchbox.core.ai_primitives.dataframe_operations import AIOperationType, DataFrameAIResult

        result = DataFrameAIResult.failure(AIOperationType.SENTIMENT_SINGLE, "dependency missing")
        assert result.success is False
        assert result.error_message == "dependency missing"

    def test_operations_manager_init(self):
        from benchbox.core.ai_primitives.dataframe_operations import DataFrameAIOperationsManager

        mgr = DataFrameAIOperationsManager("polars-df")
        assert mgr.platform_name == "polars-df"
        caps = mgr.get_capabilities()
        assert caps.platform_name == "polars-df"

    def test_operations_manager_supports(self):
        from benchbox.core.ai_primitives.dataframe_operations import AIOperationType, DataFrameAIOperationsManager

        mgr = DataFrameAIOperationsManager("polars-df")

        result = mgr.supports_operation(AIOperationType.COSINE_SIMILARITY)
        assert isinstance(result, bool)

    def test_operations_manager_unsupported_message(self):
        from benchbox.core.ai_primitives.dataframe_operations import AIOperationType, DataFrameAIOperationsManager

        mgr = DataFrameAIOperationsManager("polars-df")
        msg = mgr.get_unsupported_message(AIOperationType.EMBEDDING_SINGLE)
        assert isinstance(msg, str)

    def test_classify_priority(self):
        from benchbox.core.ai_primitives.dataframe_operations import classify_priority

        assert classify_priority("This is urgent please fix") == "urgent"
        assert classify_priority("handle it whenever you can") == "low priority"
        assert classify_priority("normal request") == "normal"

    def test_classify_segment(self):
        from benchbox.core.ai_primitives.dataframe_operations import classify_segment

        assert classify_segment("car parts and motor oil") == "AUTOMOBILE"
        assert classify_segment("building materials") == "BUILDING"
        assert classify_segment("random text") == "AUTOMOBILE"

    def test_extract_entities_regex_fallback(self):
        from benchbox.core.ai_primitives.dataframe_operations import extract_entities

        result = extract_entities("A large red steel chair")
        assert isinstance(result, dict)

        assert "color" in result
        assert "size" in result

    def test_cosine_similarity(self):
        from benchbox.core.ai_primitives.dataframe_operations import cosine_similarity

        sim = cosine_similarity([1.0, 0.0], [1.0, 0.0])
        assert abs(sim - 1.0) < 1e-6
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_euclidean_distance(self):
        from benchbox.core.ai_primitives.dataframe_operations import euclidean_distance

        dist = euclidean_distance([0.0, 0.0], [3.0, 4.0])
        assert abs(dist - 5.0) < 1e-6

    def test_top_k_similarity(self):
        from benchbox.core.ai_primitives.dataframe_operations import top_k_similarity

        vectors = [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]]
        results = top_k_similarity([1.0, 0.0], vectors, k=2)
        assert len(results) == 2
        assert results[0][0] == 0

        assert top_k_similarity([0.0, 0.0], vectors, k=2) == []


class TestMetadataPrimitivesDataFrameOperations:
    def test_operation_type_enum(self):
        from benchbox.core.metadata_primitives.dataframe_operations import MetadataOperationType

        assert MetadataOperationType.LIST_COLUMNS.value == "list_columns"
        assert MetadataOperationType.LIST_TABLES.value == "list_tables"

    def test_operation_category_enum(self):
        from benchbox.core.metadata_primitives.dataframe_operations import MetadataOperationCategory

        assert MetadataOperationCategory.SCHEMA.value == "schema"
        assert MetadataOperationCategory.CATALOG.value == "catalog"
        assert MetadataOperationCategory.LAKEHOUSE.value == "lakehouse"

    def test_operation_categories_mapping(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            OPERATION_CATEGORIES,
            MetadataOperationCategory,
            MetadataOperationType,
        )

        assert OPERATION_CATEGORIES[MetadataOperationType.LIST_COLUMNS] == MetadataOperationCategory.SCHEMA
        assert OPERATION_CATEGORIES[MetadataOperationType.LIST_TABLES] == MetadataOperationCategory.CATALOG

    def test_capabilities_dataclass(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataCapabilities

        caps = DataFrameMetadataCapabilities(platform_name="test-platform")
        assert caps.platform_name == "test-platform"
        assert caps.supports_schema_introspection is True
        assert caps.supports_catalog is False

    def test_capabilities_supports_operation(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            DataFrameMetadataCapabilities,
            MetadataOperationType,
        )

        caps = DataFrameMetadataCapabilities(platform_name="test", supports_catalog=True)
        assert caps.supports_operation(MetadataOperationType.LIST_COLUMNS) is True
        assert caps.supports_operation(MetadataOperationType.LIST_TABLES) is True
        assert caps.supports_operation(MetadataOperationType.TABLE_HISTORY) is False

    def test_capabilities_get_supported_operations(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataCapabilities

        caps = DataFrameMetadataCapabilities(platform_name="test")
        supported = caps.get_supported_operations()
        assert isinstance(supported, list)
        assert len(supported) > 0

    def test_capabilities_get_unsupported_operations(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataCapabilities

        caps = DataFrameMetadataCapabilities(platform_name="test")
        unsupported = caps.get_unsupported_operations()
        assert isinstance(unsupported, list)

    def test_capabilities_get_supported_categories(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataCapabilities

        caps = DataFrameMetadataCapabilities(platform_name="test", supports_catalog=True)
        cats = caps.get_supported_categories()
        assert isinstance(cats, list)
        assert len(cats) >= 1

    def test_result_success(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            DataFrameMetadataResult,
            MetadataOperationType,
        )

        result = DataFrameMetadataResult.success_result(
            operation_type=MetadataOperationType.LIST_COLUMNS,
            start_time=time.time(),
            result_count=5,
            result_data=["a", "b", "c", "d", "e"],
        )
        assert result.success is True
        assert result.result_count == 5

    def test_result_failure(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            DataFrameMetadataResult,
            MetadataOperationType,
        )

        result = DataFrameMetadataResult.failure_result(
            operation_type=MetadataOperationType.LIST_COLUMNS,
            error_message="something went wrong",
        )
        assert result.success is False
        assert "something went wrong" in result.error_message

    def test_unsupported_operation_error(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            MetadataOperationType,
            UnsupportedOperationError,
        )

        err = UnsupportedOperationError(
            operation=MetadataOperationType.LIST_TABLES,
            platform_name="polars-df",
        )
        assert "polars-df" in str(err)
        assert "list_tables" in str(err)

    def test_unsupported_operation_error_with_suggestion(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            MetadataOperationType,
            UnsupportedOperationError,
        )

        err = UnsupportedOperationError(
            operation=MetadataOperationType.TABLE_HISTORY,
            platform_name="polars-df",
            suggestion="Use pyspark-df instead.",
        )
        assert "pyspark-df" in str(err)

    def test_get_unsupported_message_catalog(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            MetadataOperationType,
            get_unsupported_message,
        )

        msg = get_unsupported_message(MetadataOperationType.LIST_TABLES, "polars-df")
        assert "catalog" in msg.lower()

    def test_get_unsupported_message_lakehouse(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            MetadataOperationType,
            get_unsupported_message,
        )

        msg = get_unsupported_message(MetadataOperationType.TABLE_HISTORY, "polars-df")
        assert "delta" in msg.lower()

    def test_get_unsupported_message_iceberg(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            MetadataOperationType,
            get_unsupported_message,
        )

        msg = get_unsupported_message(MetadataOperationType.SNAPSHOT_INFO, "polars-df")
        assert "iceberg" in msg.lower()

    def test_get_unsupported_message_schema(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            MetadataOperationType,
            get_unsupported_message,
        )

        msg = get_unsupported_message(MetadataOperationType.LIST_COLUMNS, "polars-df")
        assert isinstance(msg, str)

    def test_get_platform_capabilities_polars(self):
        from benchbox.core.metadata_primitives.dataframe_operations import get_platform_capabilities

        caps = get_platform_capabilities("polars-df")
        assert caps.platform_name == "polars-df"
        assert caps.supports_schema_introspection is True
        assert caps.supports_catalog is False

    def test_get_platform_capabilities_pyspark(self):
        from benchbox.core.metadata_primitives.dataframe_operations import get_platform_capabilities

        caps = get_platform_capabilities("pyspark-df")
        assert caps.supports_catalog is True

    def test_get_platform_capabilities_unknown(self):
        from benchbox.core.metadata_primitives.dataframe_operations import get_platform_capabilities

        caps = get_platform_capabilities("unknown-platform")
        assert caps.platform_name == "unknown-platform"
        assert caps.supports_schema_introspection is True

    def test_get_platform_capabilities_with_overrides(self):
        from benchbox.core.metadata_primitives.dataframe_operations import get_platform_capabilities

        caps = get_platform_capabilities("polars-df", supports_delta_lake=True)
        assert caps.supports_delta_lake is True

    def test_platform_presets(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            DATAFUSION_METADATA_CAPABILITIES,
            PANDAS_METADATA_CAPABILITIES,
            POLARS_METADATA_CAPABILITIES,
            PYSPARK_METADATA_CAPABILITIES,
        )

        for caps in [
            POLARS_METADATA_CAPABILITIES,
            PANDAS_METADATA_CAPABILITIES,
            PYSPARK_METADATA_CAPABILITIES,
            DATAFUSION_METADATA_CAPABILITIES,
        ]:
            assert len(caps.platform_name) > 0
            assert isinstance(caps.supports_schema_introspection, bool)

    def test_manager_init(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataOperationsManager

        mgr = DataFrameMetadataOperationsManager("polars-df")
        assert mgr.platform_name == "polars-df"
        caps = mgr.get_capabilities()
        assert caps.supports_schema_introspection is True

    def test_manager_supports_operation(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            DataFrameMetadataOperationsManager,
            MetadataOperationType,
        )

        mgr = DataFrameMetadataOperationsManager("polars-df")
        assert mgr.supports_operation(MetadataOperationType.LIST_COLUMNS) is True
        assert mgr.supports_operation(MetadataOperationType.LIST_TABLES) is False

    def test_manager_get_supported_operations(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataOperationsManager

        mgr = DataFrameMetadataOperationsManager("polars-df")
        ops = mgr.get_supported_operations()
        assert isinstance(ops, list)
        assert len(ops) > 0

    def test_manager_validate_operation_raises(self):
        from benchbox.core.metadata_primitives.dataframe_operations import (
            DataFrameMetadataOperationsManager,
            MetadataOperationType,
            UnsupportedOperationError,
        )

        mgr = DataFrameMetadataOperationsManager("polars-df")
        with pytest.raises(UnsupportedOperationError):
            mgr.validate_operation(MetadataOperationType.LIST_TABLES)

    @needs_polars
    def test_manager_execute_list_columns(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataOperationsManager

        mgr = DataFrameMetadataOperationsManager("polars-df")
        df = pl.DataFrame({"a": [1], "b": [2], "c": [3]})
        result = mgr.execute_list_columns(df)
        assert result.success is True
        assert result.result_count == 3

    @needs_polars
    def test_manager_execute_get_dtypes(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataOperationsManager

        mgr = DataFrameMetadataOperationsManager("polars-df")
        df = pl.DataFrame({"x": [1], "y": ["hello"]})
        result = mgr.execute_get_dtypes(df)
        assert result.success is True
        assert result.result_count == 2

    @needs_polars
    def test_manager_execute_get_schema(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataOperationsManager

        mgr = DataFrameMetadataOperationsManager("polars-df")
        df = pl.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
        result = mgr.execute_get_schema(df)
        assert result.success is True
        assert result.result_count == 2
        assert result.result_data[0]["name"] == "col1"

    def test_manager_spark_style_schema_branches(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataOperationsManager

        field_a = SimpleNamespace(name="a", dataType="int", nullable=False)
        field_b = SimpleNamespace(name="b", dataType="string", nullable=True)
        fake_schema = SimpleNamespace(fields=[field_a, field_b])
        fake_df = SimpleNamespace(
            columns=["a", "b"],
            schema=fake_schema,
            describe=lambda: SimpleNamespace(collect=lambda: [{"summary": "count"}], count=lambda: 1),
        )

        mgr = DataFrameMetadataOperationsManager("spark")
        cols = mgr.execute_list_columns(fake_df)
        dtypes = mgr.execute_get_dtypes(fake_df)
        schema = mgr.execute_get_schema(fake_df)
        stats = mgr.execute_describe_stats(fake_df)
        assert cols.success is True
        assert dtypes.success is True
        assert schema.success is True
        assert stats.success is True

    def test_manager_datafusion_style_schema_branches(self):
        from benchbox.core.metadata_primitives.dataframe_operations import DataFrameMetadataOperationsManager

        fields = [
            SimpleNamespace(name="x", type="Utf8", nullable=True),
            SimpleNamespace(name="y", type="Int64", nullable=False),
        ]

        fake_df = SimpleNamespace(
            columns=["x", "y"],
            schema=lambda: fields,
            dtypes={"x": "Utf8", "y": "Int64"},
            describe=lambda: {"x": {"count": 1}},
        )

        mgr = DataFrameMetadataOperationsManager("datafusion")
        cols = mgr.execute_list_columns(fake_df)
        dtypes = mgr.execute_get_dtypes(fake_df)
        schema = mgr.execute_get_schema(fake_df)
        stats = mgr.execute_describe_stats(fake_df)
        assert cols.success is True
        assert dtypes.success is True
        assert schema.success is True
        assert stats.success is True
