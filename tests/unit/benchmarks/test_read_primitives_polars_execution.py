# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from datetime import date, timedelta

import pytest

try:
    import polars as pl

    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    POLARS_AVAILABLE = True
except ImportError:
    POLARS_AVAILABLE = False
    pl = None
    PolarsDataFrameAdapter = None

from benchbox.core.read_primitives.dataframe_queries import (
    REGISTRY,
    SKIP_FOR_DATAFRAME,
    SKIP_FOR_EXPRESSION_FAMILY,
    SKIP_FOR_POLARS,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed"),
]


EXPRESSION_SKIP_SET = (
    {q.upper() for q in SKIP_FOR_EXPRESSION_FAMILY}
    | {q.upper() for q in SKIP_FOR_DATAFRAME}
    | {q.upper() for q in SKIP_FOR_POLARS}
)
ALL_QUERY_IDS = REGISTRY.get_query_ids()
RUNNABLE_QUERY_IDS = [qid for qid in ALL_QUERY_IDS if qid.upper() not in EXPRESSION_SKIP_SET]


def _create_tpch_test_data() -> dict[str, pl.LazyFrame]:
    nation = pl.DataFrame(
        {
            "n_nationkey": [0, 1, 2, 3, 4],
            "n_name": ["ALGERIA", "ARGENTINA", "BRAZIL", "CANADA", "EGYPT"],
            "n_regionkey": [0, 1, 1, 1, 4],
            "n_comment": ["comment a", "comment b", "comment c", "comment d", "comment e"],
        }
    ).lazy()

    region = pl.DataFrame(
        {
            "r_regionkey": [0, 1, 2, 3, 4],
            "r_name": ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"],
            "r_comment": ["region a", "region b", "region c", "region d", "region e"],
        }
    ).lazy()

    customer = pl.DataFrame(
        {
            "c_custkey": [1, 2, 3, 4, 5],
            "c_name": ["Customer#001", "Customer#002", "Customer#003", "Customer#004", "Customer#005"],
            "c_address": ["Addr1", "Addr2", "Addr3", "Addr4", "Addr5"],
            "c_nationkey": [0, 1, 1, 2, 3],
            "c_phone": ["11-111", "22-222", "33-333", "44-444", "55-555"],
            "c_acctbal": [711.56, 121.65, 7498.12, 2866.83, 794.47],
            "c_mktsegment": ["BUILDING", "AUTOMOBILE", "BUILDING", "MACHINERY", "HOUSEHOLD"],
            "c_comment": [
                "regular deposits above the blithely express platelets",
                "furiously bold requests across silent accounts",
                "even regular ideas integrate slyly about the packages",
                "final accounts are carefully special deposits",
                "packages maintain blithely after the carefully ironic",
            ],
        }
    ).lazy()

    supplier = pl.DataFrame(
        {
            "s_suppkey": [1, 2, 3, 4, 5],
            "s_name": ["Supplier#001", "Supplier#002", "Supplier#003", "Supplier#004", "Supplier#005"],
            "s_address": ["SAddr1", "SAddr2", "SAddr3", "SAddr4", "SAddr5"],
            "s_nationkey": [0, 1, 1, 2, 3],
            "s_phone": ["11-111", "22-222", "33-333", "44-444", "55-555"],
            "s_acctbal": [5755.94, 4180.44, 4192.40, 7603.40, 3956.64],
            "s_comment": [
                "carefully final requests sleep slyly",
                "express accounts wake carefully",
                "blithely ironic pinto beans boost quickly",
                "accounts are furiously among the special",
                "ideas after the blithely pending packages",
            ],
        }
    ).lazy()

    part = pl.DataFrame(
        {
            "p_partkey": [1, 2, 3, 4, 5, 6, 7, 8],
            "p_name": [
                "goldenrod lace spring",
                "blush thistle blue",
                "spring green yellow",
                "cornflower chocolate smoke",
                "forest brown coral",
                "metallic almond aqua",
                "bisque orange ivory",
                "dim rosy khaki",
            ],
            "p_mfgr": [
                "Manufacturer#1",
                "Manufacturer#1",
                "Manufacturer#4",
                "Manufacturer#3",
                "Manufacturer#3",
                "Manufacturer#2",
                "Manufacturer#2",
                "Manufacturer#4",
            ],
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
            "p_retailprice": [901.00, 902.00, 903.00, 904.00, 905.00, 906.00, 907.00, 908.00],
            "p_comment": ["comment1", "comment2", "comment3", "comment4", "comment5", "c6", "c7", "c8"],
        }
    ).lazy()

    partsupp = pl.DataFrame(
        {
            "ps_partkey": [1, 1, 2, 2, 3, 3, 4, 4, 5, 5],
            "ps_suppkey": [1, 2, 1, 3, 2, 4, 3, 5, 1, 4],
            "ps_availqty": [3325, 8076, 5955, 4286, 4651, 7012, 1336, 2804, 4472, 9894],
            "ps_supplycost": [771.64, 993.49, 337.09, 357.84, 920.92, 337.09, 650.52, 109.98, 120.01, 444.37],
            "ps_comment": [
                "even express ideas",
                "ven ideas quickly final",
                "furiously pending notornis",
                "blithely even accounts",
                "carefully final accounts",
                "furiously pending pinto beans",
                "quickly regular packages",
                "ven ideas quickly",
                "express requests sleep",
                "final packages after",
            ],
        }
    ).lazy()

    orders = pl.DataFrame(
        {
            "o_orderkey": list(range(1, 21)),
            "o_custkey": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
            "o_orderstatus": ["O", "O", "F", "O", "F", "F", "O", "F", "O", "P"] * 2,
            "o_totalprice": [
                172799.49,
                44694.46,
                193846.25,
                32151.78,
                148959.00,
                86098.38,
                109189.49,
                61845.92,
                184133.15,
                56208.02,
                123456.78,
                98765.43,
                201345.67,
                45678.90,
                167890.12,
                78901.23,
                145678.90,
                234567.89,
                67890.12,
                189012.34,
            ],
            "o_orderdate": [date(1995, 1, 2 + i) if i < 15 else date(1995, 2, i - 14) for i in range(20)],
            "o_orderpriority": (["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"] * 4),
            "o_clerk": [f"Clerk#{i:09d}" for i in range(1, 21)],
            "o_shippriority": [0] * 20,
            "o_comment": [
                "nstructions sleep furiously among the blithely express",
                "accounts wake carefully final foxes",
                "special requests against the furiously regular",
                "requests should have to nag after the blithely",
                "pending ideas hinder blithely ironic deposits",
                "final, bold packages unwind carefully pending",
                "express accounts sleep across the slyly bold",
                "even accounts detect furiously bold packages",
                "slyly regular accounts across the unusual deposits",
                "carefully final deposits nag carefully across",
                "pinto beans sleep above the bold deposits",
                "carefully special requests detect among the express",
                "furiously unusual instructions use among the blithely",
                "quickly pending deposits wake express packages",
                "ironic deposits against the blithely pending accounts",
                "final requests integrate quickly across the accounts",
                "regular ideas boost slyly regular packages",
                "bold accounts are slyly special packages",
                "special deposits wake carefully pending ideas",
                "carefully regular accounts hang blithely express",
            ],
        }
    ).lazy()

    n_lineitem = 40
    lineitem = pl.DataFrame(
        {
            "l_orderkey": [((i // 2) % 20) + 1 for i in range(n_lineitem)],
            "l_partkey": [(i % 8) + 1 for i in range(n_lineitem)],
            "l_suppkey": [(i % 5) + 1 for i in range(n_lineitem)],
            "l_linenumber": [(i % 4) + 1 for i in range(n_lineitem)],
            "l_quantity": [float(10 + (i * 3) % 50) for i in range(n_lineitem)],
            "l_extendedprice": [float(1000 + (i * 137) % 5000) for i in range(n_lineitem)],
            "l_discount": [round(0.01 * (i % 11), 2) for i in range(n_lineitem)],
            "l_tax": [round(0.01 * (i % 9), 2) for i in range(n_lineitem)],
            "l_returnflag": ["N" if i % 3 == 0 else ("R" if i % 3 == 1 else "A") for i in range(n_lineitem)],
            "l_linestatus": ["O" if i % 2 == 0 else "F" for i in range(n_lineitem)],
            "l_shipdate": [date(1995, 1, 1) + timedelta(days=i) for i in range(n_lineitem)],
            "l_commitdate": [date(1995, 1, 5) + timedelta(days=i) for i in range(n_lineitem)],
            "l_receiptdate": [date(1995, 1, 10) + timedelta(days=i) for i in range(n_lineitem)],
            "l_shipinstruct": [
                ["DELIVER IN PERSON", "COLLECT COD", "TAKE BACK RETURN", "NONE"][i % 4] for i in range(n_lineitem)
            ],
            "l_shipmode": [
                ["REG AIR", "AIR", "RAIL", "SHIP", "TRUCK", "MAIL", "FOB"][i % 7] for i in range(n_lineitem)
            ],
            "l_comment": [f"lineitem comment {i}" for i in range(n_lineitem)],
        }
    ).lazy()

    return {
        "nation": nation,
        "region": region,
        "customer": customer,
        "supplier": supplier,
        "part": part,
        "partsupp": partsupp,
        "orders": orders,
        "lineitem": lineitem,
    }


@pytest.fixture(scope="module")
def polars_ctx():
    adapter = PolarsDataFrameAdapter()
    ctx = adapter.create_context()
    for name, df in _create_tpch_test_data().items():
        ctx.register_table(name, df)
    return ctx


@pytest.fixture(scope="module")
def polars_adapter():
    return PolarsDataFrameAdapter()


class TestAllRunnableQueriesExecute:
    @pytest.mark.parametrize("query_id", RUNNABLE_QUERY_IDS, ids=lambda qid: qid)
    def test_expression_impl_executes(self, polars_ctx, polars_adapter, query_id):
        query = REGISTRY.get(query_id)
        assert query is not None, f"Query {query_id} not found in registry"
        assert query.has_expression_impl(), f"Query {query_id} has no expression_impl"

        impl = query.expression_impl
        result = impl(polars_ctx)

        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        if isinstance(result, UnifiedLazyFrame):
            collected = result.native.collect()
        elif hasattr(result, "collect"):
            collected = result.collect()
        else:
            collected = result

        assert collected is not None, f"Query {query_id} returned None"


class TestJoinColumnNaming:
    def test_polars_drops_right_join_key(self, polars_ctx):

        partsupp = polars_ctx.get_table("partsupp")
        part = polars_ctx.get_table("part")

        merged = partsupp.join(part, left_on="ps_partkey", right_on="p_partkey")
        collected = merged.native.collect()
        columns = collected.columns

        assert "ps_partkey" in columns, "Left join key 'ps_partkey' should be in result"
        assert "p_partkey" not in columns, "Right join key 'p_partkey' should be dropped by Polars"
        assert "p_name" in columns, "Non-key right column 'p_name' should be present"

    def test_chained_joins_drop_both_right_keys(self, polars_ctx):
        partsupp = polars_ctx.get_table("partsupp")
        part = polars_ctx.get_table("part")
        supplier = polars_ctx.get_table("supplier")

        merged = partsupp.join(part, left_on="ps_partkey", right_on="p_partkey").join(
            supplier, left_on="ps_suppkey", right_on="s_suppkey"
        )
        collected = merged.native.collect()
        columns = collected.columns

        assert "ps_partkey" in columns
        assert "ps_suppkey" in columns
        assert "p_partkey" not in columns, "p_partkey should be dropped (right key of first join)"
        assert "s_suppkey" not in columns, "s_suppkey should be dropped (right key of second join)"

    def test_max_by_with_ties_uses_left_join_keys(self, polars_ctx):
        from benchbox.core.read_primitives.dataframe_queries import max_by_with_ties_expression_impl

        result = max_by_with_ties_expression_impl(polars_ctx)
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        if isinstance(result, UnifiedLazyFrame):
            collected = result.native.collect()
        else:
            collected = result.collect()

        assert "ps_partkey" in collected.columns or "max_supply_cost" in collected.columns

    def test_min_by_with_ties_uses_left_join_keys(self, polars_ctx):
        from benchbox.core.read_primitives.dataframe_queries import min_by_with_ties_expression_impl

        result = min_by_with_ties_expression_impl(polars_ctx)
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        if isinstance(result, UnifiedLazyFrame):
            collected = result.native.collect()
        else:
            collected = result.collect()

        assert "ps_partkey" in collected.columns or "min_supply_cost" in collected.columns


class TestAsofJoinFix:
    def test_asof_join_basic_executes(self, polars_ctx):
        from benchbox.core.read_primitives.dataframe_queries import asof_join_basic_expression_impl

        result = asof_join_basic_expression_impl(polars_ctx)
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        if isinstance(result, UnifiedLazyFrame):
            collected = result.native.collect()
        else:
            collected = result.collect()

        assert collected is not None
        expected_cols = {"l_orderkey", "l_shipdate", "o_orderdate", "o_totalprice", "days_to_ship"}
        assert expected_cols.issubset(set(collected.columns)), (
            f"Missing columns: {expected_cols - set(collected.columns)}"
        )


class TestUnifiedDtExprTotalDays:
    def test_total_days_returns_correct_values(self):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        df = pl.DataFrame(
            {
                "start": [date(2024, 1, 1), date(2024, 6, 1)],
                "end": [date(2024, 1, 11), date(2024, 6, 15)],
            }
        ).lazy()

        result = df.with_columns((pl.col("end") - pl.col("start")).dt.total_days().alias("days")).collect()
        assert result["days"].to_list() == [10, 14]

    def test_total_days_through_unified_expr(self, polars_ctx):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        orders = polars_ctx.get_table("orders")
        lineitem = polars_ctx.get_table("lineitem")
        col = polars_ctx.col

        result = (
            lineitem.join(orders, left_on="l_orderkey", right_on="o_orderkey")
            .with_columns((col("l_shipdate") - col("o_orderdate")).dt.total_days().alias("days_diff"))
            .select("l_orderkey", "days_diff")
            .limit(5)
        )
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        if isinstance(result, UnifiedLazyFrame):
            collected = result.native.collect()
        else:
            collected = result.collect()

        assert "days_diff" in collected.columns
        assert collected.height > 0


class TestSkipListConsistency:
    def test_skipped_queries_actually_fail_on_polars(self, polars_ctx):
        queries_that_now_work = []
        for query_id in SKIP_FOR_POLARS:
            query = REGISTRY.get(query_id)
            if query is None:
                continue
            try:
                result = query.expression_impl(polars_ctx)
                from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

                if isinstance(result, UnifiedLazyFrame):
                    result.native.collect()
                elif hasattr(result, "collect"):
                    result.collect()
                queries_that_now_work.append(query_id)
            except (AttributeError, NotImplementedError, TypeError):
                pass

        if queries_that_now_work:
            pytest.fail(
                f"These queries now work on Polars and should be removed from SKIP_FOR_POLARS: {queries_that_now_work}"
            )

    def test_non_skipped_queries_do_not_use_unsupported_apis(self, polars_ctx):
        failed_queries = []
        for query_id in RUNNABLE_QUERY_IDS:
            query = REGISTRY.get(query_id)
            if query is None:
                continue
            try:
                result = query.expression_impl(polars_ctx)
                from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

                if isinstance(result, UnifiedLazyFrame):
                    result.native.collect()
                elif hasattr(result, "collect"):
                    result.collect()
            except AttributeError as e:
                failed_queries.append((query_id, str(e)))

        if failed_queries:
            msg = "These queries failed with AttributeError and should be added to SKIP_FOR_POLARS:\n"
            for qid, err in failed_queries:
                msg += f"  - {qid}: {err}\n"
            pytest.fail(msg)


