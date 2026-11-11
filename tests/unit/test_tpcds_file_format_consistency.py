from pathlib import Path

import pytest

from benchbox.core.tpcds.generator import TPCDSDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_generator(compression_enabled: bool, verbose: bool = False) -> TPCDSDataGenerator:
    generator = object.__new__(TPCDSDataGenerator)
    generator.verbose = verbose
    generator.compression_type = "zstd" if compression_enabled else "none"
    generator.should_use_compression = lambda: compression_enabled

    if compression_enabled:
        generator.get_compressor = lambda: type(
            "Compressor", (), {"get_file_extension": staticmethod(lambda: ".dat.zst")}
        )()
    else:
        generator.get_compressor = lambda: type("Compressor", (), {"get_file_extension": staticmethod(lambda: "")})()

    generator._is_valid_data_file = lambda p: p.stat().st_size > 0
    return generator


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_compressed_streams_disallow_raw_dat_files(temp_dir: Path):
    generator = _make_generator(compression_enabled=True)

    for name in ("table1.dat.zst", "table2.dat.zst"):
        (temp_dir / name).write_bytes(b"compressed")

    generator._validate_file_format_consistency(temp_dir)

    (temp_dir / "bad_table.dat").write_text("raw data")
    with pytest.raises(RuntimeError, match="raw .dat files when compression is enabled"):
        generator._validate_file_format_consistency(temp_dir)


def test_uncompressed_streams_only_dat_files(temp_dir: Path):
    generator = _make_generator(compression_enabled=False)

    for name in ("table1.dat", "table2.dat"):
        (temp_dir / name).write_text("rows")

    generator._validate_file_format_consistency(temp_dir)

    dat_files = list(temp_dir.glob("*.dat"))
    assert dat_files


def test_prune_stale_table_artifacts_removes_conflicting_variants_only(temp_dir: Path):
    generator = _make_generator(compression_enabled=True)
    generator._known_table_names = lambda: ["store_sales"]
    generator.parallel = 1

    stale_files = [
        temp_dir / "store_sales.dat",
        temp_dir / "store_sales.dat.zst",
        temp_dir / "store_sales_1_2.dat",
        temp_dir / "store_sales_1_2.dat.zst",
    ]
    for path in stale_files:
        path.write_text("stale")
    keep_file = temp_dir / "_datagen_manifest.json"
    keep_file.write_text("{}")

    removed = generator._prune_stale_table_artifacts(temp_dir)

    assert "store_sales.dat" in {p.name for p in removed}
    assert "store_sales_1_2.dat" in {p.name for p in removed}
    assert "store_sales_1_2.dat.zst" in {p.name for p in removed}
    assert not (temp_dir / "store_sales.dat").exists()
    assert not (temp_dir / "store_sales_1_2.dat").exists()
    assert not (temp_dir / "store_sales_1_2.dat.zst").exists()
    assert (temp_dir / "store_sales.dat.zst").exists()
    assert keep_file.exists()


def test_prune_stale_table_artifacts_keeps_prefix_neighbor_table_shards(temp_dir: Path):
    generator = _make_generator(compression_enabled=True)
    generator._known_table_names = lambda: ["customer", "customer_address"]
    generator.parallel = 2

    (temp_dir / "customer_1_2.dat").write_text("stale")
    keep_neighbor = temp_dir / "customer_address_1_2.dat.zst"
    keep_neighbor.write_text("valid")

    removed = generator._prune_stale_table_artifacts(temp_dir)

    assert "customer_1_2.dat" in {p.name for p in removed}
    assert keep_neighbor.exists()


def test_prune_stale_table_artifacts_removes_compressed_when_uncompressed_mode(temp_dir: Path):
    generator = _make_generator(compression_enabled=False)
    generator._known_table_names = lambda: ["store_sales"]
    generator.parallel = 1

    stale_compressed = temp_dir / "store_sales.dat.zst"
    stale_compressed.write_text("stale")

    removed = generator._prune_stale_table_artifacts(temp_dir)

    assert "store_sales.dat.zst" in {p.name for p in removed}
    assert not stale_compressed.exists()


