from pathlib import Path

import pytest

from benchbox.platforms.base.data_loading import (
    DeltaFileHandler,
    FileFormatRegistry,
    IcebergFileHandler,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_get_base_data_extension_multi_suffix_tbl_zst():

    p = Path("/tmp/customer.tbl.1.zst")
    base_ext = FileFormatRegistry.get_base_data_extension(p)
    assert base_ext == ".tbl"


def test_get_handler_for_multi_suffix_tbl_zst():

    p = Path("/tmp/orders.tbl.10.zst")
    handler = FileFormatRegistry.get_handler(p)
    assert handler is not None, "Expected a file format handler for .tbl.10.zst"

    assert handler.get_delimiter() == "|"


def test_get_handler_for_delta_lake_directory(tmp_path):

    delta_table_dir = tmp_path / "customer"
    delta_table_dir.mkdir()
    delta_log_dir = delta_table_dir / "_delta_log"
    delta_log_dir.mkdir()

    handler = FileFormatRegistry.get_handler(delta_table_dir)
    assert handler is not None, "Expected a file format handler for Delta Lake directory"
    assert isinstance(handler, DeltaFileHandler)
    assert handler.get_delimiter() == ""


def test_get_handler_for_non_delta_directory(tmp_path):

    regular_dir = tmp_path / "data"
    regular_dir.mkdir()

    handler = FileFormatRegistry.get_handler(regular_dir)
    assert handler is None, "Expected None for non-Delta directory"


def test_get_handler_for_parquet_file(tmp_path):

    parquet_file = tmp_path / "customer.parquet"

    handler = FileFormatRegistry.get_handler(parquet_file)
    assert handler is not None, "Expected a file format handler for .parquet file"
    assert handler.get_delimiter() == ""


def test_get_handler_for_iceberg_directory(tmp_path):

    iceberg_table_dir = tmp_path / "customer"
    iceberg_table_dir.mkdir()
    metadata_dir = iceberg_table_dir / "metadata"
    metadata_dir.mkdir()

    handler = FileFormatRegistry.get_handler(iceberg_table_dir)
    assert handler is not None, "Expected a file format handler for Iceberg directory"
    assert isinstance(handler, IcebergFileHandler)
    assert handler.get_delimiter() == ""


