# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import csv
import tempfile
from pathlib import Path

import pytest

from benchbox.core.nyctaxi.downloader import (
    TAXI_ZONES_DATA,
    NYCTaxiDataDownloader,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestDownloaderConfiguration:
    def test_default_configuration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = NYCTaxiDataDownloader(output_dir=tmpdir)
            assert downloader.scale_factor == 1.0
            assert downloader.year == 2019
            assert downloader.months == list(range(1, 13))

    def test_scale_factor_affects_sample_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dl1 = NYCTaxiDataDownloader(scale_factor=1.0, output_dir=tmpdir)
            dl2 = NYCTaxiDataDownloader(scale_factor=10.0, output_dir=tmpdir)
            assert dl2.sample_rate > dl1.sample_rate

    def test_max_sample_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = NYCTaxiDataDownloader(scale_factor=1000.0, output_dir=tmpdir)
            assert downloader.sample_rate <= 1.0

    def test_custom_year(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = NYCTaxiDataDownloader(year=2020, output_dir=tmpdir)
            assert downloader.year == 2020

    def test_custom_months(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = NYCTaxiDataDownloader(months=[1, 2, 3], output_dir=tmpdir)
            assert downloader.months == [1, 2, 3]

    def test_seed_for_reproducibility(self, seed):
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = NYCTaxiDataDownloader(seed=seed, output_dir=tmpdir)
            assert downloader.seed == seed


class TestTaxiZonesData:
    def test_has_zones(self):
        assert len(TAXI_ZONES_DATA) > 0

    def test_zone_structure(self):
        for zone in TAXI_ZONES_DATA:
            assert len(zone) == 4
            assert isinstance(zone[0], int)
            assert isinstance(zone[1], str)
            assert isinstance(zone[2], str)
            assert isinstance(zone[3], str)

    def test_popular_zones_exist(self):
        zone_ids = {z[0] for z in TAXI_ZONES_DATA}
        popular = [132, 138, 161, 162, 163, 164, 186, 230, 234, 236, 237, 239]
        for zone_id in popular:
            assert zone_id in zone_ids, f"Zone {zone_id} not in data"


class TestTaxiZonesGeneration:
    @pytest.fixture
    def downloader(self, seed):
        tmpdir = tempfile.mkdtemp()
        dl = NYCTaxiDataDownloader(
            scale_factor=0.01,
            output_dir=tmpdir,
            year=2019,
            months=[1],
            seed=seed,
        )
        yield dl
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_generates_taxi_zones_csv(self, downloader):
        path = downloader._generate_taxi_zones()
        assert path.exists()
        assert path.name == "taxi_zones.csv"

    def test_taxi_zones_csv_content(self, downloader):
        path = downloader._generate_taxi_zones()

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == len(TAXI_ZONES_DATA)
        assert "location_id" in rows[0]
        assert "borough" in rows[0]
        assert "zone" in rows[0]


class TestSyntheticDataGeneration:
    @pytest.fixture
    def downloader(self, seed):
        tmpdir = tempfile.mkdtemp()
        dl = NYCTaxiDataDownloader(
            scale_factor=0.01,
            output_dir=tmpdir,
            year=2019,
            months=[1],
            seed=seed,
        )
        yield dl
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_synthetic_generation_produces_rows(self, downloader):
        output_path = downloader.output_dir / "test_trips.csv"

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["trip_id", "vendor_id", "pickup_datetime", "total_amount"])
            count = downloader._generate_synthetic_month(writer, 0)

        assert count > 0
        assert output_path.exists()

    def test_synthetic_data_has_expected_columns(self, downloader):
        output_path = downloader.output_dir / "test_trips.csv"

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            header = [
                "trip_id",
                "vendor_id",
                "pickup_datetime",
                "dropoff_datetime",
                "passenger_count",
                "trip_distance",
                "pickup_location_id",
                "dropoff_location_id",
                "rate_code_id",
                "store_and_fwd_flag",
                "payment_type",
                "fare_amount",
                "extra",
                "mta_tax",
                "tip_amount",
                "tolls_amount",
                "improvement_surcharge",
                "congestion_surcharge",
                "airport_fee",
                "total_amount",
            ]
            writer.writerow(header)
            downloader._generate_synthetic_month(writer, 0)

        with open(output_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
            assert "pickup_datetime" in row
            assert "total_amount" in row


class TestDataDownload:
    @pytest.fixture(scope="class")
    def downloader_with_data(self):
        tmpdir = tempfile.mkdtemp()
        dl = NYCTaxiDataDownloader(
            scale_factor=0.01,
            output_dir=tmpdir,
            year=2019,
            months=[1],
            seed=42,
            force_redownload=True,
        )
        result = dl.download()
        yield dl, result
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_download_returns_table_files(self, downloader_with_data):
        downloader, result = downloader_with_data
        assert isinstance(result, dict)
        assert "taxi_zones" in result
        assert "trips" in result

    def test_download_creates_files(self, downloader_with_data):
        downloader, result = downloader_with_data
        for table_name, path in result.items():
            assert Path(path).exists(), f"File for {table_name} not created"


class TestDownloadStats:
    def test_stats_structure(self, seed):
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = NYCTaxiDataDownloader(
                scale_factor=1.0,
                output_dir=tmpdir,
                year=2019,
                months=[1, 2, 3],
                seed=seed,
            )
            stats = downloader.get_download_stats()

        assert "scale_factor" in stats
        assert "sample_rate" in stats
        assert "year" in stats
        assert "months" in stats

    def test_stats_values(self, seed):
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = NYCTaxiDataDownloader(
                scale_factor=2.0,
                output_dir=tmpdir,
                year=2020,
                months=[6, 7],
                seed=seed,
            )
            stats = downloader.get_download_stats()

        assert stats["scale_factor"] == 2.0
        assert stats["year"] == 2020
        assert stats["months"] == [6, 7]


class TestSourceContractIdentity:
    def test_contract_id_stable_and_pin_sensitive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = NYCTaxiDataDownloader(output_dir=tmpdir)
            second = NYCTaxiDataDownloader(output_dir=tmpdir)
            other_year = NYCTaxiDataDownloader(output_dir=tmpdir, year=2020)
        assert first.source_contract_id() == second.source_contract_id()
        assert first.source_contract_id() != other_year.source_contract_id()

    def test_stats_report_tlc_when_nothing_synthetic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = NYCTaxiDataDownloader(output_dir=tmpdir).get_download_stats()
        assert stats["source"] == "nyc-tlc"
        assert stats["synthetic_months"] == []

    def test_stats_report_mixed_and_synthetic_provenance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = NYCTaxiDataDownloader(output_dir=tmpdir, year=2020, months=[6, 7])
            downloader._record_synthetic_fallback("https://x/yellow_tripdata_2020-06.parquet")
            assert downloader.get_download_stats()["source"] == "nyc-tlc-synthetic-mixed"
            downloader._record_synthetic_fallback("https://x/yellow_tripdata_2020-07.parquet")
            stats = downloader.get_download_stats()
            assert stats["source"] == "synthetic"
            assert stats["synthetic_months"] == ["2020-06", "2020-07"]

    def test_stale_sidecar_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            output_path = tmpdir / "trips.csv.gz"
            output_path.write_text("x", encoding="utf-8")
            current = NYCTaxiDataDownloader(output_dir=tmpdir)
            current._write_contract_sidecar(output_path)
            assert current._read_persisted_contract_id(output_path) == current.source_contract_id()
            other = NYCTaxiDataDownloader(output_dir=tmpdir, year=2020)
            assert other._read_persisted_contract_id(output_path) != other.source_contract_id()

    def test_missing_sidecar_reads_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = NYCTaxiDataDownloader(output_dir=tmpdir)
            assert downloader._read_persisted_contract_id(Path(tmpdir) / "trips.csv.gz") is None


class TestCleanCsvValue:
    def test_integral_float_becomes_int(self):
        assert NYCTaxiDataDownloader._clean_csv_value(1.0, 0) == 1
        assert isinstance(NYCTaxiDataDownloader._clean_csv_value(1.0, 0), int)

    def test_fractional_float_kept(self):
        assert NYCTaxiDataDownloader._clean_csv_value(1.2, 0) == 1.2

    def test_nan_falls_back_to_default(self):
        assert NYCTaxiDataDownloader._clean_csv_value(float("nan"), 0) == 0
        assert NYCTaxiDataDownloader._clean_csv_value(None, 1) == 1

    def test_map_frame_normalizes_parquet_floats(self):
        import tempfile

        import pandas as pd

        frame = pd.DataFrame(
            {
                "VendorID": [1.0, 2.0],
                "passenger_count": [2.0, float("nan")],
                "congestion_surcharge": [float("nan"), 2.5],
                "fare_amount": [6.5, 7.0],
                "tpep_pickup_datetime": pd.to_datetime(["2019-01-01 08:00:00", None]),
                "store_and_fwd_flag": ["N", None],
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = NYCTaxiDataDownloader(output_dir=tmpdir)
            mapped = list(downloader._map_frame_to_rows(frame, 10))
        names = ["trip_id", *NYCTaxiDataDownloader._COLUMN_PROVIDER()]
        first = dict(zip(names, mapped[0]))
        second = dict(zip(names, mapped[1]))
        assert first["trip_id"] == 10
        assert first["vendor_id"] == 1
        assert isinstance(first["vendor_id"], int)
        assert first["passenger_count"] == 2
        assert first["congestion_surcharge"] == 0
        assert first["fare_amount"] == 6.5
        assert first["pickup_datetime"] == "2019-01-01 08:00:00"
        assert first["store_and_fwd_flag"] == "N"
        assert second["trip_id"] == 11
        assert second["passenger_count"] == 1
        assert second["congestion_surcharge"] == 2.5
        assert second["pickup_datetime"] == ""
        assert second["store_and_fwd_flag"] == "N"
        assert first["airport_fee"] == 0

    def test_parquet_ingest_maps_columns_without_row_iteration(self, tmp_path, monkeypatch):
        import csv
        import io
        import shutil

        import pandas as pd

        from benchbox.core.nyctaxi import downloader as module

        source = tmp_path / "month.parquet"
        pd.DataFrame(
            {
                "VendorID": [1, 2, 1],
                "passenger_count": [1.0, 2.0, float("nan")],
                "fare_amount": [5.5, 7.0, 9.25],
                "tpep_pickup_datetime": pd.to_datetime(["2019-01-01 08:00:00"] * 3),
            }
        ).to_parquet(source)
        monkeypatch.setattr(module, "download_to_file", lambda _url, path, **_kw: shutil.copyfile(source, path))

        def forbidden(self):
            raise AssertionError("rows must be mapped column-wise")

        monkeypatch.setattr(pd.DataFrame, "iterrows", forbidden)
        downloader = NYCTaxiDataDownloader(months=[1], output_dir=tmp_path, scale_factor=10.0)
        buffer = io.StringIO()
        written = downloader._process_parquet_file(downloader.source_contract()["urls"][0], csv.writer(buffer), 0)

        rows = list(csv.reader(io.StringIO(buffer.getvalue())))
        names = ["trip_id", *NYCTaxiDataDownloader._COLUMN_PROVIDER()]
        assert written == len(rows) == 3
        by_col = [dict(zip(names, row)) for row in rows]
        assert [row["vendor_id"] for row in by_col] == ["1", "2", "1"]
        assert [row["passenger_count"] for row in by_col] == ["1", "2", "1"]
        assert [row["fare_amount"] for row in by_col] == ["5.5", "7", "9.25"]
        assert by_col[0]["pickup_datetime"] == "2019-01-01 08:00:00"
