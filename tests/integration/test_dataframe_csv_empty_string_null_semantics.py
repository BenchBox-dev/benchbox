from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

from benchbox.platforms.base.data_loading import DUCKDB_NO_NULL_CONVERSION_SENTINEL
from benchbox.platforms.dataframe.benchmark_mixin import DataFrameRunOptions
from benchbox.platforms.dataframe.cudf_df import CUDF_AVAILABLE, CuDFDataFrameAdapter
from benchbox.platforms.dataframe.dask_df import DASK_AVAILABLE, DaskDataFrameAdapter
from benchbox.platforms.dataframe.datafusion_df import DATAFUSION_DF_AVAILABLE, DataFusionDataFrameAdapter
from benchbox.platforms.dataframe.pandas_df import PANDAS_AVAILABLE, PandasDataFrameAdapter
from benchbox.platforms.dataframe.polars_df import POLARS_AVAILABLE, PolarsDataFrameAdapter
from benchbox.platforms.dataframe.pyspark_df import PYSPARK_AVAILABLE, PySparkDataFrameAdapter
from tests.utilities.optional_engines import pyspark_usable

pytestmark = [
    pytest.mark.integration,
    pytest.mark.fast,
]

_NULL = "<NULL>"


class _CsvNullMarkerBenchmark:
    name = "csv_null_marker_fixture"
    display_name = "CSV Null Marker Fixture"
    scale_factor = 0.01
    csv_delimiter = ","
    csv_has_header = True

    def __init__(self, csv_path: Path, null_marker: str | None) -> None:
        self.csv_null_marker = null_marker
        self.tables = {"events": csv_path}

    def get_schema(self) -> dict[str, dict[str, list[dict[str, str]]]]:
        return {
            "events": {
                "columns": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "phrase", "type": "VARCHAR"},
                ]
            }
        }


def _write_events_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "events.csv"
    csv_path.write_text("id,phrase\n1,\n2,abc\n", encoding="utf-8")
    return csv_path


def _normalize_null(value: Any) -> Any:
    if value is None:
        return _NULL
    if isinstance(value, float) and math.isnan(value):
        return _NULL
    try:
        import pandas as pd

        if value is pd.NA or (pd.api.types.is_scalar(value) and pd.isna(value)):
            return _NULL
    except (ImportError, TypeError, ValueError):
        pass
    return value


def _to_pandas(adapter: Any, native_table: Any) -> Any:
    to_pandas = getattr(adapter, "to_pandas", None)
    if callable(to_pandas):
        return to_pandas(native_table)
    table = native_table
    if hasattr(table, "collect"):
        table = table.collect()
    if hasattr(table, "to_pandas"):
        return table.to_pandas()
    if hasattr(table, "compute"):
        return table.compute()
    return table


def _load_phrase_values(adapter: Any, csv_path: Path, null_marker: str | None) -> list[Any]:
    benchmark = _CsvNullMarkerBenchmark(csv_path, null_marker)
    ctx = adapter.load_benchmark_into_context(
        benchmark,
        csv_path.parent,
        options=DataFrameRunOptions(prefer_parquet=False),
    )
    native_table = ctx._tables["events"]
    pdf = _to_pandas(adapter, native_table)
    return [_normalize_null(value) for value in pdf.sort_values("id")["phrase"].tolist()]


def _sql_phrase_values(csv_path: Path, null_marker: str | None) -> list[Any]:
    import duckdb

    nullstr = DUCKDB_NO_NULL_CONVERSION_SENTINEL if null_marker is None else null_marker
    escaped = nullstr.replace("'", "''")
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            f"SELECT phrase FROM read_csv('{csv_path}', header=true, nullstr='{escaped}', "
            "columns={'id': 'INTEGER', 'phrase': 'VARCHAR'}) ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return [_normalize_null(row[0]) for row in rows]


@dataclass(frozen=True)
class _AdapterCase:
    id: str
    available: bool
    factory: Callable[[], Any]


def _make_pyspark() -> Any:
    return PySparkDataFrameAdapter(master="local[1]", driver_memory="1g", shuffle_partitions=1)


def _make_dask() -> Any:
    return DaskDataFrameAdapter(execution_engine="threaded")


_ADAPTER_CASES = [
    _AdapterCase("datafusion", DATAFUSION_DF_AVAILABLE, DataFusionDataFrameAdapter),
    _AdapterCase("pyspark", PYSPARK_AVAILABLE and pyspark_usable(), _make_pyspark),
    _AdapterCase("pandas", PANDAS_AVAILABLE, PandasDataFrameAdapter),
    _AdapterCase("polars", POLARS_AVAILABLE, PolarsDataFrameAdapter),
    _AdapterCase("dask", DASK_AVAILABLE, _make_dask),
    _AdapterCase("cudf", CUDF_AVAILABLE, CuDFDataFrameAdapter),
]


_CROSS_SURFACE_IDS = {"datafusion", "pandas", "polars"}


@contextlib.contextmanager
def _adapter_context(case: _AdapterCase) -> Any:
    if not case.available:
        pytest.skip(f"{case.id} DataFrame adapter is not installed")
    with contextlib.ExitStack() as stack:
        if case.id == "dask":
            import dask

            stack.enter_context(dask.config.set(scheduler="synchronous"))
        adapter = case.factory()
        try:
            yield adapter
        finally:
            close = getattr(adapter, "close", None)
            if callable(close):
                close()


@pytest.fixture(params=_ADAPTER_CASES, ids=lambda case: case.id)
def csv_adapter(request: pytest.FixtureRequest) -> Any:
    if request.param.id == "pyspark":
        request.getfixturevalue("pyspark_test_environment")
    with _adapter_context(request.param) as adapter:
        yield adapter


@pytest.fixture(
    params=[case for case in _ADAPTER_CASES if case.id in _CROSS_SURFACE_IDS],
    ids=lambda case: case.id,
)
def cheap_csv_adapter(request: pytest.FixtureRequest) -> Any:
    with _adapter_context(request.param) as adapter:
        yield adapter


def test_csv_direct_keeps_empty_string_when_null_marker_is_none(csv_adapter: Any, tmp_path: Path) -> None:
    values = _load_phrase_values(csv_adapter, _write_events_csv(tmp_path), null_marker=None)
    assert values == ["", "abc"]


def test_csv_direct_preserves_null_when_null_marker_is_empty_string(csv_adapter: Any, tmp_path: Path) -> None:
    values = _load_phrase_values(csv_adapter, _write_events_csv(tmp_path), null_marker="")
    assert values == [_NULL, "abc"]


@pytest.mark.parametrize("null_marker", [None, ""], ids=["null_marker_none", "null_marker_empty"])
def test_cross_surface_csv_path_sql_dataframe_parity(
    cheap_csv_adapter: Any, tmp_path: Path, null_marker: str | None
) -> None:
    csv_path = _write_events_csv(tmp_path)
    sql_values = _sql_phrase_values(csv_path, null_marker)
    dataframe_values = _load_phrase_values(cheap_csv_adapter, csv_path, null_marker)
    assert dataframe_values == sql_values
