# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path

import pytest

from benchbox.utils.compression import (
    ZSTD_AVAILABLE,
    CompressionError,
    CompressionManager,
    GzipCompressor,
    NoCompressor,
    ZstdCompressor,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCompressionManager:
    def setup_method(self):
        self.manager = CompressionManager()

    def test_get_available_compressors(self):
        available = self.manager.get_available_compressors()
        assert "none" in available
        assert "gzip" in available

        if ZSTD_AVAILABLE:
            assert "zstd" in available

    def test_get_compressor_gzip(self):
        compressor = self.manager.get_compressor("gzip")
        assert isinstance(compressor, GzipCompressor)
        assert compressor.level == 6

    def test_get_compressor_none(self):
        compressor = self.manager.get_compressor("none")
        assert isinstance(compressor, NoCompressor)

    @pytest.mark.skipif(not ZSTD_AVAILABLE, reason="zstandard not available")
    def test_get_compressor_zstd(self):
        compressor = self.manager.get_compressor("zstd")
        assert isinstance(compressor, ZstdCompressor)
        assert compressor.level == 3

    def test_get_compressor_with_level(self):
        compressor = self.manager.get_compressor("gzip", level=9)
        assert compressor.level == 9

    def test_get_compressor_invalid_type(self):
        with pytest.raises(CompressionError):
            self.manager.get_compressor("invalid")

    def test_detect_compression(self):
        assert self.manager.detect_compression(Path("test.txt")) == "none"
        assert self.manager.detect_compression(Path("test.txt.gz")) == "gzip"
        assert self.manager.detect_compression(Path("test.txt.zst")) == "zstd"


class TestGzipCompressor:
    def setup_method(self):
        self.compressor = GzipCompressor()

    def test_file_extension(self):
        assert self.compressor.get_file_extension() == ".gz"

    def test_invalid_level(self):
        with pytest.raises(ValueError):
            GzipCompressor(level=0)
        with pytest.raises(ValueError):
            GzipCompressor(level=10)

    def test_compress_decompress_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            test_data = "Hello, World!\n" * 1000
            input_file = temp_path / "test.txt"
            input_file.write_text(test_data)

            compressed_file = self.compressor.compress_file(input_file)
            assert compressed_file.exists()
            assert str(compressed_file).endswith(".gz")
            assert compressed_file.stat().st_size < input_file.stat().st_size

            decompressed_file = self.compressor.decompress_file(compressed_file)
            assert decompressed_file.exists()
            assert decompressed_file.read_text() == test_data

    def test_open_for_write_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            test_data = "Line 1\nLine 2\nLine 3\n"
            compressed_file = temp_path / "test.txt.gz"

            with self.compressor.open_for_write(compressed_file, "wt") as f:
                f.write(test_data)

            assert compressed_file.exists()

            with self.compressor.open_for_read(compressed_file, "rt") as f:
                read_data = f.read()

            assert read_data == test_data


class TestNoCompressor:
    def setup_method(self):
        self.compressor = NoCompressor()

    def test_file_extension(self):
        assert self.compressor.get_file_extension() == ""

    def test_compress_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            test_data = "Hello, World!"
            input_file = temp_path / "test.txt"
            input_file.write_text(test_data)

            output_file = temp_path / "compressed.txt"
            result_file = self.compressor.compress_file(input_file, output_file)

            assert result_file == output_file
            assert result_file.exists()
            assert result_file.read_text() == test_data


@pytest.mark.skipif(not ZSTD_AVAILABLE, reason="zstandard not available")
class TestZstdCompressor:
    def setup_method(self):
        self.compressor = ZstdCompressor()

    def test_file_extension(self):
        assert self.compressor.get_file_extension() == ".zst"

    def test_invalid_level(self):
        with pytest.raises(ValueError):
            ZstdCompressor(level=0)
        with pytest.raises(ValueError):
            ZstdCompressor(level=23)

    def test_compress_decompress_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            test_data = "Hello, World!\n" * 1000
            input_file = temp_path / "test.txt"
            input_file.write_text(test_data)

            compressed_file = self.compressor.compress_file(input_file)
            assert compressed_file.exists()
            assert str(compressed_file).endswith(".zst")
            assert compressed_file.stat().st_size < input_file.stat().st_size

            decompressed_file = self.compressor.decompress_file(compressed_file)
            assert decompressed_file.exists()
            assert decompressed_file.read_text() == test_data


