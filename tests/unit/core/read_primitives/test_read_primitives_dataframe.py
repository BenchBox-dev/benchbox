# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

try:
    import pandas as pd
    import polars as pl

    from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    DEPS_AVAILABLE = True
except ImportError:
    DEPS_AVAILABLE = False

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(not DEPS_AVAILABLE, reason="Polars and/or Pandas not installed"),
]


def _to_pandas(result: Any) -> pd.DataFrame:
    from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame
    from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

    if isinstance(result, UnifiedLazyFrame):
        result = result.native
    if isinstance(result, UnifiedPandasFrame):
        result = result.native
    if isinstance(result, pl.LazyFrame):
        result = result.collect()
    if isinstance(result, pl.DataFrame):
        return result.to_pandas()
    if isinstance(result, pd.DataFrame):
        return result
    raise TypeError(f"Cannot convert {type(result)} to pandas DataFrame")


def _create_polars_context():
    adapter = PolarsDataFrameAdapter()
    return adapter.create_context()


def _create_pandas_context():
    adapter = PandasDataFrameAdapter()
    return adapter.create_context()


def _tpch_nation_polars() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "n_nationkey": [0, 1, 2, 3, 4],
            "n_name": ["ALGERIA", "ARGENTINA", "BRAZIL", "CANADA", "EGYPT"],
            "n_regionkey": [0, 1, 1, 1, 0],
            "n_comment": ["special", "al foxes", "ven packages", "eas hang", "y above"],
        }
    ).lazy()


def _tpch_region_polars() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "r_regionkey": [0, 1],
            "r_name": ["AFRICA", "AMERICA"],
            "r_comment": ["lar deposits", "hs use ironic"],
        }
    ).lazy()


def _tpch_customer_polars() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "c_custkey": [1, 2, 3, 4, 5],
            "c_name": [
                "Customer#000000001",
                "Customer#000000002",
                "Customer#000000003",
                "Customer#000000004",
                "Customer#000000005",
            ],
            "c_address": ["addr1", "addr2", "addr3", "addr4", "addr5"],
            "c_nationkey": [0, 1, 1, 2, 0],
            "c_phone": ["25-989-741-2988", "23-768-687-3665", "11-719-748-3364", "14-128-190-5944", "13-750-942-6364"],
            "c_acctbal": [711.56, 121.65, 7498.12, 2866.83, 794.47],
            "c_mktsegment": ["BUILDING", "AUTOMOBILE", "AUTOMOBILE", "MACHINERY", "HOUSEHOLD"],
            "c_comment": ["comment1", "comment2", "comment3", "comment4", "comment5"],
        }
    ).lazy()


def _tpch_orders_polars() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "o_orderkey": [1, 2, 3, 4, 5, 6, 7, 32],
            "o_custkey": [1, 2, 3, 4, 5, 1, 2, 3],
            "o_orderstatus": ["O", "O", "F", "O", "F", "F", "O", "O"],
            "o_totalprice": [
                173665.47,
                46929.18,
                193846.25,
                32151.78,
                144659.20,
                58749.59,
                252004.18,
                130654.30,
            ],
            "o_orderdate": [
                date(1996, 1, 2),
                date(1996, 12, 1),
                date(1993, 10, 14),
                date(1995, 10, 11),
                date(1994, 7, 30),
                date(1992, 2, 21),
                date(1996, 1, 10),
                date(1995, 7, 16),
            ],
            "o_orderpriority": [
                "5-LOW",
                "1-URGENT",
                "5-LOW",
                "5-LOW",
                "5-LOW",
                "4-NOT SPECIFIED",
                "2-HIGH",
                "3-MEDIUM",
            ],
            "o_clerk": [
                "Clerk#1",
                "Clerk#2",
                "Clerk#3",
                "Clerk#4",
                "Clerk#5",
                "Clerk#6",
                "Clerk#7",
                "Clerk#8",
            ],
            "o_shippriority": [0, 0, 0, 0, 0, 0, 0, 0],
            "o_comment": ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"],
        }
    ).lazy()


def _tpch_lineitem_polars() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "l_orderkey": [1, 1, 1, 2, 3, 3, 4, 5, 6, 7],
            "l_partkey": [101, 102, 103, 104, 105, 106, 107, 108, 109, 110],
            "l_suppkey": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
            "l_linenumber": [1, 2, 3, 1, 1, 2, 1, 1, 1, 1],
            "l_quantity": [17.0, 36.0, 8.0, 19.0, 24.0, 32.0, 28.0, 50.0, 3.0, 46.0],
            "l_extendedprice": [
                21168.23,
                45983.16,
                13309.60,
                28955.64,
                22824.48,
                49620.16,
                25284.00,
                66882.00,
                3135.24,
                68065.12,
            ],
            "l_discount": [0.04, 0.09, 0.10, 0.09, 0.10, 0.07, 0.09, 0.06, 0.02, 0.00],
            "l_tax": [0.02, 0.06, 0.02, 0.06, 0.04, 0.02, 0.06, 0.03, 0.06, 0.05],
            "l_returnflag": ["N", "N", "N", "N", "R", "R", "N", "R", "A", "N"],
            "l_linestatus": ["O", "O", "O", "O", "F", "F", "O", "F", "F", "O"],
            "l_shipdate": [
                date(1996, 3, 13),
                date(1996, 4, 12),
                date(1996, 1, 29),
                date(1997, 1, 28),
                date(1994, 2, 2),
                date(1993, 11, 9),
                date(1996, 3, 21),
                date(1994, 9, 25),
                date(1992, 4, 27),
                date(1996, 2, 1),
            ],
            "l_commitdate": [
                date(1996, 2, 12),
                date(1996, 2, 28),
                date(1996, 3, 5),
                date(1997, 1, 14),
                date(1994, 1, 4),
                date(1993, 12, 20),
                date(1996, 1, 30),
                date(1994, 7, 8),
                date(1992, 2, 24),
                date(1996, 1, 10),
            ],
            "l_receiptdate": [
                date(1996, 4, 12),
                date(1996, 5, 11),
                date(1996, 2, 19),
                date(1997, 3, 2),
                date(1994, 2, 8),
                date(1993, 12, 22),
                date(1996, 4, 15),
                date(1994, 10, 10),
                date(1992, 5, 10),
                date(1996, 3, 2),
            ],
            "l_shipinstruct": [
                "DELIVER IN PERSON",
                "NONE",
                "TAKE BACK RETURN",
                "NONE",
                "DELIVER IN PERSON",
                "NONE",
                "NONE",
                "COLLECT COD",
                "DELIVER IN PERSON",
                "TAKE BACK RETURN",
            ],
            "l_shipmode": [
                "TRUCK",
                "MAIL",
                "REG AIR",
                "RAIL",
                "AIR",
                "TRUCK",
                "SHIP",
                "FOB",
                "MAIL",
                "REG AIR",
            ],
            "l_comment": ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "c10"],
        }
    ).lazy()


