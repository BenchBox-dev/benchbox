# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import gzip
from pathlib import Path

import pytest

from benchbox.utils.compression import CompressionError
from benchbox.utils.file_format import (
    COMPRESSION_EXTENSIONS,
    DATA_FORMAT_EXTENSIONS,
    detect_compression,
    detect_data_format,
    get_base_name_without_compression,
    get_data_extension,
    get_delimiter_for_file,
    has_trailing_delimiter,
    is_compression_extension,
    is_csv_format,
    is_data_format_extension,
    is_parquet_format,
    is_tpc_format,
    normalize_format_extension,
    strip_compression_suffix,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCompressionExtensionsConstant:
    def test_contains_all_expected_extensions(self):
        expected = {".zst", ".gz", ".bz2", ".xz", ".lz4", ".snappy"}
        assert expected == COMPRESSION_EXTENSIONS

    def test_is_frozenset(self):
        assert isinstance(COMPRESSION_EXTENSIONS, frozenset)


class TestDataFormatExtensionsConstant:
    def test_contains_all_expected_extensions(self):
        expected = {".parquet", ".vortex", ".tbl", ".csv", ".dat"}
        assert expected == DATA_FORMAT_EXTENSIONS

    def test_is_frozenset(self):
        assert isinstance(DATA_FORMAT_EXTENSIONS, frozenset)


class TestIsCompressionExtension:
    @pytest.mark.parametrize(
        "suffix",
        [".zst", ".gz", ".bz2", ".xz", ".lz4", ".snappy"],
    )
    def test_valid_extensions_with_dot(self, suffix):
        assert is_compression_extension(suffix) is True

    @pytest.mark.parametrize(
        "suffix",
        ["zst", "gz", "bz2", "xz", "lz4", "snappy"],
    )
    def test_valid_extensions_without_dot(self, suffix):
        assert is_compression_extension(suffix) is True

    @pytest.mark.parametrize(
        "suffix",
        [".ZST", ".GZ", ".BZ2", ".XZ", ".LZ4", ".SNAPPY"],
    )
    def test_case_insensitive(self, suffix):
        assert is_compression_extension(suffix) is True

    @pytest.mark.parametrize(
        "suffix",
        [".parquet", ".tbl", ".csv", ".txt", ".json", ""],
    )
    def test_non_compression_extensions(self, suffix):
        assert is_compression_extension(suffix) is False


class TestIsDataFormatExtension:
    @pytest.mark.parametrize(
        "suffix",
        [".parquet", ".vortex", ".tbl", ".csv", ".dat"],
    )
    def test_valid_extensions_with_dot(self, suffix):
        assert is_data_format_extension(suffix) is True

    @pytest.mark.parametrize(
        "suffix",
        ["parquet", "vortex", "tbl", "csv", "dat"],
    )
    def test_valid_extensions_without_dot(self, suffix):
        assert is_data_format_extension(suffix) is True

    @pytest.mark.parametrize(
        "suffix",
        [".PARQUET", ".VORTEX", ".TBL", ".CSV", ".DAT"],
    )
    def test_case_insensitive(self, suffix):
        assert is_data_format_extension(suffix) is True

    @pytest.mark.parametrize(
        "suffix",
        [".zst", ".gz", ".txt", ".json", ""],
    )
    def test_non_format_extensions(self, suffix):
        assert is_data_format_extension(suffix) is False


class TestDetectCompression:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("data.tbl.zst", "zstd"),
            ("data.csv.gz", "gzip"),
            ("archive.tar.bz2", "bzip2"),
            ("file.xz", "xz"),
            ("data.lz4", "lz4"),
            ("data.snappy", "snappy"),
        ],
    )
    def test_compressed_files(self, filename, expected):
        assert detect_compression(Path(filename)) == expected

    @pytest.mark.parametrize(
        "filename",
        [
            "data.parquet",
            "data.tbl",
            "data.csv",
            "file.txt",
            "noextension",
        ],
    )
    def test_uncompressed_files(self, filename):
        assert detect_compression(Path(filename)) is None

    def test_accepts_string_path(self):
        assert detect_compression("data.csv.gz") == "gzip"

    def test_case_insensitive(self):
        assert detect_compression(Path("data.ZST")) == "zstd"
        assert detect_compression(Path("data.GZ")) == "gzip"


