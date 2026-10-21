# Copyright 2026 Joe Harris / BenchBox Project

# This implementation is derived from TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox import SSB
from benchbox.core.ssb.benchmark import SSBBenchmark

from .fixtures.benchmark_test_mixin import BenchmarkTestMixin

pytestmark = pytest.mark.medium

GENERATE_DATA_SCALE_FACTOR = 0.01


@pytest.mark.ssb
class TestSSB:
    @pytest.fixture
    def ssb(self, small_scale_factor: float, temp_dir: Path) -> SSB:
        return SSB(
            scale_factor=small_scale_factor,
            output_dir=temp_dir,
            compress_data=False,
            compression_type="none",
        )

    def test_generate_data(self, temp_dir: Path) -> None:
        ssb = SSB(
            scale_factor=GENERATE_DATA_SCALE_FACTOR,
            output_dir=temp_dir,
            compress_data=False,
            compression_type="none",
        )
        data_paths = ssb.generate_data()

        expected_tables = ["date", "customer", "supplier", "part", "lineorder"]
        expected_row_counts = {
            "date": 2_557,
            "customer": int(30_000 * GENERATE_DATA_SCALE_FACTOR),
            "supplier": int(2_000 * GENERATE_DATA_SCALE_FACTOR),
            "part": int(200_000 * GENERATE_DATA_SCALE_FACTOR),
            "lineorder": int(6_000_000 * GENERATE_DATA_SCALE_FACTOR),
        }

        assert isinstance(data_paths, dict), "generate_data should return a dictionary"

        for table in expected_tables:
            assert table in data_paths, f"Table {table} not found in generated data"

        for _table_name, path in data_paths.items():
            assert Path(path).exists(), f"Generated file {path} does not exist"
            assert Path(path).stat().st_size >= 0, f"Generated file {path} has negative size"

        for table_name, expected_row_count in expected_row_counts.items():
            with open(data_paths[table_name], encoding="utf-8") as data_file:
                assert sum(1 for _ in data_file) == expected_row_count, table_name

    def test_get_queries(self, ssb: SSB) -> None:
        queries = ssb.get_queries()

        expected_queries = [
            "Q1.1",
            "Q1.2",
            "Q1.3",
            "Q2.1",
            "Q2.2",
            "Q2.3",
            "Q3.1",
            "Q3.2",
            "Q3.3",
            "Q3.4",
            "Q4.1",
            "Q4.2",
            "Q4.3",
        ]

        assert len(queries) == 13
        for query_id in expected_queries:
            assert query_id in queries, f"Query {query_id} not found"
            assert isinstance(queries[query_id], str)
            assert queries[query_id].strip()

            query_sql = queries[query_id].upper()
            assert "SELECT" in query_sql
            assert "FROM" in query_sql

            if query_id.startswith("Q1"):
                assert "LINEORDER" in query_sql
            elif query_id.startswith("Q2"):
                assert "LINEORDER" in query_sql
                assert any(table in query_sql for table in ["SUPPLIER", "PART"])
            elif query_id.startswith("Q3"):
                assert "LINEORDER" in query_sql
                assert "CUSTOMER" in query_sql
            elif query_id.startswith("Q4"):
                assert "LINEORDER" in query_sql
                assert any(table in query_sql for table in ["CUSTOMER", "SUPPLIER", "PART"])

    def test_get_query(self, ssb: SSB) -> None:
        query1_1 = ssb.get_query("Q1.1")
        assert isinstance(query1_1, str)
        assert "SELECT" in query1_1.upper()
        assert "LINEORDER" in query1_1.upper()

        query3_1 = ssb.get_query("Q3.1")
        assert isinstance(query3_1, str)
        assert "CUSTOMER" in query3_1.upper()
        assert "LINEORDER" in query3_1.upper()

    def test_translate_query(self, ssb: SSB, sql_dialect: str) -> None:
        ssb.get_query("Q1.1")
        translated_query = ssb.translate_query("Q1.1", dialect=sql_dialect)

        assert isinstance(translated_query, str)
        assert "SELECT" in translated_query.upper()

    def test_invalid_query_id(self, ssb: SSB) -> None:
        with pytest.raises(ValueError):
            ssb.get_query("Q5.1")

        with pytest.raises(ValueError):
            ssb.get_query("Q1.4")

    def test_get_query_with_params(self, ssb: SSB) -> None:
        param_query = ssb.get_query("Q1.1")
        assert isinstance(param_query, str)
        assert "SELECT" in param_query.upper()

        custom_params = {"year": 1994}
        param_query = ssb.get_query("Q1.1", params=custom_params)
        assert isinstance(param_query, str)
        assert "SELECT" in param_query.upper()

    def test_get_schema(self, ssb: SSB) -> None:
        schema = ssb.get_schema()

        assert isinstance(schema, dict), "Schema should be a dictionary"
        expected_tables = ["date", "customer", "supplier", "part", "lineorder"]

        for table in expected_tables:
            assert table in schema, f"Table {table} not found in schema"

        lineorder_table = schema["lineorder"]
        column_names = [col["name"] for col in lineorder_table["columns"]]
        expected_lineorder_columns = [
            "lo_orderkey",
            "lo_linenumber",
            "lo_custkey",
            "lo_partkey",
            "lo_suppkey",
            "lo_orderdate",
            "lo_orderpriority",
            "lo_shippriority",
            "lo_quantity",
            "lo_extendedprice",
            "lo_ordtotalprice",
            "lo_discount",
            "lo_revenue",
            "lo_supplycost",
            "lo_tax",
            "lo_commitdate",
            "lo_shipmode",
        ]

        for column in expected_lineorder_columns:
            assert column in column_names

        date_table = schema["date"]
        date_columns = [col["name"] for col in date_table["columns"]]
        expected_date_columns = [
            "d_datekey",
            "d_date",
            "d_dayofweek",
            "d_month",
            "d_year",
        ]

        for column in expected_date_columns:
            assert column in date_columns

    def test_get_create_tables_sql(self, ssb: SSB) -> None:
        sql = ssb.get_create_tables_sql()

        assert isinstance(sql, str)
        assert "CREATE TABLE" in sql

        expected_tables = ["date", "customer", "supplier", "part", "lineorder"]

        for table in expected_tables:
            assert f"CREATE TABLE {table}" in sql

    def test_ssb_properties(self, ssb: SSB) -> None:
        schema = ssb.get_schema()
        assert len(schema) == 5

        queries = ssb.get_queries()

        flight1_queries = [q for q in queries if q.startswith("Q1")]
        assert len(flight1_queries) == 3

        flight2_queries = [q for q in queries if q.startswith("Q2")]
        assert len(flight2_queries) == 3

        flight3_queries = [q for q in queries if q.startswith("Q3")]
        assert len(flight3_queries) == 4

        flight4_queries = [q for q in queries if q.startswith("Q4")]
        assert len(flight4_queries) == 3

    def test_query_complexity_progression(self, ssb: SSB) -> None:
        queries = ssb.get_queries()

        q1_1 = queries["Q1.1"].upper()

        q4_1 = queries["Q4.1"].upper()

        q1_1.count("JOIN") + q1_1.count("WHERE")
        q4_1.count("JOIN") + q4_1.count("WHERE")


