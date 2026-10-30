from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

import numpy as np
import pytest

try:
    import dask
    import dask.dataframe as dd
    import pandas as pd
    import pyarrow as pa

    DEPS_AVAILABLE = True
except ImportError:
    DEPS_AVAILABLE = False

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(not DEPS_AVAILABLE, reason="Dask and/or Pandas not installed"),
]

ORDERED_QUERIES = [
    "window_rank",
    "window_multiple_orderings",
    "window_moving_frame",
    "qualify_row_number",
    "qualify_dense_rank",
    "qualify_ntile",
    "qualify_percentile",
    "qualify_cume_dist",
    "qualify_lag_lead",
    "array_agg_simple",
    "array_agg_distinct",
    "array_contains",
    "array_distinct",
    "array_length",
    "array_min_max",
    "array_slice",
    "array_sort",
    "list_filter",
    "list_transform",
    "list_reduce",
    "max_by_simple",
    "max_by_complex",
    "min_by_simple",
    "olap_cube_analysis",
    "olap_rollup_analysis",
    "optimizer_common_subexpression",
    "pivot_basic",
    "statistical_correlation",
    "timeseries_trend_analysis",
    "groupby_all_complex",
    "aggregation_materialize",
    "aggregation_selective",
    "aggregation_simple",
]

UNORDERED_QUERIES = [
    "window_row_number",
    "window_lead_lag_same_frame",
    "window_unbounded_frame",
    "statistical_variance_stddev",
    "window_sum",
    "struct_access",
    "map_access",
    "map_keys_values",
    "filter_in_predicate_selective",
    "min_max_runtime_filter",
    "predicate_ordering_subquery",
]


def _build_tables() -> dict[str, pd.DataFrame]:
    rng = random.Random(7)
    start = date(1994, 1, 1)

    def day() -> date:
        return start + timedelta(days=rng.randrange(0, 1400))

    region = pd.DataFrame({"r_regionkey": range(5), "r_name": [f"R{i}" for i in range(5)]})
    nation = pd.DataFrame(
        {"n_nationkey": range(25), "n_name": [f"N{i}" for i in range(25)], "n_regionkey": [i % 5 for i in range(25)]}
    )
    segments = ["BUILDING", "AUTOMOBILE", "MACHINERY", "HOUSEHOLD"]
    customer = pd.DataFrame(
        {
            "c_custkey": range(1, 61),
            "c_name": [f"C{i}" for i in range(1, 61)],
            "c_address": [f"A{i}" for i in range(1, 61)],
            "c_phone": [f"P{i}" for i in range(1, 61)],
            "c_nationkey": [1 if i % 3 == 0 else rng.randrange(25) for i in range(60)],
            "c_mktsegment": [segments[i % 4] for i in range(60)],
            "c_acctbal": [round(rng.uniform(0, 10000), 2) for _ in range(60)],
        }
    )
    priorities = ["1-URGENT", "2-HIGH", "3-MEDIUM"]
    orders = pd.DataFrame(
        {
            "o_orderkey": range(1, 501),
            "o_custkey": [rng.randrange(1, 61) for _ in range(500)],
            "o_orderdate": [day() for _ in range(500)],
            "o_totalprice": [round(rng.uniform(1000, 400000), 2) + i / 1000 for i in range(500)],
            "o_orderpriority": [priorities[i % 3] for i in range(500)],
            "o_comment": [f"comment number {i}" for i in range(500)],
        }
    )
    modes = ["AIR", "RAIL", "SHIP", "TRUCK", "MAIL"]
    lineitem = pd.DataFrame(
        {
            "l_orderkey": [rng.randrange(1, 501) for _ in range(1500)],
            "l_partkey": [rng.randrange(1, 81) for _ in range(1500)],
            "l_suppkey": [rng.randrange(1, 13) for _ in range(1500)],
            "l_linenumber": [i % 7 + 1 for i in range(1500)],
            "l_quantity": [float(rng.randrange(1, 51)) for _ in range(1500)],
            "l_extendedprice": [round(rng.uniform(900, 90000), 2) + i / 1000 for i in range(1500)],
            "l_discount": [round(rng.choice([0.02, 0.04, 0.06, 0.08]), 2) for _ in range(1500)],
            "l_tax": [round(rng.choice([0.0, 0.02, 0.05]), 2) for _ in range(1500)],
            "l_returnflag": [rng.choice(["A", "N", "R"]) for _ in range(1500)],
            "l_linestatus": [rng.choice(["F", "O"]) for _ in range(1500)],
            "l_shipdate": [day() for _ in range(1500)],
            "l_shipmode": [rng.choice(modes) for _ in range(1500)],
            "l_shipinstruct": ["DELIVER IN PERSON"] * 1500,
        }
    )
    lineitem = lineitem.drop_duplicates(["l_orderkey", "l_linenumber"]).reset_index(drop=True)
    part = pd.DataFrame(
        {
            "p_partkey": range(1, 81),
            "p_name": [f"part {i}" for i in range(1, 81)],
            "p_brand": [f"Brand#{i % 5}" for i in range(80)],
            "p_type": [f"TYPE{i % 6}" for i in range(80)],
            "p_size": [rng.randrange(1, 50) for _ in range(80)],
            "p_retailprice": [round(900 + i * 1.7 + (i % 5) * 0.31, 2) for i in range(80)],
        }
    )
    partsupp = pd.DataFrame(
        {
            "ps_partkey": [p for p in range(1, 81) for _ in range(3)],
            "ps_suppkey": [(p + k) % 12 + 1 for p in range(1, 81) for k in range(3)],
            "ps_availqty": [rng.randrange(1, 9999) for _ in range(240)],
            "ps_supplycost": [round(rng.uniform(1, 1000), 2) for _ in range(240)],
        }
    ).drop_duplicates(["ps_partkey", "ps_suppkey"])
    orders["o_orderdate"] = orders["o_orderdate"].astype(pd.ArrowDtype(pa.date32()))
    lineitem["l_shipdate"] = lineitem["l_shipdate"].astype(pd.ArrowDtype(pa.date32()))
    return {
        "region": region,
        "nation": nation,
        "customer": customer,
        "orders": orders,
        "lineitem": lineitem,
        "part": part,
        "partsupp": partsupp,
    }