class TestDetectDataFormat:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("data.parquet", "parquet"),
            ("data.tbl", "tbl"),
            ("data.csv", "csv"),
            ("customer.dat", "tbl"),
            ("data.tbl.zst", "tbl"),
            ("data.csv.gz", "csv"),
            ("data.parquet.lz4", "parquet"),
            ("customer.tbl.bz2", "tbl"),
            ("orders.csv.xz", "csv"),
            ("data.dat.snappy", "tbl"),
        ],
    )
    def test_format_detection(self, filename, expected):
        assert detect_data_format(Path(filename)) == expected

    def test_unknown_format_defaults_to_csv(self):
        assert detect_data_format(Path("data.txt")) == "csv"
        assert detect_data_format(Path("data.json")) == "csv"
        assert detect_data_format(Path("noextension")) == "csv"

    def test_accepts_string_path(self):
        assert detect_data_format("data.tbl.zst") == "tbl"

    def test_case_insensitive(self):
        assert detect_data_format(Path("data.TBL.ZST")) == "tbl"
        assert detect_data_format(Path("DATA.PARQUET")) == "parquet"

    def test_multiple_suffixes(self):
        assert detect_data_format(Path("data.backup.tbl")) == "tbl"
        assert detect_data_format(Path("file.1.csv")) == "csv"


class TestStripCompressionSuffix:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("data.tbl.zst", "data.tbl"),
            ("data.csv.gz", "data.csv"),
            ("archive.tar.bz2", "archive.tar"),
            ("file.xz", "file"),
            ("data.parquet.lz4", "data.parquet"),
            ("data.snappy", "data"),
        ],
    )
    def test_strips_compression_suffix(self, filename, expected):
        result = strip_compression_suffix(Path(filename))
        assert result == Path(expected)

    @pytest.mark.parametrize(
        "filename",
        [
            "data.parquet",
            "data.tbl",
            "data.csv",
            "noextension",
        ],
    )
    def test_preserves_non_compressed_paths(self, filename):
        path = Path(filename)
        result = strip_compression_suffix(path)
        assert result == path

    def test_accepts_string_path(self):
        result = strip_compression_suffix("data.tbl.zst")
        assert result == Path("data.tbl")

    def test_returns_path_object(self):
        result = strip_compression_suffix("data.tbl.zst")
        assert isinstance(result, Path)

    def test_case_insensitive(self):
        result = strip_compression_suffix(Path("data.ZST"))
        assert result == Path("data")


class TestGetBaseNameWithoutCompression:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/data/file.tbl.zst", "file.tbl"),
            ("file.csv.gz", "file.csv"),
            ("/path/to/data.parquet", "data.parquet"),
            ("nocompression.tbl", "nocompression.tbl"),
        ],
    )
    def test_extracts_base_name(self, path, expected):
        assert get_base_name_without_compression(path) == expected

    def test_accepts_path_object(self):
        result = get_base_name_without_compression(Path("/data/file.tbl.zst"))
        assert result == "file.tbl"


class TestNormalizeFormatExtension:
    @pytest.mark.parametrize(
        ("suffix", "expected"),
        [
            (".parquet", "parquet"),
            (".tbl", "tbl"),
            (".csv", "csv"),
            (".dat", "tbl"),
            ("parquet", "parquet"),
            ("TBL", "tbl"),
        ],
    )
    def test_normalizes_extensions(self, suffix, expected):
        assert normalize_format_extension(suffix) == expected

    def test_unknown_extension_defaults_to_csv(self):
        assert normalize_format_extension(".txt") == "csv"
        assert normalize_format_extension(".unknown") == "csv"


