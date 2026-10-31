# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import csv
import tempfile
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.nyctaxi.downloader import FHVDataDownloader, GreenTaxiDataDownloader, HVFHVDataDownloader
from benchbox.core.nyctaxi.queries import (
    CROSS_TYPE_QUERIES,
    FHV_QUERIES,
    GREEN_QUERIES,
    HVFHV_QUERIES,
    QUERIES,
    NYCTaxiQueryManager,
)
from benchbox.core.nyctaxi.schema import (
    NYC_TAXI_SCHEMA,
    TABLE_ORDER,
    TaxiType,
    get_create_tables_sql,
    get_fhv_trips_columns,
    get_green_trips_columns,
    get_hvfhv_trips_columns,
    get_table_columns,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def no_network_downloads(monkeypatch):
    def _raise(*args, **kwargs):
        raise OSError("Network calls blocked in unit tests - using synthetic data")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)


class TestTaxiTypeEnum:
    def test_has_all_types(self):
        assert TaxiType.YELLOW.value == "yellow"
        assert TaxiType.GREEN.value == "green"
        assert TaxiType.HVFHV.value == "hvfhv"
        assert TaxiType.FHV.value == "fhv"

    def test_enum_members_are_distinct(self):
        types = list(TaxiType)
        assert len(types) == len(set(types))


class TestGreenTripsSchema:
    def test_schema_includes_green_trips(self):
        assert "green_trips" in NYC_TAXI_SCHEMA

    def test_green_trips_has_required_columns(self):
        cols = set(NYC_TAXI_SCHEMA["green_trips"]["columns"].keys())
        required = {
            "trip_id",
            "vendor_id",
            "pickup_datetime",
            "dropoff_datetime",
            "pickup_location_id",
            "dropoff_location_id",
            "trip_distance",
            "fare_amount",
            "total_amount",
            "trip_type",
            "ehail_fee",
        }
        assert required.issubset(cols)

    def test_green_trips_has_primary_key(self):
        assert NYC_TAXI_SCHEMA["green_trips"]["primary_key"] == ["trip_id"]

    def test_get_green_trips_columns_excludes_trip_id(self):
        cols = get_green_trips_columns()
        assert "trip_id" not in cols
        assert "vendor_id" in cols
        assert "total_amount" in cols
        assert "trip_type" in cols

    def test_get_table_columns_works_for_green_trips(self):
        cols = get_table_columns("green_trips")
        assert len(cols) > 0
        assert "trip_id" in cols


class TestHVFHVTripsSchema:
    def test_schema_includes_hvfhv_trips(self):
        assert "hvfhv_trips" in NYC_TAXI_SCHEMA

    def test_hvfhv_trips_has_required_columns(self):
        cols = set(NYC_TAXI_SCHEMA["hvfhv_trips"]["columns"].keys())
        required = {
            "trip_id",
            "hvfhs_license_num",
            "pickup_datetime",
            "dropoff_datetime",
            "pickup_location_id",
            "dropoff_location_id",
            "trip_miles",
            "trip_time",
            "base_passenger_fare",
            "driver_pay",
            "shared_request_flag",
            "shared_match_flag",
        }
        assert required.issubset(cols)

    def test_hvfhv_trips_has_no_fare_amount_column(self):
        cols = set(NYC_TAXI_SCHEMA["hvfhv_trips"]["columns"].keys())
        assert "fare_amount" not in cols
        assert "base_passenger_fare" in cols

    def test_hvfhv_trips_has_primary_key(self):
        assert NYC_TAXI_SCHEMA["hvfhv_trips"]["primary_key"] == ["trip_id"]

    def test_get_hvfhv_trips_columns_excludes_trip_id(self):
        cols = get_hvfhv_trips_columns()
        assert "trip_id" not in cols
        assert "hvfhs_license_num" in cols
        assert "base_passenger_fare" in cols
        assert "driver_pay" in cols


