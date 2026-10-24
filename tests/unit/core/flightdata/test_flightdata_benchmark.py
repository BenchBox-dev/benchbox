# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import csv
import io
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import pytest

from benchbox.core.flightdata.benchmark import FlightDataBenchmark
from benchbox.core.flightdata.downloader import FlightDataDownloader
from benchbox.core.flightdata.schema import FLIGHT_SCHEMA

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBenchmarkInitialization:
    def test_default_init(self):
        bm = FlightDataBenchmark()
        assert bm.scale_factor == 1.0
        assert bm.end_year == 2024

    def test_custom_scale_factor(self):
        bm = FlightDataBenchmark(scale_factor=0.01)
        assert bm.scale_factor == 0.01

    def test_invalid_scale_factor(self):
        with pytest.raises(ValueError, match="must be positive"):
            FlightDataBenchmark(scale_factor=0)

        with pytest.raises(ValueError, match="must be positive"):
            FlightDataBenchmark(scale_factor=-1.0)

    def test_custom_output_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bm = FlightDataBenchmark(output_dir=tmpdir)
            assert Path(bm.output_dir) == Path(tmpdir)

    def test_benchmark_metadata(self):
        bm = FlightDataBenchmark(scale_factor=0.01)
        assert bm._name == "Flight Data OLAP"
        assert bm._version == "1.0"
        assert "BTS" in bm._description or "aviation" in bm._description.lower()

    def test_query_date_range_set(self):
        bm = FlightDataBenchmark(scale_factor=0.01)
        assert bm._query_start_date is not None
        assert bm._query_end_date is not None
        assert bm._query_start_date < bm._query_end_date

    def test_query_end_date_is_exclusive_upper_bound(self):
        bm = FlightDataBenchmark(scale_factor=0.01)
        newest_year, newest_month = bm.downloader.months[0]

        if newest_month == 12:
            expected_end = date(newest_year + 1, 1, 1)
        else:
            expected_end = date(newest_year, newest_month + 1, 1)

        assert date.fromisoformat(bm._query_end_date) == expected_end


class TestQueries:
    def setup_method(self):
        self.bm = FlightDataBenchmark(scale_factor=0.01)

    def test_get_all_queries(self):
        queries = self.bm.get_queries()
        assert len(queries) == 20

    def test_query_keys_are_strings(self):
        queries = self.bm.get_queries()
        for key in queries:
            assert isinstance(key, str)

    def test_query_sql_non_empty(self):
        queries = self.bm.get_queries()
        for key, sql in queries.items():
            assert sql.strip(), f"Query {key!r} has empty SQL"

    def test_get_query_by_key(self):
        sql = self.bm.get_query("ontime-by-carrier")
        assert "reporting_airline" in sql.lower()
        assert "SELECT" in sql.upper()

    def test_get_query_by_numeric_id(self):
        sql = self.bm.get_query("1")
        assert "reporting_airline" in sql.lower()

    def test_get_query_invalid(self):
        with pytest.raises(ValueError, match="Unknown query"):
            self.bm.get_query("nonexistent-query")

    def test_queries_contain_date_params(self):
        queries = self.bm.get_queries()
        for key, sql in queries.items():
            assert "{start_date}" not in sql, f"Query {key!r} has unfilled {{start_date}}"
            assert "{end_date}" not in sql, f"Query {key!r} has unfilled {{end_date}}"

    def test_postgres_queries_cast_round_arguments_to_numeric(self):
        queries = self.bm.get_queries(dialect="postgres")

        assert len(queries) == 20
        assert "ROUND(CAST(AVG(f.dep_delay) AS DECIMAL), 2)" in queries["delay-by-airport"]
        assert "ROUND(AVG(f.dep_delay), 2)" not in queries["delay-by-airport"]

    def test_query_categories(self):
        categories = self.bm.query_manager.get_categories()
        assert "ontime" in categories
        assert "delay" in categories
        assert "routes" in categories
        assert "temporal" in categories
        assert "carriers" in categories

    def test_queries_by_category(self):
        assert len(self.bm.get_queries_by_category("ontime")) == 5
        assert len(self.bm.get_queries_by_category("delay")) == 4
        assert len(self.bm.get_queries_by_category("routes")) == 4
        assert len(self.bm.get_queries_by_category("temporal")) == 4
        assert len(self.bm.get_queries_by_category("carriers")) == 3

    def test_query_info(self):
        info = self.bm.get_query_info("ontime-by-carrier")
        assert info["id"] == "1"
        assert "name" in info
        assert "description" in info
        assert info["category"] == "ontime"