class TestEdgeCases:
    def test_empty_path(self):
        assert detect_data_format(Path("")) == "csv"
        assert detect_compression(Path("")) is None
        assert strip_compression_suffix(Path("")) == Path("")

    def test_only_compression_extension(self):
        assert detect_data_format(Path("file.zst")) == "csv"
        assert detect_compression(Path("file.zst")) == "zstd"
        assert strip_compression_suffix(Path("file.zst")) == Path("file")

    def test_hidden_files(self):
        assert detect_data_format(Path(".data.tbl.zst")) == "tbl"
        assert detect_compression(Path(".data.gz")) == "gzip"

    def test_deeply_nested_path(self):
        path = Path("/very/deep/nested/path/to/file.tbl.zst")
        assert detect_data_format(path) == "tbl"
        assert detect_compression(path) == "zstd"
        assert strip_compression_suffix(path) == Path("/very/deep/nested/path/to/file.tbl")

    def test_sharded_file_pattern(self):
        path = Path("customer.tbl.1.zst")
        assert detect_compression(path) == "zstd"
        assert detect_data_format(path) == "tbl"
        assert strip_compression_suffix(path) == Path("customer.tbl.1")

    def test_double_compression_extension(self):
        path = Path("data.gz.zst")
        assert detect_compression(path) == "zstd"
        stripped = strip_compression_suffix(path)
        assert stripped == Path("data.gz")


class TestIsTpcFormat:
    @pytest.mark.parametrize("path", ["data.tbl", Path("data.tbl")])
    def test_simple_tbl(self, path):
        assert is_tpc_format(path) is True

    @pytest.mark.parametrize("path", ["data.dat", Path("data.dat")])
    def test_simple_dat(self, path):
        assert is_tpc_format(path) is True

    @pytest.mark.parametrize(
        "path",
        ["data.tbl.zst", "data.tbl.gz", "data.tbl.bz2", "data.tbl.xz", "data.tbl.lz4"],
    )
    def test_compressed_tbl(self, path):
        assert is_tpc_format(path) is True

    @pytest.mark.parametrize("path", ["data.dat.zst", "data.dat.gz"])
    def test_compressed_dat(self, path):
        assert is_tpc_format(path) is True

    @pytest.mark.parametrize("path", ["data.csv", "data.parquet", "data.json", "data.txt"])
    def test_non_tpc_formats(self, path):
        assert is_tpc_format(path) is False

    def test_sharded_tpc_file(self):
        assert is_tpc_format("customer.tbl.1") is True
        assert is_tpc_format("customer.tbl.1.zst") is True

    def test_case_insensitive(self):
        assert is_tpc_format("data.TBL") is True
        assert is_tpc_format("data.DAT.ZST") is True


class TestIsParquetFormat:
    @pytest.mark.parametrize("path", ["data.parquet", Path("data.parquet")])
    def test_simple_parquet(self, path):
        assert is_parquet_format(path) is True

    @pytest.mark.parametrize(
        "path",
        ["data.parquet.zst", "data.parquet.gz", "data.parquet.bz2", "data.parquet.lz4"],
    )
    def test_compressed_parquet(self, path):
        assert is_parquet_format(path) is True

    @pytest.mark.parametrize("path", ["data.csv", "data.tbl", "data.dat", "data.json"])
    def test_non_parquet_formats(self, path):
        assert is_parquet_format(path) is False

    def test_sharded_parquet_file(self):
        assert is_parquet_format("data.parquet.1") is True
        assert is_parquet_format("data.parquet.1.zst") is True

    def test_case_insensitive(self):
        assert is_parquet_format("data.PARQUET") is True
        assert is_parquet_format("data.Parquet.ZST") is True

    def test_empty_path(self):
        assert is_parquet_format("") is False
        assert is_parquet_format(Path("")) is False


