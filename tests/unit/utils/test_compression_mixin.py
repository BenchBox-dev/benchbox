# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path

import pytest

from benchbox.utils.compression_mixin import CompressionMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class MockDataGenerator(CompressionMixin):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.output_dir = Path(kwargs.get("output_dir", Path.cwd()))


class TestCompressionMixin:
    def test_init_defaults(self):
        generator = MockDataGenerator()
        assert not generator.compress_data
        assert generator.compression_type == "none"
        assert generator.compression_level is None

    def test_init_compression_enabled(self):
        generator = MockDataGenerator(compress_data=True)
        assert generator.compress_data
        assert generator.compression_type == "zstd"

    def test_init_custom_compression(self):
        generator = MockDataGenerator(compress_data=True, compression_type="gzip", compression_level=9)
        assert generator.compress_data
        assert generator.compression_type == "gzip"
        assert generator.compression_level == 9

    def test_init_uncompressed_output(self):
        generator = MockDataGenerator(uncompressed_output=True)
        assert not generator.compress_data
        assert generator.compression_type == "none"
        assert generator.compression_level is None

    def test_init_uncompressed_output_overrides_compression(self):
        generator = MockDataGenerator(
            uncompressed_output=True,
            compress_data=True,
            compression_type="zstd",
            compression_level=5,
        )
        assert not generator.compress_data
        assert generator.compression_type == "none"
        assert generator.compression_level is None

    def test_init_invalid_compression_type(self):
        with pytest.raises(ValueError):
            MockDataGenerator(compression_type="invalid")

    def test_get_compressed_filename(self):
        generator = MockDataGenerator()
        assert generator.get_compressed_filename("test.txt") == "test.txt"

        generator = MockDataGenerator(uncompressed_output=True)
        assert generator.get_compressed_filename("test.txt") == "test.txt"

        generator = MockDataGenerator(compress_data=True, compression_type="gzip")
        assert generator.get_compressed_filename("test.txt") == "test.txt.gz"

    def test_should_use_compression(self):
        generator = MockDataGenerator()
        assert not generator.should_use_compression()

        generator = MockDataGenerator(uncompressed_output=True)
        assert not generator.should_use_compression()

        generator = MockDataGenerator(compress_data=True)
        assert generator.should_use_compression()

    def test_should_use_compression_with_gzip(self):
        generator = MockDataGenerator(compress_data=True, compression_type="gzip")
        assert generator.should_use_compression()

    def test_open_output_file_no_compression(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            generator = MockDataGenerator(uncompressed_output=True, output_dir=temp_dir)
            file_path = Path(temp_dir) / "test.txt"

            with generator.open_output_file(file_path, "w") as f:
                f.write("Hello, World!")

            assert file_path.exists()
            assert file_path.read_text() == "Hello, World!"

    def test_open_output_file_with_compression(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            generator = MockDataGenerator(compress_data=True, compression_type="gzip", output_dir=temp_dir)
            file_path = Path(temp_dir) / "test.txt"

            with generator.open_output_file(file_path, "wt") as f:
                f.write("Hello, World!")

            compressed_path = Path(temp_dir) / "test.txt.gz"
            assert compressed_path.exists()

            import gzip

            with gzip.open(compressed_path, "rt") as f:
                content = f.read()
                assert content == "Hello, World!"

    def test_compress_existing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            generator = MockDataGenerator(compress_data=True, compression_type="gzip", output_dir=temp_dir)

            test_file = Path(temp_dir) / "test.txt"
            test_file.write_text("Hello, World!")

            compressed_file = generator.compress_existing_file(test_file)
            assert compressed_file.exists()
            assert str(compressed_file).endswith(".gz")

    def test_get_compression_report_no_compression(self):
        generator = MockDataGenerator(uncompressed_output=True)
        files = {"test": Path("test.txt")}
        report = generator.get_compression_report(files)
        assert report == {}

    def test_get_compression_report_with_compression(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            generator = MockDataGenerator(compress_data=True, compression_type="gzip", output_dir=temp_dir)

            original_file = Path(temp_dir) / "test.txt"
            compressed_file = Path(temp_dir) / "test.txt.gz"

            test_data = "Hello, World!\n" * 100
            original_file.write_text(test_data)

            compressor = generator.get_compressor()
            compressor.compress_file(original_file, compressed_file)

            files = {"test": compressed_file}
            report = generator.get_compression_report(files)

            assert "test" in report
            assert "compression_ratio" in report["test"]
            assert report["test"]["compression_ratio"] > 1.0


