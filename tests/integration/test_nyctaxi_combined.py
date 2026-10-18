# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
import urllib.request
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark
from benchbox.core.nyctaxi.downloader import GreenTaxiDataDownloader, HVFHVDataDownloader
from benchbox.core.nyctaxi.queries import CROSS_TYPE_QUERIES, GREEN_QUERIES, HVFHV_QUERIES
from benchbox.core.nyctaxi.schema import TaxiType, get_create_tables_sql

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.fixture(autouse=True)
def no_network_downloads(monkeypatch):
    def _raise(*args, **kwargs):
        raise OSError("Network calls blocked in integration tests - using synthetic data")

    monkeypatch.setattr(urllib.request, "urlretrieve", _raise)


@pytest.fixture(scope="module")
def multi_type_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        conn = duckdb.connect(":memory:")

        with patch.object(urllib.request, "urlretrieve", side_effect=OSError("no network")):
            benchmark = NYCTaxiBenchmark(
                scale_factor=0.01,
                output_dir=tmpdir,
                year=2019,
                months=[1, 2],
                seed=42,
                taxi_types=[TaxiType.YELLOW, TaxiType.GREEN, TaxiType.HVFHV],
            )
            benchmark.generate_data()

        sql = benchmark.get_create_tables_sql(dialect="duckdb")
        for stmt in sql.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt.strip())

        for table_name, csv_path in benchmark.tables.items():
            conn.execute(f"COPY {table_name} FROM '{csv_path}' (HEADER TRUE, DELIMITER ',')")

        yield conn
        conn.close()


