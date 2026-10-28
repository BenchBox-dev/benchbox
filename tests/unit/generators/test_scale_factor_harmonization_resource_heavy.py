from __future__ import annotations

import json

import pytest

from benchbox.core.flightdata.benchmark import FlightDataBenchmark
from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
]


def test_flightdata_benchmark_supports_compressed_generation(tmp_path):
    benchmark = FlightDataBenchmark(
        scale_factor=0.01,
        output_dir=tmp_path,
        seed=42,
        compress_data=True,
        compression_type="zstd",
    )

    paths = benchmark.generate_data()
    assert len(paths) == 3
    assert all(path.suffix == ".zst" for path in paths)

    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text())
    assert manifest["compression"]["enabled"] is True
    assert manifest["compression"]["type"] == "zstd"


def test_nyctaxi_benchmark_supports_compressed_generation(tmp_path, monkeypatch):
    def source_unreachable(url, destination, **kwargs):
        raise OSError("trip data source is not reachable from unit tests")

    monkeypatch.setattr("benchbox.core.nyctaxi.downloader.download_to_file", source_unreachable)
    benchmark = NYCTaxiBenchmark(
        scale_factor=0.01,
        output_dir=tmp_path,
        year=2019,
        months=[1],
        seed=42,
        compress_data=True,
        compression_type="zstd",
    )

    paths = benchmark.generate_data()
    assert len(paths) == 2
    assert all(path.suffix == ".zst" for path in paths)

    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text())
    assert manifest["compression"]["enabled"] is True
    assert manifest["compression"]["type"] == "zstd"
