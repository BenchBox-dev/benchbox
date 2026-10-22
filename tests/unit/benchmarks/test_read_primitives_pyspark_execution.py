# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import sys
from datetime import date

import pytest

from benchbox.platforms.pyspark import PYSPARK_AVAILABLE
from tests.utilities.optional_engines import pyspark_skip_reason, pyspark_usable

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="PySpark tests skipped on Windows - Hadoop requires winutils.exe setup",
    ),
]

_SKIP_PYSPARK = not pyspark_usable()
_SKIP_REASON = pyspark_skip_reason() or "PySpark is usable"

if PYSPARK_AVAILABLE:
    from benchbox.platforms.dataframe.pyspark_df import PySparkDataFrameAdapter
else:  # pragma: no cover
    PySparkDataFrameAdapter = None

from benchbox.core.read_primitives.dataframe_queries import (
    REGISTRY,
    SKIP_FOR_DATAFRAME,
    SKIP_FOR_EXPRESSION_FAMILY,
    SKIP_FOR_PYSPARK,
    get_skip_for_pyspark,
)
from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

pytestmark = pytestmark + [pytest.mark.skipif(_SKIP_PYSPARK, reason=_SKIP_REASON)]

PYSPARK_SKIP_SET = (
    {q.upper() for q in SKIP_FOR_EXPRESSION_FAMILY}
    | {q.upper() for q in SKIP_FOR_DATAFRAME}
    | {q.upper() for q in SKIP_FOR_PYSPARK}
)
RUNNABLE_QUERY_IDS = [qid for qid in REGISTRY.get_query_ids() if qid.upper() not in PYSPARK_SKIP_SET]

KNOWN_EMPTY_ON_FIXTURE = frozenset(
    {
        "filter_selective",
        "filter_bigint_selective",
        "filter_decimal_selective",
        "filter_string_selective",
        "predicate_ordering_costs",
        "optimizer_aggregate_pushdown",
        "optimizer_column_pruning",
        "optimizer_predicate_pushdown",
        "optimizer_runtime_filter",
        "optimizer_join_reordering",
        "array_of_struct",
        "json_extract_nested",
        "shuffle_1mb_rows",
    }
)


