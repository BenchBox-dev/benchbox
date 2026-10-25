# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) H (TPC-H) - Copyright (C) Transaction Processing Performance Council.
# This implementation is derived from TPC-H.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.utils.compression import CompressionManager
from benchbox.utils.file_format import TRAILING_DUMMY_COLUMN

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_CUSTOMER_COLUMNS = [
    "c_custkey",
    "c_name",
    "c_address",
    "c_nationkey",
    "c_phone",
    "c_acctbal",
    "c_mktsegment",
    "c_comment",
]
_TRAILING_ROWS = (
    "1|Customer#000000001|IVhzIApeRb|15|25-989-741-2988|711.56|BUILDING|regular accounts|\n"
    "2|Customer#000000002|XSTf4NCwDV|13|23-768-687-3665|121.65|AUTOMOBILE|ironic requests|\n"
)


def _write_variant(dir_path: Path, filename: str) -> Path:
    path = dir_path / filename
    if filename.endswith(".zst"):
        compressor = CompressionManager().get_compressor("zstd")
        with compressor.open_for_write(path, "wt") as handle:
            handle.write(_TRAILING_ROWS)
    else:
        path.write_text(_TRAILING_ROWS)
    return path


_VARIANTS = [
    "customer.tbl",
    "customer.tbl.zst",
    "customer.tbl.1",
    "customer.tbl.1.zst",
]


try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    _PYARROW = True
except ImportError:
    _PYARROW = False

try:
    import datafusion

    from benchbox.platforms.dataframe.datafusion_df import (
        DATAFUSION_DF_AVAILABLE,
        DataFusionDataFrameAdapter,
    )
except ImportError:
    DATAFUSION_DF_AVAILABLE = False


@pytest.mark.skipif(not _PYARROW, reason="pyarrow not installed")
@pytest.mark.parametrize("filename", _VARIANTS)
def test_sql_adapter_loads_trailing_delimiter_chunk(tmp_path, filename):
    from benchbox.platforms.datafusion import DataFusionAdapter

    data_path = _write_variant(tmp_path, filename)

    mock_benchmark = Mock()
    mock_benchmark.__class__.__name__ = "TPCHBenchmark"
    mock_benchmark.get_schema.return_value = {
        "customer": {
            "name": "customer",
            "columns": [{"name": name, "type": "VARCHAR"} for name in _CUSTOMER_COLUMNS],
        }
    }

    with patch("benchbox.platforms.datafusion.SessionContext"):
        adapter = DataFusionAdapter(working_dir=str(tmp_path / "wd"), data_format="parquet")
        with (
            patch.object(adapter, "_get_constraint_configuration", return_value=(False, False)),
            patch.object(adapter, "_log_constraint_configuration"),
        ):
            adapter.create_schema(mock_benchmark, Mock())

        row_count = adapter._load_table_parquet(Mock(), "customer", [data_path], tmp_path)

    assert row_count == 2, f"{filename}: expected 2 rows, got {row_count}"

    written = adapter.working_dir / "customer.parquet"
    schema = pq.read_schema(written)
    assert TRAILING_DUMMY_COLUMN not in schema.names, f"{filename}: dummy column leaked: {schema.names}"
    assert len(schema.names) == len(_CUSTOMER_COLUMNS), f"{filename}: wrong column count: {schema.names}"


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
@pytest.mark.parametrize("filename", _VARIANTS)
def test_dataframe_adapter_reads_trailing_delimiter_chunk(tmp_path, filename):
    data_path = _write_variant(tmp_path, filename)

    adapter = DataFusionDataFrameAdapter()
    df = adapter.read_csv(
        data_path,
        delimiter="|",
        has_header=False,
        column_names=_CUSTOMER_COLUMNS,
    )
    result = adapter.collect(df)

    assert result.num_rows == 2, f"{filename}: expected 2 rows, got {result.num_rows}"
    assert result.column_names == _CUSTOMER_COLUMNS, f"{filename}: wrong columns: {result.column_names}"
    assert TRAILING_DUMMY_COLUMN not in result.column_names, f"{filename}: dummy column leaked"


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
def test_dataframe_header_bearing_trailing_delimiter_skips_header(tmp_path):
    data_path = tmp_path / "customer.tbl"
    header = "|".join(_CUSTOMER_COLUMNS) + "|\n"
    data_path.write_text(header + _TRAILING_ROWS)

    adapter = DataFusionDataFrameAdapter()
    df = adapter.read_csv(
        data_path,
        delimiter="|",
        has_header=True,
        column_names=_CUSTOMER_COLUMNS,
    )
    result = adapter.collect(df)

    assert result.num_rows == 2, f"header row leaked into data: got {result.num_rows} rows"
    assert result.column_names == _CUSTOMER_COLUMNS
    assert str(result.column("c_custkey").to_pylist()[0]) == "1"