class TestTableOrder:
    def test_table_order_includes_new_tables(self):
        assert "green_trips" in TABLE_ORDER
        assert "hvfhv_trips" in TABLE_ORDER
        assert "fhv_trips" in TABLE_ORDER

    def test_taxi_zones_first(self):
        assert TABLE_ORDER[0] == "taxi_zones"

    def test_yellow_trips_before_new_types(self):
        yellow_idx = TABLE_ORDER.index("trips")
        green_idx = TABLE_ORDER.index("green_trips")
        hvfhv_idx = TABLE_ORDER.index("hvfhv_trips")
        assert yellow_idx < green_idx
        assert yellow_idx < hvfhv_idx


class TestCreateTablesSqlWithTaxiTypes:
    def test_default_none_creates_yellow_only(self):
        sql = get_create_tables_sql(dialect="duckdb", taxi_types=None)
        assert "CREATE TABLE trips" in sql
        assert "CREATE TABLE taxi_zones" in sql
        assert "CREATE TABLE green_trips" not in sql
        assert "CREATE TABLE hvfhv_trips" not in sql

    def test_yellow_only_explicit(self):
        sql = get_create_tables_sql(dialect="duckdb", taxi_types=[TaxiType.YELLOW])
        assert "CREATE TABLE trips" in sql
        assert "CREATE TABLE green_trips" not in sql
        assert "CREATE TABLE hvfhv_trips" not in sql

    def test_with_green_type(self):
        sql = get_create_tables_sql(dialect="duckdb", taxi_types=[TaxiType.YELLOW, TaxiType.GREEN])
        assert "CREATE TABLE trips" in sql
        assert "CREATE TABLE green_trips" in sql
        assert "CREATE TABLE hvfhv_trips" not in sql

    def test_with_hvfhv_type(self):
        sql = get_create_tables_sql(dialect="duckdb", taxi_types=[TaxiType.YELLOW, TaxiType.HVFHV])
        assert "CREATE TABLE trips" in sql
        assert "CREATE TABLE hvfhv_trips" in sql
        assert "CREATE TABLE green_trips" not in sql

    def test_all_types(self):
        sql = get_create_tables_sql(dialect="duckdb", taxi_types=[TaxiType.YELLOW, TaxiType.GREEN, TaxiType.HVFHV])
        assert "CREATE TABLE trips" in sql
        assert "CREATE TABLE green_trips" in sql
        assert "CREATE TABLE hvfhv_trips" in sql

    def test_green_trips_has_trip_type_column(self):
        sql = get_create_tables_sql(dialect="duckdb", taxi_types=[TaxiType.YELLOW, TaxiType.GREEN])
        assert "trip_type" in sql

    def test_hvfhv_trips_has_shared_flags(self):
        sql = get_create_tables_sql(dialect="duckdb", taxi_types=[TaxiType.YELLOW, TaxiType.HVFHV])
        assert "shared_request_flag" in sql
        assert "shared_match_flag" in sql


class TestFHVTripsSchema:
    def test_schema_includes_fhv_trips(self):
        assert "fhv_trips" in NYC_TAXI_SCHEMA

    def test_fhv_trips_has_required_columns(self):
        cols = set(NYC_TAXI_SCHEMA["fhv_trips"]["columns"].keys())
        for col in (
            "dispatching_base_num",
            "affiliated_base_number",
            "pickup_datetime",
            "dropoff_datetime",
            "pickup_location_id",
            "dropoff_location_id",
            "sr_flag",
        ):
            assert col in cols, f"fhv_trips missing {col}"

    def test_fhv_trips_has_no_fare_columns(self):
        cols = set(NYC_TAXI_SCHEMA["fhv_trips"]["columns"].keys())
        assert "fare_amount" not in cols
        assert "total_amount" not in cols
        assert "trip_distance" not in cols

    def test_fhv_trips_has_primary_key(self):
        assert NYC_TAXI_SCHEMA["fhv_trips"]["primary_key"] == ["trip_id"]

    def test_get_fhv_trips_columns_excludes_trip_id(self):
        cols = get_fhv_trips_columns()
        assert "trip_id" not in cols
        assert "dispatching_base_num" in cols

    def test_get_table_columns_works_for_fhv_trips(self):
        cols = get_table_columns("fhv_trips")
        assert "sr_flag" in cols

    def test_with_fhv_type(self):
        sql = get_create_tables_sql(dialect="duckdb", taxi_types=[TaxiType.YELLOW, TaxiType.FHV])
        assert "CREATE TABLE trips" in sql
        assert "CREATE TABLE fhv_trips" in sql
        assert "CREATE TABLE green_trips" not in sql
        assert "CREATE TABLE hvfhv_trips" not in sql


