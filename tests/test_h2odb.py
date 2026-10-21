# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox import H2ODB
from benchbox.core.h2odb.benchmark import H2OBenchmark as H2ODBBenchmark
from benchbox.utils.path_utils import resolve_benchmark_runs_dir

from .fixtures.benchmark_test_mixin import BenchmarkTestMixin

pytestmark = pytest.mark.medium

GENERATE_DATA_SCALE_FACTOR = 0.01


@pytest.mark.h2odb
class TestH2ODB:
    @pytest.fixture
    def h2odb(self, small_scale_factor: float, temp_dir: Path) -> H2ODB:
        return H2ODB(scale_factor=small_scale_factor, output_dir=temp_dir)

    def test_generate_data(self, temp_dir: Path) -> None:
        h2odb = H2ODB(scale_factor=GENERATE_DATA_SCALE_FACTOR, output_dir=temp_dir)
        data_paths = h2odb.generate_data()

        expected_tables = ["trips"]
        expected_row_counts = {"trips": int(10_000_000 * GENERATE_DATA_SCALE_FACTOR)}

        assert isinstance(data_paths, dict), "generate_data should return a dictionary"

        for table in expected_tables:
            assert table in data_paths, f"Table {table} not found in generated data"

        for _table_name, path in data_paths.items():
            assert Path(path).exists(), f"Generated file {path} does not exist"

        for table_name, expected_row_count in expected_row_counts.items():
            with open(data_paths[table_name], encoding="utf-8") as data_file:
                assert sum(1 for _ in data_file) == expected_row_count, table_name

    def test_get_queries(self, h2odb: H2ODB) -> None:
        queries = h2odb.get_queries()

        expected_queries = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q10"]

        assert len(queries) == 10
        for query_id in expected_queries:
            assert query_id in queries, f"Query {query_id} not found"
            assert isinstance(queries[query_id], str)
            assert queries[query_id].strip()

            query_sql = queries[query_id].upper()
            assert "SELECT" in query_sql
            assert "FROM" in query_sql
            assert "TRIPS" in query_sql

            if "GROUP BY" in query_sql or "COUNT" in query_sql or "SUM" in query_sql or "AVG" in query_sql:
                pass

    def test_get_query(self, h2odb: H2ODB) -> None:
        query1 = h2odb.get_query("Q1")
        assert isinstance(query1, str)
        assert "SELECT" in query1.upper()
        assert "TRIPS" in query1.upper()

        query5 = h2odb.get_query("Q5")
        assert isinstance(query5, str)
        assert "SELECT" in query5.upper()
        assert "TRIPS" in query5.upper()

    def test_translate_query(self, h2odb: H2ODB, sql_dialect: str) -> None:
        h2odb.get_query("Q1")
        translated_query = h2odb.translate_query("Q1", dialect=sql_dialect)

        assert isinstance(translated_query, str)
        assert "SELECT" in translated_query.upper()

    def test_invalid_query_id(self, h2odb: H2ODB) -> None:
        with pytest.raises(ValueError):
            h2odb.get_query("Q11")

        with pytest.raises(ValueError):
            h2odb.get_query("Q0")

    def test_get_query_params(self, h2odb: H2ODB) -> None:
        param_query = h2odb.get_query("Q1")
        assert isinstance(param_query, str)
        assert "SELECT" in param_query.upper()

        with pytest.raises(ValueError, match="H2O DB queries are static and don't accept parameters"):
            h2odb.get_query("Q1", params={"param1": "value1"})

    def test_get_schema(self, h2odb: H2ODB) -> None:
        schema = h2odb.get_schema()

        assert isinstance(schema, dict), "Schema should be a dictionary"
        assert "trips" in schema, "trips table not found in schema"

        trips_table = schema["trips"]
        column_names = [col["name"] for col in trips_table["columns"]]

        expected_columns = [
            "vendor_id",
            "pickup_datetime",
            "dropoff_datetime",
            "passenger_count",
            "trip_distance",
            "pickup_longitude",
            "pickup_latitude",
            "dropoff_longitude",
            "dropoff_latitude",
            "payment_type",
            "fare_amount",
            "extra",
            "mta_tax",
            "tip_amount",
            "tolls_amount",
            "total_amount",
        ]

        for column in expected_columns:
            assert column in column_names, f"Column {column} not found in trips table"

    def test_get_create_tables_sql(self, h2odb: H2ODB) -> None:
        sql = h2odb.get_create_tables_sql()

        assert isinstance(sql, str)
        assert "CREATE TABLE" in sql
        assert "trips" in sql

        assert "vendor_id" in sql
        assert "pickup_datetime" in sql
        assert "fare_amount" in sql

    def test_H2ODBBenchmark_properties(self, h2odb: H2ODB) -> None:
        schema = h2odb.get_schema()
        assert len(schema) == 1

        queries = h2odb.get_queries()
        assert len(queries) == 10

        all_queries_text = " ".join(queries.values()).upper()

        analytical_keywords = ["COUNT", "SUM", "AVG", "GROUP BY", "ORDER BY"]
        found_keywords = [kw for kw in analytical_keywords if kw in all_queries_text]
        assert len(found_keywords) >= 3, "H2O queries should contain various analytical operations"

    def test_taxi_data_focus(self, h2odb: H2ODB) -> None:
        schema = h2odb.get_schema()
        trips_table = schema["trips"]
        column_names = [col["name"] for col in trips_table["columns"]]

        taxi_columns = [
            "pickup_datetime",
            "dropoff_datetime",
            "trip_distance",
            "fare_amount",
            "tip_amount",
            "passenger_count",
        ]

        for col in taxi_columns:
            assert col in column_names, f"Taxi-specific column {col} not found"

        queries = h2odb.get_queries()
        all_queries_text = " ".join(queries.values()).upper()

        taxi_keywords = ["TRIP", "FARE", "TIP", "PASSENGER", "PICKUP", "DROPOFF"]
        found_keywords = [kw for kw in taxi_keywords if kw in all_queries_text]
        assert len(found_keywords) >= 2, "H2O queries should reference taxi/trip concepts"

    def test_analytical_query_patterns(self, h2odb: H2ODB) -> None:
        queries = h2odb.get_queries()

        has_aggregation = False
        has_grouping = False

        for _query_id, query_sql in queries.items():
            query_upper = query_sql.upper()

            if any(agg in query_upper for agg in ["COUNT", "SUM", "AVG", "MIN", "MAX"]):
                has_aggregation = True

            if "GROUP BY" in query_upper:
                has_grouping = True

            if "ORDER BY" in query_upper:
                pass

            if "WHERE" in query_upper:
                pass

        assert has_aggregation, "H2O  H2ODBBenchmark should include aggregation queries"
        assert has_grouping, "H2O  H2ODBBenchmark should include grouping queries"


