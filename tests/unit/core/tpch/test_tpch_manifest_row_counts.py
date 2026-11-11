from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.tpch.generator import TPCHDataGenerator

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _manifest_lineitem_counts(output_dir: Path) -> list[int]:
    payload = json.loads((output_dir / "_datagen_manifest.json").read_text(encoding="utf-8"))
    return [entry["row_count"] for entry in payload["tables"]["lineitem"]["formats"]["tbl"]]


def _write_rows(path: Path, row_count: int) -> None:
    path.write_text("".join(f"row-{index}|\n" for index in range(row_count)), encoding="utf-8")


def test_manifest_records_uneven_rows_for_explicit_shards(tmp_path: Path) -> None:
    generator = TPCHDataGenerator(
        scale_factor=0.01,
        output_dir=tmp_path,
        parallel=2,
        compression_type="none",
        quiet=True,
    )
    shard_paths = [tmp_path / "lineitem.tbl.1", tmp_path / "lineitem.tbl.2"]
    _write_rows(shard_paths[0], 3)
    _write_rows(shard_paths[1], 4)

    generator._write_manifest(tmp_path, {"lineitem": shard_paths})

    assert _manifest_lineitem_counts(tmp_path) == [3, 4]
    payload = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))
    assert payload["row_counts_source"] == "measured"


@pytest.mark.parametrize("compression_type", ["gzip", "zstd"])
def test_manifest_records_uneven_rows_for_legacy_compressed_shards(tmp_path: Path, compression_type: str) -> None:
    generator = TPCHDataGenerator(
        scale_factor=0.01,
        output_dir=tmp_path,
        parallel=2,
        compression_type=compression_type,
        compress_data=True,
        quiet=True,
    )
    raw_paths = [tmp_path / "lineitem.tbl.1", tmp_path / "lineitem.tbl.2"]
    _write_rows(raw_paths[0], 2)
    _write_rows(raw_paths[1], 5)
    shard_paths = [generator.compress_existing_file(path, remove_original=True) for path in raw_paths]

    generator._write_manifest(tmp_path, {"lineitem": shard_paths[0]})

    assert _manifest_lineitem_counts(tmp_path) == [2, 5]


def test_reused_data_refreshes_stale_manifest_counts(tmp_path: Path) -> None:
    generator = TPCHDataGenerator(
        scale_factor=0.01,
        output_dir=tmp_path,
        parallel=2,
        compression_type="none",
        quiet=True,
    )
    shard_paths = [tmp_path / "lineitem.tbl.1", tmp_path / "lineitem.tbl.2"]
    _write_rows(shard_paths[0], 3)
    _write_rows(shard_paths[1], 4)

    stale_manifest = {
        "version": 2,
        "tables": {"lineitem": {"formats": {"tbl": [{"path": path.name, "row_count": 1} for path in shard_paths]}}},
    }
    (tmp_path / "_datagen_manifest.json").write_text(json.dumps(stale_manifest), encoding="utf-8")

    with patch.object(generator, "_find_or_build_dbgen", return_value=Path("/tmp/dbgen")):
        with patch.object(generator.validator, "should_regenerate_data", return_value=(False, None)):
            with patch.object(generator, "_collect_existing_table_files", return_value={"lineitem": shard_paths}):
                result = generator._generate_local(tmp_path)

    assert result == {"lineitem": shard_paths}
    assert _manifest_lineitem_counts(tmp_path) == [3, 4]


def test_reused_measured_manifest_does_not_rescan_files(tmp_path: Path) -> None:
    generator = TPCHDataGenerator(
        scale_factor=0.01,
        output_dir=tmp_path,
        parallel=2,
        compression_type="none",
        quiet=True,
    )
    shard_paths = [tmp_path / "lineitem.tbl.1", tmp_path / "lineitem.tbl.2"]
    _write_rows(shard_paths[0], 3)
    _write_rows(shard_paths[1], 4)
    generator._write_manifest(tmp_path, {"lineitem": shard_paths})

    with patch.object(generator, "_find_or_build_dbgen", return_value=Path("/tmp/dbgen")):
        with patch.object(generator.validator, "should_regenerate_data", return_value=(False, None)):
            with patch.object(generator, "_collect_existing_table_files") as collect_existing:
                with patch.object(generator, "_count_file_rows", side_effect=AssertionError("unexpected rescan")):
                    result = generator._generate_local(tmp_path)

    assert result == {"lineitem": shard_paths}
    collect_existing.assert_not_called()