def _tpch_part_polars() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "p_partkey": [101, 102, 103, 104, 105],
            "p_name": [
                "goldenrod blue lemon",
                "blush thistle blue",
                "spring green yellow",
                "cornflower chocolate smoke",
                "forest brown coral",
            ],
            "p_mfgr": ["Manufacturer#1", "Manufacturer#1", "Manufacturer#2", "Manufacturer#3", "Manufacturer#3"],
            "p_brand": ["Brand#13", "Brand#13", "Brand#23", "Brand#34", "Brand#32"],
            "p_type": [
                "PROMO BURNISHED COPPER",
                "SMALL PLATED COPPER",
                "STANDARD POLISHED TIN",
                "LARGE BRUSHED BRASS",
                "STANDARD POLISHED TIN",
            ],
            "p_size": [7, 1, 21, 14, 15],
            "p_container": ["JUMBO PKG", "LG CASE", "MED BAG", "LG BOX", "SM PACK"],
            "p_retailprice": [901.00, 902.00, 903.00, 904.00, 905.00],
            "p_comment": ["pc1", "pc2", "pc3", "pc4", "pc5"],
        }
    ).lazy()


def _tpch_supplier_polars() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "s_suppkey": [1, 2, 3, 4, 5],
            "s_name": ["Supplier#1", "Supplier#2", "Supplier#3", "Supplier#4", "Supplier#5"],
            "s_address": ["s_addr1", "s_addr2", "s_addr3", "s_addr4", "s_addr5"],
            "s_nationkey": [0, 1, 1, 2, 0],
            "s_phone": ["27-918-335-1736", "15-679-861-2259", "11-383-516-1199", "19-770-882-9880", "16-128-150-5944"],
            "s_acctbal": [5755.94, -261.68, 4032.68, 4641.08, -531.88],
            "s_comment": ["sc1", "sc2", "sc3", "sc4", "sc5"],
        }
    ).lazy()


def _tpch_partsupp_polars() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "ps_partkey": [101, 102, 103, 104, 105],
            "ps_suppkey": [1, 2, 3, 4, 5],
            "ps_availqty": [3325, 8076, 4651, 1627, 9653],
            "ps_supplycost": [771.64, 993.49, 337.09, 357.84, 220.51],
            "ps_comment": ["psc1", "psc2", "psc3", "psc4", "psc5"],
        }
    ).lazy()


def _tpch_nation_pandas() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "n_nationkey": [0, 1, 2, 3, 4],
            "n_name": ["ALGERIA", "ARGENTINA", "BRAZIL", "CANADA", "EGYPT"],
            "n_regionkey": [0, 1, 1, 1, 0],
            "n_comment": ["special", "al foxes", "ven packages", "eas hang", "y above"],
        }
    )


def _tpch_region_pandas() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "r_regionkey": [0, 1],
            "r_name": ["AFRICA", "AMERICA"],
            "r_comment": ["lar deposits", "hs use ironic"],
        }
    )


def _tpch_customer_pandas() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "c_custkey": [1, 2, 3, 4, 5],
            "c_name": [
                "Customer#000000001",
                "Customer#000000002",
                "Customer#000000003",
                "Customer#000000004",
                "Customer#000000005",
            ],
            "c_address": ["addr1", "addr2", "addr3", "addr4", "addr5"],
            "c_nationkey": [0, 1, 1, 2, 0],
            "c_phone": ["25-989-741-2988", "23-768-687-3665", "11-719-748-3364", "14-128-190-5944", "13-750-942-6364"],
            "c_acctbal": [711.56, 121.65, 7498.12, 2866.83, 794.47],
            "c_mktsegment": ["BUILDING", "AUTOMOBILE", "AUTOMOBILE", "MACHINERY", "HOUSEHOLD"],
            "c_comment": ["comment1", "comment2", "comment3", "comment4", "comment5"],
        }
    )


def _tpch_orders_pandas() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "o_orderkey": [1, 2, 3, 4, 5, 6, 7, 32],
            "o_custkey": [1, 2, 3, 4, 5, 1, 2, 3],
            "o_orderstatus": ["O", "O", "F", "O", "F", "F", "O", "O"],
            "o_totalprice": [
                173665.47,
                46929.18,
                193846.25,
                32151.78,
                144659.20,
                58749.59,
                252004.18,
                130654.30,
            ],
            "o_orderdate": [
                date(1996, 1, 2),
                date(1996, 12, 1),
                date(1993, 10, 14),
                date(1995, 10, 11),
                date(1994, 7, 30),
                date(1992, 2, 21),
                date(1996, 1, 10),
                date(1995, 7, 16),
            ],
            "o_orderpriority": [
                "5-LOW",
                "1-URGENT",
                "5-LOW",
                "5-LOW",
                "5-LOW",
                "4-NOT SPECIFIED",
                "2-HIGH",
                "3-MEDIUM",
            ],
            "o_clerk": [
                "Clerk#1",
                "Clerk#2",
                "Clerk#3",
                "Clerk#4",
                "Clerk#5",
                "Clerk#6",
                "Clerk#7",
                "Clerk#8",
            ],
            "o_shippriority": [0, 0, 0, 0, 0, 0, 0, 0],
            "o_comment": ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"],
        }
    )


def _tpch_lineitem_pandas() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "l_orderkey": [1, 1, 1, 2, 3, 3, 4, 5, 6, 7],
            "l_partkey": [101, 102, 103, 104, 105, 106, 107, 108, 109, 110],
            "l_suppkey": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
            "l_linenumber": [1, 2, 3, 1, 1, 2, 1, 1, 1, 1],
            "l_quantity": [17.0, 36.0, 8.0, 19.0, 24.0, 32.0, 28.0, 50.0, 3.0, 46.0],
            "l_extendedprice": [
                21168.23,
                45983.16,
                13309.60,
                28955.64,
                22824.48,
                49620.16,
                25284.00,
                66882.00,
                3135.24,
                68065.12,
            ],
            "l_discount": [0.04, 0.09, 0.10, 0.09, 0.10, 0.07, 0.09, 0.06, 0.02, 0.00],
            "l_tax": [0.02, 0.06, 0.02, 0.06, 0.04, 0.02, 0.06, 0.03, 0.06, 0.05],
            "l_returnflag": ["N", "N", "N", "N", "R", "R", "N", "R", "A", "N"],
            "l_linestatus": ["O", "O", "O", "O", "F", "F", "O", "F", "F", "O"],
            "l_shipdate": [
                date(1996, 3, 13),
                date(1996, 4, 12),
                date(1996, 1, 29),
                date(1997, 1, 28),
                date(1994, 2, 2),
                date(1993, 11, 9),
                date(1996, 3, 21),
                date(1994, 9, 25),
                date(1992, 4, 27),
                date(1996, 2, 1),
            ],
            "l_commitdate": [
                date(1996, 2, 12),
                date(1996, 2, 28),
                date(1996, 3, 5),
                date(1997, 1, 14),
                date(1994, 1, 4),
                date(1993, 12, 20),
                date(1996, 1, 30),
                date(1994, 7, 8),
                date(1992, 2, 24),
                date(1996, 1, 10),
            ],
            "l_receiptdate": [
                date(1996, 4, 12),
                date(1996, 5, 11),
                date(1996, 2, 19),
                date(1997, 3, 2),
                date(1994, 2, 8),
                date(1993, 12, 22),
                date(1996, 4, 15),
                date(1994, 10, 10),
                date(1992, 5, 10),
                date(1996, 3, 2),
            ],
            "l_shipinstruct": [
                "DELIVER IN PERSON",
                "NONE",
                "TAKE BACK RETURN",
                "NONE",
                "DELIVER IN PERSON",
                "NONE",
                "NONE",
                "COLLECT COD",
                "DELIVER IN PERSON",
                "TAKE BACK RETURN",
            ],
            "l_shipmode": [
                "TRUCK",
                "MAIL",
                "REG AIR",
                "RAIL",
                "AIR",
                "TRUCK",
                "SHIP",
                "FOB",
                "MAIL",
                "REG AIR",
            ],
            "l_comment": ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "c10"],
        }
    )


