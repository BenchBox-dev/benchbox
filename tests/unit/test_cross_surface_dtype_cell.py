from __future__ import annotations

import pytest

pytest.importorskip("duckdb", reason="duckdb not installed")
pytest.importorskip("polars", reason="polars not installed")

import duckdb
import polars as pl

from benchbox.core.equivalence.cross_surface import (
    _dtype_categories_equivalent,
    find_cross_surface_dtype_divergences,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def typed_conn():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE t (a VARCHAR, b INTEGER)")
    yield conn
    conn.close()


def _query(frame):
    class _FakeQuery:
        def get_impl_for_family(self, backend):
            if backend != "expression":
                return None

            def impl(_context):
                return frame.lazy() if isinstance(frame, pl.DataFrame) else frame

            return impl

    return _FakeQuery()


def _run(conn, dataframe_query, **kwargs):
    return find_cross_surface_dtype_divergences(
        conn,
        query_ids=["q1"],
        reference_sql=lambda _qid: "SELECT a, b FROM t",
        dataframe_query=lambda _qid: dataframe_query,
        contexts={"expression": object()},
        **kwargs,
    )


def test_equivalence_map_accepts_only_documented_pairs():
    assert _dtype_categories_equivalent("string", "string")
    assert _dtype_categories_equivalent("decimal", "float")
    assert not _dtype_categories_equivalent("float", "decimal")
    assert not _dtype_categories_equivalent("string", "null")
    assert not _dtype_categories_equivalent("temporal", "string")
    assert not _dtype_categories_equivalent("integer", "string")


def test_matching_frame_is_green(typed_conn):
    divergences, compared = _run(typed_conn, _query(pl.DataFrame({"a": ["x"], "b": [1]})))
    assert divergences == []
    assert compared == {"expression": 1}


def test_width_mismatch_is_flagged(typed_conn):
    divergences, _ = _run(typed_conn, _query(pl.DataFrame({"a": ["x"]})))
    assert len(divergences) == 1
    assert "width" in divergences[0].detail


def test_text_to_null_corruption_is_flagged(typed_conn):
    frame = pl.DataFrame({"a": [None, None], "b": [1, 2]})
    divergences, _ = _run(typed_conn, _query(frame))
    assert any("reference string, frame null" in divergence.detail for divergence in divergences)


def test_untyped_frame_is_flagged_not_passed(typed_conn):
    pd = pytest.importorskip("pandas", reason="pandas not installed")
    divergences, _ = _run(typed_conn, _query(pd.DataFrame({"a": ["x"], "b": [1]})))
    assert len(divergences) == 1
    assert "no Polars-style schema" in divergences[0].detail


def test_skips_are_honored(typed_conn):
    frame = pl.DataFrame({"a": [None, None], "b": [1, 2]})
    divergences, compared = _run(typed_conn, _query(frame), skip_keys=frozenset({"q1_expression"}))
    assert divergences == []
    assert compared == {"expression": 0}
    divergences, compared = _run(typed_conn, _query(frame), skip_query_ids=frozenset({"q1"}))
    assert divergences == []
    assert compared == {"expression": 0}


def test_value_divergence_key_is_checked_without_explicit_dtype_waiver(typed_conn):
    frame = pl.DataFrame({"a": [None, None], "b": [1, 2]})
    divergences, compared = _run(typed_conn, _query(frame), skip_keys=frozenset())

    assert compared == {"expression": 1}
    assert any("reference string, frame null" in divergence.detail for divergence in divergences)


