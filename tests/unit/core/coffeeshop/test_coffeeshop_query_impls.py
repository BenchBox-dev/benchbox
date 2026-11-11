# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Expr:
    def __gt__(self, other):
        return _Expr()

    def __ge__(self, other):
        return _Expr()

    def __lt__(self, other):
        return _Expr()

    def __le__(self, other):
        return _Expr()

    def __ne__(self, other):
        return _Expr()

    def __and__(self, other):
        return _Expr()

    def __or__(self, other):
        return _Expr()

    def __getitem__(self, key):
        return _Expr()

    def __getattr__(self, name):
        return _Expr()

    def __call__(self, *args, **kwargs):
        return _Expr()

    def __truediv__(self, other):
        return _Expr()

    def __floordiv__(self, other):
        return _Expr()

    def __add__(self, other):
        return _Expr()

    def __sub__(self, other):
        return _Expr()

    def __mul__(self, other):
        return _Expr()


class _DataFrame:
    def __gt__(self, other):
        return _DataFrame()

    def __ge__(self, other):
        return _DataFrame()

    def __lt__(self, other):
        return _DataFrame()

    def __le__(self, other):
        return _DataFrame()

    def __and__(self, other):
        return _DataFrame()

    def __or__(self, other):
        return _DataFrame()

    def __getattr__(self, name):
        return _DataFrame()

    def __getitem__(self, key):
        return _DataFrame()

    def __call__(self, *args, **kwargs):
        return _DataFrame()

    def __truediv__(self, other):
        return _DataFrame()

    def __floordiv__(self, other):
        return _DataFrame()

    def __add__(self, other):
        return _DataFrame()

    def __sub__(self, other):
        return _DataFrame()

    def __mul__(self, other):
        return _DataFrame()

    def __len__(self):
        return 10

    def __iter__(self):
        return iter([])

    def scalar(self):
        return 100.0


def _mock_ctx():

    ctx = MagicMock()
    ctx.col.side_effect = lambda *a, **kw: _Expr()
    ctx.lit.side_effect = lambda *a, **kw: _Expr()
    ctx.when.side_effect = lambda *a, **kw: _Expr()
    ctx.get_table.side_effect = lambda *a, **kw: _DataFrame()
    return ctx


def _pandas_ctx():

    from datetime import datetime

    order_lines = pd.DataFrame(
        {
            "order_id": [1, 1, 2, 2],
            "order_date": pd.to_datetime(["2023-01-10", "2023-01-10", "2023-01-15", "2024-06-01"]),
            "location_record_id": [1, 1, 2, 2],
            "product_record_id": [10, 20, 10, 20],
            "total_price": [5.0, 3.0, 8.0, 4.0],
            "quantity": [1, 2, 1, 1],
            "unit_price": [5.0, 3.0, 8.0, 4.0],
            "order_time": ["08:00:00", "12:00:00", "16:00:00", "20:00:00"],
        }
    )
    dim_locations = pd.DataFrame(
        {
            "record_id": [1, 2],
            "region": ["South", "North"],
            "location_id": [101, 102],
            "city": ["Atlanta", "Boston"],
            "state": ["GA", "MA"],
        }
    )
    dim_products = pd.DataFrame(
        {
            "record_id": [10, 20],
            "subcategory": ["Coffee", "Tea"],
            "name": ["Espresso", "Green Tea"],
            "product_id": [1001, 1002],
            "from_date": pd.to_datetime(["2022-01-01", "2022-01-01"]),
            "to_date": pd.to_datetime(["2025-12-31", "2025-12-31"]),
        }
    )

    tables = {
        "order_lines": order_lines,
        "dim_locations": dim_locations,
        "dim_products": dim_products,
    }

    ctx = MagicMock()
    ctx.get_table.side_effect = lambda name: tables[name]
    return ctx


def test_parse_date_from_string():
    from benchbox.core.coffeeshop.dataframe_queries.queries import _parse_date

    result = _parse_date("2023-01-15")
    assert result == date(2023, 1, 15)