class TestSchema:
    def setup_method(self):
        self.bm = FlightDataBenchmark(scale_factor=0.01)

    def test_schema_has_all_tables(self):
        schema = self.bm.get_schema()
        assert "flights" in schema
        assert "airlines" in schema
        assert "airports" in schema

    def test_flights_table_columns(self):
        schema = FLIGHT_SCHEMA["flights"]
        cols = schema["columns"]
        required = [
            "flight_id",
            "flight_date",
            "year",
            "month",
            "reporting_airline",
            "origin",
            "dest",
            "dep_delay",
            "arr_delay",
            "cancelled",
            "distance",
        ]
        for col in required:
            assert col in cols, f"Missing column: {col}"

    def test_get_create_tables_sql(self):
        sql = self.bm.get_create_tables_sql()
        assert "CREATE TABLE" in sql
        assert "flights" in sql
        assert "airlines" in sql
        assert "airports" in sql

    def test_create_tables_sql_duckdb_dialect(self):
        sql = self.bm.get_create_tables_sql(dialect="duckdb")
        assert "DOUBLE" in sql
        assert "VARCHAR" in sql


class TestBenchmarkInfo:
    def test_benchmark_info_structure(self):
        bm = FlightDataBenchmark(scale_factor=0.01)
        info = bm.get_benchmark_info()
        assert info["name"] == "Flight Data OLAP"
        assert info["num_queries"] == 20
        assert "flights" in info["tables"]
        assert "airlines" in info["tables"]
        assert "airports" in info["tables"]
        assert info["scale_factor"] == 0.01


class TestCsvLoadingConfig:
    def test_csv_loading_config_declares_headered_comma_csv(self):
        bm = FlightDataBenchmark(scale_factor=0.01)

        assert bm.get_csv_loading_config("flights") == [
            "delim=','",
            "header=true",
            "auto_detect=true",
            "ignore_errors=true",
        ]


class TestBtsSourceDecoding:
    def test_february_2002_cp1252_tail_number_does_not_abort_month(self, tmp_path, monkeypatch):
        csv_bytes = (
            b"FlightDate,Year,Month,DayofMonth,DayOfWeek,Reporting_Airline,Tail_Number\n"
            b"2002-02-02,2002,2,2,6,UA,N835\xe41\n"
        )
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("2002_02.csv", csv_bytes)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return archive.getvalue()

        monkeypatch.setattr(
            "benchbox.core.flightdata.downloader.urllib.request.urlopen", lambda *_args, **_kwargs: Response()
        )
        downloader = FlightDataDownloader(scale_factor=0.1, output_dir=tmp_path)
        decoded_source = {}
        transform = downloader._transform_bts_row

        def capture_source(bts, flight_id):
            decoded_source.update(bts)
            return transform(bts, flight_id)

        monkeypatch.setattr(downloader, "_transform_bts_row", capture_source)
        output = io.StringIO()

        rows = downloader._download_bts_month(csv.writer(output), "https://example.invalid/bts.zip", 2002, 2, 1)

        assert rows == 1
        assert decoded_source["Tail_Number"] == "N835ä1"
        generated = next(csv.reader(io.StringIO(output.getvalue())))
        assert generated[1:7] == ["2002-02-02", "2002", "2", "2", "6", "UA"]
        assert "\ufffd" not in output.getvalue()


class TestPublicWrapper:
    def test_public_import(self):
        from benchbox import FlightData

        assert FlightData is not None

    def test_public_wrapper_init(self):
        from benchbox import FlightData

        bm = FlightData(scale_factor=0.01)
        assert bm.scale_factor == 0.01

    def test_public_wrapper_get_queries(self):
        from benchbox import FlightData

        bm = FlightData(scale_factor=0.01)
        queries = bm.get_queries()
        assert len(queries) == 20

    def test_registry_has_flightdata(self):
        from benchbox.core.benchmark_registry import BENCHMARK_CLASS_NAMES, BENCHMARK_METADATA

        assert "flightdata" in BENCHMARK_METADATA
        assert "flightdata" in BENCHMARK_CLASS_NAMES
        assert BENCHMARK_CLASS_NAMES["flightdata"] == "FlightData"
