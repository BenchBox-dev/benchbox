from __future__ import annotations

from datetime import date

import pytest

try:
    import polars as pl
    from polars.testing import assert_frame_equal

    from benchbox.platforms.dataframe.polars_df import POLARS_AVAILABLE, PolarsDataFrameAdapter
except ImportError:
    POLARS_AVAILABLE = False
    pl = None
    assert_frame_equal = None
    PolarsDataFrameAdapter = None

from benchbox.core.benchmark_registry import BENCHMARK_METADATA
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.dataframe_queries import get_dataframe_queries, get_query, list_query_ids

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

Q1_COLUMNS = [
    "l_returnflag",
    "l_linestatus",
    "sum_qty",
    "sum_base_price",
    "sum_disc_price",
    "sum_charge",
    "avg_qty",
    "avg_price",
    "avg_disc",
    "count_order",
]


class TestTPCHavocDataFrameRegistry:
    def test_all_220_variants_registered(self) -> None:
        registry = get_dataframe_queries()
        assert len(registry) == 220

    def test_registry_returns_singleton(self) -> None:
        assert get_dataframe_queries() is get_dataframe_queries()

    @pytest.mark.parametrize("query_number", range(1, 23))
    def test_each_query_has_10_variants(self, query_number: int) -> None:
        ids = list_query_ids()
        expected = {f"Q{query_number}v{variant}" for variant in range(1, 11)}
        assert expected.issubset(ids)

    def test_all_variants_have_both_families_and_categories(self) -> None:
        for query in get_dataframe_queries().get_all_queries():
            assert isinstance(query, DataFrameQuery)
            assert query.has_expression_impl(), f"{query.query_id} missing expression_impl"
            assert query.has_pandas_impl(), f"{query.query_id} missing pandas_impl"
            assert query.categories, f"{query.query_id} missing categories"

    def test_query_lookup(self) -> None:
        query = get_query("Q1v1")
        assert query.query_id == "Q1v1"
        assert QueryCategory.AGGREGATE in query.categories

    def test_benchmark_registry_supports_dataframe(self) -> None:
        assert BENCHMARK_METADATA["tpchavoc"]["supports_dataframe"] is True

    def test_benchmark_class_exposes_dataframe_registry(self) -> None:
        benchmark = TPCHavocBenchmark(scale_factor=0.01)
        assert benchmark.supports_dataframe_mode() is True
        assert len(benchmark.get_dataframe_queries()) == 220


@pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
class TestQ1Equivalence:
    @pytest.fixture
    def q1_context(self):
        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table(
            "lineitem",
            pl.DataFrame(
                {
                    "l_orderkey": [1, 2, 3, 4],
                    "l_quantity": [5.0, 10.0, 7.0, 9.0],
                    "l_extendedprice": [100.0, 200.0, 150.0, 300.0],
                    "l_discount": [0.05, 0.10, 0.00, 0.02],
                    "l_tax": [0.02, 0.05, 0.03, 0.01],
                    "l_returnflag": ["A", "A", "N", "R"],
                    "l_linestatus": ["F", "F", "O", "F"],
                    "l_shipdate": [
                        date(1994, 1, 1),
                        date(1995, 1, 1),
                        date(1996, 1, 1),
                        date(1997, 1, 1),
                    ],
                }
            ).lazy(),
        )
        return ctx

    def test_q1_equivalence_expression_variants(self, q1_context) -> None:
        base = _collect_q1(get_query("Q1v1").expression_impl(q1_context))
        for variant_id in range(2, 11):
            query = get_query(f"Q1v{variant_id}")
            result = _collect_q1(query.expression_impl(q1_context))
            assert_frame_equal(result, base)


def _collect_q1(result) -> pl.DataFrame:
    if hasattr(result, "native"):
        result = result.native
    if hasattr(result, "collect"):
        result = result.collect()
    return result.select(Q1_COLUMNS).sort(["l_returnflag", "l_linestatus"])


def _collect_frame(result):
    if hasattr(result, "native"):
        result = result.native
    if hasattr(result, "collect"):
        result = result.collect()
    return result


def _collect_pandas(result):
    import pandas as pd

    if hasattr(result, "native"):
        result = result.native
    if isinstance(result, pd.DataFrame):
        return result
    return pd.DataFrame(result)


@pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
class TestQ16Q18VariantMetadata:
    @pytest.mark.parametrize(
        ("variant_id", "expected_row_count"),
        [(f"Q16v{v}", None) for v in range(1, 11)]
        + [(f"Q17v{v}", 1) for v in range(1, 11)]
        + [(f"Q18v{v}", 100) for v in range(1, 11)],
    )
    def test_variants_preserve_canonical_metadata(self, variant_id: str, expected_row_count) -> None:
        from benchbox.core.tpch.dataframe_queries import get_query as get_tpch_query
        from benchbox.core.tpchavoc.dataframe_queries import get_query as get_havoc_query

        query_number = variant_id.split("v")[0]
        canonical = get_tpch_query(query_number)
        variant = get_havoc_query(variant_id)
        assert variant.to_dict()["expected_row_count"] == expected_row_count
        assert variant.to_dict()["expected_row_count"] == canonical.expected_row_count
        assert variant.scale_factor_dependent == canonical.scale_factor_dependent
        assert variant.timeout_seconds == canonical.timeout_seconds
        assert variant.skip_platforms == canonical.skip_platforms


@pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
class TestQ16Q18StructuralShapes:
    @pytest.fixture
    def q17_context(self):
        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table(
            "part",
            pl.DataFrame(
                {
                    "p_partkey": [1, 2, 3, 4],
                    "p_brand": ["Brand#23", "Brand#23", "Brand#23", "Brand#23"],
                    "p_container": ["MED BOX", "MED BOX", "MED BOX", "MED BOX"],
                    "p_size": [10, 20, 30, 40],
                }
            ).lazy(),
        )
        ctx.register_table(
            "lineitem",
            pl.DataFrame(
                {
                    "l_partkey": [1, 1, 1, 2, 2, 3, 3, 4, 4],
                    "l_quantity": [1.0, 2.0, 30.0, 5.0, 5.0, 4.0, 6.0, 5.0, 5.0],
                    "l_extendedprice": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0],
                }
            ).lazy(),
        )
        return ctx

    @pytest.fixture
    def q18_context(self):
        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table(
            "customer",
            pl.DataFrame(
                {
                    "c_custkey": [1, 2, 3, 4],
                    "c_name": ["A", "B", "C", "D"],
                    "c_nationkey": [3, 7, 14, 20],
                }
            ).lazy(),
        )
        ctx.register_table(
            "orders",
            pl.DataFrame(
                {
                    "o_orderkey": [1, 2, 3, 4],
                    "o_custkey": [1, 2, 3, 4],
                    "o_orderdate": [date(1994, 1, 1)] * 4,
                    "o_totalprice": [100.0, 350.0, 700.0, 500.0],
                }
            ).lazy(),
        )
        ctx.register_table(
            "lineitem",
            pl.DataFrame(
                {
                    "l_orderkey": [1, 2, 2, 3, 3, 3, 4, 4],
                    "l_quantity": [100.0, 150.0, 200.0, 300.0, 200.0, 200.0, 160.0, 160.0],
                }
            ).lazy(),
        )
        return ctx

    @pytest.fixture
    def q16_context(self):
        pytest.importorskip("pandas")
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        ctx.register_table(
            "part",
            __import__("pandas").DataFrame(
                {
                    "p_partkey": [1, 2, 3],
                    "p_brand": ["B1", "B1", "Brand#45"],
                    "p_type": ["MEDIUM POLISHED X", "PROMO Y", "MEDIUM POLISHED X"],
                    "p_size": [49, 49, 49],
                }
            ),
        )
        ctx.register_table(
            "partsupp",
            __import__("pandas").DataFrame({"ps_partkey": [1, 1, 1, 2, 2], "ps_suppkey": [10, 10, 11, 10, 12]}),
        )
        ctx.register_table(
            "supplier",
            __import__("pandas").DataFrame(
                {"s_suppkey": [10, 11, 12], "s_comment": ["ok", "has Customer Complaints issue", "ok"]}
            ),
        )
        return ctx

    def test_q17v6_concats_two_nonempty_branches(self, q17_context) -> None:
        heights: list[list[int]] = []
        original_concat = q17_context.concat
        q17_context.concat = lambda dfs: (
            heights.append([_collect_frame(df).height for df in dfs]),
            original_concat(dfs),
        )[1]
        _collect_frame(get_query("Q17v6").expression_impl(q17_context))
        assert len(heights) == 1 and len(heights[0]) == 2, f"Q17v6 must concat exactly two branches, got {heights}"
        assert all(height > 0 for height in heights[0]), (
            f"Q17v6 size bands must both be non-empty, got row counts {heights[0]}"
        )

    def test_q17v10_filters_before_part_join(self, q17_context, monkeypatch) -> None:
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        joins: list[tuple[list[str], list[str]]] = []
        original_join = UnifiedLazyFrame.join
        original_filter = UnifiedLazyFrame.filter

        def _spy_join(self, other, *args, **kwargs):
            left_columns, right_columns = list(self.columns), list(other.columns)
            joins.append((left_columns, right_columns))
            if any(str(column).startswith("p_") for column in left_columns + right_columns):
                part_joined["seen"] = True
            return original_join(self, other, *args, **kwargs)

        filtered_lineitem_legs = {"count": 0}
        part_joined = {"seen": False}

        def _spy_filter(self, *args, **kwargs):
            if any(str(column).startswith("l_") for column in self.columns) and not part_joined["seen"]:
                filtered_lineitem_legs["count"] += 1
            return original_filter(self, *args, **kwargs)

        monkeypatch.setattr(UnifiedLazyFrame, "join", _spy_join)
        monkeypatch.setattr(UnifiedLazyFrame, "filter", _spy_filter)
        _collect_frame(get_query("Q17v10").expression_impl(q17_context))
        assert joins, "Q17v10 must perform joins"
        left, right = joins[0]
        assert any(column.startswith("l_") for column in left + right), (
            f"Q17v10 first join must be the lineitem threshold leg, got {joins[0]}"
        )
        assert not any(column.startswith("p_") for column in left + right), (
            f"Q17v10 must filter before touching part, got {joins[0]}"
        )
        assert filtered_lineitem_legs["count"] >= 1, "Q17v10 must filter the threshold leg before the part join"

    def test_q18v4_threshold_gate_skips_prune_for_small_thresholds(self, q18_context, monkeypatch) -> None:
        import benchbox.core.tpch.dataframe_queries as tpch_queries
        import benchbox.core.tpchavoc.dataframe_queries.q18 as q18_module

        monkeypatch.setattr(q18_module, "get_tpch_parameters", lambda _n: {"quantity_threshold": 30})
        monkeypatch.setattr(tpch_queries, "get_tpch_parameters", lambda _n: {"quantity_threshold": 30})
        base = _collect_frame(get_query("Q18v1").expression_impl(q18_context))
        result = _collect_frame(get_query("Q18v4").expression_impl(q18_context))
        assert result.height == 4, f"Q18v4 must keep all four orders at threshold 30, got {result.height}"
        assert_frame_equal(result, base)

    def test_q18v4_threshold_gate_skips_prune_for_small_thresholds_pandas(self, q18_context, monkeypatch) -> None:
        pytest.importorskip("pandas")
        from pandas.testing import assert_frame_equal as assert_pandas_equal

        import benchbox.core.tpch.dataframe_queries as tpch_queries
        import benchbox.core.tpchavoc.dataframe_queries.q18 as q18_module
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        monkeypatch.setattr(q18_module, "get_tpch_parameters", lambda _n: {"quantity_threshold": 30})
        monkeypatch.setattr(tpch_queries, "get_tpch_parameters", lambda _n: {"quantity_threshold": 30})
        pandas_ctx = PandasDataFrameAdapter().create_context()
        for table in ("customer", "orders", "lineitem"):
            frame = _collect_frame(q18_context.get_table(table))
            pandas_ctx.register_table(table, frame.to_pandas())
        base = _collect_pandas(get_query("Q18v1").pandas_impl(pandas_ctx)).reset_index(drop=True)
        result = _collect_pandas(get_query("Q18v4").pandas_impl(pandas_ctx)).reset_index(drop=True)
        assert len(result) == 4, f"Q18v4 pandas must keep all four orders at threshold 30, got {len(result)}"
        assert_pandas_equal(result, base, check_dtype=False)

    def test_q18v6_bands_are_disjoint_nonempty_and_cover_large_orders(self, q18_context) -> None:
        seen: list[list[int]] = []
        original_concat = q18_context.concat

        def _spy_concat(dfs):
            seen.append([_collect_frame(df).height for df in dfs])
            collected = [set(_collect_frame(df)["o_orderkey"].to_list()) for df in dfs]
            assert collected[0].isdisjoint(collected[1]), f"Q18v6 bands overlap: {collected}"
            assert collected[0] | collected[1] == {2, 3, 4}, f"Q18v6 bands must cover large orders: {collected}"
            return original_concat(dfs)

        q18_context.concat = _spy_concat
        result = _collect_frame(get_query("Q18v6").expression_impl(q18_context)).sort("o_orderkey")
        assert result.height == 3
        assert len(seen) == 1 and len(seen[0]) == 2, f"Q18v6 must concat two nation bands, got {seen}"
        assert all(height > 0 for height in seen[0]), (
            f"Q18v6 nation bands must both be non-empty, got row counts {seen[0]}"
        )

    def test_q16v9_differs_from_q16v2_plan(self, q16_context, monkeypatch) -> None:
        import pandas as pd

        import benchbox.core.tpchavoc.dataframe_queries.q16 as q16_module

        params = {"brand": "Brand#45", "type_prefix": "MEDIUM POLISHED", "sizes": [49]}
        monkeypatch.setattr(q16_module, "get_tpch_parameters", lambda _n: params)
        widths: dict[str, list[int]] = {"Q16v2": [], "Q16v9": []}
        current = {"variant": "Q16v2"}
        original_drop_duplicates = pd.DataFrame.drop_duplicates

        def _spy_drop_duplicates(self, *args, **kwargs):
            widths[current["variant"]].append(self.shape[1])
            return original_drop_duplicates(self, *args, **kwargs)

        monkeypatch.setattr(pd.DataFrame, "drop_duplicates", _spy_drop_duplicates)
        for variant_id in ("Q16v2", "Q16v9"):
            current["variant"] = variant_id
            get_query(variant_id).pandas_impl(q16_context)
        assert widths["Q16v2"] == [], f"Q16v2 must not dedup DataFrames, got {widths['Q16v2']}"
        assert widths["Q16v9"] == [4], f"Q16v9 must dedup the 4-column pair frame, got {widths['Q16v9']}"

    def test_q16v3_semi_joins_distinct_part_keys(self, q16_context, monkeypatch) -> None:
        import pandas as pd

        import benchbox.core.tpchavoc.dataframe_queries.q16 as q16_module

        params = {"brand": "Brand#45", "type_prefix": "MEDIUM POLISHED", "sizes": [49]}
        monkeypatch.setattr(q16_module, "get_tpch_parameters", lambda _n: params)
        widths: list[int] = []
        original_drop_duplicates = pd.DataFrame.drop_duplicates

        def _spy_drop_duplicates(self, *args, **kwargs):
            widths.append(self.shape[1])
            return original_drop_duplicates(self, *args, **kwargs)

        monkeypatch.setattr(pd.DataFrame, "drop_duplicates", _spy_drop_duplicates)
        get_query("Q16v3").pandas_impl(q16_context)
        assert widths == [1], f"Q16v3 must dedup only the 1-column key frame, got {widths}"

    def test_q16v7_filters_after_the_join(self, q16_context, monkeypatch) -> None:
        import pandas as pd

        import benchbox.core.tpchavoc.dataframe_queries.q16 as q16_module

        params = {"brand": "Brand#45", "type_prefix": "MEDIUM POLISHED", "sizes": [49]}
        monkeypatch.setattr(q16_module, "get_tpch_parameters", lambda _n: params)
        filtered_frames: list[list[str]] = []
        original_getitem = pd.DataFrame.__getitem__

        def _spy_getitem(self, key):
            try:
                import pandas as _pd

                if isinstance(key, _pd.Series) and key.dtype == bool:
                    filtered_frames.append(list(self.columns))
            except Exception:
                pass
            return original_getitem(self, key)

        monkeypatch.setattr(pd.DataFrame, "__getitem__", _spy_getitem)
        get_query("Q16v7").pandas_impl(q16_context)
        assert filtered_frames, "Q16v7 must apply boolean filters"
        assert any("ps_suppkey" in columns for columns in filtered_frames), (
            f"Q16v7 must filter joined rows carrying partsupp columns, saw {filtered_frames}"
        )

    def test_q17v7_left_joins_average_table(self, q17_context, monkeypatch) -> None:
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        hows: list[str] = []
        original_join = UnifiedLazyFrame.join

        def _spy_join(self, other, *args, **kwargs):
            hows.append(str(kwargs.get("how", "inner")))
            return original_join(self, other, *args, **kwargs)

        monkeypatch.setattr(UnifiedLazyFrame, "join", _spy_join)
        _collect_frame(get_query("Q17v7").expression_impl(q17_context))
        assert "left" in hows, f"Q17v7 must use a left join for the average table, got {hows}"

    def test_q17v8_commutes_division_before_sum(self, monkeypatch) -> None:
        pytest.importorskip("pandas")
        import pandas as pd

        import benchbox.core.tpchavoc.dataframe_queries.q17 as q17_module
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        ctx.register_table(
            "part",
            pd.DataFrame({"p_partkey": [1], "p_brand": ["Brand#23"], "p_container": ["MED BOX"]}),
        )
        ctx.register_table(
            "lineitem",
            pd.DataFrame(
                {
                    "l_partkey": [1, 1],
                    "l_quantity": [1.0, 10.0],
                    "l_extendedprice": [10.0, 20.0],
                }
            ),
        )
        calls = {"sum_helper": 0}
        original_sum = q17_module._q17_pandas_sum

        def _spy_sum(filtered):
            calls["sum_helper"] += 1
            return original_sum(filtered)

        monkeypatch.setattr(q17_module, "_q17_pandas_sum", _spy_sum)
        result = _collect_pandas(get_query("Q17v8").pandas_impl(ctx))
        assert calls["sum_helper"] == 0, "Q17v8 must compute the commuted formula, not delegate to the baseline sum"
        assert result["avg_yearly"][0] == pytest.approx(10.0 / 7.0)

    def test_q18v2_inner_joins_distinct_keys(self, q18_context, monkeypatch) -> None:
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        hows: list[str] = []
        original_join = UnifiedLazyFrame.join

        def _spy_join(self, other, *args, **kwargs):
            hows.append(str(kwargs.get("how", "inner")))
            return original_join(self, other, *args, **kwargs)

        monkeypatch.setattr(UnifiedLazyFrame, "join", _spy_join)
        _collect_frame(get_query("Q18v2").expression_impl(q18_context))
        assert "semi" not in hows, f"Q18v2 must not semi-join, got {hows}"
        assert "inner" in hows, f"Q18v2 must inner-join the distinct keys, got {hows}"

    def test_q18v7_left_joins_large_order_keys(self, q18_context, monkeypatch) -> None:
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        hows: list[str] = []
        original_join = UnifiedLazyFrame.join

        def _spy_join(self, other, *args, **kwargs):
            hows.append(str(kwargs.get("how", "inner")))
            return original_join(self, other, *args, **kwargs)

        monkeypatch.setattr(UnifiedLazyFrame, "join", _spy_join)
        _collect_frame(get_query("Q18v7").expression_impl(q18_context))
        assert "left" in hows, f"Q18v7 must use a left join for the key filter, got {hows}"
        assert "semi" not in hows, f"Q18v7 must not semi-join, got {hows}"

    def test_q18v8_aggregates_before_enrichment(self, q18_context, monkeypatch) -> None:
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        joins: list[tuple[list[str], list[str]]] = []
        original_join = UnifiedLazyFrame.join

        def _spy_join(self, other, *args, **kwargs):
            joins.append((list(self.columns), list(other.columns)))
            return original_join(self, other, *args, **kwargs)

        monkeypatch.setattr(UnifiedLazyFrame, "join", _spy_join)
        _collect_frame(get_query("Q18v8").expression_impl(q18_context))
        assert joins, "Q18v8 must perform joins"
        sum_joins = [
            pair
            for pair in joins
            if any("sum_qty" in str(column) for column in pair[0] + pair[1])
            and any("c_custkey" in str(column) for column in pair[0] + pair[1])
        ]
        assert sum_joins, (
            f"Q18v8 must join the pre-aggregated per-order sums with customer/order rows, saw joins {joins}"
        )

    def test_q18v9_limits_before_lineitem_join(self, q18_context, monkeypatch) -> None:
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        events: list[str] = []
        original_join = UnifiedLazyFrame.join
        original_limit = UnifiedLazyFrame.limit

        def _spy_join(self, other, *args, **kwargs):
            if any(str(column).startswith("l_quantity") for column in list(other.columns)):
                events.append("lineitem-join")
            return original_join(self, other, *args, **kwargs)

        def _spy_limit(self, *args, **kwargs):
            events.append("limit")
            return original_limit(self, *args, **kwargs)

        monkeypatch.setattr(UnifiedLazyFrame, "join", _spy_join)
        monkeypatch.setattr(UnifiedLazyFrame, "limit", _spy_limit)
        _collect_frame(get_query("Q18v9").expression_impl(q18_context))
        assert "limit" in events and "lineitem-join" in events, f"Q18v9 must limit and join lineitem, got {events}"
        assert events.index("limit") < events.index("lineitem-join"), (
            f"Q18v9 must limit before the lineitem join, got order {events}"
        )


@pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
class TestQ16Q18VariantEquivalence:
    @pytest.fixture
    def q17_expr_context(self):
        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table(
            "part",
            pl.DataFrame(
                {
                    "p_partkey": [1, 2, 3, 4],
                    "p_brand": ["Brand#23", "Brand#23", "Other", "Brand#23"],
                    "p_container": ["MED BOX", "MED BOX", "MED BOX", "MED BOX"],
                    "p_size": [10, 20, 30, 40],
                }
            ).lazy(),
        )
        ctx.register_table(
            "lineitem",
            pl.DataFrame(
                {
                    "l_partkey": [1, 1, 1, 2, 2, 3, 4, 4],
                    "l_quantity": [1.0, 2.0, 30.0, 0.5, 9.5, 5.0, 0.5, 9.5],
                    "l_extendedprice": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0],
                }
            ).lazy(),
        )
        return ctx

    @pytest.fixture
    def q18_expr_context(self):
        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table(
            "customer",
            pl.DataFrame(
                {
                    "c_custkey": [1, 2, 3, 4],
                    "c_name": ["A", "B", "C", "D"],
                    "c_nationkey": [3, 7, 14, 20],
                }
            ).lazy(),
        )
        ctx.register_table(
            "orders",
            pl.DataFrame(
                {
                    "o_orderkey": [1, 2, 3, 4],
                    "o_custkey": [1, 2, 3, 4],
                    "o_orderdate": [date(1994, 1, 1)] * 4,
                    "o_totalprice": [100.0, 350.0, 700.0, 500.0],
                }
            ).lazy(),
        )
        ctx.register_table(
            "lineitem",
            pl.DataFrame(
                {
                    "l_orderkey": [1, 2, 2, 3, 3, 3, 4, 4],
                    "l_quantity": [100.0, 150.0, 200.0, 300.0, 200.0, 200.0, 160.0, 160.0],
                }
            ).lazy(),
        )
        return ctx

    @pytest.fixture
    def q16_expr_context(self, monkeypatch):
        import benchbox.core.tpch.dataframe_queries as tpch_queries
        import benchbox.core.tpchavoc.dataframe_queries.q16 as q16_module

        params = {"brand": "Brand#45", "type_prefix": "MEDIUM POLISHED", "sizes": [49]}
        monkeypatch.setattr(q16_module, "get_tpch_parameters", lambda _n: params)
        monkeypatch.setattr(tpch_queries, "get_tpch_parameters", lambda _n: params)
        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table(
            "part",
            pl.DataFrame(
                {
                    "p_partkey": [1, 2, 3],
                    "p_brand": ["B1", "B1", "Brand#45"],
                    "p_type": ["MEDIUM POLISHED X", "PROMO Y", "MEDIUM POLISHED X"],
                    "p_size": [49, 49, 49],
                }
            ).lazy(),
        )
        ctx.register_table(
            "partsupp",
            pl.DataFrame({"ps_partkey": [1, 1, 1, 2, 2], "ps_suppkey": [10, 10, 11, 10, 12]}).lazy(),
        )
        ctx.register_table(
            "supplier",
            pl.DataFrame(
                {
                    "s_suppkey": [10, 11, 12],
                    "s_comment": ["Customer Complaints here", "has Customer Complaints issue", "ok"],
                }
            ).lazy(),
        )
        return ctx

    @pytest.fixture
    def q17_pandas_context(self):
        pytest.importorskip("pandas")
        import pandas as pd

        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        ctx.register_table(
            "part",
            pd.DataFrame(
                {
                    "p_partkey": [1, 2, 3, 4],
                    "p_brand": ["Brand#23", "Brand#23", "Other", "Brand#23"],
                    "p_container": ["MED BOX", "MED BOX", "MED BOX", "MED BOX"],
                    "p_size": [10, 20, 30, 40],
                }
            ),
        )
        ctx.register_table(
            "lineitem",
            pd.DataFrame(
                {
                    "l_partkey": [1, 1, 1, 2, 2, 3, 4, 4],
                    "l_quantity": [1.0, 2.0, 30.0, 0.5, 9.5, 5.0, 0.5, 9.5],
                    "l_extendedprice": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0],
                }
            ),
        )
        return ctx

    @pytest.fixture
    def q18_pandas_context(self):
        pytest.importorskip("pandas")
        import pandas as pd

        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        ctx.register_table(
            "customer",
            pd.DataFrame(
                {
                    "c_custkey": [1, 2, 3, 4],
                    "c_name": ["A", "B", "C", "D"],
                    "c_nationkey": [3, 7, 14, 20],
                }
            ),
        )
        ctx.register_table(
            "orders",
            pd.DataFrame(
                {
                    "o_orderkey": [1, 2, 3, 4],
                    "o_custkey": [1, 2, 3, 4],
                    "o_orderdate": [date(1994, 1, 1)] * 4,
                    "o_totalprice": [100.0, 350.0, 700.0, 500.0],
                }
            ),
        )
        ctx.register_table(
            "lineitem",
            pd.DataFrame(
                {
                    "l_orderkey": [1, 2, 2, 3, 3, 3, 4, 4],
                    "l_quantity": [100.0, 150.0, 200.0, 300.0, 200.0, 200.0, 160.0, 160.0],
                }
            ),
        )
        return ctx

    @pytest.fixture
    def q16_pandas_context(self, monkeypatch):
        pytest.importorskip("pandas")
        import pandas as pd

        import benchbox.core.tpch.dataframe_queries as tpch_queries
        import benchbox.core.tpchavoc.dataframe_queries.q16 as q16_module
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        params = {"brand": "Brand#45", "type_prefix": "MEDIUM POLISHED", "sizes": [49]}
        monkeypatch.setattr(q16_module, "get_tpch_parameters", lambda _n: params)
        monkeypatch.setattr(tpch_queries, "get_tpch_parameters", lambda _n: params)
        ctx = PandasDataFrameAdapter().create_context()
        ctx.register_table(
            "part",
            pd.DataFrame(
                {
                    "p_partkey": [1, 2, 3],
                    "p_brand": ["B1", "B1", "Brand#45"],
                    "p_type": ["MEDIUM POLISHED X", "PROMO Y", "MEDIUM POLISHED X"],
                    "p_size": [49, 49, 49],
                }
            ),
        )
        ctx.register_table(
            "partsupp", pd.DataFrame({"ps_partkey": [1, 1, 1, 2, 2], "ps_suppkey": [10, 10, 11, 10, 12]})
        )
        ctx.register_table(
            "supplier",
            pd.DataFrame(
                {
                    "s_suppkey": [10, 11, 12],
                    "s_comment": ["Customer Complaints here", "has Customer Complaints issue", "ok"],
                }
            ),
        )
        return ctx

    @pytest.mark.parametrize("variant_id", [f"Q17v{v}" for v in range(1, 11)])
    def test_q17_expression_variants_match_baseline(self, q17_expr_context, variant_id: str) -> None:
        base = _collect_frame(get_query("Q17v1").expression_impl(q17_expr_context))
        result = _collect_frame(get_query(variant_id).expression_impl(q17_expr_context))
        assert_frame_equal(result, base, check_exact=False, abs_tol=1e-9)

    @pytest.mark.parametrize("variant_id", [f"Q18v{v}" for v in range(1, 11)])
    def test_q18_expression_variants_match_baseline(self, q18_expr_context, variant_id: str) -> None:
        base = _collect_frame(get_query("Q18v1").expression_impl(q18_expr_context))
        result = _collect_frame(get_query(variant_id).expression_impl(q18_expr_context))
        assert_frame_equal(result, base)

    @pytest.mark.parametrize("variant_id", [f"Q16v{v}" for v in range(1, 11)])
    def test_q16_expression_variants_match_baseline(self, q16_expr_context, variant_id: str) -> None:
        base = _collect_frame(get_query("Q16v1").expression_impl(q16_expr_context))
        result = _collect_frame(get_query(variant_id).expression_impl(q16_expr_context))
        assert_frame_equal(result, base)

    @pytest.mark.parametrize("variant_id", [f"Q17v{v}" for v in range(1, 11)])
    def test_q17_pandas_variants_match_baseline(self, q17_pandas_context, variant_id: str) -> None:
        from pandas.testing import assert_frame_equal as assert_pandas_equal

        base = _collect_pandas(get_query("Q17v1").pandas_impl(q17_pandas_context))
        result = _collect_pandas(get_query(variant_id).pandas_impl(q17_pandas_context))
        assert_pandas_equal(result, base, check_dtype=False)

    @pytest.mark.parametrize("variant_id", [f"Q18v{v}" for v in range(1, 11)])
    def test_q18_pandas_variants_match_baseline(self, q18_pandas_context, variant_id: str) -> None:
        from pandas.testing import assert_frame_equal as assert_pandas_equal

        base = _collect_pandas(get_query("Q18v1").pandas_impl(q18_pandas_context)).reset_index(drop=True)
        result = _collect_pandas(get_query(variant_id).pandas_impl(q18_pandas_context)).reset_index(drop=True)
        assert_pandas_equal(result, base, check_dtype=False)

    @pytest.mark.parametrize("variant_id", [f"Q16v{v}" for v in range(1, 11)])
    def test_q16_pandas_variants_match_baseline(self, q16_pandas_context, variant_id: str) -> None:
        from pandas.testing import assert_frame_equal as assert_pandas_equal

        base = _collect_pandas(get_query("Q16v1").pandas_impl(q16_pandas_context)).reset_index(drop=True)
        result = _collect_pandas(get_query(variant_id).pandas_impl(q16_pandas_context)).reset_index(drop=True)
        assert_pandas_equal(result, base, check_dtype=False)