def _tpch_part_pandas() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "p_partkey": [101, 102, 103, 104, 105],
            "p_name": [
                "goldenrod blue lemon",
                "blush thistle blue",
                "spring green yellow",
                "cornflower chocolate smoke",
                "forest brown coral",
            ],
            "p_mfgr": ["Manufacturer#1", "Manufacturer#1", "Manufacturer#2", "Manufacturer#3", "Manufacturer#3"],
            "p_brand": ["Brand#13", "Brand#13", "Brand#23", "Brand#34", "Brand#32"],
            "p_type": [
                "PROMO BURNISHED COPPER",
                "SMALL PLATED COPPER",
                "STANDARD POLISHED TIN",
                "LARGE BRUSHED BRASS",
                "STANDARD POLISHED TIN",
            ],
            "p_size": [7, 1, 21, 14, 15],
            "p_container": ["JUMBO PKG", "LG CASE", "MED BAG", "LG BOX", "SM PACK"],
            "p_retailprice": [901.00, 902.00, 903.00, 904.00, 905.00],
            "p_comment": ["pc1", "pc2", "pc3", "pc4", "pc5"],
        }
    )


def _tpch_supplier_pandas() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "s_suppkey": [1, 2, 3, 4, 5],
            "s_name": ["Supplier#1", "Supplier#2", "Supplier#3", "Supplier#4", "Supplier#5"],
            "s_address": ["s_addr1", "s_addr2", "s_addr3", "s_addr4", "s_addr5"],
            "s_nationkey": [0, 1, 1, 2, 0],
            "s_phone": ["27-918-335-1736", "15-679-861-2259", "11-383-516-1199", "19-770-882-9880", "16-128-150-5944"],
            "s_acctbal": [5755.94, -261.68, 4032.68, 4641.08, -531.88],
            "s_comment": ["sc1", "sc2", "sc3", "sc4", "sc5"],
        }
    )


def _tpch_partsupp_pandas() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ps_partkey": [101, 102, 103, 104, 105],
            "ps_suppkey": [1, 2, 3, 4, 5],
            "ps_availqty": [3325, 8076, 4651, 1627, 9653],
            "ps_supplycost": [771.64, 993.49, 337.09, 357.84, 220.51],
            "ps_comment": ["psc1", "psc2", "psc3", "psc4", "psc5"],
        }
    )


def _register_all_polars(ctx):
    ctx.register_table("nation", _tpch_nation_polars())
    ctx.register_table("region", _tpch_region_polars())
    ctx.register_table("customer", _tpch_customer_polars())
    ctx.register_table("orders", _tpch_orders_polars())
    ctx.register_table("lineitem", _tpch_lineitem_polars())
    ctx.register_table("part", _tpch_part_polars())
    ctx.register_table("supplier", _tpch_supplier_polars())
    ctx.register_table("partsupp", _tpch_partsupp_polars())


def _register_all_pandas(ctx):
    ctx.register_table("nation", _tpch_nation_pandas())
    ctx.register_table("region", _tpch_region_pandas())
    ctx.register_table("customer", _tpch_customer_pandas())
    ctx.register_table("orders", _tpch_orders_pandas())
    ctx.register_table("lineitem", _tpch_lineitem_pandas())
    ctx.register_table("part", _tpch_part_pandas())
    ctx.register_table("supplier", _tpch_supplier_pandas())
    ctx.register_table("partsupp", _tpch_partsupp_pandas())


def _compare_results(expr_result: pd.DataFrame, pandas_result: pd.DataFrame, *, rtol: float = 1e-5) -> None:
    expr_cols = sorted(expr_result.columns.tolist())
    pandas_cols = sorted(pandas_result.columns.tolist())
    assert expr_cols == pandas_cols, f"Column mismatch: expr={expr_cols}, pandas={pandas_cols}"

    assert len(expr_result) == len(pandas_result), (
        f"Row count mismatch: expr={len(expr_result)}, pandas={len(pandas_result)}"
    )

    if len(expr_result) == 0:
        return

    common_cols = sorted(expr_result.columns.tolist())
    expr_sorted = expr_result[common_cols].reset_index(drop=True)
    pandas_sorted = pandas_result[common_cols].reset_index(drop=True)

    expr_sorted = expr_sorted.sort_values(common_cols, ignore_index=True)
    pandas_sorted = pandas_sorted.sort_values(common_cols, ignore_index=True)

    for col_name in common_cols:
        e_col = expr_sorted[col_name]
        p_col = pandas_sorted[col_name]
        if pd.api.types.is_numeric_dtype(e_col) and pd.api.types.is_numeric_dtype(p_col):
            pd.testing.assert_series_equal(
                e_col.astype(float),
                p_col.astype(float),
                check_names=False,
                rtol=rtol,
                atol=1e-10,
            )
        else:
            pd.testing.assert_series_equal(
                e_col.astype(str),
                p_col.astype(str),
                check_names=False,
            )


class TestReadPrimitivesRegistry:
    def test_registry_loads(self):
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        assert registry is not None
        assert len(registry.get_all_queries()) > 0

    def test_registry_has_expected_categories(self):
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        queries = registry.get_all_queries()

        all_categories = set()
        for q in queries:
            for cat in q.categories:
                all_categories.add(cat)

        from benchbox.core.dataframe.query import QueryCategory

        assert QueryCategory.AGGREGATE in all_categories
        assert QueryCategory.FILTER in all_categories
        assert QueryCategory.JOIN in all_categories
        assert QueryCategory.WINDOW in all_categories

    def test_all_queries_have_both_impls(self):
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        for query in registry.get_all_queries():
            assert query.has_expression_impl(), f"{query.query_id} missing expression_impl"
            assert query.has_pandas_impl(), f"{query.query_id} missing pandas_impl"

    def test_skip_lists_are_valid(self):
        from benchbox.core.read_primitives.dataframe_queries import (
            get_dataframe_queries,
            get_skip_for_dataframe,
            get_skip_for_datafusion,
            get_skip_for_expression_family,
            get_skip_for_polars,
            get_skip_for_pyspark,
        )

        registry = get_dataframe_queries()
        all_ids = {q.query_id for q in registry.get_all_queries()}

        for skip_id in get_skip_for_dataframe():
            assert skip_id not in all_ids, f"{skip_id} is in skip_for_dataframe but also registered"

        for skip_id in get_skip_for_expression_family():
            assert skip_id in all_ids, f"{skip_id} is in skip_for_expression_family but not registered"

        for skip_id in get_skip_for_datafusion():
            assert skip_id in all_ids, f"{skip_id} is in skip_for_datafusion but not registered"

        for skip_id in get_skip_for_polars():
            assert skip_id in all_ids, f"{skip_id} is in skip_for_polars but not registered"

        for skip_id in get_skip_for_pyspark():
            assert skip_id in all_ids, f"{skip_id} is in skip_for_pyspark but not registered"

    def test_query_ids_are_unique(self):
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        ids = [q.query_id for q in registry.get_all_queries()]
        assert len(ids) == len(set(ids)), "Duplicate query IDs found"


