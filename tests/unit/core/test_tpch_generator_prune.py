from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.tpch.generator import TPCHDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@patch("benchbox.core.tpch.generator.TPCHDataGenerator._find_or_build_dbgen")
def test_prune_stale_table_artifacts_removes_conflicting_variants_only(mock_find_dbgen, tmp_path: Path):
    mock_find_dbgen.return_value = Path("/tmp/dbgen")
    generator = TPCHDataGenerator(output_dir=tmp_path)

    stale_files = [
        tmp_path / "customer.tbl",
        tmp_path / "customer.tbl.1",
        tmp_path / "customer.tbl.zst",
        tmp_path / "customer.tbl.1.zst",
    ]
    for path in stale_files:
        path.write_text("stale", encoding="utf-8")
    keep_file = tmp_path / "_datagen_manifest.json"
    keep_file.write_text("{}", encoding="utf-8")

    removed = generator._prune_stale_table_artifacts(tmp_path)

    assert "customer.tbl.1" in {p.name for p in removed}
    assert "customer.tbl.1.zst" in {p.name for p in removed}
    assert "customer.tbl.zst" in {p.name for p in removed}
    assert (tmp_path / "customer.tbl").exists()
    assert not (tmp_path / "customer.tbl.1").exists()
    assert not (tmp_path / "customer.tbl.1.zst").exists()
    assert not (tmp_path / "customer.tbl.zst").exists()
    assert keep_file.exists()


@patch("benchbox.core.tpch.generator.TPCHDataGenerator._find_or_build_dbgen")
def test_generate_local_prunes_stale_artifacts_when_regenerating(mock_find_dbgen, tmp_path: Path):
    mock_find_dbgen.return_value = Path("/tmp/dbgen")
    generator = TPCHDataGenerator(output_dir=tmp_path)

    with patch.object(generator.validator, "should_regenerate_data", return_value=(True, None)):
        with patch.object(generator, "_prune_stale_table_artifacts", return_value=[]) as mock_prune:
            with patch.object(generator, "_run_dbgen_native", return_value=None):
                with patch.object(generator, "_finalize_generation", return_value={}):
                    generator._generate_local(tmp_path)

    mock_prune.assert_called_once_with(tmp_path)


@patch("benchbox.core.tpch.generator.TPCHDataGenerator._find_or_build_dbgen")
def test_prune_stale_table_artifacts_removes_compressed_when_uncompressed_mode(mock_find_dbgen, tmp_path: Path):
    mock_find_dbgen.return_value = Path("/tmp/dbgen")
    generator = TPCHDataGenerator(output_dir=tmp_path, compress_data=False, compression_type="none")

    stale_compressed = tmp_path / "customer.tbl.zst"
    stale_compressed.write_text("stale", encoding="utf-8")

    removed = generator._prune_stale_table_artifacts(tmp_path)

    assert "customer.tbl.zst" in {p.name for p in removed}
    assert not stale_compressed.exists()


@patch("benchbox.core.tpch.generator.TPCHDataGenerator._find_or_build_dbgen")
def test_finalize_generation_prefers_data_organization_over_compression(mock_find_dbgen, tmp_path: Path):
    mock_find_dbgen.return_value = Path("/tmp/dbgen")
    generator = TPCHDataGenerator(output_dir=tmp_path, compress_data=True, compression_type="zstd")

    tbl = tmp_path / "lineitem.tbl"
    tbl.write_text("1|1992-01-01|\n", encoding="utf-8")
    organized = tmp_path / "lineitem.parquet"
    organized.write_text("placeholder", encoding="utf-8")
    generator._data_organization_config = object()

    with patch.object(generator, "_gather_generated_table_paths", return_value=({"lineitem": tbl}, set())):
        with patch.object(generator, "_apply_data_organization", return_value={"lineitem": organized}) as mock_apply:
            with patch.object(generator, "_compress_table_paths") as mock_compress:
                with patch.object(generator, "_write_manifest") as mock_manifest:
                    result = generator._finalize_generation(tmp_path)

    assert result == {"lineitem": organized}
    mock_apply.assert_called_once_with(tmp_path, {"lineitem": tbl})
    mock_compress.assert_not_called()
    mock_manifest.assert_called_once_with(tmp_path, {"lineitem": organized})


@patch("benchbox.core.tpch.generator.TPCHDataGenerator._find_or_build_dbgen")
def test_build_schema_registry_includes_tpch_columns(mock_find_dbgen, tmp_path: Path):
    mock_find_dbgen.return_value = Path("/tmp/dbgen")
    generator = TPCHDataGenerator(output_dir=tmp_path)

    schema_registry = generator._build_schema_registry()

    assert "lineitem" in schema_registry
    column_names = [column["name"] for column in schema_registry["lineitem"]["columns"]]
    assert "l_shipdate" in column_names


