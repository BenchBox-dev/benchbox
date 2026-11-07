from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.transaction_primitives.generator import TransactionPrimitivesDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def generator(tmp_path: Path) -> TransactionPrimitivesDataGenerator:
    gen = TransactionPrimitivesDataGenerator.__new__(TransactionPrimitivesDataGenerator)
    gen.files_dir = tmp_path / "aux"
    gen.files_dir.mkdir(parents=True, exist_ok=True)
    gen.output_dir = tmp_path
    gen.scale_factor = 1.0
    gen.parallel = 1
    gen.force_regenerate = False
    gen.log_verbose = lambda *_args, **_kwargs: None
    return gen


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("valid.csv", "valid.csv"),
        ("../unsafe.csv", "unsafe.csv"),
        ("nested/path/file.tbl", "file.tbl"),
    ],
)
def test_validate_filename_sanitizes_paths(
    generator: TransactionPrimitivesDataGenerator,
    filename: str,
    expected: str,
) -> None:
    assert generator._validate_filename(filename) == expected


@pytest.mark.parametrize(
    "filename",
    ["", "   ", "bad\x00name.csv", ".hidden", "file with spaces.csv", ".."],
)
def test_validate_filename_rejects_unsafe_inputs(
    generator: TransactionPrimitivesDataGenerator,
    filename: str,
) -> None:
    with pytest.raises(ValueError):
        generator._validate_filename(filename)


def test_get_tpch_row_count_scales_except_fixed_tables(generator: TransactionPrimitivesDataGenerator) -> None:
    generator.scale_factor = 0.1

    assert generator._get_tpch_row_count("region") == 5
    assert generator._get_tpch_row_count("nation") == 25
    assert generator._get_tpch_row_count("orders") == 150000
    assert generator._get_tpch_row_count("unknown") == 0


def test_parse_tbl_stream_removes_trailing_delimiter_and_honors_limit(
    generator: TransactionPrimitivesDataGenerator,
) -> None:
    content = "1|2|\n3|4|\n5|6|\n"
    rows = generator._parse_tbl_stream(content.splitlines(), limit=2)

    assert rows == [("1", "2"), ("3", "4")]


def test_read_tbl_file_reads_rows_with_trailing_delimiter(
    generator: TransactionPrimitivesDataGenerator, tmp_path: Path
) -> None:
    file_path = tmp_path / "orders.tbl"
    file_path.write_text("1|2|\n3|4|\n", encoding="utf-8")

    rows = generator._read_tbl_file(file_path, limit=10)

    assert rows == [("1", "2"), ("3", "4")]


def test_write_csv_creates_header_and_rows(generator: TransactionPrimitivesDataGenerator) -> None:
    path = generator._write_csv("sample.csv", [("1", "2", "O", "1.0", "1992-01-01", "1-URGENT", "Clerk#1", "0", "x")])

    lines = Path(path).read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("o_orderkey,o_custkey")
    assert lines[1].startswith("1,2,O,1.0")


def test_compress_file_supports_gzip_and_bzip2_and_rejects_unknown(
    generator: TransactionPrimitivesDataGenerator,
) -> None:
    src = generator.files_dir / "data.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")

    gz = generator._compress_file(src, "gzip")
    bz2 = generator._compress_file(src, "bzip2")

    assert gz.suffix == ".gz"
    assert bz2.suffix == ".bz2"
    assert gz.exists()
    assert bz2.exists()

    with pytest.raises(ValueError, match="Unsupported compression"):
        generator._compress_file(src, "zip")


def test_check_bulk_load_files_exist_validates_required_files_and_scale(
    generator: TransactionPrimitivesDataGenerator,
) -> None:
    assert not generator.check_bulk_load_files_exist()

    for name in [
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
    ]:
        (generator.files_dir / name).write_text("x", encoding="utf-8")

    metadata = generator.files_dir / ".bulk_load_metadata.json"
    metadata.write_text(json.dumps({"scale_factor": 1.0}), encoding="utf-8")

    assert generator.check_bulk_load_files_exist()

    metadata.write_text(json.dumps({"scale_factor": 2.0}), encoding="utf-8")
    assert not generator.check_bulk_load_files_exist()


def test_write_bulk_load_metadata_writes_json(generator: TransactionPrimitivesDataGenerator) -> None:
    (generator.files_dir / "dummy.txt").write_text("x", encoding="utf-8")

    generator._write_bulk_load_metadata()

    metadata_path = generator.files_dir / ".bulk_load_metadata.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert data["scale_factor"] == 1.0
    assert data["file_count"] >= 1
    assert "generated_at" in data


def test_write_staging_file_without_compression(generator: TransactionPrimitivesDataGenerator) -> None:
    generator.tpch_generator = SimpleNamespace(should_use_compression=lambda: False)
    (generator.output_dir / "orders.tbl").write_text("1|2|\n3|4|\n", encoding="utf-8")

    output = generator._write_staging_file("orders_stage.tbl", "orders.tbl*")

    assert Path(output).exists()
    assert Path(output).read_text(encoding="utf-8").splitlines() == ["1|2", "3|4"]


def test_write_staging_file_with_compression(generator: TransactionPrimitivesDataGenerator) -> None:
    compressed_path = generator.output_dir / "orders_stage.tbl.gz"
    (generator.output_dir / "orders.tbl").write_text("1|2|\n", encoding="utf-8")

    class Compressor:
        def compress_file(self, source_path: Path) -> Path:
            compressed_path.write_bytes(source_path.read_bytes())
            return compressed_path

    generator.tpch_generator = SimpleNamespace(should_use_compression=lambda: True, get_compressor=lambda: Compressor())

    output = generator._write_staging_file("orders_stage.tbl", "orders.tbl*")

    assert output == compressed_path
    assert compressed_path.exists()
    assert not (generator.output_dir / "orders_stage.tbl").exists()


