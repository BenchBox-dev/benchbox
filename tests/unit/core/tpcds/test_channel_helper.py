from __future__ import annotations

import pandas as pd
import pytest

from benchbox.core.tpcds.dataframe_queries.channel_helper import (
    get_available_returns_columns,
    get_available_sales_columns,
    get_returns_column,
    get_sales_column,
    union_returns_channels_pandas,
    union_sales_channels_expression,
    union_sales_channels_pandas,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Expr:
    def __init__(self, value: str):
        self.value = value

    def alias(self, name: str):
        return ("alias", self.value, name)


class _ExprTable:
    def __init__(self, name: str):
        self.name = name

    def select(self, exprs):
        return {"table": self.name, "exprs": exprs}


class _ExpressionCtx:
    def col(self, name: str) -> _Expr:
        return _Expr(name)

    def lit(self, value):
        return _Expr(str(value))

    def get_table(self, name: str) -> _ExprTable:
        return _ExprTable(name)

    def concat(self, dfs):
        return dfs


class _PandasCtx:
    def __init__(self):
        self.tables = {
            "store_sales": pd.DataFrame({"ss_item_sk": [1], "ss_quantity": [2], "ss_sales_price": [3.0]}),
            "catalog_sales": pd.DataFrame({"cs_item_sk": [10], "cs_quantity": [20], "cs_sales_price": [30.0]}),
            "store_returns": pd.DataFrame({"sr_item_sk": [1], "sr_return_quantity": [1]}),
        }

    def get_table(self, name: str) -> pd.DataFrame:
        return self.tables[name]

    def concat(self, dfs):
        return pd.concat(dfs, ignore_index=True)


def test_get_sales_column_and_returns_column():
    assert get_sales_column("store", "item_sk") == "ss_item_sk"
    assert get_returns_column("store", "item_sk") == "sr_item_sk"

    with pytest.raises(ValueError, match="Unknown channel"):
        get_sales_column("bad", "item_sk")

    with pytest.raises(ValueError, match="Unknown column"):
        get_returns_column("store", "bad_col")


def test_available_column_helpers():
    shared_sales = get_available_sales_columns()
    assert "item_sk" in shared_sales
    assert shared_sales == sorted(shared_sales)

    store_returns = get_available_returns_columns("store")
    assert "return_quantity" in store_returns


def test_union_sales_channels_expression_happy_path_and_validation():
    ctx = _ExpressionCtx()
    out = union_sales_channels_expression(ctx, channels=["store", "catalog"], columns=["item_sk", "quantity"])
    assert len(out) == 2
    assert out[0]["table"] == "store_sales"
    assert out[1]["table"] == "catalog_sales"

    with pytest.raises(ValueError, match="Unknown channel"):
        union_sales_channels_expression(ctx, channels=["invalid"], columns=["item_sk"])

    with pytest.raises(ValueError, match="not available for channel"):
        union_sales_channels_expression(ctx, channels=["store"], columns=["bad_col"])


def test_union_sales_and_returns_channels_pandas():
    ctx = _PandasCtx()
    sales = union_sales_channels_pandas(ctx, channels=["store", "catalog"], columns=["item_sk", "quantity"])
    assert list(sales.columns) == ["item_sk", "quantity", "channel"]
    assert set(sales["channel"]) == {"store", "catalog"}

    returns = union_returns_channels_pandas(ctx, channels=["store"], columns=["item_sk", "return_quantity"])
    assert list(returns.columns) == ["item_sk", "return_quantity", "channel"]
    assert returns.iloc[0]["channel"] == "store"


