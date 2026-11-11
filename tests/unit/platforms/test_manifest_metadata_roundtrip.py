from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.manifest.io import load_manifest
from benchbox.core.manifest.models import ManifestV2
from benchbox.utils.datagen_manifest import DataGenerationManifest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _write_and_reload(tmp_path: Path, benchmark: str, tables_meta: dict) -> ManifestV2:

    manifest = DataGenerationManifest(
        output_dir=tmp_path,
        benchmark=benchmark,
        scale_factor=0.01,
        compression={"enabled": False, "type": None, "level": None},
        parallel=1,
    )
    for table_name, meta in tables_meta.items():
        stub = tmp_path / f"{table_name}.csv"
        stub.write_text("row1\n")
        manifest.add_entry(table_name, stub, row_count=1, metadata=meta)
    manifest.write()

    manifest_path = tmp_path / "_datagen_manifest.json"
    loaded = load_manifest(manifest_path)
    assert isinstance(loaded, ManifestV2), f"Expected ManifestV2, got {type(loaded)}"
    return loaded


def test_local_manifest_entry_path_normalization_is_lazy_cloud_safe(tmp_path: Path) -> None:

    stub = tmp_path / "lineitem.tbl"
    stub.write_text("1|2|3|\n")

    manifest = DataGenerationManifest(
        output_dir=tmp_path,
        benchmark="tpch",
        scale_factor=0.01,
        compression={"enabled": False, "type": None, "level": None},
        parallel=1,
    )
    manifest.add_entry("lineitem", stub, row_count=1)
    manifest_path = manifest.write()

    assert manifest_path == tmp_path / "_datagen_manifest.json"
    loaded = load_manifest(manifest_path)
    assert isinstance(loaded, ManifestV2)
    assert "lineitem" in loaded.tables


def _get_first_entry_metadata(loaded: ManifestV2, table: str) -> dict:

    table_formats = loaded.tables.get(table)
    assert table_formats is not None, f"Table '{table}' not in manifest"
    all_entries = [e for entries in table_formats.formats.values() for e in entries]
    assert all_entries, f"No entries for table '{table}'"
    return all_entries[0].metadata


def test_nyctaxi_metadata_roundtrips(tmp_path: Path) -> None:

    meta = {"csv_has_header": True, "csv_null_marker": None}
    loaded = _write_and_reload(tmp_path, "nyctaxi", {"trips": meta})
    result = _get_first_entry_metadata(loaded, "trips")
    assert result.get("csv_has_header") is True

    assert result.get("csv_null_marker") is None


def test_nyctaxi_metadata_backwards_compat_no_metadata(tmp_path: Path) -> None:

    loaded = _write_and_reload(tmp_path, "nyctaxi_plain", {"trips": None})
    result = _get_first_entry_metadata(loaded, "trips")
    assert result == {}


def test_tsbs_devops_metadata_roundtrips(tmp_path: Path) -> None:

    meta = {"csv_has_header": True}
    tables = dict.fromkeys(("tags", "cpu", "mem", "disk", "net"), meta)
    loaded = _write_and_reload(tmp_path, "tsbs_devops", tables)
    for table in tables:
        result = _get_first_entry_metadata(loaded, table)
        assert result.get("csv_has_header") is True, f"Missing csv_has_header for {table}"


def test_flightdata_metadata_roundtrips(tmp_path: Path) -> None:

    meta = {"csv_has_header": True}
    loaded = _write_and_reload(tmp_path, "flightdata", {"flights": meta, "airlines": meta})
    for table in ("flights", "airlines"):
        result = _get_first_entry_metadata(loaded, table)
        assert result.get("csv_has_header") is True


def test_vector_search_metadata_roundtrips(tmp_path: Path) -> None:

    meta = {"csv_has_header": True}
    loaded = _write_and_reload(tmp_path, "vector_search", {"vectors": meta, "vector_queries": meta})
    for table in ("vectors", "vector_queries"):
        result = _get_first_entry_metadata(loaded, table)
        assert result.get("csv_has_header") is True


def test_clickbench_metadata_roundtrips(tmp_path: Path) -> None:

    meta = {"csv_delimiter": "|", "csv_null_marker": None}
    loaded = _write_and_reload(tmp_path, "clickbench", {"hits": meta})
    result = _get_first_entry_metadata(loaded, "hits")
    assert result.get("csv_delimiter") == "|"
    assert result.get("csv_null_marker") is None


def test_tpcdi_metadata_roundtrips(tmp_path: Path) -> None:

    meta = {"csv_normalize_booleans": True, "csv_null_marker": ""}
    tables = dict.fromkeys(("DimAccount", "DimCustomer", "FactTrade"), meta)
    loaded = _write_and_reload(tmp_path, "tpcdi", tables)
    for table in tables:
        result = _get_first_entry_metadata(loaded, table)
        assert result.get("csv_normalize_booleans") is True, f"Missing csv_normalize_booleans for {table}"
        assert result.get("csv_null_marker") == "", f"Wrong csv_null_marker for {table}"