def test_generate_staging_table_files_with_source_tables(
    generator: TransactionPrimitivesDataGenerator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (generator.output_dir / "orders.tbl").write_text("1|2|\n", encoding="utf-8")
    (generator.output_dir / "lineitem.tbl").write_text("1|2|\n", encoding="utf-8")

    monkeypatch.setattr(generator, "_write_staging_file", lambda filename, _pattern: generator.output_dir / filename)

    result = generator._generate_staging_table_files()

    assert result["orders_stage"].name == "orders_stage.tbl"
    assert result["lineitem_stage"].name == "lineitem_stage.tbl"


def test_write_manifest_skips_empty_staging_tables(
    generator: TransactionPrimitivesDataGenerator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchbox.core.transaction_primitives.generator as gen_mod

    added_entries: list[tuple[str, int]] = []
    wrote = {"value": False}

    class FakeManifest:
        def __init__(self, **_kwargs) -> None:
            pass

        def add_entry(self, table_name: str, _file_path: Path, row_count: int) -> None:
            added_entries.append((table_name, row_count))

        def write(self) -> None:
            wrote["value"] = True

    monkeypatch.setattr(gen_mod, "DataGenerationManifest", FakeManifest)
    monkeypatch.setattr(gen_mod, "resolve_compression_metadata", lambda _tpch: {"type": "none"})
    generator.tpch_generator = SimpleNamespace(seed=7)
    monkeypatch.setattr(generator, "_get_tpch_row_count", lambda name: {"orders": 10, "region": 5}.get(name, 1))

    generator._write_manifest(
        {
            "orders": generator.output_dir / "orders.tbl",
            "orders_new": generator.output_dir / "orders_new.tbl",
            "region": generator.output_dir / "region.tbl",
        }
    )

    assert ("orders", 10) in added_entries
    assert ("region", 5) in added_entries
    assert all(name != "orders_new" for name, _ in added_entries)
    assert wrote["value"]


def test_write_manifest_writes_entries_and_file(
    generator: TransactionPrimitivesDataGenerator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchbox.core.transaction_primitives.generator as gen_mod

    entries_added: list[tuple[str, Path, int]] = []
    wrote = {"value": False}

    class FakeManifest:
        def __init__(self, **_kwargs) -> None:
            pass

        def add_entry(self, table_name: str, file_path: Path, row_count: int) -> None:
            entries_added.append((table_name, file_path, row_count))

        def write(self) -> None:
            wrote["value"] = True

    monkeypatch.setattr(gen_mod, "DataGenerationManifest", FakeManifest)
    monkeypatch.setattr(gen_mod, "resolve_compression_metadata", lambda _tpch: {"type": "none"})
    generator.tpch_generator = SimpleNamespace(seed=7)
    monkeypatch.setattr(generator, "_get_tpch_row_count", lambda _name: 10)

    orders_path = generator.output_dir / "orders.tbl"
    generator._write_manifest({"orders": orders_path})

    assert wrote["value"], "Manifest.write() must be called"
    assert len(entries_added) == 1
    assert entries_added[0] == ("orders", orders_path, 10)


def test_lock_acquire_and_release(generator: TransactionPrimitivesDataGenerator) -> None:
    assert generator._acquire_bulk_load_lock(timeout=1)
    assert (generator.files_dir / ".bulk_load_generation.lock").exists()
    generator._release_bulk_load_lock()
    assert not (generator.files_dir / ".bulk_load_generation.lock").exists()


def test_is_process_running_handles_oserror(
    generator: TransactionPrimitivesDataGenerator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_oserror(_pid: int, _sig: int) -> None:
        raise OSError("no such process")

    monkeypatch.setattr("os.kill", raise_oserror)
    assert not generator._is_process_running(999999)


def test_generate_combines_tables_and_writes_manifest(
    generator: TransactionPrimitivesDataGenerator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_tables = {"orders": generator.output_dir / "orders.tbl"}
    stage_tables = {"orders_stage": generator.output_dir / "orders_stage.tbl"}
    wrote_manifest: dict[str, dict[str, Path]] = {}

    generator.tpch_generator = SimpleNamespace(generate=lambda: base_tables)
    monkeypatch.setattr(generator, "_generate_staging_table_files", lambda: stage_tables)
    monkeypatch.setattr(generator, "check_bulk_load_files_exist", lambda: True)
    monkeypatch.setattr(generator, "_write_manifest", lambda tables: wrote_manifest.update({"tables": tables}))

    result = generator.generate()

    assert result == {**base_tables, **stage_tables}
    assert wrote_manifest["tables"] == result


def test_generate_bulk_load_files_returns_empty_without_orders(
    generator: TransactionPrimitivesDataGenerator,
) -> None:
    assert generator.generate_bulk_load_files() == {}


def test_generate_special_test_files_creates_expected_outputs(
    generator: TransactionPrimitivesDataGenerator,
) -> None:
    rows = [("1", "2", "O", "1.0", "1992-01-01", "1-URGENT", "Clerk#1", "0", "comment")] * 120

    created = generator._generate_special_test_files(rows)

    expected_keys = {
        "csv_with_errors",
        "csv_with_nulls",
        "csv_quoted_fields",
        "csv_custom_delim",
        "csv_custom_dates",
        "csv_utf8_encoded",
        "csv_upsert_data",
        "csv_parallel_part1",
        "csv_parallel_part2",
        "csv_parallel_part3",
        "csv_parallel_part4",
    }
    assert expected_keys.issubset(set(created))
    assert all(path.exists() for path in created.values())


def test_get_data_source_benchmark(generator: TransactionPrimitivesDataGenerator) -> None:
    assert generator.get_data_source_benchmark() == "tpch"
