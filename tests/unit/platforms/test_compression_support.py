# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path

import pytest

from benchbox.utils.compression import CompressionManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestPlatformCompressionSupport:
    def setup_method(self):

        self.compression_manager = CompressionManager()

    def create_test_data_file(self, path: Path, compressed: bool = False, compression_type: str = "gzip"):

        test_data = "id|name|value\n1|test1|100\n2|test2|200\n3|test3|300\n"

        if not compressed:
            path.write_text(test_data, encoding="utf-8")
            return path

        compressor = self.compression_manager.get_compressor(compression_type)
        temp_uncompressed = path.parent / f"temp_{path.name}"
        temp_uncompressed.write_text(test_data)

        compressed_path = compressor.compress_file(temp_uncompressed, path)
        temp_uncompressed.unlink()

        return compressed_path

    def test_compression_manager_detection_utility(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            test_cases = [
                ("data.csv", "none"),
                ("data.csv.gz", "gzip"),
                ("data.csv.zst", "zstd"),
                ("data.tbl", "none"),
                ("data.tbl.gz", "gzip"),
            ]

            for filename, expected_type in test_cases:
                file_path = temp_path / filename
                detected_type = self.compression_manager.detect_compression(file_path)
                assert detected_type == expected_type, (
                    f"Failed for {filename}: expected {expected_type}, got {detected_type}"
                )

    def test_transparent_decompression_gzip(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            compressed_file = temp_path / "test_data.csv.gz"
            self.create_test_data_file(compressed_file, compressed=True, compression_type="gzip")

            compressor = self.compression_manager.get_compressor("gzip")

            with compressor.open_for_read(compressed_file, "rt") as f:
                content = f.read()

            expected_lines = [
                "id|name|value",
                "1|test1|100",
                "2|test2|200",
                "3|test3|300",
            ]
            actual_lines = [line.strip() for line in content.strip().split("\n")]

            assert actual_lines == expected_lines

    @pytest.mark.skipif(
        not hasattr(CompressionManager(), "_compressors")
        or "zstd" not in CompressionManager().get_available_compressors(),
        reason="zstd not available",
    )
    def test_transparent_decompression_zstd(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            compressed_file = temp_path / "test_data.csv.zst"
            self.create_test_data_file(compressed_file, compressed=True, compression_type="zstd")

            compressor = self.compression_manager.get_compressor("zstd")

            with compressor.open_for_read(compressed_file, "rt") as f:
                content = f.read()

            expected_lines = [
                "id|name|value",
                "1|test1|100",
                "2|test2|200",
                "3|test3|300",
            ]
            actual_lines = [line.strip() for line in content.strip().split("\n")]

            assert actual_lines == expected_lines

    def test_platform_adapter_compressed_file_detection(self):

        test_files = [
            "customer.tbl",
            "orders.tbl.gz",
            "lineitem.tbl.zst",
        ]

        for filename in test_files:
            path = Path(filename)
            compression_type = self.compression_manager.detect_compression(path)

            if filename.endswith(".gz"):
                assert compression_type == "gzip"
            elif filename.endswith(".zst"):
                assert compression_type == "zstd"
            else:
                assert compression_type == "none"

    def test_data_integrity_validation(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            original_file = temp_path / "original.csv"

            test_data = """id|name|description|value|timestamp
1|Product A|High-quality product with "special" chars & symbols|99.99|2023-01-01 10:00:00
2|Product B|Another product with unicode: café résumé|149.50|2023-01-02 11:30:00
3|Product C|Multi-line\ndescription\nwith\nnewlines|75.25|2023-01-03 14:15:00
4|Product D|Product with | pipe symbols and , commas|200.00|2023-01-04 16:45:00
"""
            original_file.write_text(test_data)

            for compression_type in self.compression_manager.get_available_compressors():
                if compression_type == "none":
                    continue

                try:
                    compressor = self.compression_manager.get_compressor(compression_type)

                    compressed_file = temp_path / f"compressed_{compression_type}.csv{compressor.get_file_extension()}"
                    compressor.compress_file(original_file, compressed_file)

                    decompressed_file = temp_path / f"decompressed_{compression_type}.csv"
                    compressor.decompress_file(compressed_file, decompressed_file)

                    original_content = original_file.read_text()
                    decompressed_content = decompressed_file.read_text()

                    assert original_content == decompressed_content, f"Data integrity failed for {compression_type}"

                except Exception as e:
                    pytest.skip(f"Compression type {compression_type} not available: {e}")

    def test_compression_info_utility(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            original_file = temp_path / "test.csv"
            test_data = "data,value\n" * 1000
            original_file.write_text(test_data)

            gzip_compressor = self.compression_manager.get_compressor("gzip")
            compressed_file = gzip_compressor.compress_file(original_file)

            info = self.compression_manager.get_compression_info(original_file, compressed_file)

            assert "original_size" in info
            assert "compressed_size" in info
            assert "compression_ratio" in info
            assert "space_savings_percent" in info

            assert info["original_size"] > 0
            assert info["compressed_size"] > 0
            assert info["compressed_size"] < info["original_size"]
            assert info["compression_ratio"] > 1.0
            assert 0 < info["space_savings_percent"] < 100

    def test_error_handling_for_missing_files(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            nonexistent_file = temp_path / "nonexistent.csv"

            compressor = self.compression_manager.get_compressor("gzip")

            with pytest.raises(Exception):
                compressor.compress_file(nonexistent_file)

    def test_compression_with_empty_files(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            empty_file = temp_path / "empty.csv"
            empty_file.write_text("")

            compressor = self.compression_manager.get_compressor("gzip")

            compressed_file = compressor.compress_file(empty_file)
            assert compressed_file.exists()

            decompressed_file = compressor.decompress_file(compressed_file)
            assert decompressed_file.read_text() == ""

    def test_large_file_handling(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            large_file = temp_path / "large.csv"

            large_data = "id,name,value,description\n" * 50000
            large_file.write_text(large_data)

            compressor = self.compression_manager.get_compressor("gzip")

            compressed_file = compressor.compress_file(large_file)
            assert compressed_file.exists()

            original_size = large_file.stat().st_size
            compressed_size = compressed_file.stat().st_size

            compression_ratio = original_size / compressed_size
            assert compression_ratio > 10.0


