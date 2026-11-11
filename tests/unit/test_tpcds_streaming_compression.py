#!/usr/bin/env python3

import tempfile
from pathlib import Path
from unittest.mock import call, patch

import pytest

from benchbox.core.data_organization.config import DataOrganizationConfig, SortColumn
from benchbox.core.tpcds.generator import TPCDSDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTPCDSStreamingCompression:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def mock_dsdgen_path(self, temp_dir):
        dsdgen_exe = temp_dir / "dsdgen"
        dsdgen_exe.write_text("#!/bin/bash\necho 'mock dsdgen'")
        dsdgen_exe.chmod(0o755)
        return dsdgen_exe

    def test_streaming_compression_enabled(self, temp_dir, mock_dsdgen_path):
        with patch.object(TPCDSDataGenerator, "_find_or_build_dsdgen") as mock_find:
            mock_find.return_value = mock_dsdgen_path

            generator = TPCDSDataGenerator(
                scale_factor=1.0,
                output_dir=temp_dir,
                compression_type="zstd",
                compress_data=True,
                force_regenerate=True,
            )

            with patch.object(generator, "_run_streaming_dsdgen") as mock_streaming:
                with patch.object(generator, "_copy_distribution_files"):
                    generator._run_single_threaded_dsdgen(temp_dir)
                    mock_streaming.assert_called_once_with(temp_dir)

    def test_file_based_fallback_when_no_compression(self, temp_dir, mock_dsdgen_path):
        with patch.object(TPCDSDataGenerator, "_find_or_build_dsdgen") as mock_find:
            mock_find.return_value = mock_dsdgen_path

            generator = TPCDSDataGenerator(
                scale_factor=1.0,
                output_dir=temp_dir,
                compression_type="none",
                compress_data=False,
                force_regenerate=True,
            )

            with patch.object(generator, "_run_file_based_dsdgen") as mock_file_based:
                with patch.object(generator, "_copy_distribution_files"):
                    generator._run_single_threaded_dsdgen(temp_dir)
                    mock_file_based.assert_called_once_with(temp_dir)

    def test_parent_child_table_handling(self, temp_dir, mock_dsdgen_path):
        with patch.object(TPCDSDataGenerator, "_find_or_build_dsdgen") as mock_find:
            mock_find.return_value = mock_dsdgen_path

            generator = TPCDSDataGenerator(
                scale_factor=1.0,
                output_dir=temp_dir,
                compression_type="zstd",
                compress_data=True,
                force_regenerate=True,
            )

            with patch.object(generator, "_generate_parent_table_with_children") as mock_parent:
                with patch.object(generator, "_copy_distribution_files"):
                    generator._generate_table_with_streaming(temp_dir, "catalog_sales")
                    mock_parent.assert_called_once_with(temp_dir, "catalog_sales", ["catalog_returns"])

            with patch.object(generator, "_generate_single_table_streaming") as mock_single:
                with patch.object(generator, "_copy_distribution_files"):
                    generator._generate_table_with_streaming(temp_dir, "customer")
                    mock_single.assert_called_once_with(temp_dir, "customer")

    def test_parallel_streaming_compression(self, temp_dir, mock_dsdgen_path):
        with patch.object(TPCDSDataGenerator, "_find_or_build_dsdgen") as mock_find:
            mock_find.return_value = mock_dsdgen_path

            generator = TPCDSDataGenerator(
                scale_factor=1.0,
                output_dir=temp_dir,
                parallel=2,
                compression_type="zstd",
                compress_data=True,
                force_regenerate=True,
            )

            with patch.object(generator, "_run_parallel_streaming_dsdgen") as mock_parallel:
                with patch.object(generator, "_copy_distribution_files"):
                    generator._run_parallel_dsdgen(temp_dir)
                    mock_parallel.assert_called_once_with(temp_dir)

    def test_compression_filename_generation(self, temp_dir, mock_dsdgen_path):
        with patch.object(TPCDSDataGenerator, "_find_or_build_dsdgen") as mock_find:
            mock_find.return_value = mock_dsdgen_path

            generator = TPCDSDataGenerator(
                scale_factor=1.0,
                output_dir=temp_dir,
                compression_type="zstd",
                compress_data=True,
                force_regenerate=True,
            )

            filename = generator.get_compressed_filename("customer.dat")
            assert filename == "customer.dat.zst"

            assert generator.should_use_compression()

    def test_generated_files_mapping(self, temp_dir, mock_dsdgen_path):
        with patch.object(TPCDSDataGenerator, "_find_or_build_dsdgen") as mock_find:
            mock_find.return_value = mock_dsdgen_path

            generator = TPCDSDataGenerator(
                scale_factor=1.0,
                output_dir=temp_dir,
                compression_type="zstd",
                compress_data=True,
                force_regenerate=True,
            )

            compressed_files = {}
            table_names = ["customer", "item", "store_sales", "catalog_sales"]
            for table in table_names:
                compressed_file = temp_dir / f"{table}.dat.zst"
                compressed_file.write_text(f"compressed {table} data")
                compressed_files[table] = compressed_file

            with patch.object(generator, "_run_dsdgen_native", return_value=None):
                with patch.object(generator, "should_use_compression", return_value=True):
                    with patch.object(generator, "get_compressor") as mock_compressor:
                        mock_compressor.return_value.get_file_extension.return_value = ".zst"

                        result = generator._generate_local(temp_dir)

                        assert isinstance(result, dict)
                        for table_paths in result.values():
                            assert isinstance(table_paths, list), "Expected list of paths"
                            for file_path in table_paths:
                                assert str(file_path).endswith(".zst"), f"Expected .zst file, got {file_path}"

    def test_generate_local_prunes_stale_artifacts_when_regenerating(self, temp_dir, mock_dsdgen_path):
        with patch.object(TPCDSDataGenerator, "_find_or_build_dsdgen") as mock_find:
            mock_find.return_value = mock_dsdgen_path

            generator = TPCDSDataGenerator(
                scale_factor=1.0,
                output_dir=temp_dir,
                compression_type="none",
                compress_data=False,
                force_regenerate=True,
            )

            with patch.object(generator.validator, "should_regenerate_data", return_value=(True, None)):
                with patch.object(generator, "_prune_stale_table_artifacts", return_value=[]) as mock_prune:
                    with patch.object(generator, "_run_dsdgen_native", return_value=None):
                        with patch.object(generator, "_gather_existing_table_files", return_value={}):
                            with patch.object(generator, "_validate_file_format_consistency"):
                                with patch.object(generator, "_write_manifest"):
                                    generator._generate_local(temp_dir)

            mock_prune.assert_called_once_with(temp_dir)


