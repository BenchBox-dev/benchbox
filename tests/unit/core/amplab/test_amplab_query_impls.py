# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from unittest.mock import MagicMock

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

    def __contains__(self, item):
        return True


def _mock_ctx():

    ctx = MagicMock()
    ctx.col.side_effect = lambda *a, **kw: _Expr()
    ctx.lit.side_effect = lambda *a, **kw: _Expr()
    ctx.when.side_effect = lambda *a, **kw: _Expr()
    ctx.get_table.side_effect = lambda *a, **kw: _DataFrame()
    return ctx


def _pandas_ctx_with_documents():

    documents = pd.DataFrame(
        {
            "url": ["http://example.com/1", "http://example.com/2"],
            "contents": [
                "This is a long web document about data and databases. " * 30,
                "Short doc.",
            ],
        }
    )
    ctx = MagicMock()
    ctx.get_table.side_effect = lambda name: documents
    return ctx


def test_q1_expression_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q1_expression_impl

    ctx = _mock_ctx()
    result = q1_expression_impl(ctx)
    assert result is not None


def test_q1_pandas_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q1_pandas_impl

    ctx = _mock_ctx()
    result = q1_pandas_impl(ctx)
    assert result is not None


def test_q1a_expression_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q1a_expression_impl

    ctx = _mock_ctx()
    result = q1a_expression_impl(ctx)
    assert result is not None


def test_q1a_pandas_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q1a_pandas_impl

    ctx = _mock_ctx()
    result = q1a_pandas_impl(ctx)
    assert result is not None


def test_q2_expression_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q2_expression_impl

    ctx = _mock_ctx()
    result = q2_expression_impl(ctx)
    assert result is not None


def test_q2_pandas_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q2_pandas_impl

    ctx = _mock_ctx()
    result = q2_pandas_impl(ctx)
    assert result is not None


def test_q2a_expression_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q2a_expression_impl

    ctx = _mock_ctx()
    result = q2a_expression_impl(ctx)
    assert result is not None


def test_q2a_pandas_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q2a_pandas_impl

    ctx = _mock_ctx()
    result = q2a_pandas_impl(ctx)
    assert result is not None


def test_q3_expression_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q3_expression_impl

    ctx = _mock_ctx()
    result = q3_expression_impl(ctx)
    assert result is not None


def test_q3_pandas_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q3_pandas_impl

    ctx = _mock_ctx()
    result = q3_pandas_impl(ctx)
    assert result is not None


def test_q3a_expression_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q3a_expression_impl

    ctx = _mock_ctx()
    result = q3a_expression_impl(ctx)
    assert result is not None


def test_q3a_pandas_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q3a_pandas_impl

    ctx = _pandas_ctx_with_documents()
    result = q3a_pandas_impl(ctx)
    assert result is not None


def test_q4_expression_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q4_expression_impl

    ctx = _mock_ctx()
    result = q4_expression_impl(ctx)
    assert result is not None


def test_q4_pandas_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q4_pandas_impl

    ctx = _mock_ctx()
    result = q4_pandas_impl(ctx)
    assert result is not None


def test_q5_expression_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q5_expression_impl

    ctx = _mock_ctx()
    result = q5_expression_impl(ctx)
    assert result is not None


def test_q5_pandas_impl():
    from benchbox.core.amplab.dataframe_queries.queries import q5_pandas_impl

    ctx = _mock_ctx()
    result = q5_pandas_impl(ctx)
    assert result is not None


def test_all_8_queries_registered_with_callable_impls():
    from benchbox.core.amplab.dataframe_queries import AMPLAB_DATAFRAME_QUERIES

    queries = AMPLAB_DATAFRAME_QUERIES.get_all_queries()
    assert len(queries) == 8
    for q in queries:
        assert callable(q.expression_impl), f"{q.query_id} expression_impl not callable"
        assert callable(q.pandas_impl), f"{q.query_id} pandas_impl not callable"
        assert q.query_id, "query_id must not be empty"
        assert q.description, f"{q.query_id} description must not be empty"