@pytest.fixture(scope="module")
def contexts() -> Any:
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter
    from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

    tables = _build_tables()
    pandas_ctx = PandasDataFrameAdapter().create_context()
    adapter = DaskDataFrameAdapter(execution_engine="threaded")
    dask_ctx = adapter.create_context()
    with dask.config.set({"dataframe.convert-string": False}):
        for name, frame in tables.items():
            pandas_ctx.register_table(name, frame)
            dask_ctx.register_table(name, dd.from_pandas(frame, npartitions=4))
    yield pandas_ctx, dask_ctx
    adapter.close()


def _frame(result: Any) -> pd.DataFrame:
    native = getattr(result, "native", result)
    if hasattr(native, "compute"):
        native = native.compute()
    if isinstance(native, pd.Series):
        native = native.to_frame()
    return native.reset_index(drop=True)


def _cell(value: Any) -> Any:
    if isinstance(value, (list, tuple, np.ndarray)):
        return tuple(sorted((_cell(item) for item in list(value)), key=repr))
    if isinstance(value, dict):
        return tuple(sorted(((key, _cell(item)) for key, item in value.items()), key=repr))
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (float, np.floating)):
        return None if value != value else float(value)
    return value


def _rows(result: Any) -> list[tuple[Any, ...]]:
    frame = _frame(result)
    return [tuple(_cell(value) for value in row) for row in frame.itertuples(index=False, name=None)]


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, float) and isinstance(right, float):
        return left == pytest.approx(right, rel=1e-9)
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def _same_rows(actual: list[tuple[Any, ...]], expected: list[tuple[Any, ...]]) -> bool:
    return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected, strict=True))


def _identity_key(row: tuple[Any, ...]) -> str:
    return repr([cell for cell in row if not isinstance(cell, float)])


def _run(name: str, ctx: Any) -> Any:
    from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

    return get_dataframe_queries().get_or_raise(name).get_impl_for_family("pandas")(ctx)


@pytest.mark.parametrize("query_id", ORDERED_QUERIES)
def test_dask_matches_pandas_in_order(contexts: Any, query_id: str) -> None:
    pandas_ctx, dask_ctx = contexts
    expected = _rows(_run(query_id, pandas_ctx))
    assert expected

    assert _same_rows(_rows(_run(query_id, dask_ctx)), expected)


@pytest.mark.parametrize("query_id", UNORDERED_QUERIES)
def test_dask_matches_pandas_ignoring_order(contexts: Any, query_id: str) -> None:
    pandas_ctx, dask_ctx = contexts
    expected = _rows(_run(query_id, pandas_ctx))
    assert expected

    actual = sorted(_rows(_run(query_id, dask_ctx)), key=_identity_key)

    assert _same_rows(actual, sorted(expected, key=_identity_key))


def test_head_on_a_sorted_dask_frame_returns_global_top_rows() -> None:
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter
    from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

    adapter = DaskDataFrameAdapter(execution_engine="threaded")
    try:
        frame = dd.from_pandas(pd.DataFrame({"v": range(400)}), npartitions=8)
        wrapped = UnifiedPandasFrame(frame[frame["v"] % 40 == 0], adapter)

        head = wrapped.sort_values("v", ascending=False).head(7)

        assert hasattr(head.native, "compute")
        assert head.compute()["v"].tolist() == [360, 320, 280, 240, 200, 160, 120]
    finally:
        adapter.close()
