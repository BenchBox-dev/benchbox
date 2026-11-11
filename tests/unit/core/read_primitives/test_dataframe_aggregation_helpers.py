from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.read_primitives.dataframe_queries import (
    aggregation_distinct_groupby_pandas_impl,
    aggregation_groupby_large_pandas_impl,
    aggregation_groupby_small_pandas_impl,
)

pd = pytest.importorskip("pandas")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def pandas_ctx() -> SimpleNamespace:
    lineitem = pd.DataFrame(
        {
            "l_returnflag": ["R", "R", "A", "A"],
            "l_linestatus": ["F", "F", "O", "O"],
            "l_orderkey": [1, 1, 2, 3],
            "l_partkey": [10, 11, 12, 12],
            "l_suppkey": [100, 101, 102, 103],
            "l_quantity": [5, 7, 11, 13],
            "l_extendedprice": [100.0, 200.0, 300.0, 500.0],
        }
    )
    nation = pd.DataFrame(
        {
            "n_regionkey": [0, 0, 1],
            "n_nationkey": [1, 2, 3],
            "n_name": ["ALGERIA", "BRAZIL", "CANADA"],
        }
    )

    tables = {"lineitem": lineitem, "nation": nation}
    return SimpleNamespace(get_table=tables.__getitem__)


def test_aggregation_distinct_groupby_pandas_impl_groups_and_counts_uniques(pandas_ctx) -> None:
    result = (
        aggregation_distinct_groupby_pandas_impl(pandas_ctx)
        .sort_values(["l_returnflag", "l_linestatus"])
        .reset_index(drop=True)
    )

    assert list(result["unique_orders"]) == [2, 1]
    assert list(result["unique_parts"]) == [1, 2]


def test_aggregation_groupby_large_pandas_impl_groups_and_aggregates(pandas_ctx) -> None:
    result = (
        aggregation_groupby_large_pandas_impl(pandas_ctx)
        .sort_values(["l_orderkey", "l_partkey", "l_suppkey"])
        .reset_index(drop=True)
    )

    assert list(result["total_qty"]) == [5, 7, 11, 13]
    assert list(result["avg_price"]) == [100.0, 200.0, 300.0, 500.0]


def test_aggregation_groupby_small_pandas_impl_groups_and_aggregates(pandas_ctx) -> None:
    result = aggregation_groupby_small_pandas_impl(pandas_ctx).sort_values("n_regionkey").reset_index(drop=True)

    assert list(result["nation_count"]) == [2, 1]
    assert list(result["last_nation"]) == ["BRAZIL", "CANADA"]


