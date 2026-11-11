from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from benchbox.core.coffeeshop.generator import CoffeeShopDataGenerator
from benchbox.core.datavault.etl.transformer import DataVaultETLTransformer
from benchbox.core.flightdata.downloader import FlightDataDownloader
from benchbox.core.h2odb.generator import H2ODataGenerator
from benchbox.core.ssb.generator import SSBDataGenerator
from benchbox.core.tpcdi.generator.manifest import ManifestMixin
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.core.tpch_skew.generator import TPCHSkewDataGenerator
from benchbox.core.tpch_skew.skew_config import SkewConfiguration
from benchbox.core.transaction_primitives.generator import TransactionPrimitivesDataGenerator
from benchbox.core.write_primitives.generator import WritePrimitivesDataGenerator
from benchbox.utils.data_validation import BenchmarkDataValidator
from benchbox.utils.datagen_manifest import MANIFEST_FILENAME, require_manifest_files

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

Writer = Callable[[dict], None]


class _TpcdiHarness(ManifestMixin):
    scale_factor = 0.01
    max_workers = 1
    compression_type = "none"
    compression_level = None

    def should_use_compression(self) -> bool:
        return False


def _ssb(tmp_path: Path) -> Writer:
    gen = SSBDataGenerator(scale_factor=0.0001, output_dir=tmp_path)
    return lambda paths: gen._write_manifest(tmp_path, paths)


def _tpch_skew(tmp_path: Path) -> Writer:
    gen = TPCHSkewDataGenerator(scale_factor=0.01, output_dir=tmp_path, skew_config=SkewConfiguration())
    return gen._write_manifest


def _tpcdi(tmp_path: Path) -> Writer:
    return lambda paths: _TpcdiHarness()._write_manifest(tmp_path, {name: str(path) for name, path in paths.items()})


def _datavault(tmp_path: Path) -> Writer:
    transformer = DataVaultETLTransformer(scale_factor=0.01)
    return lambda paths: transformer._write_manifest(
        output_dir=tmp_path,
        table_paths=paths,
        table_row_counts=dict.fromkeys(paths, 1),
        output_format="tbl",
        load_timestamp=datetime(2026, 1, 1),
    )


def _flightdata(tmp_path: Path) -> Writer:
    return FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)._write_manifest


def _tpch(tmp_path: Path) -> Writer:
    gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
    return lambda paths: gen._write_manifest(tmp_path, paths)


def _write_primitives(tmp_path: Path) -> Writer:
    return WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)._write_manifest


def _transaction_primitives(tmp_path: Path) -> Writer:
    return TransactionPrimitivesDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)._write_manifest


def _scan(tmp_path: Path) -> Writer:
    validator = BenchmarkDataValidator("tpch", 0.01)
    return lambda paths: validator._write_manifest_from_scan(tmp_path)


_REJECTING_WRITERS = {
    "ssb": (_ssb, {}),
    "tpch_skew": (_tpch_skew, {}),
    "tpcdi": (_tpcdi, {}),
    "datavault": (_datavault, {}),
    "flightdata": (_flightdata, {}),
    "tpch": (_tpch, {"customer": []}),
    "write_primitives": (_write_primitives, {"customer": []}),
    "transaction_primitives": (_transaction_primitives, {"customer": []}),
    "data_validation_scan": (_scan, {}),
}


@pytest.mark.parametrize("name", sorted(_REJECTING_WRITERS))
def test_writer_refuses_a_manifest_without_table_files(name: str, tmp_path: Path) -> None:
    factory, empty_input = _REJECTING_WRITERS[name]
    writer = factory(tmp_path)

    with pytest.raises(RuntimeError, match="produced no table files"):
        writer(empty_input)

    assert not (tmp_path / MANIFEST_FILENAME).exists()


@pytest.mark.parametrize("name", sorted(_REJECTING_WRITERS))
def test_writer_still_records_generated_files(name: str, tmp_path: Path) -> None:
    factory, _ = _REJECTING_WRITERS[name]
    writer = factory(tmp_path)
    data_file = tmp_path / "customer.tbl"
    data_file.write_text("1|x|\n", encoding="utf-8")

    writer({"customer": data_file})

    manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert "customer" in manifest["tables"]


def _coffeeshop(tmp_path: Path) -> Writer:
    return CoffeeShopDataGenerator(scale_factor=0.000001, output_dir=tmp_path)._write_manifest


def _h2odb(tmp_path: Path) -> Writer:
    return H2ODataGenerator(scale_factor=1.0, output_dir=tmp_path, compression_enabled=False)._write_manifest


@pytest.mark.parametrize("factory", [_coffeeshop, _h2odb, _tpch], ids=["coffeeshop", "h2odb", "tpch"])
def test_writers_with_an_empty_table_mapping_write_no_manifest(
    factory: Callable[[Path], Writer], tmp_path: Path
) -> None:
    factory(tmp_path)({})

    assert not (tmp_path / MANIFEST_FILENAME).exists()


@pytest.mark.parametrize("file_count", [0, -1])
def test_require_manifest_files_rejects_non_positive_counts(file_count: int, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Demo data generation produced no table files in .*lists no tables"):
        require_manifest_files(file_count, label="Demo", output_dir=tmp_path)


def test_require_manifest_files_accepts_a_positive_count(tmp_path: Path) -> None:
    require_manifest_files(1, label="Demo", output_dir=tmp_path)


