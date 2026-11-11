#!/usr/bin/env python3

import tempfile
from pathlib import Path

import pytest

from benchbox.core.tpcds.generator import TPCDSDataGenerator

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


class TestTPCDSStreamingPerformance:
    @pytest.fixture
    def temp_dirs(self):
        with (
            tempfile.TemporaryDirectory() as streaming_dir,
            tempfile.TemporaryDirectory() as traditional_dir,
        ):
            yield Path(streaming_dir), Path(traditional_dir)

    @pytest.mark.performance
    @pytest.mark.slow
    def test_compression_ratio_comparison(self, temp_dirs):
        streaming_dir, traditional_dir = temp_dirs

        scale_factor = 1.0

        try:
            streaming_generator = TPCDSDataGenerator(
                scale_factor=scale_factor,
                output_dir=streaming_dir,
                verbose=False,
                parallel=1,
                force_regenerate=True,
                compression_type="zstd",
                compress_data=True,
            )

            streaming_files = streaming_generator.generate()
            streaming_total_size = sum(
                f.stat().st_size for files in streaming_files.values() for f in files if f.exists()
            )

            traditional_generator = TPCDSDataGenerator(
                scale_factor=scale_factor,
                output_dir=traditional_dir,
                verbose=False,
                parallel=1,
                force_regenerate=True,
                compression_type="none",
                compress_data=False,
            )

            uncompressed_files = traditional_generator.generate()
            uncompressed_total_size = sum(
                f.stat().st_size for files in uncompressed_files.values() for f in files if f.exists()
            )

            traditional_generator.compress_data = True
            traditional_generator.compression_type = "zstd"
            compressed_files = {}
            traditional_compressed_size = 0

            for table_name, file_paths in uncompressed_files.items():
                for file_path in file_paths:
                    if file_path.exists():
                        compressed_file = traditional_generator.compress_existing_file(file_path)
                        compressed_files.setdefault(table_name, []).append(compressed_file)
                        traditional_compressed_size += compressed_file.stat().st_size

            if streaming_total_size > 0 and traditional_compressed_size > 0:
                size_difference = abs(streaming_total_size - traditional_compressed_size) / streaming_total_size

                assert size_difference < 0.05, (
                    f"Compression sizes differ by {size_difference:.2%}: streaming={streaming_total_size}, traditional={traditional_compressed_size}"
                )

                if uncompressed_total_size > 0:
                    streaming_ratio = uncompressed_total_size / streaming_total_size
                    traditional_ratio = uncompressed_total_size / traditional_compressed_size

                    print(
                        f"Compression ratios - Streaming: {streaming_ratio:.2f}:1, Traditional: {traditional_ratio:.2f}:1"
                    )
                    print(
                        f"Space savings - Streaming: {((uncompressed_total_size - streaming_total_size) / uncompressed_total_size * 100):.1f}%"
                    )

        except Exception as e:
            if "dsdgen" in str(e):
                pytest.skip(f"dsdgen not available: {e}")
            else:
                raise e

    @pytest.mark.performance
    @pytest.mark.slow
    def test_disk_space_usage_during_generation(self, temp_dirs):
        streaming_dir, traditional_dir = temp_dirs

        scale_factor = 1.0

        try:
            streaming_generator = TPCDSDataGenerator(
                scale_factor=scale_factor,
                output_dir=streaming_dir,
                verbose=False,
                parallel=1,
                force_regenerate=True,
                compression_type="zstd",
                compress_data=True,
            )

            streaming_files = streaming_generator.generate()
            streaming_final_size = sum(
                f.stat().st_size for files in streaming_files.values() for f in files if f.exists()
            )

            streaming_peak_usage = streaming_final_size

            traditional_generator = TPCDSDataGenerator(
                scale_factor=scale_factor,
                output_dir=traditional_dir,
                verbose=False,
                parallel=1,
                force_regenerate=True,
                compression_type="none",
                compress_data=False,
            )

            uncompressed_files = traditional_generator.generate()
            uncompressed_size = sum(
                f.stat().st_size for files in uncompressed_files.values() for f in files if f.exists()
            )

            estimated_compressed_size = uncompressed_size / 3
            traditional_peak_usage = uncompressed_size + estimated_compressed_size

            if streaming_peak_usage > 0 and traditional_peak_usage > 0:
                space_savings = (traditional_peak_usage - streaming_peak_usage) / traditional_peak_usage

                print(
                    f"Peak disk usage - Streaming: {streaming_peak_usage:,} bytes, Traditional: {traditional_peak_usage:,} bytes"
                )
                print(f"Space savings during generation: {space_savings:.1%}")

                assert streaming_peak_usage < traditional_peak_usage, "Streaming should use less peak disk space"
                assert space_savings > 0.5, f"Expected >50% space savings, got {space_savings:.1%}"

        except Exception as e:
            if "dsdgen" in str(e):
                pytest.skip(f"dsdgen not available: {e}")
            else:
                raise e

    def test_streaming_performance_characteristics(self):

        generator = TPCDSDataGenerator(scale_factor=1.0, compression_type="zstd", compress_data=True)

        assert hasattr(generator, "_run_streaming_dsdgen")
        assert hasattr(generator, "_generate_single_table_streaming")
        assert hasattr(generator, "_generate_parent_table_with_children")
        assert hasattr(generator, "_run_parallel_streaming_dsdgen")

        assert generator.should_use_compression()
        assert generator.get_compressed_filename("test.dat") == "test.dat.zst"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