class TestExpressionAggregation:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_polars_context()
        _register_all_polars(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    def _execute(self, query_id: str) -> pd.DataFrame:
        query = self.registry.get(query_id)
        result = query.expression_impl(self.ctx)
        return _to_pandas(result)

    def test_aggregation_simple(self):
        result = self._execute("aggregation_simple")
        assert "total_orders" in result.columns
        assert "total_revenue" in result.columns
        assert result["total_orders"].iloc[0] == 8

    def test_aggregation_distinct(self):
        result = self._execute("aggregation_distinct")
        assert "unique_customers" in result.columns
        assert result["unique_customers"].iloc[0] > 0

    def test_aggregation_groupby_small(self):
        result = self._execute("aggregation_groupby_small")
        assert "n_regionkey" in result.columns
        assert "nation_count" in result.columns
        assert len(result) == 2

    def test_aggregation_groupby_large(self):
        result = self._execute("aggregation_groupby_large")
        assert "l_orderkey" in result.columns
        assert "total_qty" in result.columns
        assert len(result) > 0

    def test_aggregation_materialize(self):
        result = self._execute("aggregation_materialize")
        assert "avg_customer_spending" in result.columns
        assert len(result) == 1
        assert result["avg_customer_spending"].iloc[0] > 0

    def test_aggregation_selective(self):
        result = self._execute("aggregation_selective")
        assert "total_discount_amount" in result.columns
        assert len(result) == 1

    def test_count_star(self):
        result = self._execute("count_star")
        assert "total_lineitems" in result.columns
        assert result["total_lineitems"].iloc[0] == 10


class TestExpressionFilter:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_polars_context()
        _register_all_polars(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    def _execute(self, query_id: str) -> pd.DataFrame:
        query = self.registry.get(query_id)
        return _to_pandas(query.expression_impl(self.ctx))

    def test_filter_selective(self):
        result = self._execute("filter_selective")
        assert "l_orderkey" in result.columns
        assert "l_quantity" in result.columns

    def test_filter_non_selective(self):
        result = self._execute("filter_non_selective")
        assert "l_orderkey" in result.columns
        assert len(result) >= 8

    def test_filter_bigint_in_list(self):
        result = self._execute("filter_bigint_in_list_selective")
        assert "o_orderkey" in result.columns
        assert len(result) >= 1

    def test_filter_string_selective(self):
        result = self._execute("filter_string_selective")
        assert "c_custkey" in result.columns

    def test_filter_string_non_selective(self):
        result = self._execute("filter_string_non_selective")
        assert "count" in result.columns
        assert result["count"].iloc[0] > 0


class TestExpressionString:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_polars_context()
        _register_all_polars(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    def _execute(self, query_id: str) -> pd.DataFrame:
        query = self.registry.get(query_id)
        return _to_pandas(query.expression_impl(self.ctx))

    def test_string_like(self):
        result = self._execute("string_like")
        assert "p_partkey" in result.columns
        assert "p_name" in result.columns
        assert len(result) == 2

    def test_string_starts_with(self):
        result = self._execute("string_starts_with")
        assert "p_partkey" in result.columns
        assert len(result) >= 1

    def test_string_ends_with(self):
        result = self._execute("string_ends_with")
        assert "p_partkey" in result.columns
        assert len(result) >= 1

    def test_string_concat(self):
        result = self._execute("string_concat")
        assert "customer_info" in result.columns
        assert len(result) > 0
        first_value = result["customer_info"].iloc[0]
        assert " - " in str(first_value)

    def test_string_substring(self):
        result = self._execute("string_substring")
        assert "country_code" in result.columns
        for code in result["country_code"]:
            assert len(str(code)) == 3


class TestExpressionOrderBy:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_polars_context()
        _register_all_polars(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    def _execute(self, query_id: str) -> pd.DataFrame:
        query = self.registry.get(query_id)
        return _to_pandas(query.expression_impl(self.ctx))

    def test_orderby_simple(self):
        result = self._execute("orderby_simple")
        assert "o_orderdate" in result.columns
        dates = result["o_orderdate"].tolist()
        assert dates == sorted(dates)

    def test_orderby_desc(self):
        result = self._execute("orderby_desc")
        assert "o_totalprice" in result.columns
        prices = result["o_totalprice"].tolist()
        assert prices == sorted(prices, reverse=True)

    def test_topn(self):
        result = self._execute("topn")
        assert "l_extendedprice" in result.columns
        assert len(result) == 10
        prices = result["l_extendedprice"].tolist()
        assert prices == sorted(prices, reverse=True)

    def test_limit(self):
        result = self._execute("limit")
        assert "l_orderkey" in result.columns
        assert len(result) == 10

    def test_orderby_shortstrings_distinct_precedes_sort(self):
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        class TrackingFrame:
            def __init__(self):
                self.calls = []

            def select(self, *cols):
                self.calls.append(("select", cols))
                return self

            def unique(self):
                self.calls.append(("unique",))
                return self

            def sort(self, by, descending=False):
                self.calls.append(("sort", by, descending))
                return self

            def limit(self, n):
                self.calls.append(("limit", n))
                return self

        class TrackingContext:
            def __init__(self):
                self.frame = TrackingFrame()

            def get_table(self, name):
                assert name == "lineitem"
                return self.frame

        ctx = TrackingContext()
        query = get_dataframe_queries().get("orderby_shortstrings")

        assert query.expression_impl(ctx) is ctx.frame
        assert ctx.frame.calls == [
            ("select", ("l_returnflag", "l_linestatus")),
            ("unique",),
            ("sort", ["l_returnflag", "l_linestatus"], False),
        ]


class TestExpressionWindow:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_polars_context()
        _register_all_polars(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    def _execute(self, query_id: str) -> pd.DataFrame:
        query = self.registry.get(query_id)
        return _to_pandas(query.expression_impl(self.ctx))

    def test_window_row_number(self):
        result = self._execute("window_row_number")
        assert "order_rank" in result.columns
        assert result["order_rank"].min() == 1
        for _, group in result.groupby("o_custkey"):
            ranks = sorted(int(rank) for rank in group["order_rank"].tolist())
            assert ranks == list(range(1, len(ranks) + 1))

    def test_window_rank(self):
        result = self._execute("window_rank")
        assert "qty_rank" in result.columns
        assert result["qty_rank"].max() <= 5

    def test_window_sum(self):
        result = self._execute("window_sum")
        assert "order_total" in result.columns
        assert "l_extendedprice" in result.columns
        assert (result["order_total"] >= result["l_extendedprice"] - 0.01).all()


class TestExpressionJoin:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_polars_context()
        _register_all_polars(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    def _execute(self, query_id: str) -> pd.DataFrame:
        query = self.registry.get(query_id)
        return _to_pandas(query.expression_impl(self.ctx))

    def test_broadcast_join_two_tables(self):
        result = self._execute("broadcast_join_two_tables")
        assert "supplier_count" in result.columns
        assert result["supplier_count"].iloc[0] == 5

    def test_broadcast_join_three_tables(self):
        result = self._execute("broadcast_join_three_tables")
        assert "supplier_count" in result.columns
        assert len(result) > 0

    def test_shuffle_join(self):
        result = self._execute("shuffle_join")
        assert "o_orderkey" in result.columns
        assert "total_qty" in result.columns
        assert len(result) > 0

    def test_empty_build_join(self):
        result = self._execute("empty_build_join")
        assert "l_orderkey" in result.columns
        assert len(result) == 10

    def test_shuffle_inner_join_groupby(self):
        result = self._execute("shuffle_inner_join_one_to_many_string_with_groupby")
        assert "c_mktsegment" in result.columns
        assert "order_count" in result.columns
        assert len(result) > 0


class TestExpressionDecimalArithmetic:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_polars_context()
        _register_all_polars(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    def _execute(self, query_id: str) -> pd.DataFrame:
        query = self.registry.get(query_id)
        return _to_pandas(query.expression_impl(self.ctx))

    def test_decimal_arithmetic(self):
        result = self._execute("decimal_arithmetic")
        assert "final_price" in result.columns
        assert "unit_price" in result.columns
        assert (result["final_price"] > 0).all()
        assert (result["unit_price"] > 0).all()


CORE_EXPRESSION_QUERIES = [
    "aggregation_simple",
    "aggregation_distinct",
    "aggregation_groupby_small",
    "aggregation_materialize",
    "aggregation_selective",
    "count_star",
    "filter_selective",
    "filter_non_selective",
    "filter_bigint_in_list_selective",
    "filter_string_selective",
    "string_like",
    "string_starts_with",
    "string_ends_with",
    "string_concat",
    "string_substring",
    "orderby_simple",
    "orderby_desc",
    "orderby_multi",
    "topn",
    "limit",
    "window_row_number",
    "window_rank",
    "window_sum",
    "broadcast_join_two_tables",
    "broadcast_join_three_tables",
    "shuffle_join",
    "empty_build_join",
    "decimal_arithmetic",
    "groupby_bigint_lowndv",
    "groupby_bigint_highndv",
    "predicate_ordering_aggregation",
    "aggregation_materialize_subquery",
    "aggregation_partition",
    "filter_bigint_selective",
    "filter_bigint_non_selective",
    "filter_bigint_in_list_selective",
    "filter_decimal_selective",
    "filter_decimal_non_selective",
    "filter_string_non_selective",
    "filter_in_predicate_selective",
    "groupby_bigint_pk",
    "groupby_decimal_highndv",
    "groupby_decimal_lowndv",
    "orderby_all",
    "orderby_bigint",
    "orderby_bigint_expression",
    "orderby_multicol",
    "orderby_shortstrings",
    "window_running_sum",
    "window_growing_frame",
    "window_lead_lag_same_frame",
    "window_moving_frame",
    "window_multiple_orderings",
    "window_unbounded_frame",
    "broadcast_join_four_tables",
    "shuffle_inner_join_one_to_many_string_with_groupby",
    "shuffle_left_join_one_to_many_string_with_groupby",
    "shuffle_full_join_one_to_many_string_with_groupby",
    "shuffle_inner_join_union_all_with_groupby",
    "string_equal_predicate",
    "string_equal_predicate_lower",
    "string_in_predicate",
    "string_like_predicate_center",
    "string_like_predicate_end",
    "string_like_predicate_start",
    "predicate_ordering_aggregation_groupby",
    "predicate_ordering_costs",
    "aggregation_distinct_groupby",
    "aggregation_groupby_large",
    "approx_count_distinct_simple",
    "approx_count_distinct_groupby",
    "any_value_simple",
    "any_value_with_filter",
    "array_agg_distinct",
    "array_agg_simple",
    "array_contains",
    "array_distinct",
    "array_length",
    "array_min_max",
    "array_slice",
    "array_sort",
    "array_unnest",
    "exchange_broadcast",
    "exchange_merge",
    "exchange_shuffle",
    "filter_decimal_in_list_selective",
    "filter_string_like",
    "fulltext_boolean_search",
    "fulltext_phrase_search",
    "fulltext_simple_search",
    "groupby_all_simple",
    "approx_quantile_groupby",
    "intrinsic_to_date",
    "json_aggregates",
    "json_extract_nested",
    "json_extract_simple",
    "limit_ordered",
    "list_filter",
    "list_reduce",
    "list_transform",
    "long_predicate",
    "max_by_complex",
    "max_by_simple",
    "max_by_with_ties",
    "min_by_complex",
    "min_by_simple",
    "min_by_with_ties",
    "min_max_runtime_filter",
    "olap_cube_analysis",
    "olap_rollup_analysis",
    "optimizer_aggregate_pushdown",
    "optimizer_column_pruning",
    "optimizer_common_subexpression",
    "optimizer_constant_folding",
    "optimizer_distinct_elimination",
    "optimizer_groupjoin",
    "optimizer_join_reordering",
    "optimizer_limit_pushdown",
    "optimizer_predicate_pushdown",
    "optimizer_runtime_filter",
    "optimizer_union_optimization",
    "orderby_all_desc",
    "orderby_all_simple",
    "orderby_decimal16",
    "pivot_basic",
    "predicate_ordering_subquery",
    "qualify_dense_rank",
    "qualify_lag_lead",
    "qualify_percentile",
    "qualify_row_number",
    "shuffle_1mb_rows",
    "statistical_correlation",
    "statistical_percentiles",
    "statistical_variance_stddev",
    "string_ilike_predicate_end",
    "string_ilike_predicate_multi",
    "string_ilike_predicate_start",
    "string_like_predicate_center_insensitive",
    "string_like_predicate_multi",
    "struct_access",
    "struct_construction",
    "timeseries_trend_analysis",
    "topn_aggregate_2columns",
    "topn_ordered_allcols",
    "unpivot_basic",
]

EXPRESSION_ONLY_QUERIES = [
    "array_of_struct",
    "asof_join_basic",
    "groupby_all_complex",
    "qualify_cume_dist",
    "qualify_ntile",
]

PANDAS_ONLY_QUERIES = [
    "map_access",
    "map_construction",
    "map_keys_values",
]


class TestParametrizedExpressionExecution:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_polars_context()
        _register_all_polars(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    @pytest.mark.parametrize("query_id", CORE_EXPRESSION_QUERIES)
    def test_expression_query_executes(self, query_id: str):
        query = self.registry.get(query_id)
        result = _to_pandas(query.expression_impl(self.ctx))
        assert isinstance(result, pd.DataFrame)
        assert len(result.columns) > 0


class TestParametrizedPandasExecution:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_pandas_context()
        _register_all_pandas(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    @pytest.mark.parametrize("query_id", CORE_EXPRESSION_QUERIES)
    def test_pandas_query_executes(self, query_id: str):
        query = self.registry.get(query_id)
        result = _to_pandas(query.pandas_impl(self.ctx))
        assert isinstance(result, pd.DataFrame)
        assert len(result.columns) > 0


class TestExpressionOnlyAdvancedExecution:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_polars_context()
        _register_all_polars(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    @pytest.mark.parametrize("query_id", EXPRESSION_ONLY_QUERIES)
    def test_expression_only_query_executes(self, query_id: str):
        query = self.registry.get(query_id)
        result = _to_pandas(query.expression_impl(self.ctx))
        assert isinstance(result, pd.DataFrame)
        assert len(result.columns) > 0


class TestPandasOnlyAdvancedExecution:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_pandas_context()
        _register_all_pandas(self.ctx)
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    @pytest.mark.parametrize("query_id", PANDAS_ONLY_QUERIES)
    def test_pandas_only_query_executes(self, query_id: str):
        query = self.registry.get(query_id)
        result = _to_pandas(query.pandas_impl(self.ctx))
        assert isinstance(result, pd.DataFrame)
        assert len(result.columns) > 0


CROSS_FAMILY_QUERIES = [
    "aggregation_simple",
    "aggregation_groupby_small",
    "count_star",
    "filter_non_selective",
    "string_like",
    "broadcast_join_two_tables",
    "groupby_bigint_lowndv",
    "optimizer_groupjoin",
]


class TestCrossFamilyComparison:
    @pytest.fixture(autouse=True)
    def setup_contexts(self):
        self.expr_ctx = _create_polars_context()
        _register_all_polars(self.expr_ctx)

        self.pandas_ctx = _create_pandas_context()
        _register_all_pandas(self.pandas_ctx)

        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()

    @pytest.mark.parametrize("query_id", CROSS_FAMILY_QUERIES)
    def test_expression_vs_pandas(self, query_id: str):
        query = self.registry.get(query_id)

        expr_result = _to_pandas(query.expression_impl(self.expr_ctx))
        pandas_result = _to_pandas(query.pandas_impl(self.pandas_ctx))

        _compare_results(expr_result, pandas_result)


class TestTransactionCapabilitiesSupportsOperation:
    def test_delta_lake_supports_all_operations(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DELTA_LAKE_TRANSACTION_CAPABILITIES,
            TransactionOperationType,
        )

        caps = DELTA_LAKE_TRANSACTION_CAPABILITIES
        for op in TransactionOperationType:
            assert caps.supports_operation(op) is True, f"Delta Lake should support {op}"

    def test_polars_supports_no_operations(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            POLARS_TRANSACTION_CAPABILITIES,
            TransactionOperationType,
        )

        caps = POLARS_TRANSACTION_CAPABILITIES
        for op in TransactionOperationType:
            assert caps.supports_operation(op) is False, f"Polars should not support {op}"

    def test_pandas_supports_no_operations(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            PANDAS_TRANSACTION_CAPABILITIES,
            TransactionOperationType,
        )

        caps = PANDAS_TRANSACTION_CAPABILITIES
        for op in TransactionOperationType:
            assert caps.supports_operation(op) is False, f"Pandas should not support {op}"

    def test_get_unsupported_operations(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            POLARS_TRANSACTION_CAPABILITIES,
            TransactionOperationType,
        )

        caps = POLARS_TRANSACTION_CAPABILITIES
        unsupported = caps.get_unsupported_operations()
        assert len(unsupported) == len(TransactionOperationType)


class TestTransactionResultFailure:
    def test_failure_result_fields(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionResult,
            TransactionOperationType,
        )

        result = DataFrameTransactionResult.failure(
            TransactionOperationType.ATOMIC_INSERT,
            "test error message",
        )
        assert result.success is False
        assert result.error_message == "test error message"
        assert result.operation_type == TransactionOperationType.ATOMIC_INSERT
        assert result.rows_affected == 0
        assert result.validation_passed is False
        assert result.duration_ms == 0.0

    def test_failure_with_start_time(self):
        import time

        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionResult,
            TransactionOperationType,
        )

        start = time.time() - 0.1
        result = DataFrameTransactionResult.failure(
            TransactionOperationType.ATOMIC_DELETE,
            "timed failure",
            start_time=start,
        )
        assert result.duration_ms > 0
        assert result.start_time == start


class TestTransactionManagerPolars:
    def test_polars_manager_no_transactions(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionOperationsManager,
        )

        manager = DataFrameTransactionOperationsManager("polars-df")
        assert manager.supports_transactions() is False

    def test_polars_unsupported_message(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionOperationsManager,
        )

        manager = DataFrameTransactionOperationsManager("polars-df")
        msg = manager.get_unsupported_message()
        assert "ACID" in msg or "transaction" in msg.lower()
        assert "Delta Lake" in msg or "pyspark" in msg.lower()

    def test_polars_atomic_insert_fails(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionOperationsManager,
        )

        manager = DataFrameTransactionOperationsManager("polars-df")
        result = manager.execute_atomic_insert(
            table_path="/tmp/nonexistent",
            dataframe=None,
        )
        assert result.success is False

    def test_polars_atomic_update_fails(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionOperationsManager,
        )

        manager = DataFrameTransactionOperationsManager("polars-df")
        result = manager.execute_atomic_update(
            table_path="/tmp/nonexistent",
            condition="id = 1",
            updates={"value": 42},
        )
        assert result.success is False

    def test_polars_atomic_delete_fails(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionOperationsManager,
        )

        manager = DataFrameTransactionOperationsManager("polars-df")
        result = manager.execute_atomic_delete(
            table_path="/tmp/nonexistent",
            condition="id = 1",
        )
        assert result.success is False

    def test_polars_atomic_merge_fails(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionOperationsManager,
        )

        manager = DataFrameTransactionOperationsManager("polars-df")
        result = manager.execute_atomic_merge(
            table_path="/tmp/nonexistent",
            source_dataframe=None,
            merge_condition="t.id = s.id",
        )
        assert result.success is False


class TestTransactionManagerPandas:
    def test_pandas_manager_no_transactions(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionOperationsManager,
        )

        manager = DataFrameTransactionOperationsManager("pandas-df")
        assert manager.supports_transactions() is False

    def test_pandas_all_operations_unsupported(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionOperationsManager,
            TransactionOperationType,
        )

        manager = DataFrameTransactionOperationsManager("pandas-df")
        for op in TransactionOperationType:
            assert manager.supports_operation(op) is False


class TestTransactionValidation:
    def test_pyspark_is_valid(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            validate_transaction_primitives_platform,
        )

        is_valid, msg = validate_transaction_primitives_platform("pyspark-df")
        assert is_valid is True
        assert msg == ""

    def test_delta_is_valid(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            validate_transaction_primitives_platform,
        )

        is_valid, msg = validate_transaction_primitives_platform("delta-lake")
        assert is_valid is True

    def test_polars_is_invalid(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            validate_transaction_primitives_platform,
        )

        is_valid, msg = validate_transaction_primitives_platform("polars-df")
        assert is_valid is False
        assert "transaction" in msg.lower() or "ACID" in msg

    def test_duckdb_is_invalid(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            validate_transaction_primitives_platform,
        )

        is_valid, msg = validate_transaction_primitives_platform("duckdb")
        assert is_valid is False

    def test_unknown_platform_is_valid(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            validate_transaction_primitives_platform,
        )

        is_valid, msg = validate_transaction_primitives_platform("my-custom-platform")
        assert is_valid is True


class TestTransactionManagerFactory:
    def test_polars_returns_manager(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            get_dataframe_transaction_manager,
        )

        manager = get_dataframe_transaction_manager("polars-df")
        assert manager is not None

    def test_pandas_returns_manager(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            get_dataframe_transaction_manager,
        )

        manager = get_dataframe_transaction_manager("pandas-df")
        assert manager is not None

    def test_non_df_platform_returns_none(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            get_dataframe_transaction_manager,
        )

        manager = get_dataframe_transaction_manager("clickhouse")
        assert manager is None

    def test_pyspark_returns_manager(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            get_dataframe_transaction_manager,
        )

        manager = get_dataframe_transaction_manager("pyspark-df")
        assert manager is not None


class TestTransactionManagerPathValidation:
    def test_path_traversal_rejected(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionOperationsManager,
        )

        manager = DataFrameTransactionOperationsManager("delta-lake")
        is_valid, msg = manager.validate_table_format("../../etc/passwd")
        assert is_valid is False
        assert "traversal" in msg.lower() or "does not exist" in msg.lower()

    def test_nonexistent_path_rejected(self):
        from benchbox.core.transaction_primitives.dataframe_operations import (
            DataFrameTransactionOperationsManager,
        )

        manager = DataFrameTransactionOperationsManager("delta-lake")
        is_valid, msg = manager.validate_table_format("/tmp/definitely_nonexistent_table_12345")
        assert is_valid is False
        assert "does not exist" in msg


try:
    import pyarrow as pa

    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    DATAFUSION_AVAILABLE = True
except ImportError:
    DATAFUSION_AVAILABLE = False
    pa = None
    DataFusionDataFrameAdapter = None


def _create_datafusion_context():
    adapter = DataFusionDataFrameAdapter()
    ctx = adapter.create_context()

    nation = pa.table(
        {
            "n_nationkey": pa.array([0, 1, 2, 3, 4], type=pa.int64()),
            "n_name": pa.array(["ALGERIA", "ARGENTINA", "BRAZIL", "CANADA", "EGYPT"]),
            "n_regionkey": pa.array([0, 1, 1, 1, 0], type=pa.int64()),
            "n_comment": pa.array(["special", "al foxes", "ven packages", "eas hang", "y above"]),
        }
    )
    region = pa.table(
        {
            "r_regionkey": pa.array([0, 1], type=pa.int64()),
            "r_name": pa.array(["AFRICA", "AMERICA"]),
            "r_comment": pa.array(["lar deposits", "hs use ironic"]),
        }
    )
    customer = pa.table(
        {
            "c_custkey": pa.array([1, 2, 3, 4, 5], type=pa.int64()),
            "c_name": pa.array(
                [
                    "Customer#000000001",
                    "Customer#000000002",
                    "Customer#000000003",
                    "Customer#000000004",
                    "Customer#000000005",
                ]
            ),
            "c_address": pa.array(["addr1", "addr2", "addr3", "addr4", "addr5"]),
            "c_nationkey": pa.array([0, 1, 1, 2, 0], type=pa.int64()),
            "c_phone": pa.array(
                ["25-989-741-2988", "23-768-687-3665", "11-719-748-3364", "14-128-190-5944", "13-750-942-6364"]
            ),
            "c_acctbal": pa.array([711.56, 121.65, 7498.12, 2866.83, 794.47], type=pa.float64()),
            "c_mktsegment": pa.array(["BUILDING", "AUTOMOBILE", "AUTOMOBILE", "MACHINERY", "HOUSEHOLD"]),
            "c_comment": pa.array(["comment1", "comment2", "comment3", "comment4", "comment5"]),
        }
    )
    orders = pa.table(
        {
            "o_orderkey": pa.array([1, 2, 3, 4, 5], type=pa.int64()),
            "o_custkey": pa.array([1, 1, 2, 3, 4], type=pa.int64()),
            "o_orderstatus": pa.array(["O", "F", "O", "F", "O"]),
            "o_totalprice": pa.array([1234.56, 2345.67, 3456.78, 4567.89, 5678.90], type=pa.float64()),
            "o_orderdate": pa.array(
                [date(1996, 1, 2), date(1996, 12, 1), date(1993, 10, 14), date(1995, 10, 11), date(1994, 7, 30)],
                type=pa.date32(),
            ),
            "o_orderpriority": pa.array(["5-LOW", "1-URGENT", "2-HIGH", "3-MEDIUM", "5-LOW"]),
            "o_clerk": pa.array(
                ["Clerk#000000951", "Clerk#000000880", "Clerk#000000955", "Clerk#000000124", "Clerk#000000925"]
            ),
            "o_shippriority": pa.array([0, 0, 0, 0, 0], type=pa.int64()),
            "o_comment": pa.array(["nstructions sleep", "tory, quickly", "y pending", "ts above the", "quickly"]),
        }
    )
    lineitem = pa.table(
        {
            "l_orderkey": pa.array([1, 1, 2, 3, 4, 5], type=pa.int64()),
            "l_partkey": pa.array([155, 67, 23, 2, 24, 15], type=pa.int64()),
            "l_suppkey": pa.array([4, 8, 4, 3, 5, 1], type=pa.int64()),
            "l_linenumber": pa.array([1, 2, 1, 1, 1, 1], type=pa.int64()),
            "l_quantity": pa.array([17.0, 36.0, 38.0, 45.0, 49.0, 15.0], type=pa.float64()),
            "l_extendedprice": pa.array(
                [24386.67, 56529.96, 60766.64, 54058.05, 46076.28, 26438.10], type=pa.float64()
            ),
            "l_discount": pa.array([0.04, 0.09, 0.00, 0.06, 0.10, 0.02], type=pa.float64()),
            "l_tax": pa.array([0.02, 0.06, 0.05, 0.00, 0.04, 0.04], type=pa.float64()),
            "l_returnflag": pa.array(["N", "N", "N", "A", "N", "R"]),
            "l_linestatus": pa.array(["O", "O", "O", "F", "O", "F"]),
            "l_shipdate": pa.array(
                [
                    date(1996, 3, 13),
                    date(1997, 1, 28),
                    date(1997, 4, 12),
                    date(1994, 2, 2),
                    date(1996, 1, 29),
                    date(1996, 4, 12),
                ],
                type=pa.date32(),
            ),
            "l_commitdate": pa.array(
                [
                    date(1996, 2, 12),
                    date(1997, 1, 14),
                    date(1997, 1, 21),
                    date(1994, 1, 23),
                    date(1995, 12, 14),
                    date(1996, 2, 28),
                ],
                type=pa.date32(),
            ),
            "l_receiptdate": pa.array(
                [
                    date(1996, 3, 22),
                    date(1998, 1, 6),
                    date(1997, 1, 29),
                    date(1994, 2, 27),
                    date(1996, 2, 12),
                    date(1996, 4, 20),
                ],
                type=pa.date32(),
            ),
            "l_shipinstruct": pa.array(
                ["DELIVER IN PERSON", "TAKE BACK RETURN", "TAKE BACK RETURN", "NONE", "NONE", "DELIVER IN PERSON"]
            ),
            "l_shipmode": pa.array(["TRUCK", "MAIL", "REG AIR", "AIR", "FOB", "MAIL"]),
            "l_comment": pa.array(["egular c", "ly final", "pending", "suggest", "pending", "forges"]),
        }
    )
    part = pa.table(
        {
            "p_partkey": pa.array([1, 2, 3, 15, 23, 24, 67, 155], type=pa.int64()),
            "p_name": pa.array(
                [
                    "goldenrod lace spring peru powder",
                    "blush thistle blue yellow saddle",
                    "dark green antique puff wheat",
                    "midnight rose chocolate gainsboro cornsilk",
                    "ghost floral violet white steel",
                    "cornflower chocolate smoke green pink",
                    "rosy burnished yellow papaya midnight",
                    "violet burnished rose cream blue",
                ],
                type=pa.string(),
            ),
            "p_mfgr": pa.array(
                [
                    "Manufacturer#1",
                    "Manufacturer#1",
                    "Manufacturer#4",
                    "Manufacturer#3",
                    "Manufacturer#4",
                    "Manufacturer#3",
                    "Manufacturer#2",
                    "Manufacturer#5",
                ],
                type=pa.string(),
            ),
            "p_brand": pa.array(
                ["Brand#13", "Brand#13", "Brand#42", "Brand#31", "Brand#45", "Brand#34", "Brand#24", "Brand#55"],
                type=pa.string(),
            ),
            "p_type": pa.array(
                [
                    "LARGE BRUSHED BRASS",
                    "LARGE BRUSHED COPPER",
                    "MEDIUM ANODIZED NICKEL",
                    "LARGE POLISHED BRASS",
                    "STANDARD POLISHED COPPER",
                    "SMALL PLATED COPPER",
                    "ECONOMY BURNISHED NICKEL",
                    "LARGE BURNISHED TIN",
                ],
                type=pa.string(),
            ),
            "p_size": pa.array([7, 1, 21, 45, 14, 3, 6, 49], type=pa.int64()),
            "p_container": pa.array(
                ["JUMBO PKG", "LG BAG", "MED BAG", "SM PKG", "MED BOX", "WRAP CASE", "MED BOX", "LG DRUM"],
                type=pa.string(),
            ),
            "p_retailprice": pa.array([901.0, 902.0, 903.0, 915.0, 923.0, 924.0, 967.0, 1155.0], type=pa.float64()),
            "p_comment": pa.array(
                ["ireful", "ven accounts", "even requests", "unusual", "reg", "sly ex", "ously", "final dep"],
                type=pa.string(),
            ),
        }
    )
    supplier = pa.table(
        {
            "s_suppkey": pa.array([1, 2, 3, 4, 5, 8], type=pa.int64()),
            "s_name": pa.array(
                [
                    "Supplier#000000001",
                    "Supplier#000000002",
                    "Supplier#000000003",
                    "Supplier#000000004",
                    "Supplier#000000005",
                    "Supplier#000000008",
                ],
                type=pa.string(),
            ),
            "s_address": pa.array(
                [
                    "N kD4on9OM Ipw3,gf0JBoQDd7tgrzrddZ",
                    "89eJ5ksX3ImxJQBvxObC,",
                    "q1,G3Pj6OjIuUYfUoH18BFTKP5e",
                    "Bk7ah4CK8SYQTepEmvMkkgMwg",
                    "Gcdm2rJRzl5yvZxH,2L",
                    "9mwz4Cq2HHXuGDVnS0K",
                ],
                type=pa.string(),
            ),
            "s_nationkey": pa.array([17, 5, 1, 20, 11, 17], type=pa.int64()),
            "s_phone": pa.array(
                [
                    "27-918-335-1736",
                    "15-679-861-2259",
                    "11-383-516-1199",
                    "30-114-968-4951",
                    "21-251-986-0071",
                    "27-817-354-8847",
                ],
                type=pa.string(),
            ),
            "s_acctbal": pa.array([5755.94, 4032.68, 4192.40, 5702.99, 4540.30, 1865.43], type=pa.float64()),
            "s_comment": pa.array(
                [
                    "each slyly above the careful",
                    "requests are carefully",
                    "deposits wake slyly pending",
                    "try. quickly regular",
                    "notornis are bold",
                    "quickly pending ideas",
                ],
                type=pa.string(),
            ),
        }
    )
    partsupp = pa.table(
        {
            "ps_partkey": pa.array([1, 1, 2, 3, 15, 23], type=pa.int64()),
            "ps_suppkey": pa.array([2, 4, 3, 2, 8, 5], type=pa.int64()),
            "ps_availqty": pa.array([3325, 8076, 8895, 4946, 2167, 7817], type=pa.int64()),
            "ps_supplycost": pa.array([771.64, 993.49, 337.09, 357.84, 771.64, 993.49], type=pa.float64()),
            "ps_comment": pa.array(
                [
                    "ven ideas. quickly",
                    "unusual accounts. eve",
                    "blithely bold accounts",
                    "slyly daring ideas",
                    "nstructions",
                    "blithely",
                ],
                type=pa.string(),
            ),
        }
    )

    for tname, tdata in [
        ("nation", nation),
        ("region", region),
        ("customer", customer),
        ("orders", orders),
        ("lineitem", lineitem),
        ("part", part),
        ("supplier", supplier),
        ("partsupp", partsupp),
    ]:
        adapter.session_ctx.register_record_batches(tname, [tdata.to_batches()])
        ctx.register_table(tname, adapter.session_ctx.table(tname))

    return ctx


DATAFUSION_QUERY_SUBSET = [
    "aggregation_simple",
    "aggregation_distinct",
    "aggregation_groupby_small",
    "count_star",
    "filter_selective",
    "filter_non_selective",
    "orderby_simple",
    "orderby_desc",
    "topn",
    "limit",
    "window_row_number",
    "window_rank",
    "window_sum",
    "broadcast_join_two_tables",
    "shuffle_join",
    "decimal_arithmetic",
]


@pytest.mark.skipif(not DATAFUSION_AVAILABLE, reason="datafusion or pyarrow not installed")
class TestDataFusionExpressionPathCoverage:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.ctx = _create_datafusion_context()
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        self.registry = get_dataframe_queries()
        from benchbox.core.read_primitives.dataframe_queries import get_skip_for_datafusion

        self.skip_ids = set(get_skip_for_datafusion())

    @pytest.mark.parametrize("query_id", DATAFUSION_QUERY_SUBSET)
    def test_datafusion_expression_query_executes(self, query_id: str):
        if query_id in self.skip_ids:
            pytest.skip(f"{query_id} is in DataFusion skip list")
        query = self.registry.get(query_id)
        result = query.expression_impl(self.ctx)
        if hasattr(result, "collect"):
            df = result.collect()
        else:
            df = result
        assert df is not None
