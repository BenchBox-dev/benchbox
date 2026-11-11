from __future__ import annotations

import csv
import gzip
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.primitives.generator_base import PrimitivesDataGeneratorBase

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _TestableGenerator(PrimitivesDataGeneratorBase):
    _benchmark_name = "test_primitives"
    _display_name = "Test Primitives"
    _auxiliary_dir = "test_primitives_auxiliary"
    _logger_name = "benchbox.core.test_primitives.generator"


def _make_gen(tmp_path: Path) -> _TestableGenerator:
    return _TestableGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)


def _write_orders_tbl(output_dir: Path, rows: list[tuple] | None = None) -> Path:
    if rows is None:
        rows = [
            ("1", "370", "O", "172799.49", "1996-01-02", "5-LOW", "Clerk#000000951", "0", "comment"),
            ("2", "781", "O", "38426.09", "1996-12-01", "1-URGENT", "Clerk#000000880", "0", "remark"),
            ("3", "1234", "F", "205654.30", "1993-10-14", "5-LOW", "Clerk#000000955", "0", "note"),
        ]
    path = output_dir / "orders.tbl"
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write("|".join(str(v) for v in row) + "\n")
    return path


class TestGetTpchRowCount:
    def test_region_fixed(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        assert gen._get_tpch_row_count("region") == 5

    def test_nation_fixed(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        assert gen._get_tpch_row_count("nation") == 25

    def test_orders_scales(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        expected = round(1_500_000 * 0.01)
        assert gen._get_tpch_row_count("orders") == expected

    def test_unknown_table_returns_zero(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        assert gen._get_tpch_row_count("nonexistent") == 0

    def test_lineitem_scales(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        expected = round(6_001_215 * 0.01)
        assert gen._get_tpch_row_count("lineitem") == expected


class TestWriteStagingFile:
    def test_strips_trailing_delimiter(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        (tmp_path / "orders.tbl").write_bytes(b"1|hello|world|\n2|foo|bar|\n")
        path = gen._write_staging_file("orders_stage.tbl", "orders.tbl*")
        assert path.read_bytes() == b"1|hello|world\n2|foo|bar\n"

    def test_keeps_all_columns_without_trailing_delimiter(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        (tmp_path / "orders.tbl").write_bytes(b"1|hello\n2|world\n")
        path = gen._write_staging_file("orders_stage.tbl", "orders.tbl*")
        assert path.read_bytes() == b"1|hello\n2|world\n"

    def test_preserves_empty_fields_and_text(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        (tmp_path / "orders.tbl").write_bytes(b"1||007|0.10|\n")
        path = gen._write_staging_file("orders_stage.tbl", "orders.tbl*")
        assert path.read_bytes() == b"1||007|0.10\n"

    def test_limit_spans_shards(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        (tmp_path / "orders.tbl.1").write_bytes(b"1|a|\n2|b|\n")
        (tmp_path / "orders.tbl.2").write_bytes(b"3|c|\n4|d|\n")
        path = gen._write_staging_file("orders_stage.tbl", "orders.tbl*", limit=3)
        assert path.read_bytes() == b"1|a\n2|b\n3|c\n"

    def test_reads_gzip_source(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        with gzip.open(tmp_path / "orders.tbl.gz", "wb") as f:
            f.write(b"1|a|\n2|b|\n")
        path = gen._write_staging_file("orders_stage.tbl", "orders.tbl*")
        assert path.read_bytes() == b"1|a\n2|b\n"

    def test_reads_zstd_source(self, tmp_path: Path):
        import zstandard

        gen = _make_gen(tmp_path)
        (tmp_path / "orders.tbl.zst").write_bytes(zstandard.ZstdCompressor().compress(b"1|a|\n2|b|\n"))
        path = gen._write_staging_file("orders_stage.tbl", "orders.tbl*")
        assert path.read_bytes() == b"1|a\n2|b\n"

    def test_empty_source(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        (tmp_path / "orders.tbl").write_bytes(b"")
        path = gen._write_staging_file("orders_stage.tbl", "orders.tbl*")
        assert path.read_bytes() == b""


class TestReadTblFile:
    def test_reads_rows(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        _write_orders_tbl(tmp_path)
        rows = gen._read_tbl_file(tmp_path / "orders.tbl")
        assert len(rows) == 3
        assert rows[0][0] == "1"

    def test_respects_limit(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        _write_orders_tbl(tmp_path)
        rows = gen._read_tbl_file(tmp_path / "orders.tbl", limit=2)
        assert len(rows) == 2

    def test_strips_trailing_empty(self, tmp_path: Path):
        gen = _make_gen(tmp_path)

        path = tmp_path / "trailing.tbl"
        path.write_text("1|hello|\n2|world|\n")
        rows = gen._read_tbl_file(path)
        assert rows[0] == ("1", "hello")


class TestReadTblFiles:
    def test_reads_single_file(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        _write_orders_tbl(tmp_path)
        rows = gen._read_tbl_files("orders.tbl*")
        assert len(rows) == 3

    def test_reads_multiple_shards(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        for i in range(1, 4):
            shard = tmp_path / f"orders.tbl.{i}"
            shard.write_text(f"{i}|data|F|100.0|1996-01-01|1-URGENT|Clerk|0|comment\n")
        rows = gen._read_tbl_files("orders.tbl*")
        assert len(rows) == 3

    def test_reads_gzip_compressed(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        path = tmp_path / "orders.tbl.gz"
        with gzip.open(path, "wt") as f:
            f.write("1|data|F|100.0|1996-01-01|1-URGENT|Clerk|0|comment\n")
        rows = gen._read_tbl_files("orders.tbl*")
        assert len(rows) == 1


class TestParseTblStream:
    def test_parses_pipe_delimited(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        stream = io.StringIO("1|hello|world\n2|foo|bar\n")
        rows = gen._parse_tbl_stream(stream, limit=10)
        assert len(rows) == 2
        assert rows[0] == ("1", "hello", "world")

    def test_respects_limit(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        stream = io.StringIO("1|a\n2|b\n3|c\n")
        rows = gen._parse_tbl_stream(stream, limit=2)
        assert len(rows) == 2


class TestValidateFilename:
    def test_valid_filename(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        assert gen._validate_filename("test.csv") == "test.csv"

    def test_strips_directory_traversal(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        assert gen._validate_filename("../../../etc/passwd") == "passwd"

    def test_rejects_empty(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        with pytest.raises(ValueError, match="cannot be empty"):
            gen._validate_filename("")

    def test_rejects_null_byte(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        with pytest.raises(ValueError, match="null byte"):
            gen._validate_filename("test\x00.csv")

    def test_rejects_special_chars(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        with pytest.raises(ValueError, match="invalid characters"):
            gen._validate_filename("file with spaces.csv")

    def test_rejects_hidden_files(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        with pytest.raises(ValueError, match="Hidden files"):
            gen._validate_filename(".hidden")

    def test_allows_dotted_extensions(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        assert gen._validate_filename("data.csv.gz") == "data.csv.gz"


class TestWriteCsv:
    def test_writes_header_and_data(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        gen.files_dir.mkdir(parents=True, exist_ok=True)
        rows = [("1", "370", "O", "172799.49", "1996-01-02", "5-LOW", "Clerk", "0", "comment")]
        path = gen._write_csv("test.csv", rows)
        content = path.read_text()
        assert "o_orderkey" in content
        assert "172799.49" in content

    def test_validates_filename(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        gen.files_dir.mkdir(parents=True, exist_ok=True)
        with pytest.raises(ValueError, match="cannot be empty"):
            gen._write_csv("", [])


class TestCompressFile:
    def test_gzip_compression(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        source = tmp_path / "test.csv"
        source.write_text("hello,world\n")
        compressed = gen._compress_file(source, "gzip")
        assert compressed.suffix == ".gz"
        assert compressed.exists()

    def test_bzip2_compression(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        source = tmp_path / "test.csv"
        source.write_text("hello,world\n")
        compressed = gen._compress_file(source, "bzip2")
        assert compressed.suffix == ".bz2"
        assert compressed.exists()

    def test_unsupported_compression_raises(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        source = tmp_path / "test.csv"
        source.write_text("data\n")
        with pytest.raises(ValueError, match="Unsupported compression"):
            gen._compress_file(source, "lz4")


class TestCheckBulkLoadFilesExist:
    def test_returns_false_when_missing(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        assert gen.check_bulk_load_files_exist() is False

    def test_returns_false_when_scale_factor_mismatch(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        gen.files_dir.mkdir(parents=True, exist_ok=True)

        expected_files = [
            "csv_small_1k.csv",
            "csv_medium_100k.csv",
            "csv_large_1m.csv",
            "parquet_small_1k.parquet",
            "parquet_medium_100k.parquet",
            "parquet_large_1m.parquet",
            "csv_with_errors.csv",
            "csv_with_nulls.csv",
            "csv_parallel_part1.csv",
            "csv_parallel_part2.csv",
            "csv_parallel_part3.csv",
            "csv_parallel_part4.csv",
        ]
        for f in expected_files:
            (gen.files_dir / f).write_text("data")

        metadata = {"scale_factor": 999.0}
        with open(gen.files_dir / ".bulk_load_metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f)
        assert gen.check_bulk_load_files_exist() is False

    def test_returns_true_when_all_present(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        gen.files_dir.mkdir(parents=True, exist_ok=True)
        expected_files = [
            "csv_small_1k.csv",
            "csv_medium_100k.csv",
            "csv_large_1m.csv",
            "parquet_small_1k.parquet",
            "parquet_medium_100k.parquet",
            "parquet_large_1m.parquet",
            "csv_with_errors.csv",
            "csv_with_nulls.csv",
            "csv_parallel_part1.csv",
            "csv_parallel_part2.csv",
            "csv_parallel_part3.csv",
            "csv_parallel_part4.csv",
        ]
        for f in expected_files:
            (gen.files_dir / f).write_text("data")

        metadata = {"scale_factor": 0.01}
        with open(gen.files_dir / ".bulk_load_metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f)
        assert gen.check_bulk_load_files_exist() is True


class TestLockMechanism:
    def test_acquire_and_release_lock(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        gen.files_dir.mkdir(parents=True, exist_ok=True)
        assert gen._acquire_bulk_load_lock(timeout=5) is True
        lock_path = gen.files_dir / ".bulk_load_generation.lock"
        assert lock_path.exists()
        gen._release_bulk_load_lock()
        assert not lock_path.exists()

    def test_release_without_acquire_is_noop(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        gen._release_bulk_load_lock()

    def test_is_process_running_current_pid(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        assert gen._is_process_running(os.getpid()) is True

    def test_is_process_running_nonexistent_pid(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        assert gen._is_process_running(99999999) is False


class TestSpecialTestFiles:
    @pytest.fixture()
    def gen_with_files_dir(self, tmp_path: Path) -> _TestableGenerator:
        gen = _make_gen(tmp_path)
        gen.files_dir.mkdir(parents=True, exist_ok=True)
        return gen

    def test_error_and_null_files(self, gen_with_files_dir: _TestableGenerator):
        rows = [
            ("1", "370", "O", "172799.49", "1996-01-02", "5-LOW", "Clerk#000000951", "0", "comment"),
        ] * 200
        files = gen_with_files_dir._generate_error_and_null_files(rows)
        assert "csv_with_errors" in files
        assert "csv_with_nulls" in files
        assert "csv_upsert_data" in files
        for path in files.values():
            assert Path(path).exists()

    def test_format_variant_files(self, gen_with_files_dir: _TestableGenerator):
        rows = [
            ("1", "370", "O", "172799.49", "1996-01-02", "5-LOW", "Clerk#000000951", "0", "comment"),
        ] * 10
        files = gen_with_files_dir._generate_format_variant_files(rows)
        assert "csv_quoted_fields" in files
        assert "csv_custom_delim" in files
        assert "csv_utf8_encoded" in files

    def test_parallel_load_files(self, gen_with_files_dir: _TestableGenerator):
        rows = [
            ("1", "370", "O", "172799.49", "1996-01-02", "5-LOW", "Clerk#000000951", "0", "comment"),
        ] * 100
        files = gen_with_files_dir._generate_parallel_load_files(rows)
        assert len(files) == 4
        for key in ["csv_parallel_part1", "csv_parallel_part2", "csv_parallel_part3", "csv_parallel_part4"]:
            assert key in files


class TestGetDataSourceBenchmark:
    def test_returns_tpch(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        assert gen.get_data_source_benchmark() == "tpch"


class TestStagingTableFileGeneration:
    def test_generates_from_orders_data(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        _write_orders_tbl(tmp_path)
        result = gen._generate_staging_table_files()
        assert "orders_stage" in result
        assert Path(result["orders_stage"]).exists()

    def test_generates_lineitem_stage(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        _write_orders_tbl(tmp_path)

        lineitem = tmp_path / "lineitem.tbl"
        lineitem.write_text(
            "1|1|155190|1|17|21168.23|0.04|0.02|N|O|1996-03-13|1996-02-12|1996-03-22|DELIVER IN PERSON|TRUCK|comment\n"
        )
        result = gen._generate_staging_table_files()
        assert "lineitem_stage" in result

    def test_skips_when_no_orders(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        result = gen._generate_staging_table_files()
        assert "orders_stage" not in result


class TestBulkLoadMetadata:
    def test_writes_metadata_json(self, tmp_path: Path):
        gen = _make_gen(tmp_path)
        gen.files_dir.mkdir(parents=True, exist_ok=True)
        gen._write_bulk_load_metadata()
        metadata_file = gen.files_dir / ".bulk_load_metadata.json"
        assert metadata_file.exists()
        data = json.loads(metadata_file.read_text())
        assert data["scale_factor"] == 0.01
        assert "generated_at" in data