def test_reused_manifest_preserves_organized_paths_and_compression_metadata(tmp_path: Path) -> None:
    generator = TPCHDataGenerator(
        scale_factor=0.01,
        output_dir=tmp_path,
        compression_type="none",
        quiet=True,
    )
    raw_path = tmp_path / "lineitem.tbl"
    parquet_path = tmp_path / "lineitem.parquet"
    _write_rows(raw_path, 5)
    parquet_path.write_bytes(b"organized output")
    original_compression = {"enabled": True, "type": "zstd", "level": 7}
    stale_manifest = {
        "version": 2,
        "benchmark": "tpch",
        "scale_factor": 0.01,
        "formats": ["parquet", "tbl"],
        "format_preference": ["parquet", "tbl"],
        "compression": original_compression,
        "tables": {
            "lineitem": {
                "formats": {
                    "tbl": [{"path": raw_path.name, "row_count": 1}],
                    "parquet": [{"path": parquet_path.name, "row_count": 5, "metadata": {"sorted": True}}],
                }
            }
        },
    }
    manifest_path = tmp_path / "_datagen_manifest.json"
    manifest_path.write_text(json.dumps(stale_manifest), encoding="utf-8")

    with patch.object(generator, "_find_or_build_dbgen", return_value=Path("/tmp/dbgen")):
        with patch.object(generator.validator, "should_regenerate_data", return_value=(False, None)):
            with patch.object(generator, "_collect_existing_table_files") as collect_existing:
                result = generator._generate_local(tmp_path)

    assert result == {"lineitem": parquet_path}
    collect_existing.assert_not_called()
    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert refreshed["compression"] == original_compression
    assert refreshed["formats"] == ["parquet", "tbl"]
    assert refreshed["format_preference"] == ["parquet", "tbl"]
    assert refreshed["row_counts_source"] == "measured"
    assert refreshed["tables"]["lineitem"]["formats"]["tbl"][0]["row_count"] == 5
    assert refreshed["tables"]["lineitem"]["formats"]["parquet"][0]["metadata"] == {"sorted": True}


def test_reused_manifest_preserves_compression_metadata_from_compressed_files(tmp_path: Path) -> None:
    writer = TPCHDataGenerator(
        scale_factor=0.01,
        output_dir=tmp_path,
        compression_type="zstd",
        compress_data=True,
        quiet=True,
    )
    raw_path = tmp_path / "lineitem.tbl"
    _write_rows(raw_path, 6)
    compressed_path = writer.compress_existing_file(raw_path, remove_original=True)

    generator = TPCHDataGenerator(
        scale_factor=0.01,
        output_dir=tmp_path,
        compression_type="none",
        quiet=True,
    )
    original_compression = {"enabled": True, "type": "zstd", "level": 4}
    manifest = {
        "version": 2,
        "benchmark": "tpch",
        "scale_factor": 0.01,
        "formats": ["tbl"],
        "format_preference": ["tbl"],
        "compression": original_compression,
        "tables": {"lineitem": {"formats": {"tbl": [{"path": compressed_path.name, "row_count": 1}]}}},
    }
    manifest_path = tmp_path / "_datagen_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with patch.object(generator, "_find_or_build_dbgen", return_value=Path("/tmp/dbgen")):
        with patch.object(generator.validator, "should_regenerate_data", return_value=(False, None)):
            result = generator._generate_local(tmp_path)

    assert result == {"lineitem": compressed_path}
    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert refreshed["compression"] == original_compression
    assert refreshed["row_counts_source"] == "measured"
    assert refreshed["tables"]["lineitem"]["formats"]["tbl"][0]["row_count"] == 6