def _create_tpch_test_data(spark):
    import pandas as pd

    def frame(data):
        return spark.createDataFrame(pd.DataFrame(data))

    customer = frame(
        {
            "c_custkey": [1, 2, 3, 4, 5],
            "c_name": ["C1", "C2", "C3", "C4", "C5"],
            "c_address": ["A1", "A2", "A3", "A4", "A5"],
            "c_nationkey": [0, 1, 1, 2, 3],
            "c_phone": ["11", "22", "33", "44", "55"],
            "c_acctbal": [711.56, 121.65, 7498.12, 2866.83, 794.47],
            "c_mktsegment": ["BUILDING", "AUTOMOBILE", "BUILDING", "MACHINERY", "HOUSEHOLD"],
            "c_comment": [
                "regular deposits BUILDING stuff",
                "furiously bold requests FURNITURE items",
                "even regular BUILDING ideas",
                "final accounts special deposits",
                "packages maintain BUILDING things",
            ],
        }
    )
    supplier = frame(
        {
            "s_suppkey": [1, 2, 3, 4, 5],
            "s_name": ["S1", "S2", "S3", "S4", "S5"],
            "s_address": ["SA1", "SA2", "SA3", "SA4", "SA5"],
            "s_nationkey": [0, 1, 1, 2, 3],
            "s_phone": ["11", "22", "33", "44", "55"],
            "s_acctbal": [5755.94, 4180.44, 4192.40, 7603.40, 3956.64],
            "s_comment": [
                "Customer Complaints about late delivery",
                "express accounts wake carefully",
                "blithely ironic pinto beans",
                "Customer Complaints resolved quickly",
                "ideas after pending packages",
            ],
        }
    )
    part = frame(
        {
            "p_partkey": [1, 2, 3, 4, 5, 6, 7, 8],
            "p_name": [
                "goldenrod STEEL spring",
                "blush COPPER blue",
                "spring green yellow",
                "cornflower chocolate smoke",
                "forest brown coral",
                "metallic almond aqua",
                "bisque orange ivory",
                "dim rosy khaki",
            ],
            "p_mfgr": ["M1", "M1", "M4", "M3", "M3", "M2", "M2", "M4"],
            "p_brand": ["Brand#13", "Brand#13", "Brand#42", "Brand#34", "Brand#32", "Brand#24", "Brand#22", "Brand#44"],
            "p_type": [
                "PROMO BURNISHED COPPER",
                "LARGE BRUSHED BRASS",
                "STANDARD POLISHED STEEL",
                "SMALL PLATED BRASS",
                "STANDARD POLISHED TIN",
                "PROMO POLISHED STEEL",
                "MEDIUM PLATED STEEL",
                "ECONOMY ANODIZED BRASS",
            ],
            "p_size": [7, 1, 21, 14, 15, 4, 45, 41],
            "p_container": ["JUMBO PKG", "LG CASE", "WRAP CASE", "MED DRUM", "SM PKG", "MED BAG", "SM BOX", "LG BOX"],
            "p_retailprice": [901.0, 902.0, 903.0, 904.0, 905.0, 906.0, 907.0, 908.0],
            "p_comment": ["STEEL comment1", "comment2", "comment3", "COPPER comment4", "c5", "c6", "c7", "c8"],
        }
    )
    partsupp = frame(
        {
            "ps_partkey": [2, 5, 1, 3, 1, 4, 2, 5, 3, 4],
            "ps_suppkey": [1, 1, 1, 2, 2, 3, 3, 4, 4, 5],
            "ps_availqty": [5955, 4472, 3325, 4651, 8076, 1336, 4286, 9894, 7012, 2804],
            "ps_supplycost": [337.09, 120.01, 771.64, 920.92, 993.49, 650.52, 357.84, 444.37, 337.09, 109.98],
            "ps_comment": ["c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9"],
        }
    )
    nation = frame(
        {
            "n_nationkey": [0, 1, 2, 3, 4],
            "n_name": ["ALGERIA", "ARGENTINA", "BRAZIL", "CANADA", "EGYPT"],
            "n_regionkey": [0, 1, 1, 1, 4],
            "n_comment": ["comment a", "comment b", "comment c", "comment d", "comment e"],
        }
    )
    region = frame(
        {
            "r_regionkey": [0, 1, 2, 3, 4],
            "r_name": ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"],
            "r_comment": ["region a", "region b", "region c", "region d", "region e"],
        }
    )
    orders = frame(
        {
            "o_orderkey": list(range(1, 13)),
            "o_custkey": [1, 1, 1, 1, 1, 1, 2, 2, 3, 4, 5, 2],
            "o_orderstatus": ["O", "F", "O", "F", "O", "F", "O", "F", "O", "F", "O", "F"],
            "o_totalprice": [float(40000 + i * 7777) for i in range(12)],
            "o_orderdate": [date(1995, 1, 2 + i) for i in range(12)],
            "o_orderpriority": ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW", "1-URGENT"] * 2,
            "o_clerk": [f"Clerk#{i:09d}" for i in range(1, 13)],
            "o_shippriority": [0] * 12,
            "o_comment": ["some comment here"] * 12,
        }
    )
    n_lineitem = 24
    lineitem = frame(
        {
            "l_orderkey": [((i // 2) % 12) + 1 for i in range(n_lineitem)],
            "l_partkey": [(i % 8) + 1 for i in range(n_lineitem)],
            "l_suppkey": [(i % 5) + 1 for i in range(n_lineitem)],
            "l_linenumber": [(i % 2) + 1 for i in range(n_lineitem)],
            "l_quantity": [float(10 + (i * 3) % 40) for i in range(n_lineitem)],
            "l_extendedprice": [float(1000 + (i * 137) % 5000) for i in range(n_lineitem)],
            "l_discount": [round(0.01 * (i % 11), 2) for i in range(n_lineitem)],
            "l_tax": [round(0.01 * (i % 9), 2) for i in range(n_lineitem)],
            "l_returnflag": ["N" if i % 3 == 0 else ("R" if i % 3 == 1 else "A") for i in range(n_lineitem)],
            "l_linestatus": ["O" if i % 2 == 0 else "F" for i in range(n_lineitem)],
            "l_shipdate": [date(1995, 1, 1 + (i % 28)) for i in range(n_lineitem)],
            "l_commitdate": [date(1995, 1, 5) for _ in range(n_lineitem)],
            "l_receiptdate": [date(1995, 1, 10) for _ in range(n_lineitem)],
            "l_shipinstruct": ["DELIVER IN PERSON" for _ in range(n_lineitem)],
            "l_shipmode": [
                ["REG AIR", "AIR", "RAIL", "SHIP", "TRUCK", "MAIL", "FOB"][i % 7] for i in range(n_lineitem)
            ],
            "l_comment": [f"lineitem comment {i}" for i in range(n_lineitem)],
        }
    )
    return {
        "customer": customer,
        "supplier": supplier,
        "part": part,
        "partsupp": partsupp,
        "orders": orders,
        "lineitem": lineitem,
        "nation": nation,
        "region": region,
    }


@pytest.fixture(scope="module")
def pyspark_ctx(pyspark_test_environment):
    adapter = PySparkDataFrameAdapter(
        master="local[2]",
        app_name="BenchBox-ReadPrim-PySpark",
        driver_memory="1g",
        shuffle_partitions=2,
    )
    ctx = adapter.create_context()
    for name, df in _create_tpch_test_data(adapter.spark).items():
        ctx.register_table(name, df)
    yield ctx
    adapter.close()


def _collect(result):
    native = result.native if isinstance(result, UnifiedLazyFrame) else result
    if hasattr(native, "toPandas"):
        return native.limit(500).toPandas()
    return native.collect()


class TestAllRunnableQueriesExecute:
    @pytest.mark.parametrize("query_id", RUNNABLE_QUERY_IDS, ids=lambda qid: qid)
    def test_expression_impl_executes(self, pyspark_ctx, query_id):
        query = REGISTRY.get(query_id)
        assert query is not None, f"Query {query_id} not found in registry"
        assert query.has_expression_impl(), f"Query {query_id} has no expression_impl"
        collected = _collect(query.expression_impl(pyspark_ctx))
        assert collected is not None, f"Query {query_id} returned None"
        if query_id not in KNOWN_EMPTY_ON_FIXTURE:
            assert len(collected) > 0, f"Query {query_id} returned an empty result on the TPC-H fixture"


class TestArrayOrdering:
    def test_array_agg_simple_collects_sorted_parts(self, pyspark_ctx):
        pdf = _collect(REGISTRY.get("array_agg_simple").expression_impl(pyspark_ctx))
        by_suppkey = {row.ps_suppkey: list(row.supplied_parts) for row in pdf.itertuples()}
        assert by_suppkey[1] == [1, 2, 5]
        assert by_suppkey[2] == [1, 3]
        for parts in by_suppkey.values():
            assert parts == sorted(parts)

    def test_array_agg_distinct_collects_sorted_keys(self, pyspark_ctx):
        pdf = _collect(REGISTRY.get("array_agg_distinct").expression_impl(pyspark_ctx))
        by_segment = {row.c_mktsegment: list(row.nation_keys) for row in pdf.itertuples()}
        assert by_segment["BUILDING"] == [0, 1]
        for keys in by_segment.values():
            assert keys == sorted(keys)

    def test_array_length_orders_by_count_then_key(self, pyspark_ctx):
        pdf = _collect(REGISTRY.get("array_length").expression_impl(pyspark_ctx))
        assert list(pdf["ps_suppkey"]) == [1, 2, 3, 4, 5]
        assert list(pdf["num_parts"]) == [3, 2, 2, 2, 1]

    def test_array_slice_keeps_descending_top_orders(self, pyspark_ctx):
        pdf = _collect(REGISTRY.get("array_slice").expression_impl(pyspark_ctx))
        assert len(pdf) > 0, "fixture must give a customer with >= 5 orders"
        for row in pdf.itertuples():
            top = list(row.top_3_orders)
            assert len(top) == 3
            assert top == sorted(top, reverse=True)


class TestMapConstruction:
    def test_map_construction_values(self, pyspark_ctx):
        pdf = _collect(REGISTRY.get("map_construction").expression_impl(pyspark_ctx))
        by_suppkey = {row.ps_suppkey: dict(row.part_costs) for row in pdf.itertuples()}
        assert {str(k): v for k, v in by_suppkey[1].items()} == {"1": 771.64, "2": 337.09, "5": 120.01}

    def test_map_access_values(self, pyspark_ctx):
        pdf = _collect(REGISTRY.get("map_access").expression_impl(pyspark_ctx))
        row = pdf.sort_values("ps_suppkey").iloc[0]
        assert row["cost_for_part_1"] == pytest.approx(771.64)
        assert row["cost_for_part_5"] == pytest.approx(120.01)


class TestFulltextScores:
    def test_boolean_search_scores(self, pyspark_ctx):
        pdf = _collect(REGISTRY.get("fulltext_boolean_search").expression_impl(pyspark_ctx))
        assert len(pdf) > 0
        assert set(pdf["relevance_score"]) == {1}

    def test_phrase_search_scores(self, pyspark_ctx):
        pdf = _collect(REGISTRY.get("fulltext_phrase_search").expression_impl(pyspark_ctx))
        assert len(pdf) == 2
        assert set(pdf["phrase_match_score"]) == {1}


class TestWindowMethods:
    def test_percent_rank_range_and_top_row(self, pyspark_ctx):
        pdf = _collect(REGISTRY.get("qualify_percentile").expression_impl(pyspark_ctx))
        assert len(pdf) > 0
        assert (pdf["price_percentile"] >= 0.9).all()
        assert (pdf["price_percentile"] <= 1.0).all()

    def test_cume_dist_range_and_top_row(self, pyspark_ctx):
        pdf = _collect(REGISTRY.get("qualify_cume_dist").expression_impl(pyspark_ctx))
        assert len(pdf) > 0
        assert (pdf["quantity_cumulative_dist"] >= 0.95).all()
        assert (pdf["quantity_cumulative_dist"] <= 1.0).all()


class TestPySparkSkipList:
    def test_skip_list_contains_raw_polars_impls(self):
        for query_id in (
            "window_moving_frame",
            "window_multiple_orderings",
            "optimizer_common_subexpression",
            "statistical_correlation",
            "statistical_variance_stddev",
            "struct_construction",
            "timeseries_trend_analysis",
            "olap_cube_analysis",
            "olap_rollup_analysis",
        ):
            assert query_id in get_skip_for_pyspark(), f"{query_id} should be skipped for PySpark"
            assert REGISTRY.get(query_id) is not None, f"{query_id} missing from registry"

    def test_skipped_queries_still_raise_on_pyspark(self, pyspark_ctx):
        from pyspark.errors import PySparkAttributeError, PySparkTypeError

        unexpectedly_working = []
        for query_id in SKIP_FOR_PYSPARK:
            query = REGISTRY.get(query_id)
            if query is None:
                continue
            try:
                _collect(query.expression_impl(pyspark_ctx))
            except (NotImplementedError, PySparkTypeError, PySparkAttributeError):
                continue
            unexpectedly_working.append(query_id)
        assert not unexpectedly_working, (
            f"These queries now work on PySpark and should be removed from SKIP_FOR_PYSPARK: {unexpectedly_working}"
        )