def _collect_scalar(result) -> pl.DataFrame:
    if hasattr(result, "native"):
        result = result.native
    if hasattr(result, "collect"):
        result = result.collect()
    return result


@pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
class TestQ14v8Regression:
    @pytest.fixture
    def q14_context(self):
        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table(
            "lineitem",
            pl.DataFrame(
                {
                    "l_orderkey": [1, 2, 3, 4],
                    "l_partkey": [10, 20, 30, 40],
                    "l_extendedprice": [100.0, 200.0, 999.0, 999.0],
                    "l_discount": [0.0, 0.0, 0.0, 0.0],
                    "l_shipdate": [
                        date(1995, 9, 2),
                        date(1995, 9, 15),
                        date(1994, 1, 1),
                        date(1995, 10, 1),
                    ],
                }
            ).lazy(),
        )
        ctx.register_table(
            "part",
            pl.DataFrame(
                {
                    "p_partkey": [10, 20, 30, 40],
                    "p_type": [
                        "PROMO ANODIZED STEEL",
                        "STANDARD BRASS",
                        "PROMO PLATED TIN",
                        "ECONOMY ANODIZED",
                    ],
                }
            ).lazy(),
        )
        return ctx

    def test_q14v8_expression_executes_without_error(self, q14_context) -> None:
        query = get_query("Q14v8")
        result = _collect_scalar(query.expression_impl(q14_context))
        assert "promo_revenue" in result.columns

    def test_q14v8_expression_result_shape(self, q14_context) -> None:
        query = get_query("Q14v8")
        result = _collect_scalar(query.expression_impl(q14_context))
        assert result.shape == (1, 1)
        assert result.columns == ["promo_revenue"]

    def test_q14v8_promo_revenue_value(self, q14_context) -> None:
        query = get_query("Q14v8")
        result = _collect_scalar(query.expression_impl(q14_context))
        value = result["promo_revenue"][0]
        assert abs(value - 100.0 * 100.0 / 300.0) < 1e-6

    def test_q14v8_parity_with_q14v1(self, q14_context) -> None:
        v1 = _collect_scalar(get_query("Q14v1").expression_impl(q14_context))
        v8 = _collect_scalar(get_query("Q14v8").expression_impl(q14_context))
        assert_frame_equal(v8, v1, check_exact=False, atol=1e-6)