def test_parse_date_from_date_object():
    from benchbox.core.coffeeshop.dataframe_queries.queries import _parse_date

    d = date(2023, 6, 1)
    assert _parse_date(d) is d


def test_sa1_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa1_expression_impl

    ctx = _mock_ctx()
    result = sa1_expression_impl(ctx)
    assert result is not None


def test_sa1_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa1_pandas_impl

    ctx = _pandas_ctx()
    result = sa1_pandas_impl(ctx)
    assert result is not None


def test_sa2_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa2_expression_impl

    ctx = _mock_ctx()
    result = sa2_expression_impl(ctx)
    assert result is not None


def test_sa2_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa2_pandas_impl

    ctx = _mock_ctx()
    result = sa2_pandas_impl(ctx)
    assert result is not None


def test_sa3_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa3_expression_impl

    ctx = _mock_ctx()
    result = sa3_expression_impl(ctx)
    assert result is not None


def test_sa3_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa3_pandas_impl

    ctx = _pandas_ctx()
    result = sa3_pandas_impl(ctx)
    assert result is not None


def test_sa4_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa4_expression_impl

    ctx = _mock_ctx()
    result = sa4_expression_impl(ctx)
    assert result is not None


def test_sa4_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa4_pandas_impl

    ctx = _pandas_ctx()
    result = sa4_pandas_impl(ctx)
    assert result is not None


def test_sa5_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa5_expression_impl

    ctx = _mock_ctx()
    result = sa5_expression_impl(ctx)
    assert result is not None


def test_sa5_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa5_pandas_impl

    ctx = _pandas_ctx()
    result = sa5_pandas_impl(ctx)
    assert result is not None


def test_pr1_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import pr1_expression_impl

    ctx = _mock_ctx()
    result = pr1_expression_impl(ctx)
    assert result is not None


def test_pr1_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import pr1_pandas_impl

    ctx = _pandas_ctx()
    result = pr1_pandas_impl(ctx)
    assert result is not None


def test_pr2_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import pr2_expression_impl

    ctx = _mock_ctx()
    result = pr2_expression_impl(ctx)
    assert result is not None


def test_pr2_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import pr2_pandas_impl

    ctx = _pandas_ctx()
    result = pr2_pandas_impl(ctx)
    assert result is not None


def test_tr1_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import tr1_expression_impl

    ctx = _mock_ctx()
    result = tr1_expression_impl(ctx)
    assert result is not None


def test_tr1_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import tr1_pandas_impl

    ctx = _pandas_ctx()
    result = tr1_pandas_impl(ctx)
    assert result is not None


def test_tm1_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import tm1_expression_impl

    ctx = _mock_ctx()
    result = tm1_expression_impl(ctx)
    assert result is not None


def test_tm1_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import tm1_pandas_impl

    ctx = _pandas_ctx()
    result = tm1_pandas_impl(ctx)
    assert result is not None


def test_qc1_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import qc1_expression_impl

    ctx = _mock_ctx()
    result = qc1_expression_impl(ctx)
    assert result is not None


def test_qc1_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import qc1_pandas_impl

    ctx = _pandas_ctx()
    result = qc1_pandas_impl(ctx)
    assert result is not None


def test_qc2_expression_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import qc2_expression_impl

    ctx = _mock_ctx()
    result = qc2_expression_impl(ctx)
    assert result is not None


def test_qc2_pandas_impl():
    from benchbox.core.coffeeshop.dataframe_queries.queries import qc2_pandas_impl

    ctx = _pandas_ctx()
    result = qc2_pandas_impl(ctx)
    assert result is not None


def test_all_11_queries_registered_with_callable_impls():
    from benchbox.core.coffeeshop.dataframe_queries import COFFEESHOP_DATAFRAME_QUERIES

    queries = COFFEESHOP_DATAFRAME_QUERIES.get_all_queries()
    assert len(queries) == 11
    for q in queries:
        assert callable(q.expression_impl), f"{q.query_id} expression_impl not callable"
        assert callable(q.pandas_impl), f"{q.query_id} pandas_impl not callable"
        assert q.query_id, "query_id must not be empty"
        assert q.description, f"{q.query_id} description must not be empty"