class TestIsCsvFormat:
    @pytest.mark.parametrize("path", ["data.csv", Path("data.csv")])
    def test_simple_csv(self, path):
        assert is_csv_format(path) is True

    @pytest.mark.parametrize(
        "path",
        ["data.csv.gz", "data.csv.zst", "data.csv.bz2", "data.csv.xz"],
    )
    def test_compressed_csv(self, path):
        assert is_csv_format(path) is True

    @pytest.mark.parametrize("path", ["data.parquet", "data.tbl", "data.dat", "data.json"])
    def test_non_csv_formats(self, path):
        assert is_csv_format(path) is False

    def test_case_insensitive(self):
        assert is_csv_format("data.CSV") is True
        assert is_csv_format("data.Csv.GZ") is True

    def test_empty_path(self):
        assert is_csv_format("") is False
        assert is_csv_format(Path("")) is False


class TestGetDelimiterForFile:
    @pytest.mark.parametrize(
        "path",
        ["lineitem.tbl", "store_sales.dat", "data.tbl.zst", "data.dat.gz"],
    )
    def test_tpc_format_returns_pipe(self, path):
        assert get_delimiter_for_file(path) == "|"

    @pytest.mark.parametrize(
        "path",
        ["data.csv", "data.parquet", "data.csv.gz", "data.json", "unknown.txt"],
    )
    def test_non_tpc_format_returns_comma(self, path):
        assert get_delimiter_for_file(path) == ","

    def test_accepts_path_object(self):
        assert get_delimiter_for_file(Path("lineitem.tbl")) == "|"
        assert get_delimiter_for_file(Path("data.csv")) == ","

    def test_sharded_tpc_file(self):
        assert get_delimiter_for_file("customer.tbl.1") == "|"
        assert get_delimiter_for_file("customer.tbl.1.zst") == "|"


class TestGetDataExtension:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("lineitem.tbl", ".tbl"),
            ("store_sales.dat", ".dat"),
            ("data.csv", ".csv"),
            ("data.parquet", ".parquet"),
            ("data.vortex", ".vortex"),
            ("store_sales.dat.zst", ".dat"),
            ("lineitem.tbl.gz", ".tbl"),
            ("data.csv.bz2", ".csv"),
            ("data.parquet.lz4", ".parquet"),
            ("data.vortex.xz", ".vortex"),
        ],
    )
    def test_basic_formats(self, filename, expected):
        assert get_data_extension(Path(filename)) == expected

    def test_numeric_shard_single_digit(self):
        assert get_data_extension(Path("customer.tbl.7.zst")) == ".tbl"

    def test_numeric_shard_multi_digit(self):
        assert get_data_extension(Path("customer.tbl.10.zst")) == ".tbl"

    def test_numeric_shard_uncompressed(self):
        assert get_data_extension(Path("customer.tbl.7")) == ".tbl"

    def test_multiple_compression_layers(self):
        assert get_data_extension(Path("hits.csv.gz.bz2")) == ".csv"

    def test_unknown_suffix_skipped(self):
        assert get_data_extension(Path("data.tbl.bak")) == ".tbl"

    def test_no_extension(self):
        assert get_data_extension(Path("README")) is None

    def test_only_compression_suffix(self):
        assert get_data_extension(Path("file.zst")) is None

    def test_unknown_suffix_only(self):
        assert get_data_extension(Path("file.txt")) is None

    def test_accepts_string_path(self):
        assert get_data_extension("lineitem.tbl.zst") == ".tbl"

    def test_case_insensitive(self):
        assert get_data_extension(Path("DATA.TBL.ZST")) == ".tbl"
        assert get_data_extension(Path("store_sales.DAT.GZ")) == ".dat"

    def test_dat_not_collapsed_to_tbl(self):
        assert get_data_extension(Path("store_sales.dat")) == ".dat"
        assert get_data_extension(Path("store_sales.dat")) != ".tbl"