def test_streaming_compression_integration():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        generator = TPCDSDataGenerator(
            scale_factor=1.0,
            output_dir=temp_path,
            verbose=False,
            parallel=1,
            force_regenerate=True,
            compression_type="zstd",
            compress_data=True,
        )

        try:
            assert hasattr(generator, "_run_streaming_dsdgen")
            assert hasattr(generator, "_generate_table_with_streaming")
            assert hasattr(generator, "_generate_single_table_streaming")
            assert hasattr(generator, "_generate_parent_table_with_children")
            assert generator.should_use_compression()
            assert generator.get_compressed_filename("test.dat") == "test.dat.zst"
        except Exception as e:
            if "dsdgen" not in str(e):
                raise e


@patch("benchbox.core.tpcds.generator.TPCDSDataGenerator._find_or_build_dsdgen")
def test_generate_local_prefers_data_organization_over_compression(mock_find_dsdgen, tmp_path: Path):
    dsdgen_exe = tmp_path / "dsdgen"
    dsdgen_exe.write_text("#!/bin/bash\necho 'mock dsdgen'", encoding="utf-8")
    dsdgen_exe.chmod(0o755)
    mock_find_dsdgen.return_value = dsdgen_exe

    data_org = DataOrganizationConfig(sort_columns=[SortColumn(name="c_customer_sk")], compression="none")
    generator = TPCDSDataGenerator(
        scale_factor=1.0,
        output_dir=tmp_path,
        compress_data=True,
        compression_type="zstd",
        data_organization=data_org,
        force_regenerate=True,
    )

    raw_customer = tmp_path / "customer.dat"
    raw_customer.write_text("1|name|\n", encoding="utf-8")
    organized = tmp_path / "customer.parquet"
    organized.write_text("placeholder", encoding="utf-8")

    with (
        patch.object(generator.validator, "should_regenerate_data", return_value=(True, None)),
        patch.object(generator, "_prune_stale_table_artifacts", return_value=[]),
        patch.object(generator, "_run_dsdgen_native", return_value=None),
        patch.object(
            generator, "_gather_existing_table_files", return_value={"customer": [raw_customer]}
        ) as mock_gather,
        patch.object(generator, "_apply_data_organization", return_value={"customer": [organized]}) as mock_apply,
        patch.object(generator, "_compress_raw_dat_files") as mock_compress,
        patch.object(generator, "_validate_file_format_consistency") as mock_validate,
        patch.object(generator, "_write_manifest") as mock_manifest,
    ):
        result = generator._generate_local(tmp_path)

    assert result == {"customer": [organized]}
    assert mock_gather.call_args_list[0] == call(tmp_path, use_compression=False)
    mock_apply.assert_called_once_with(tmp_path, {"customer": [raw_customer]})
    mock_compress.assert_not_called()
    mock_validate.assert_not_called()
    mock_manifest.assert_called_once_with(tmp_path, {"customer": [organized]})


