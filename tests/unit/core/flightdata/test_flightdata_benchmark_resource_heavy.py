from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import pytest

from benchbox.core.flightdata.benchmark import FlightDataBenchmark
from benchbox.core.flightdata.downloader import MONTHS_PER_SCALE_FACTOR, FlightDataDownloader

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
]


class TestDataGeneration:
    def test_generate_creates_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bm = FlightDataBenchmark(scale_factor=0.01, output_dir=tmpdir, seed=42)
            paths = bm.generate_data()

            assert len(paths) == 3
            for p in paths:
                assert Path(p).exists(), f"File not created: {p}"
                assert Path(p).stat().st_size > 0

    def test_generate_flights_has_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bm = FlightDataBenchmark(scale_factor=0.01, output_dir=tmpdir, seed=42)
            bm.generate_data()

            flights_path = Path(tmpdir) / "flights.csv"
            lines = flights_path.read_text().strip().splitlines()
            assert len(lines) > 1, "flights.csv should have rows beyond the header"

    def test_generate_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bm = FlightDataBenchmark(scale_factor=0.01, output_dir=tmpdir, seed=42)
            paths1 = bm.generate_data()
            paths2 = bm.generate_data()
            assert set(str(p) for p in paths1) == set(str(p) for p in paths2)

    def test_reference_data_airlines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bm = FlightDataBenchmark(scale_factor=0.01, output_dir=tmpdir, seed=42)
            bm.generate_data()

            airlines_path = Path(tmpdir) / "airlines.csv"
            header = airlines_path.read_text().splitlines()[0]
            assert "code" in header
            assert "name" in header

    def test_reference_data_airports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bm = FlightDataBenchmark(scale_factor=0.01, output_dir=tmpdir, seed=42)
            bm.generate_data()

            airports_path = Path(tmpdir) / "airports.csv"
            header = airports_path.read_text().splitlines()[0]
            assert "code" in header
            assert "latitude" in header

    def test_download_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bm = FlightDataBenchmark(scale_factor=0.01, output_dir=tmpdir, seed=42)
            bm.generate_data()
            stats = bm.get_download_stats()
            assert stats["total_flights"] > 0
            assert stats["scale_factor"] == 0.01

    def test_large_scale_generation_uses_manifest_tracked_flight_shards(self, tmp_path, monkeypatch):

        def fake_process_month(self, writer, year, month, start_id):
            writer.writerow(_flight_row(start_id, year, month))
            self._stats["downloaded_months"].append(f"{year}-{month:02d}")
            self._stats["months_downloaded"] += 1
            return 1

        monkeypatch.setattr(FlightDataDownloader, "_process_month", fake_process_month)
        downloader = FlightDataDownloader(scale_factor=1.0, output_dir=tmp_path, seed=42, verbose=1)
        downloader._num_months = MONTHS_PER_SCALE_FACTOR
        downloader._months = [(2024, 12), (2024, 11)]

        tables = downloader.download()

        flights = tables["flights"]
        assert isinstance(flights, list)
        assert len(flights) == 2
        assert not (tmp_path / "flights.csv").exists()
        assert all(path.parent == tmp_path / "flights" for path in flights)

        manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))
        flight_entries = manifest["tables"]["flights"]["formats"]["csv"]
        assert len(flight_entries) == 2
        assert [entry["row_count"] for entry in flight_entries] == [1, 1]
        assert manifest["format_preference"] == ["csv"]
        assert flight_entries[0]["metadata"] == {
            "csv_delimiter": ",",
            "csv_has_header": True,
            "csv_null_marker": "",
        }

        reused_downloader = FlightDataDownloader(scale_factor=1.0, output_dir=tmp_path, seed=42, verbose=1)
        reused_downloader._num_months = MONTHS_PER_SCALE_FACTOR
        reused_downloader._months = [(2024, 12), (2024, 11)]
        reused_downloader.download()

        reused_manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))
        reused_entries = reused_manifest["tables"]["flights"]["formats"]["csv"]
        assert [entry["row_count"] for entry in reused_entries] == [1, 1]

    def test_legacy_large_single_flight_file_is_split_and_removed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("benchbox.core.flightdata.downloader.FLIGHTS_SHARD_ROW_TARGET", 2)
        downloader = FlightDataDownloader(scale_factor=1.0, output_dir=tmp_path, seed=42, verbose=1)
        downloader._num_months = MONTHS_PER_SCALE_FACTOR

        legacy_path = tmp_path / "flights.csv"
        _write_csv(legacy_path, FlightDataDownloader._flight_header(), [_flight_row(i) for i in range(1, 6)])
        _write_csv(tmp_path / "airlines.csv", ["code", "name"], [["AA", "American"]])
        _write_csv(
            tmp_path / "airports.csv",
            ["code", "name", "city", "state", "latitude", "longitude"],
            [["ATL", "Atlanta", "Atlanta", "GA", "33.64", "-84.43"]],
        )

        repaired = downloader.repair_reusable_layout()

        assert repaired is not None
        assert not legacy_path.exists()
        flights = repaired["flights"]
        assert isinstance(flights, list)
        assert len(flights) == 3
        assert [path.name for path in flights] == ["flights_0001.csv", "flights_0002.csv", "flights_0003.csv"]
        for path in flights:
            assert path.read_text(encoding="utf-8").splitlines()[0].startswith("flight_id,flight_date")

        manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))
        entries = manifest["tables"]["flights"]["formats"]["csv"]
        assert [entry["row_count"] for entry in entries] == [2, 2, 1]

    def test_legacy_large_single_flight_file_with_bad_width_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("benchbox.core.flightdata.downloader.FLIGHTS_SHARD_ROW_TARGET", 2)
        downloader = FlightDataDownloader(scale_factor=1.0, output_dir=tmp_path, seed=42, verbose=1)
        downloader._num_months = MONTHS_PER_SCALE_FACTOR

        legacy_path = tmp_path / "flights.csv"
        _write_csv(legacy_path, FlightDataDownloader._flight_header(), [_flight_row(1)[:-1]])

        with pytest.raises(ValueError, match="row 2 has 27 columns; expected 28"):
            downloader.repair_reusable_layout()

        assert legacy_path.exists()
        assert not (tmp_path / "flights").exists()
        assert not (tmp_path / ".flights-shards.tmp").exists()

    def test_empty_legacy_large_single_flight_file_is_rejected(self, tmp_path):
        downloader = FlightDataDownloader(scale_factor=1.0, output_dir=tmp_path, seed=42, verbose=1)
        downloader._num_months = MONTHS_PER_SCALE_FACTOR
        legacy_path = tmp_path / "flights.csv"
        legacy_path.write_text("", encoding="utf-8")

        with pytest.raises(ValueError, match="is empty"):
            downloader.repair_reusable_layout()

        assert legacy_path.exists()
        assert not (tmp_path / "flights").exists()
        assert not (tmp_path / ".flights-shards.tmp").exists()

    def test_existing_flight_shard_with_bad_width_is_rejected(self, tmp_path, monkeypatch):

        def fake_process_month(self, writer, year, month, start_id):
            writer.writerow(_flight_row(start_id, year, month))
            self._stats["downloaded_months"].append(f"{year}-{month:02d}")
            self._stats["months_downloaded"] += 1
            return 1

        monkeypatch.setattr(FlightDataDownloader, "_process_month", fake_process_month)
        downloader = FlightDataDownloader(scale_factor=1.0, output_dir=tmp_path, seed=42, verbose=1)
        downloader._num_months = MONTHS_PER_SCALE_FACTOR
        downloader._months = [(2024, 12)]
        flights = downloader.download()["flights"]
        assert isinstance(flights, list)
        assert len(flights) == 1
        shard_path = flights[0]
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_before = manifest_path.read_bytes()
        assert json.loads(manifest_before)["source_contract_id"] == downloader.source_contract_id()

        _write_csv(shard_path, FlightDataDownloader._flight_header(), [_flight_row(1)[:-1]])
        malformed_shard = shard_path.read_bytes()
        reused_downloader = FlightDataDownloader(scale_factor=1.0, output_dir=tmp_path, seed=42, verbose=1)
        reused_downloader._num_months = MONTHS_PER_SCALE_FACTOR
        reused_downloader._months = [(2024, 12)]

        with pytest.raises(ValueError, match="row 2 has 27 columns; expected 28"):
            reused_downloader.download()

        assert manifest_path.read_bytes() == manifest_before
        assert shard_path.read_bytes() == malformed_shard


def _flight_row(flight_id: int, year: int = 2024, month: int = 12) -> list[str | int | float]:
    return [
        flight_id,
        f"{year}-{month:02d}-01",
        year,
        month,
        1,
        1,
        "AA",
        100,
        "ATL",
        "LAX",
        800,
        805.0,
        5.0,
        1000,
        1010.0,
        10.0,
        0,
        "",
        0,
        120.0,
        125.0,
        100.0,
        1946.0,
        "",
        "",
        "",
        "",
        "",
    ]


def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