@pytest.mark.h2odb
class TestH2ODBBenchmarkDirectly(BenchmarkTestMixin):
    benchmark_class = H2ODBBenchmark
    sample_query_id = "Q1"
    sample_table = "trips"
    sample_sql = "SELECT COUNT(*) FROM trips"
    sample_csv_filename = "trips.csv"
    sample_csv_content = "2023-01-01 12:00:00|2023-01-01 12:30:00|5.5|15.50|3.00|2|40.7589|-73.9851|40.7614|-73.9776\n"
    get_query_passes_params = False

    @pytest.fixture
    def h2odb_benchmark(self, small_scale_factor: float, temp_dir: Path) -> H2ODBBenchmark:
        return H2ODBBenchmark(scale_factor=small_scale_factor, output_dir=temp_dir)

    @pytest.fixture
    def benchmark_instance(self, h2odb_benchmark: H2ODBBenchmark) -> H2ODBBenchmark:
        return h2odb_benchmark

    def test_init_with_default_output_dir(self) -> None:
        h2odb = H2ODBBenchmark(scale_factor=0.01)
        assert h2odb.scale_factor == 0.01
        assert h2odb.output_dir == resolve_benchmark_runs_dir() / "datagen" / "h2odb_sf001"
        assert h2odb._name == "H2O Database Benchmark"
        assert h2odb._version == "1.0"

    def test_generate_data_validation(self, h2odb_benchmark: H2ODBBenchmark) -> None:
        with pytest.raises(ValueError, match="Unsupported output format"):
            h2odb_benchmark.generate_data(output_format="json")

        with pytest.raises(ValueError, match="Invalid table names"):
            h2odb_benchmark.generate_data(tables=["invalid_table"])

    def test_load_data_to_database_batch_processing(self, h2odb_benchmark: H2ODBBenchmark) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_file = Path(temp_dir) / "trips.csv"
            rows = []
            for i in range(15000):
                rows.append(
                    f"2023-01-01 12:00:00|2023-01-01 12:30:00|{i}.5|15.50|3.00|2|40.7589|-73.9851|40.7614|-73.9776"
                )
            csv_file.write_text("\n".join(rows) + "\n")

            h2odb_benchmark.tables = {"trips": str(csv_file)}

            mock_connection = Mock()
            mock_connection.executescript = Mock()
            mock_connection.executemany = Mock()
            mock_connection.commit = Mock()

            with patch.object(H2ODBBenchmark, "get_create_tables_sql") as mock_get_sql:
                mock_get_sql.return_value = "CREATE TABLE trips (...);"

                h2odb_benchmark.load_data_to_database(mock_connection)

                assert mock_connection.executemany.call_count >= 2

    def test_run_H2ODBBenchmark_default_queries(self, h2odb_benchmark: H2ODBBenchmark) -> None:
        mock_connection = Mock()

        with patch.object(h2odb_benchmark, "execute_query") as mock_execute:
            mock_execute.return_value = [("result1",), ("result2",)]
            with patch.object(h2odb_benchmark.query_manager, "get_all_queries") as mock_get_all:
                mock_get_all.return_value = {
                    "Q1": "SELECT COUNT(*) FROM trips",
                    "Q2": "SELECT AVG(fare_amount) FROM trips",
                }

                result = h2odb_benchmark.run_benchmark(mock_connection, iterations=1)

                assert result["benchmark"] == "H2O Database Benchmark"
                assert result["scale_factor"] == h2odb_benchmark.scale_factor
                assert result["iterations"] == 1
                assert len(result["queries"]) == 2
                assert "Q1" in result["queries"]
                assert "Q2" in result["queries"]

    def test_run_H2ODBBenchmark_timing_calculation(self, h2odb_benchmark: H2ODBBenchmark) -> None:
        mock_connection = Mock()

        with patch.object(H2ODBBenchmark, "execute_query") as mock_execute:
            mock_execute.return_value = [("result1",), ("result2",)]
            mock_clock = Mock(side_effect=[0.0, 0.5, 1.0, 1.2])
            with (
                patch("benchbox.core.h2odb.benchmark.mono_time", mock_clock),
                patch("benchbox.utils.clock.mono_time", mock_clock),
            ):
                result = h2odb_benchmark.run_benchmark(mock_connection, queries=["Q1"], iterations=2)

                query_result = result["queries"]["Q1"]
                assert abs(query_result["avg_time"] - 0.35) < 0.01
                assert abs(query_result["min_time"] - 0.2) < 0.01
                assert abs(query_result["max_time"] - 0.5) < 0.01

    def test_run_H2ODBBenchmark_with_exceptions(self, h2odb_benchmark: H2ODBBenchmark) -> None:
        mock_connection = Mock()

        with patch.object(H2ODBBenchmark, "execute_query") as mock_execute:
            mock_execute.side_effect = Exception("Database error")

            result = h2odb_benchmark.run_benchmark(mock_connection, queries=["Q1"], iterations=1)

            query_result = result["queries"]["Q1"]
            assert query_result["iterations"][0]["success"] is False
            assert query_result["iterations"][0]["error"] == "Database error"
            assert query_result["avg_time"] == 0

    def test_sqlite_integration(self, h2odb_benchmark: H2ODBBenchmark) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_file = Path(temp_dir) / "trips.csv"
            csv_file.write_text(
                "1|2023-01-01 12:00:00|2023-01-01 12:30:00|2|5.5|-73.9851|40.7589|1|N|-73.9776|40.7614|1|15.50|0.50|0.50|3.00|0.00|0.30|19.80|161|239|2.50\n2|2023-01-01 13:00:00|2023-01-01 13:15:00|1|2.1|-73.9934|40.7505|1|N|-73.9889|40.7490|2|8.50|0.00|0.50|1.50|0.00|0.30|10.80|230|186|2.50\n"
            )

            h2odb_benchmark.tables = {"trips": str(csv_file)}

            conn = sqlite3.connect(":memory:")

            try:
                h2odb_benchmark.load_data_to_database(conn, tables=["trips"])

                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM trips")
                count = cursor.fetchone()[0]
                assert count == 2

                cursor.execute("SELECT pickup_datetime, trip_distance FROM trips ORDER BY pickup_datetime")
                rows = cursor.fetchall()
                assert len(rows) == 2
                assert rows[0][0] == "2023-01-01 12:00:00"
                assert float(rows[0][1]) == 5.5

            finally:
                conn.close()

    def test_schema_methods_delegation(self, h2odb_benchmark: H2ODBBenchmark) -> None:
        schema = h2odb_benchmark.get_schema()
        assert isinstance(schema, dict)
        assert "trips" in schema

        sql = h2odb_benchmark.get_create_tables_sql()
        assert isinstance(sql, str)
        assert "CREATE TABLE" in sql

    def test_get_all_queries_method(self, h2odb_benchmark: H2ODBBenchmark) -> None:
        with patch.object(h2odb_benchmark.query_manager, "get_all_queries") as mock_get_all:
            mock_get_all.return_value = {"Q1": "SELECT ...", "Q2": "SELECT ..."}

            result = h2odb_benchmark.get_all_queries()

            mock_get_all.assert_called_once()
            assert result == {"Q1": "SELECT ...", "Q2": "SELECT ..."}

    def test_csv_parsing_edge_cases(self, h2odb_benchmark: H2ODBBenchmark) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_file = Path(temp_dir) / "trips.csv"
            csv_file.write_text(
                "2023-01-01 12:00:00|2023-01-01 12:30:00|5.5|15.50|3.00|2|40.7589|-73.9851|40.7614|-73.9776\n"
            )

            h2odb_benchmark.tables = {"trips": str(csv_file)}

            mock_connection = Mock()
            mock_connection.executescript = Mock()
            mock_connection.executemany = Mock()
            mock_connection.commit = Mock()

            with patch.object(H2ODBBenchmark, "get_create_tables_sql") as mock_get_sql:
                mock_get_sql.return_value = "CREATE TABLE trips (...);"

                h2odb_benchmark.load_data_to_database(mock_connection)

                call_args = mock_connection.executemany.call_args
                assert call_args is not None
                sql, data = call_args[0]
                assert "INSERT INTO trips VALUES" in sql
                assert len(data) == 1
                assert data[0][0] == "2023-01-01 12:00:00"
                assert data[0][1] == "2023-01-01 12:30:00"
                assert data[0][2] == "5.5"