@pytest.mark.ssb
class TestSSBBenchmarkCoverage(BenchmarkTestMixin):
    benchmark_class = SSBBenchmark
    sample_query_id = "Q1.1"
    sample_table = "lineorder"
    sample_sql = "SELECT COUNT(*) FROM lineorder"
    sample_csv_filename = "lineorder.csv"
    sample_csv_content = "1|1|1|1|19920101|1|10|20|30|40|50|60|70|80|90|100\n"

    @pytest.fixture
    def ssb_benchmark(self, small_scale_factor: float, temp_dir: Path) -> SSBBenchmark:
        return SSBBenchmark(
            scale_factor=small_scale_factor,
            output_dir=temp_dir,
            compress_data=False,
            compression_type="none",
        )

    @pytest.fixture
    def benchmark_instance(self, ssb_benchmark: SSBBenchmark) -> SSBBenchmark:
        return ssb_benchmark

    def test_run_benchmark_coverage(self, ssb_benchmark: SSBBenchmark) -> None:
        mock_connection = Mock()

        with patch.object(ssb_benchmark, "execute_query") as mock_execute:
            mock_execute.return_value = [("result1",)]
            with patch.object(ssb_benchmark.query_manager, "get_all_queries") as mock_get_all:
                mock_get_all.return_value = {"Q1.1": "SELECT COUNT(*) FROM lineorder"}

                result = ssb_benchmark.run_benchmark(mock_connection, iterations=1)

                assert result["benchmark"] == "Star Schema Benchmark"
                assert any(q["query_id"] == "Q1.1" for q in result["query_results"])