class TestHasTrailingDelimiterFraming:
    FRAMING_CASES = [
        pytest.param(b"1|x|\r\n", True, id="crlf-trailing-delimiter"),
        pytest.param(b"1|x\r\n", False, id="crlf-clean"),
        pytest.param(b"1|x|\n", True, id="lf-trailing-delimiter"),
        pytest.param(b"1|x\n", False, id="lf-clean"),
    ]

    @pytest.mark.parametrize("raw,expected", FRAMING_CASES)
    def test_field_count_checker_handles_both_terminators(self, tmp_path, raw, expected):
        path = tmp_path / "region.tbl"
        path.write_bytes(raw)
        assert has_trailing_delimiter(path, "|", ["a", "b"]) is expected

    @pytest.mark.parametrize("raw,expected", FRAMING_CASES)
    def test_ends_with_checker_handles_both_terminators(self, tmp_path, raw, expected):
        path = tmp_path / "region.tbl"
        path.write_bytes(raw)
        assert has_trailing_delimiter(path, "|") is expected

    def test_classification_is_taken_from_the_first_row_of_a_multi_row_file(self, tmp_path):
        path = tmp_path / "region.tbl"
        path.write_bytes(b"0|AFRICA|comment|\r\n1|AMERICA|comment|\r\n2|ASIA|comment|\r\n")
        assert has_trailing_delimiter(path, "|", ["r_regionkey", "r_name", "r_comment"]) is True

    def test_leading_blank_lines_do_not_decide_the_framing(self, tmp_path):
        path = tmp_path / "region.tbl"
        path.write_bytes(b"\r\n\r\n0|AFRICA|comment|\r\n")
        assert has_trailing_delimiter(path, "|", ["r_regionkey", "r_name", "r_comment"]) is True

    @pytest.mark.parametrize("raw,expected", [(b"0|AFRICA|comment|\r\n", True), (b"0|AFRICA|comment\r\n", False)])
    def test_gzip_framing_uses_the_compressed_read_path(self, tmp_path, raw, expected):
        path = tmp_path / "region.tbl.gz"
        with gzip.open(path, "wb") as handle:
            handle.write(raw)

        assert has_trailing_delimiter(path, "|", ["r_regionkey", "r_name", "r_comment"]) is expected
        assert has_trailing_delimiter(path, "|") is expected


class TestHasTrailingDelimiterSurfacesReadFailures:
    def test_missing_file_raises_rather_than_reporting_clean(self, tmp_path):
        with pytest.raises(OSError):
            has_trailing_delimiter(tmp_path / "does-not-exist.tbl", "|", ["a", "b"])

    def test_unreadable_file_raises_rather_than_reporting_clean(self, tmp_path, monkeypatch):
        path = tmp_path / "locked.tbl"
        path.write_bytes(b"1|x|\n")

        def deny_open(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "open", deny_open)
        with pytest.raises(OSError):
            has_trailing_delimiter(path, "|", ["a", "b"])

    def test_a_directory_in_place_of_a_file_raises(self, tmp_path):
        target = tmp_path / "a_directory.tbl"
        target.mkdir()
        with pytest.raises(OSError):
            has_trailing_delimiter(target, "|", ["a", "b"])

    def test_unsupported_compression_raises_rather_than_reporting_clean(self, tmp_path):
        path = tmp_path / "region.tbl.bz2"
        path.write_bytes(b"not-decoded")
        with pytest.raises(CompressionError, match="Unsupported compression type 'bzip2'"):
            has_trailing_delimiter(path, "|", ["a", "b"])

    def test_an_empty_file_still_answers_false(self, tmp_path):
        path = tmp_path / "empty.tbl"
        path.write_bytes(b"")
        assert has_trailing_delimiter(path, "|", ["a", "b"]) is False

    def test_a_blank_line_only_file_still_answers_false(self, tmp_path):
        path = tmp_path / "blank.tbl"
        path.write_bytes(b"\n\r\n   \n")
        assert has_trailing_delimiter(path, "|", ["a", "b"]) is False