@pytest.mark.duckdb
@pytest.mark.nyctaxi
class TestMultiTypeSchemaCreation:
    def test_all_tables_exist(self, multi_type_db):
        tables = {
            row[0]
            for row in multi_type_db.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert "taxi_zones" in tables
        assert "trips" in tables
        assert "green_trips" in tables
        assert "hvfhv_trips" in tables

    def test_yellow_trips_has_data(self, multi_type_db):
        count = multi_type_db.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        assert count > 0

    def test_green_trips_has_data(self, multi_type_db):
        count = multi_type_db.execute("SELECT COUNT(*) FROM green_trips").fetchone()[0]
        assert count > 0

    def test_hvfhv_trips_has_data(self, multi_type_db):
        count = multi_type_db.execute("SELECT COUNT(*) FROM hvfhv_trips").fetchone()[0]
        assert count > 0

    def test_green_trips_has_trip_type_column(self, multi_type_db):
        result = multi_type_db.execute("SELECT DISTINCT trip_type FROM green_trips").fetchall()
        trip_types = {int(float(row[0])) for row in result if row[0] is not None}
        assert trip_types.issubset({1, 2})

    def test_hvfhv_trips_has_valid_license_numbers(self, multi_type_db):
        result = multi_type_db.execute("SELECT DISTINCT hvfhs_license_num FROM hvfhv_trips LIMIT 10").fetchall()
        valid_licenses = {"HV0002", "HV0003", "HV0004", "HV0005"}
        for row in result:
            assert row[0] in valid_licenses, f"Invalid license: {row[0]}"

    def test_hvfhv_trips_has_shared_ride_flags(self, multi_type_db):
        result = multi_type_db.execute("SELECT DISTINCT shared_request_flag FROM hvfhv_trips").fetchall()
        flags = {row[0] for row in result}
        assert flags.issubset({"Y", "N"})


@pytest.mark.duckdb
@pytest.mark.nyctaxi
class TestGreenTaxiQueries:
    def test_green_borough_trips_query(self, multi_type_db):
        sql = """
            SELECT z.borough, COUNT(*) as trip_count
            FROM green_trips g
            LEFT JOIN taxi_zones z ON g.pickup_location_id = z.location_id
            WHERE g.pickup_datetime >= '2019-01-01'
              AND g.pickup_datetime < '2019-03-01'
              AND z.borough IS NOT NULL
            GROUP BY z.borough
            ORDER BY trip_count DESC
        """
        result = multi_type_db.execute(sql).fetchall()
        assert len(result) > 0
        for row in result:
            assert row[0] in ("Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "EWR")

    def test_green_trip_type_split_query(self, multi_type_db):
        sql = """
            SELECT trip_type, COUNT(*) as trip_count
            FROM green_trips
            WHERE pickup_datetime >= '2019-01-01'
              AND pickup_datetime < '2019-03-01'
              AND trip_type IN (1, 2)
            GROUP BY trip_type
            ORDER BY trip_type
        """
        result = multi_type_db.execute(sql).fetchall()
        assert len(result) > 0
        trip_types = {int(row[0]) for row in result}
        assert trip_types.issubset({1, 2})

    def test_all_green_queries_execute(self, multi_type_db):
        for qid, qdef in GREEN_QUERIES.items():
            sql = qdef["sql"].strip().format(start_date="2019-01-01", end_date="2019-03-01")
            result = multi_type_db.execute(sql).fetchall()
            assert result is not None, f"Query {qid} returned None"


@pytest.mark.duckdb
@pytest.mark.nyctaxi
class TestHVFHVQueries:
    def test_hvfhv_shared_ride_rate_query(self, multi_type_db):
        sql = """
            SELECT
                hvfhs_license_num,
                COUNT(*) as total_trips,
                SUM(CASE WHEN shared_request_flag = 'Y' THEN 1 ELSE 0 END) as shared_requests
            FROM hvfhv_trips
            WHERE pickup_datetime >= '2019-01-01'
              AND pickup_datetime < '2019-03-01'
            GROUP BY hvfhs_license_num
            ORDER BY total_trips DESC
        """
        result = multi_type_db.execute(sql).fetchall()
        assert len(result) > 0
        valid_licenses = {"HV0002", "HV0003", "HV0004", "HV0005"}
        for row in result:
            assert row[0] in valid_licenses

    def test_hvfhv_driver_pay_analysis_query(self, multi_type_db):
        sql = """
            SELECT
                FLOOR(base_passenger_fare / 5) * 5 as fare_bucket,
                COUNT(*) as trip_count,
                AVG(driver_pay) as avg_driver_pay
            FROM hvfhv_trips
            WHERE pickup_datetime >= '2019-01-01'
              AND pickup_datetime < '2019-03-01'
              AND base_passenger_fare BETWEEN 2 AND 100
            GROUP BY FLOOR(base_passenger_fare / 5) * 5
            ORDER BY fare_bucket
        """
        result = multi_type_db.execute(sql).fetchall()
        assert len(result) > 0

    def test_all_hvfhv_queries_execute(self, multi_type_db):
        for qid, qdef in HVFHV_QUERIES.items():
            sql = qdef["sql"].strip().format(start_date="2019-01-01", end_date="2019-03-01")
            result = multi_type_db.execute(sql).fetchall()
            assert result is not None, f"Query {qid} returned None"


@pytest.mark.duckdb
@pytest.mark.nyctaxi
class TestCrossTypeQueries:
    def test_cross_market_share_query(self, multi_type_db):
        sql = """
            WITH monthly_counts AS (
                SELECT DATE_TRUNC('month', pickup_datetime) as month, 'yellow' as taxi_type, COUNT(*) as trip_count
                FROM trips
                WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                GROUP BY DATE_TRUNC('month', pickup_datetime)
                UNION ALL
                SELECT DATE_TRUNC('month', pickup_datetime) as month, 'green' as taxi_type, COUNT(*) as trip_count
                FROM green_trips
                WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                GROUP BY DATE_TRUNC('month', pickup_datetime)
                UNION ALL
                SELECT DATE_TRUNC('month', pickup_datetime) as month, 'hvfhv' as taxi_type, COUNT(*) as trip_count
                FROM hvfhv_trips
                WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                GROUP BY DATE_TRUNC('month', pickup_datetime)
            ),
            monthly_totals AS (SELECT month, SUM(trip_count) as total_trips FROM monthly_counts GROUP BY month)
            SELECT mc.month, mc.taxi_type, mc.trip_count,
                   ROUND(mc.trip_count * 100.0 / mt.total_trips, 2) as market_share_pct
            FROM monthly_counts mc
            JOIN monthly_totals mt ON mc.month = mt.month
            ORDER BY mc.month, mc.taxi_type
        """
        result = multi_type_db.execute(sql).fetchall()
        assert len(result) > 0
        taxi_types_seen = {row[1] for row in result}
        assert taxi_types_seen == {"yellow", "green", "hvfhv"}

    def test_cross_geographic_coverage_query(self, multi_type_db):
        sql = """
            SELECT z.borough,
                SUM(CASE WHEN src.taxi_type = 'yellow' THEN src.trip_count ELSE 0 END) as yellow_trips,
                SUM(CASE WHEN src.taxi_type = 'green' THEN src.trip_count ELSE 0 END) as green_trips,
                SUM(CASE WHEN src.taxi_type = 'hvfhv' THEN src.trip_count ELSE 0 END) as hvfhv_trips
            FROM (
                SELECT pickup_location_id, 'yellow' as taxi_type, COUNT(*) as trip_count
                FROM trips WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                GROUP BY pickup_location_id
                UNION ALL
                SELECT pickup_location_id, 'green' as taxi_type, COUNT(*) as trip_count
                FROM green_trips WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                GROUP BY pickup_location_id
                UNION ALL
                SELECT pickup_location_id, 'hvfhv' as taxi_type, COUNT(*) as trip_count
                FROM hvfhv_trips WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                GROUP BY pickup_location_id
            ) src
            JOIN taxi_zones z ON src.pickup_location_id = z.location_id
            GROUP BY z.borough
            ORDER BY (yellow_trips + green_trips + hvfhv_trips) DESC
        """
        result = multi_type_db.execute(sql).fetchall()
        assert len(result) > 0

    def test_cross_fare_comparison_query(self, multi_type_db):
        sql = """
            SELECT taxi_type, COUNT(*) as trip_count, AVG(fare) as avg_fare
            FROM (
                SELECT 'yellow' as taxi_type, total_amount as fare, trip_distance as distance
                FROM trips WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                    AND total_amount BETWEEN 2.50 AND 200
                UNION ALL
                SELECT 'green' as taxi_type, total_amount as fare, trip_distance as distance
                FROM green_trips WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                    AND total_amount BETWEEN 2.50 AND 200
                UNION ALL
                SELECT 'hvfhv' as taxi_type,
                       base_passenger_fare + tips + tolls + congestion_surcharge as fare,
                       trip_miles as distance
                FROM hvfhv_trips WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                    AND base_passenger_fare BETWEEN 2.50 AND 200
            ) all_trips
            WHERE distance > 0
            GROUP BY taxi_type
            ORDER BY avg_fare DESC
        """
        result = multi_type_db.execute(sql).fetchall()
        assert len(result) == 3
        taxi_types = {row[0] for row in result}
        assert taxi_types == {"yellow", "green", "hvfhv"}

    def test_cross_peak_hour_patterns_query(self, multi_type_db):
        sql = """
            SELECT hour,
                SUM(CASE WHEN taxi_type = 'yellow' THEN trip_count ELSE 0 END) as yellow_trips,
                SUM(CASE WHEN taxi_type = 'green' THEN trip_count ELSE 0 END) as green_trips,
                SUM(CASE WHEN taxi_type = 'hvfhv' THEN trip_count ELSE 0 END) as hvfhv_trips
            FROM (
                SELECT EXTRACT(HOUR FROM pickup_datetime) as hour, 'yellow' as taxi_type, COUNT(*) as trip_count
                FROM trips WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                GROUP BY EXTRACT(HOUR FROM pickup_datetime)
                UNION ALL
                SELECT EXTRACT(HOUR FROM pickup_datetime) as hour, 'green' as taxi_type, COUNT(*) as trip_count
                FROM green_trips WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                GROUP BY EXTRACT(HOUR FROM pickup_datetime)
                UNION ALL
                SELECT EXTRACT(HOUR FROM pickup_datetime) as hour, 'hvfhv' as taxi_type, COUNT(*) as trip_count
                FROM hvfhv_trips WHERE pickup_datetime >= '2019-01-01' AND pickup_datetime < '2019-03-01'
                GROUP BY EXTRACT(HOUR FROM pickup_datetime)
            ) hourly
            GROUP BY hour
            ORDER BY hour
        """
        result = multi_type_db.execute(sql).fetchall()
        assert len(result) > 0
        hours = {row[0] for row in result}
        assert all(0 <= h <= 23 for h in hours)

    def test_all_cross_type_queries_execute(self, multi_type_db):
        for qid, qdef in CROSS_TYPE_QUERIES.items():
            sql = qdef["sql"].strip().format(start_date="2019-01-01", end_date="2019-12-31")
            result = multi_type_db.execute(sql).fetchall()
            assert result is not None, f"Query {qid} returned None"


@pytest.mark.duckdb
@pytest.mark.nyctaxi
class TestBenchmarkMultiTypeIntegration:
    def test_benchmark_default_is_yellow_only(self, tmp_path):
        with patch.object(urllib.request, "urlretrieve", side_effect=OSError("no network")):
            bm = NYCTaxiBenchmark(
                scale_factor=0.01,
                output_dir=tmp_path,
                year=2019,
                months=[1],
                taxi_types=None,
            )
            bm.generate_data()

        table_names = set(bm.tables.keys())
        assert "trips" in table_names
        assert "taxi_zones" in table_names
        assert "green_trips" not in table_names
        assert "hvfhv_trips" not in table_names

    def test_benchmark_multi_type_generates_all_files(self, tmp_path):
        with patch.object(urllib.request, "urlretrieve", side_effect=OSError("no network")):
            bm = NYCTaxiBenchmark(
                scale_factor=0.01,
                output_dir=tmp_path,
                year=2019,
                months=[2],
                seed=42,
                taxi_types=[TaxiType.YELLOW, TaxiType.GREEN, TaxiType.HVFHV],
            )
            bm.generate_data()

        assert (tmp_path / "trips.csv").exists()
        assert (tmp_path / "green_trips.csv").exists()
        assert (tmp_path / "hvfhv_trips.csv").exists()

    def test_benchmark_multi_type_query_manager_has_all_queries(self, tmp_path):
        with patch.object(urllib.request, "urlretrieve", side_effect=OSError("no network")):
            bm = NYCTaxiBenchmark(
                scale_factor=0.01,
                output_dir=tmp_path,
                year=2019,
                months=[2],
                taxi_types=[TaxiType.YELLOW, TaxiType.GREEN, TaxiType.HVFHV],
            )

        count = bm.query_manager.get_query_count()
        from benchbox.core.nyctaxi.queries import CROSS_TYPE_QUERIES, GREEN_QUERIES, HVFHV_QUERIES, QUERIES

        expected = len(QUERIES) + len(GREEN_QUERIES) + len(HVFHV_QUERIES) + len(CROSS_TYPE_QUERIES)
        assert count == expected

    def test_yellow_only_benchmark_unchanged(self, tmp_path):
        with patch.object(urllib.request, "urlretrieve", side_effect=OSError("no network")):
            bm = NYCTaxiBenchmark(
                scale_factor=0.01,
                output_dir=tmp_path,
                year=2019,
                months=[1],
            )
            bm.generate_data()

        queries = bm.get_queries()
        assert len(queries) == 25
        assert "trips-per-hour" in queries
        assert "full-scan-count" in queries
        assert "green-borough-trips" not in queries
        assert "hvfhv-shared-ride-rate" not in queries
