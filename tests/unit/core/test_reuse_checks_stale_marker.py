from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.datavault.benchmark import DataVaultBenchmark
from benchbox.core.flightdata.downloader import FlightDataDownloader
from benchbox.core.nyctaxi.downloader import NYCTaxiDataDownloader
from benchbox.core.tpcds_obt.benchmark import TPCDSOBTBenchmark
from benchbox.utils.generation_marker import GENERATION_IN_PROGRESS_MARKER

pytestmark = [pytest.mark.unit, pytest.mark.medium]

CONTRACT_ID = "contract-id"


def _vault(tmp_path: Path) -> DataVaultBenchmark:
    names = [f"table_{index}" for index in range(21)]
    for name in names:
        (tmp_path / f"{name}.tbl").write_text("1|row\n", encoding="utf-8")
    manifest = {
        "benchmark": "datavault",
        "scale_factor": 0.01,
        "tables": {name: [{"path": f"{name}.tbl", "size_bytes": 6, "row_count": 1}] for name in names},
    }
    (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
    return benchmark


def _vault_reuses(tmp_path: Path) -> bool:
    return _vault(tmp_path)._check_existing_manifest() is not None


def _obt_reuses(tmp_path: Path) -> bool:
    (tmp_path / "tpcds_sales_returns_obt.parquet").write_text("data", encoding="utf-8")
    (tmp_path / "tpcds_sales_returns_obt_manifest.json").write_text("{}", encoding="utf-8")
    benchmark = TPCDSOBTBenchmark.__new__(TPCDSOBTBenchmark)
    benchmark.output_dir = tmp_path
    benchmark.tables = {}
    benchmark._manifest_matches_model = lambda manifest_path: True
    return benchmark._existing_obt("parquet")


def _nyctaxi_reuses(tmp_path: Path) -> bool:
    (tmp_path / "trips.csv").write_text("trip_id\n", encoding="utf-8")
    downloader = NYCTaxiDataDownloader(scale_factor=0.01, output_dir=tmp_path, months=[1], compress_data=False)
    downloads: list[str] = []
    downloader._generate_taxi_zones = lambda: tmp_path / "taxi_zones.csv"
    downloader._read_persisted_contract_id = lambda path: CONTRACT_ID
    downloader.source_contract_id = lambda: CONTRACT_ID
    downloader._restore_persisted_provenance = lambda path: None
    downloader._write_contract_sidecar = lambda path: None

    def process(url, writer, start_trip_id):
        downloads.append(url)
        return 1

    downloader._process_parquet_file = process
    downloader.download()
    return not downloads


def _flightdata_reuses(tmp_path: Path) -> bool:
    (tmp_path / "airlines.csv").write_text("a\n", encoding="utf-8")
    (tmp_path / "airports.csv").write_text("a\n", encoding="utf-8")
    (tmp_path / "_datagen_manifest.json").write_text("{}", encoding="utf-8")
    downloader = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path, compress_data=False)
    copies: list[str] = []
    downloader._persisted_source_contract_id = lambda: CONTRACT_ID
    downloader.source_contract_id = lambda: CONTRACT_ID
    downloader.manifest_matches_source_identity = lambda manifest: True
    downloader._restore_persisted_provenance = lambda: None
    downloader._copy_reference_file = lambda name, target: copies.append(name)
    downloader._record_existing_csv_file = lambda name, path: None
    downloader._ensure_flights_data = lambda path: []
    downloader.download()
    return not copies


CHECKS = {
    "datavault": _vault_reuses,
    "tpcds_obt": _obt_reuses,
    "nyctaxi": _nyctaxi_reuses,
    "flightdata": _flightdata_reuses,
}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_complete_looking_files_are_reused_without_the_marker(tmp_path, name):
    assert CHECKS[name](tmp_path) is True


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_complete_looking_files_are_not_reused_beside_a_stale_marker(tmp_path, name):
    (tmp_path / GENERATION_IN_PROGRESS_MARKER).touch()

    assert CHECKS[name](tmp_path) is False
