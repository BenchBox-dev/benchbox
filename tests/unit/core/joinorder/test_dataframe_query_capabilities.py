from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from benchbox.core.joinorder.benchmark import JoinOrderBenchmark
from benchbox.core.joinorder.dataframe_queries import (
    _HAND_TRANSLATED_DATAFRAME_QUERY_IDS,
    get_dataframe_queries,
    get_implemented_dataframe_query_ids,
    get_untranslated_dataframe_query_ids,
)
from benchbox.core.joinorder.queries import JoinOrderQueryManager

try:
    import polars as pl

    from benchbox.platforms.dataframe.polars_df import POLARS_AVAILABLE, PolarsDataFrameAdapter
except ImportError:
    POLARS_AVAILABLE = False
    pl = None

try:
    from benchbox.platforms.dataframe.dask_df import DASK_AVAILABLE, DaskDataFrameAdapter
except ImportError:
    DASK_AVAILABLE = False

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

REPO_ROOT = Path(__file__).resolve().parents[4]
TINY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "joinorder_canonical_tiny"


class PandasContext:
    platform = "pandas"

    def __init__(self, tables: dict[str, pd.DataFrame]) -> None:
        self._tables = tables

    def get_table(self, name: str) -> pd.DataFrame:
        return self._tables[name]


@pytest.fixture(scope="module")
def pandas_ctx() -> PandasContext:
    return PandasContext({path.stem: pd.read_parquet(path) for path in TINY_FIXTURE.glob("*.parquet")})


@pytest.fixture(scope="module")
def duckdb_conn() -> Any:
    conn = duckdb.connect(database=":memory:")
    for parquet_path in sorted(TINY_FIXTURE.glob("*.parquet")):
        conn.execute(f"CREATE TABLE {parquet_path.stem} AS SELECT * FROM read_parquet(?)", [str(parquet_path)])
    try:
        yield conn
    finally:
        conn.close()


def _null_normalized_row(frame: pd.DataFrame) -> dict[str, Any]:
    as_object = frame.astype(object)
    return as_object.where(pd.notna(as_object), None).iloc[0].to_dict()


@pytest.fixture(scope="module")
def polars_ctx() -> Any:
    if not POLARS_AVAILABLE:
        pytest.skip("Polars not installed")
    adapter = PolarsDataFrameAdapter()
    ctx = adapter.create_context()
    for parquet_path in sorted(TINY_FIXTURE.glob("*.parquet")):
        ctx.register_table(parquet_path.stem, pl.scan_parquet(parquet_path))
    return ctx


def test_dataframe_registry_exposes_all_canonical_query_ids_by_default() -> None:
    benchmark = JoinOrderBenchmark()
    registry = benchmark.get_dataframe_queries()
    skip_ids = set(benchmark.get_dataframe_skip_queries())
    selected_ids = [query.query_id for query in registry.get_all_queries() if query.query_id not in skip_ids]

    assert len(registry) == 113
    assert set(get_implemented_dataframe_query_ids()).isdisjoint(get_untranslated_dataframe_query_ids())
    assert len(get_implemented_dataframe_query_ids()) == 113
    assert get_untranslated_dataframe_query_ids() == []
    assert selected_ids == sorted(get_implemented_dataframe_query_ids())


def test_generated_pandas_queries_match_tiny_duckdb_oracle(
    pandas_ctx: PandasContext,
    duckdb_conn: Any,
) -> None:
    registry = get_dataframe_queries()
    query_ids = [
        query_id
        for query_id in JoinOrderQueryManager().get_query_ids()
        if query_id not in _HAND_TRANSLATED_DATAFRAME_QUERY_IDS
    ]

    for query_id in query_ids:
        query = registry.get_or_raise(query_id)
        actual = query.pandas_impl(pandas_ctx)
        expected = duckdb_conn.execute(JoinOrderQueryManager().get_query(query_id)).fetchdf()

        assert list(actual.columns) == list(expected.columns), query_id
        assert len(actual) == len(expected) == 1, query_id
        assert _null_normalized_row(actual) == _null_normalized_row(expected), query_id


def test_generated_pandas_query_aggregation_supports_lazy_dask_frame(duckdb_conn: Any) -> None:
    if not DASK_AVAILABLE:
        pytest.skip("Dask not installed")

    adapter = DaskDataFrameAdapter(execution_engine="threaded")
    ctx = adapter.create_context()
    for parquet_path in sorted(TINY_FIXTURE.glob("*.parquet")):
        ctx.register_table(parquet_path.stem, adapter.read_parquet(parquet_path))

    query = get_dataframe_queries().get_or_raise("1c")
    actual = query.pandas_impl(ctx)
    expected = duckdb_conn.execute(JoinOrderQueryManager().get_query("1c")).fetchdf()

    assert list(actual.columns) == list(expected.columns)
    assert len(actual) == len(expected) == 1
    actual_row = _null_normalized_row(actual)
    expected_row = _null_normalized_row(expected)
    assert actual_row == expected_row


def test_generated_expression_queries_match_tiny_duckdb_oracle(
    polars_ctx: Any,
    duckdb_conn: Any,
) -> None:
    registry = get_dataframe_queries()
    query_ids = [
        query_id
        for query_id in JoinOrderQueryManager().get_query_ids()
        if query_id not in _HAND_TRANSLATED_DATAFRAME_QUERY_IDS
    ]

    for query_id in query_ids:
        query = registry.get_or_raise(query_id)
        actual = query.expression_impl(polars_ctx).native.collect().to_pandas()
        expected = duckdb_conn.execute(JoinOrderQueryManager().get_query(query_id)).fetchdf()

        assert list(actual.columns) == list(expected.columns), query_id
        assert len(actual) == len(expected) == 1, query_id
        assert _null_normalized_row(actual) == _null_normalized_row(expected), query_id
