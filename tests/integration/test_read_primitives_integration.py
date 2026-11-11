# Copyright 2026 Joe Harris / BenchBox Project

# This implementation is derived from TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.read_primitives.schema import TABLES

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
class TestPrimitivesIntegration:
    def test_end_to_end_small_dataset(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            primitives_benchmark = ReadPrimitivesBenchmark(scale_factor=0.01, output_dir=temp_dir)

            file_paths = primitives_benchmark.generate_data(["region", "nation", "customer"])

            assert len(file_paths) >= 2, f"Expected at least 2 tables, got {len(file_paths)}: {list(file_paths.keys())}"
            for file_path in file_paths.values():
                assert Path(file_path).exists()
                assert Path(file_path).stat().st_size > 0

            queries = primitives_benchmark.get_queries()
            assert len(queries) > 0

            schema = primitives_benchmark.get_schema()
            assert len(schema) == 8

    def test_query_sql_validity(self):

        primitives_benchmark = ReadPrimitivesBenchmark()
        queries = primitives_benchmark.get_queries()

        test_queries = [
            "aggregation_simple",
            "filter_bigint_selective",
            "window_growing_frame",
        ]

        for query_id in test_queries:
            if query_id in queries:
                query = queries[query_id]

                assert "SELECT" in query.upper()
                assert "FROM" in query.upper()

                query_upper = query.upper()
                table_found = False
                for table_name in TABLES:
                    if table_name.upper() in query_upper:
                        table_found = True
                        break
                assert table_found, f"No valid table found in query {query_id}"


@pytest.mark.integration
class TestReadPrimitivesBenchmarkExtended:
    @pytest.fixture
    def primitives_benchmark(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            return ReadPrimitivesBenchmark(scale_factor=0.01, output_dir=temp_dir)

    def test_execute_query_direct_connection(self, primitives_benchmark: ReadPrimitivesBenchmark):

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("result1",), ("result2",)]
        mock_connection.execute.return_value = mock_cursor

        with patch.object(primitives_benchmark, "get_query") as mock_get_query:
            mock_get_query.return_value = "SELECT * FROM orders"

            result = primitives_benchmark.execute_query("aggregation_simple", mock_connection)

            mock_get_query.assert_called_once_with("aggregation_simple")
            mock_connection.execute.assert_called_once_with("SELECT * FROM orders")
            mock_cursor.fetchall.assert_called_once()
            assert result == [("result1",), ("result2",)]

    def test_execute_query_cursor_connection(self, primitives_benchmark: ReadPrimitivesBenchmark):

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("result1",), ("result2",)]
        mock_connection.cursor.return_value = mock_cursor
        del mock_connection.execute

        with patch.object(primitives_benchmark, "get_query") as mock_get_query:
            mock_get_query.return_value = "SELECT * FROM orders"

            result = primitives_benchmark.execute_query("aggregation_simple", mock_connection, {"param": "value"})

            mock_get_query.assert_called_once_with("aggregation_simple")
            mock_connection.cursor.assert_called_once()
            mock_cursor.execute.assert_called_once_with("SELECT * FROM orders")
            mock_cursor.fetchall.assert_called_once()
            assert result == [("result1",), ("result2",)]

    def test_execute_query_unsupported_connection(self, primitives_benchmark: ReadPrimitivesBenchmark):

        mock_connection = Mock()
        del mock_connection.execute
        del mock_connection.cursor

        with patch.object(primitives_benchmark, "get_query") as mock_get_query:
            mock_get_query.return_value = "SELECT * FROM orders"

            with pytest.raises(ValueError, match="Unsupported connection type"):
                primitives_benchmark.execute_query("aggregation_simple", mock_connection)

    def test_load_data_to_database_executescript_path(self, primitives_benchmark: ReadPrimitivesBenchmark):

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_file = Path(temp_dir) / "region.csv"
            csv_file.write_text(
                "0|AFRICA|lar deposits. blithely final packages cajole. regular waters are final requests. regular accounts are according to \n"
            )

            primitives_benchmark.tables = {"region": str(csv_file)}

            mock_connection = Mock()
            mock_connection.executescript = Mock()
            mock_connection.executemany = Mock()
            mock_connection.commit = Mock()

            with patch.object(primitives_benchmark, "get_create_tables_sql") as mock_get_sql:
                mock_get_sql.return_value = "CREATE TABLE region (...);"

                primitives_benchmark.load_data_to_database(mock_connection)

                mock_connection.executescript.assert_called_once()
                mock_connection.executemany.assert_called()
                mock_connection.commit.assert_called_once()

    def test_load_data_to_database_cursor_mode(self, primitives_benchmark: ReadPrimitivesBenchmark):

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_file = Path(temp_dir) / "region.csv"
            csv_file.write_text("0|AFRICA|lar deposits\n")

            primitives_benchmark.tables = {"region": str(csv_file)}

            mock_connection = Mock()
            mock_cursor = Mock()
            mock_connection.cursor.return_value = mock_cursor
            mock_connection.commit = Mock()
            del mock_connection.executescript
            del mock_connection.executemany

            with patch.object(primitives_benchmark, "get_create_tables_sql") as mock_get_sql:
                mock_get_sql.return_value = "CREATE TABLE region (...);"

                primitives_benchmark.load_data_to_database(mock_connection)

                mock_cursor.execute.assert_called()
                mock_connection.commit.assert_called_once()

    def test_load_data_to_database_no_commit(self, primitives_benchmark: ReadPrimitivesBenchmark):
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_file = Path(temp_dir) / "region.csv"
            csv_file.write_text("0|AFRICA|lar deposits\n")

            primitives_benchmark.tables = {"region": str(csv_file)}

            mock_connection = Mock()
            mock_connection.executescript = Mock()
            mock_connection.executemany = Mock()
            del mock_connection.commit

            with patch.object(primitives_benchmark, "get_create_tables_sql") as mock_get_sql:
                mock_get_sql.return_value = "CREATE TABLE region (...);"

                primitives_benchmark.load_data_to_database(mock_connection)

    def test_run_benchmark_with_categories(self, primitives_benchmark: ReadPrimitivesBenchmark):

        mock_connection = Mock()

        with patch.object(primitives_benchmark, "execute_query") as mock_execute:
            mock_execute.return_value = [("result1",)]
            with patch.object(primitives_benchmark, "get_queries_by_category") as mock_get_cat:
                mock_get_cat.return_value = {"aggregation_simple": "SELECT COUNT(*) FROM orders"}

                result = primitives_benchmark.run_benchmark(mock_connection, categories=["aggregation"], iterations=1)

                mock_get_cat.assert_called_once_with("aggregation")
                assert result["categories"] == ["aggregation"]
                assert len(result["queries"]) == 1
                assert "aggregation_simple" in result["queries"]

    def test_run_benchmark_timing_and_results(self, primitives_benchmark: ReadPrimitivesBenchmark):

        mock_connection = Mock()

        with patch.object(primitives_benchmark, "execute_query") as mock_execute:
            mock_execute.return_value = [("result1",), ("result2",), ("result3",)]
            with patch("time.perf_counter") as mock_time:
                mock_time.side_effect = [0.0, 0.5, 1.0, 1.3]

                result = primitives_benchmark.run_benchmark(
                    mock_connection, queries=["aggregation_simple"], iterations=2
                )

                query_result = result["queries"]["aggregation_simple"]
                assert query_result["category"] == "aggregation"
                assert abs(query_result["avg_time"] - 0.4) < 0.01
                assert abs(query_result["min_time"] - 0.3) < 0.01
                assert abs(query_result["max_time"] - 0.5) < 0.01
                assert query_result["iterations"][0]["rows"] == 3
                assert query_result["iterations"][1]["rows"] == 3

    def test_run_benchmark_with_errors(self, primitives_benchmark: ReadPrimitivesBenchmark):

        mock_connection = Mock()

        with patch.object(primitives_benchmark, "execute_query") as mock_execute:
            mock_execute.side_effect = Exception("Database connection error")

            result = primitives_benchmark.run_benchmark(mock_connection, queries=["aggregation_simple"], iterations=1)

            query_result = result["queries"]["aggregation_simple"]
            assert query_result["iterations"][0]["success"] is False
            assert query_result["iterations"][0]["error"] == "Database connection error"
            assert query_result["avg_time"] == 0

    def test_run_category_benchmark(self, primitives_benchmark: ReadPrimitivesBenchmark):

        mock_connection = Mock()

        with patch.object(primitives_benchmark, "run_benchmark") as mock_run:
            mock_run.return_value = {"results": "test"}

            result = primitives_benchmark.run_category_benchmark(mock_connection, "aggregation", iterations=3)

            mock_run.assert_called_once_with(mock_connection, None, 3, ["aggregation"])
            assert result == {"results": "test"}

    def test_get_benchmark_info(self, primitives_benchmark: ReadPrimitivesBenchmark):

        with patch.object(primitives_benchmark.query_manager, "get_all_queries") as mock_get_all:
            mock_get_all.return_value = {"q1": "SELECT ...", "q2": "SELECT ..."}
            with patch.object(primitives_benchmark, "get_query_categories") as mock_get_cats:
                mock_get_cats.return_value = ["aggregation", "window", "filter"]

                info = primitives_benchmark.get_benchmark_info()

                assert info["name"] == "Read Primitives Benchmark"
                assert info["version"] == "1.0"
                assert info["scale_factor"] == primitives_benchmark.scale_factor
                assert info["total_queries"] == 2
                assert info["categories"] == ["aggregation", "window", "filter"]
                assert info["schema"] == "TPC-H"
                assert len(info["tables"]) == 8

    def test_sqlite_integration_with_real_data(self, primitives_benchmark: ReadPrimitivesBenchmark):

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_file = Path(temp_dir) / "region.csv"
            csv_file.write_text(
                "0|AFRICA|lar deposits. blithely final packages cajole\n1|AMERICA|hs use ironic, even requests\n"
            )

            primitives_benchmark.tables = {"region": str(csv_file)}

            conn = sqlite3.connect(":memory:")

            try:
                primitives_benchmark.load_data_to_database(conn, tables=["region"])

                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM region")
                count = cursor.fetchone()[0]
                assert count == 2

                cursor.execute("SELECT r_regionkey, r_name FROM region ORDER BY r_regionkey")
                rows = cursor.fetchall()
                assert len(rows) == 2
                assert rows[0][0] == 0
                assert rows[0][1] == "AFRICA"
                assert rows[1][0] == 1
                assert rows[1][1] == "AMERICA"

            finally:
                conn.close()

    def test_load_data_batch_processing(self, primitives_benchmark: ReadPrimitivesBenchmark):

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_file = Path(temp_dir) / "region.csv"
            data_lines = [f"{i}|REGION_{i}|comment for region {i}" for i in range(5)]
            csv_file.write_text("\n".join(data_lines) + "\n")

            primitives_benchmark.tables = {"region": str(csv_file)}

            mock_connection = Mock()
            mock_connection.executescript = Mock()
            mock_connection.executemany = Mock()
            mock_connection.commit = Mock()

            with patch.object(primitives_benchmark, "get_create_tables_sql") as mock_get_sql:
                mock_get_sql.return_value = "CREATE TABLE region (...);"

                primitives_benchmark.load_data_to_database(mock_connection)

                call_args = mock_connection.executemany.call_args
                assert call_args is not None
                sql, data = call_args[0]
                assert "INSERT INTO region VALUES" in sql
                assert len(data) == 5
                assert data[0] == ["0", "REGION_0", "comment for region 0"]
                assert data[4] == ["4", "REGION_4", "comment for region 4"]

    def test_run_benchmark_query_category_extraction(self, primitives_benchmark: ReadPrimitivesBenchmark):

        mock_connection = Mock()

        with patch.object(primitives_benchmark, "execute_query") as mock_execute:
            mock_execute.return_value = [("result1",)]

            result = primitives_benchmark.run_benchmark(mock_connection, queries=["window_growing_frame"], iterations=1)

            query_result = result["queries"]["window_growing_frame"]
            assert query_result["category"] == "window"

    def test_run_benchmark_unknown_category(self, primitives_benchmark: ReadPrimitivesBenchmark):

        mock_connection = Mock()

        with patch.object(primitives_benchmark, "execute_query") as mock_execute:
            mock_execute.return_value = [("result1",)]

            result = primitives_benchmark.run_benchmark(mock_connection, queries=["unknown_query"], iterations=1)

            query_result = result["queries"]["unknown_query"]
            assert query_result["category"] == "unknown"

    def test_run_benchmark_inf_min_time_handling(self, primitives_benchmark: ReadPrimitivesBenchmark):

        mock_connection = Mock()

        with patch.object(primitives_benchmark, "execute_query") as mock_execute:
            mock_execute.side_effect = Exception("Database error")

            result = primitives_benchmark.run_benchmark(mock_connection, queries=["aggregation_simple"], iterations=1)

            query_result = result["queries"]["aggregation_simple"]
            assert query_result["min_time"] == float("inf")
            assert query_result["max_time"] == 0
            assert query_result["avg_time"] == 0