class TestGreenTaxiDataDownloader:
    @pytest.fixture
    def green_downloader(self, tmp_path):
        return GreenTaxiDataDownloader(
            scale_factor=0.01,
            output_dir=tmp_path,
            year=2019,
            months=[1],
            seed=42,
        )

    def test_init_sets_correct_attributes(self, green_downloader, tmp_path):
        assert green_downloader.scale_factor == 0.01
        assert green_downloader.year == 2019
        assert green_downloader.months == [1]
        assert green_downloader.seed == 42

    def test_download_creates_green_trips_csv(self, green_downloader, tmp_path):
        green_path = green_downloader.download()
        assert green_path.exists()
        assert green_path.name == "green_trips.csv"

    def test_generated_csv_has_correct_columns(self, green_downloader, tmp_path):
        green_path = green_downloader.download()
        with open(green_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
        expected_cols = ["trip_id"] + get_green_trips_columns()
        assert header == expected_cols

    def test_generated_csv_has_rows(self, green_downloader, tmp_path):
        green_path = green_downloader.download()
        with open(green_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) > 0

    def test_generated_csv_has_trip_type_column(self, green_downloader, tmp_path):
        green_path = green_downloader.download()
        with open(green_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            first_row = next(reader)
        assert int(float(first_row["trip_type"])) in (1, 2)

    def test_download_stats(self, green_downloader):
        stats = green_downloader.get_download_stats()
        assert stats["taxi_type"] == "green"
        assert stats["scale_factor"] == 0.01

    def test_skips_existing_file_with_matching_source_contract(self, tmp_path):
        existing = tmp_path / "green_trips.csv"
        existing.write_text("already_here")
        downloader = GreenTaxiDataDownloader(scale_factor=0.01, output_dir=tmp_path, year=2019, months=[1])
        downloader._write_contract_sidecar(existing)
        result_path = downloader.download()
        assert result_path.read_text() == "already_here"

    def test_force_redownload_regenerates(self, tmp_path):
        downloader = GreenTaxiDataDownloader(
            scale_factor=0.01, output_dir=tmp_path, year=2019, months=[1], force_redownload=True
        )
        path = downloader.download()
        assert path.exists()
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) > 0


class TestHVFHVDataDownloader:
    @pytest.fixture
    def hvfhv_downloader(self, tmp_path):
        return HVFHVDataDownloader(
            scale_factor=0.01,
            output_dir=tmp_path,
            year=2019,
            months=[2],
            seed=42,
        )

    def test_init_sets_correct_attributes(self, hvfhv_downloader):
        assert hvfhv_downloader.scale_factor == 0.01
        assert hvfhv_downloader.year == 2019
        assert hvfhv_downloader.seed == 42

    def test_default_months_skip_jan_2019(self):
        downloader = HVFHVDataDownloader(scale_factor=0.01, year=2019)
        assert 1 not in downloader.months
        assert 2 in downloader.months

    def test_default_months_full_year_for_later_years(self):
        downloader = HVFHVDataDownloader(scale_factor=0.01, year=2020)
        assert downloader.months == list(range(1, 13))

    def test_download_creates_hvfhv_trips_csv(self, hvfhv_downloader, tmp_path):
        hvfhv_path = hvfhv_downloader.download()
        assert hvfhv_path.exists()
        assert hvfhv_path.name == "hvfhv_trips.csv"

    def test_generated_csv_has_correct_columns(self, hvfhv_downloader, tmp_path):
        hvfhv_path = hvfhv_downloader.download()
        with open(hvfhv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
        expected_cols = ["trip_id"] + get_hvfhv_trips_columns()
        assert header == expected_cols

    def test_generated_csv_has_rows(self, hvfhv_downloader, tmp_path):
        hvfhv_path = hvfhv_downloader.download()
        with open(hvfhv_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) > 0

    def test_generated_csv_has_license_num(self, hvfhv_downloader, tmp_path):
        hvfhv_path = hvfhv_downloader.download()
        valid_licenses = {"HV0002", "HV0003", "HV0004", "HV0005"}
        with open(hvfhv_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                assert row["hvfhs_license_num"] in valid_licenses
                break

    def test_generated_csv_has_shared_flags(self, hvfhv_downloader, tmp_path):
        hvfhv_path = hvfhv_downloader.download()
        with open(hvfhv_path, encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["shared_request_flag"] in ("Y", "N")
        assert row["shared_match_flag"] in ("Y", "N")

    def test_download_stats(self, hvfhv_downloader):
        stats = hvfhv_downloader.get_download_stats()
        assert stats["taxi_type"] == "hvfhv"
        assert stats["scale_factor"] == 0.01


class TestGreenQuerySet:
    def test_green_queries_exist(self):
        assert len(GREEN_QUERIES) >= 3

    def test_green_queries_have_required_fields(self):
        for qid, qdef in GREEN_QUERIES.items():
            assert "id" in qdef, f"{qid} missing 'id'"
            assert "name" in qdef, f"{qid} missing 'name'"
            assert "sql" in qdef, f"{qid} missing 'sql'"
            assert "category" in qdef, f"{qid} missing 'category'"

    def test_green_queries_reference_green_trips_table(self):
        for qid, qdef in GREEN_QUERIES.items():
            assert "green_trips" in qdef["sql"], f"{qid} does not reference green_trips table"

    def test_green_queries_do_not_reference_yellow_trips(self):
        for qid, qdef in GREEN_QUERIES.items():
            assert "FROM trips" not in qdef["sql"], f"{qid} references yellow trips table"


class TestHVFHVQuerySet:
    def test_hvfhv_queries_exist(self):
        assert len(HVFHV_QUERIES) >= 3

    def test_hvfhv_queries_have_required_fields(self):
        for qid, qdef in HVFHV_QUERIES.items():
            assert "id" in qdef, f"{qid} missing 'id'"
            assert "sql" in qdef, f"{qid} missing 'sql'"

    def test_hvfhv_queries_reference_hvfhv_trips_table(self):
        for qid, qdef in HVFHV_QUERIES.items():
            assert "hvfhv_trips" in qdef["sql"], f"{qid} does not reference hvfhv_trips table"

    def test_hvfhv_queries_use_hvfhv_column_names(self):
        combined_sql = " ".join(q["sql"] for q in HVFHV_QUERIES.values())
        assert "trip_miles" in combined_sql

    def test_hvfhv_queries_no_fare_amount(self):
        combined_sql = " ".join(q["sql"] for q in HVFHV_QUERIES.values())
        assert "base_passenger_fare" in combined_sql
        assert "fare_amount" not in combined_sql


class TestFHVDataDownloader:
    @pytest.fixture
    def fhv_downloader(self, tmp_path):
        return FHVDataDownloader(
            scale_factor=0.01,
            output_dir=tmp_path,
            year=2019,
            months=[1],
            seed=42,
        )

    def test_init_sets_correct_attributes(self, fhv_downloader):
        assert fhv_downloader.scale_factor == 0.01
        assert fhv_downloader.year == 2019
        assert fhv_downloader.seed == 42

    def test_download_creates_fhv_trips_csv(self, fhv_downloader, tmp_path):
        fhv_path = fhv_downloader.download()
        assert fhv_path.exists()
        assert fhv_path.name == "fhv_trips.csv"

    def test_generated_csv_has_correct_columns(self, fhv_downloader, tmp_path):
        fhv_path = fhv_downloader.download()
        with open(fhv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
        expected_cols = ["trip_id"] + get_fhv_trips_columns()
        assert header == expected_cols

    def test_generated_csv_has_rows(self, fhv_downloader, tmp_path):
        fhv_path = fhv_downloader.download()
        with open(fhv_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) > 0

    def test_generated_csv_has_dispatch_base(self, fhv_downloader, tmp_path):
        fhv_path = fhv_downloader.download()
        with open(fhv_path, encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["dispatching_base_num"].startswith("B")
        assert row["sr_flag"] in ("Y", "N")

    def test_row_mapping_accepts_exact_tlc_spellings(self, fhv_downloader):
        import pandas as pd

        frame = pd.DataFrame(
            [
                {
                    "dispatching_base_num": "B01234",
                    "Affiliated_base_number": "B01234",
                    "pickup_datetime": "2019-01-01 08:00:00",
                    "dropOff_datetime": "2019-01-01 08:20:00",
                    "PUlocationID": 161,
                    "DOlocationID": 162,
                    "SR_Flag": None,
                }
            ]
        )
        mapped = list(fhv_downloader._map_frame_to_rows(frame, 1))[0]
        assert mapped[1] == "B01234"
        assert mapped[5] == 161
        assert mapped[6] == 162
        assert mapped[7] == "N"

    def test_row_mapping_normalizes_numeric_shared_flag(self, fhv_downloader):
        import pandas as pd

        base = {
            "dispatching_base_num": "B01234",
            "Affiliated_base_number": "B01234",
            "pickup_datetime": "2019-01-01 08:00:00",
            "dropOff_datetime": "2019-01-01 08:20:00",
            "PUlocationID": 161,
            "DOlocationID": 162,
        }
        for flag, expected in ((1, "Y"), (1.0, "Y"), ("Y", "Y"), ("N", "N")):
            frame = pd.DataFrame([{**base, "SR_Flag": flag}])
            assert list(fhv_downloader._map_frame_to_rows(frame, 1))[0][7] == expected

    def test_download_stats(self, fhv_downloader):
        stats = fhv_downloader.get_download_stats()
        assert stats["taxi_type"] == "fhv"
        assert stats["scale_factor"] == 0.01


class TestFHVQuerySet:
    def test_fhv_queries_exist(self):
        assert len(FHV_QUERIES) >= 3

    def test_fhv_queries_have_required_fields(self):
        for qid, qdef in FHV_QUERIES.items():
            assert "id" in qdef, f"{qid} missing 'id'"
            assert "name" in qdef, f"{qid} missing 'name'"
            assert "sql" in qdef, f"{qid} missing 'sql'"
            assert "category" in qdef, f"{qid} missing 'category'"

    def test_fhv_queries_reference_fhv_trips_table(self):
        for qid, qdef in FHV_QUERIES.items():
            assert "fhv_trips" in qdef["sql"], f"{qid} does not reference fhv_trips table"

    def test_fhv_queries_use_dispatch_columns(self):
        combined_sql = " ".join(q["sql"] for q in FHV_QUERIES.values())
        assert "dispatching_base_num" in combined_sql
        assert "sr_flag" in combined_sql
        assert "fare_amount" not in combined_sql
        assert "total_amount" not in combined_sql


class TestCrossTypeQuerySet:
    def test_cross_type_queries_exist(self):
        assert len(CROSS_TYPE_QUERIES) >= 5

    def test_cross_type_queries_reference_all_three_tables(self):
        for qid, qdef in CROSS_TYPE_QUERIES.items():
            sql = qdef["sql"]
            assert "trips" in sql, f"{qid} missing yellow trips"
            assert "green_trips" in sql, f"{qid} missing green_trips"
            assert "hvfhv_trips" in sql, f"{qid} missing hvfhv_trips"

    def test_cross_type_queries_have_taxi_type_labels(self):
        for qid, qdef in CROSS_TYPE_QUERIES.items():
            sql = qdef["sql"]
            assert "'yellow'" in sql, f"{qid} missing 'yellow' type label"
            assert "'green'" in sql, f"{qid} missing 'green' type label"
            assert "'hvfhv'" in sql, f"{qid} missing 'hvfhv' type label"

    def test_ids_are_unique_across_all_query_sets(self):
        all_ids = (
            [q["id"] for q in QUERIES.values()]
            + [q["id"] for q in GREEN_QUERIES.values()]
            + [q["id"] for q in HVFHV_QUERIES.values()]
            + [q["id"] for q in FHV_QUERIES.values()]
            + [q["id"] for q in CROSS_TYPE_QUERIES.values()]
        )
        assert len(all_ids) == len(set(all_ids)), "Duplicate query IDs found"


class TestQueryManagerMultiType:
    def test_default_manager_has_yellow_only(self):
        qm = NYCTaxiQueryManager()
        assert qm.get_query_count() == len(QUERIES)

    def test_manager_with_green_queries(self):
        qm = NYCTaxiQueryManager(include_green_queries=True)
        assert qm.get_query_count() == len(QUERIES) + len(GREEN_QUERIES)

    def test_manager_with_hvfhv_queries(self):
        qm = NYCTaxiQueryManager(include_hvfhv_queries=True)
        assert qm.get_query_count() == len(QUERIES) + len(HVFHV_QUERIES)

    def test_manager_with_fhv_queries(self):
        qm = NYCTaxiQueryManager(include_fhv_queries=True)
        assert qm.get_query_count() == len(QUERIES) + len(FHV_QUERIES)

    def test_manager_with_all_query_sets(self):
        qm = NYCTaxiQueryManager(
            include_green_queries=True,
            include_hvfhv_queries=True,
            include_cross_type_queries=True,
        )
        expected = len(QUERIES) + len(GREEN_QUERIES) + len(HVFHV_QUERIES) + len(CROSS_TYPE_QUERIES)
        assert qm.get_query_count() == expected

    def test_green_query_can_be_retrieved(self):
        qm = NYCTaxiQueryManager(include_green_queries=True)
        sql = qm.get_query("green-borough-trips")
        assert "green_trips" in sql
        assert "borough" in sql

    def test_hvfhv_query_can_be_retrieved(self):
        qm = NYCTaxiQueryManager(include_hvfhv_queries=True)
        sql = qm.get_query("hvfhv-shared-ride-rate")
        assert "hvfhv_trips" in sql
        assert "shared_request_flag" in sql

    def test_fhv_query_can_be_retrieved(self):
        qm = NYCTaxiQueryManager(include_fhv_queries=True)
        sql = qm.get_query("fhv-base-volume")
        assert "fhv_trips" in sql
        assert "dispatching_base_num" in sql

    def test_fhv_query_not_available_in_default_manager(self):
        qm = NYCTaxiQueryManager()
        with pytest.raises(ValueError, match="Unknown query"):
            qm.get_query("fhv-base-volume")

    def test_cross_type_query_can_be_retrieved(self):
        qm = NYCTaxiQueryManager(include_cross_type_queries=True)
        sql = qm.get_query("cross-market-share")
        assert "green_trips" in sql
        assert "hvfhv_trips" in sql
        assert "market_share_pct" in sql

    def test_green_query_not_available_in_default_manager(self):
        qm = NYCTaxiQueryManager()
        with pytest.raises(ValueError, match="Unknown query"):
            qm.get_query("green-borough-trips")

    def test_categories_include_new_types_when_enabled(self):
        qm = NYCTaxiQueryManager(
            include_green_queries=True,
            include_hvfhv_queries=True,
            include_cross_type_queries=True,
        )
        categories = qm.get_categories()
        assert any("green" in c for c in categories)
        assert any("hvfhv" in c for c in categories)
        assert any("cross" in c for c in categories)

    def test_get_queries_by_category_green(self):
        qm = NYCTaxiQueryManager(include_green_queries=True)
        green_cat_queries = qm.get_queries_by_category("green-geographic")
        assert len(green_cat_queries) > 0
        assert all(qid.startswith("green-") for qid in green_cat_queries)
