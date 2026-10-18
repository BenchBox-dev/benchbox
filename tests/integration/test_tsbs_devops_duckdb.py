# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import datetime

import duckdb
import pytest

from benchbox import TSBSDevOps

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.duckdb
@pytest.mark.tsbs_devops
class TestTSBSDevOpsDuckDBIntegration:
    @pytest.fixture
    def tsbs_benchmark(self, temp_dir):
        return TSBSDevOps(
            scale_factor=0.1,
            output_dir=temp_dir,
            num_hosts=5,
            duration_days=1,
            interval_seconds=3600,
        )

    @pytest.fixture
    def duckdb_conn(self):
        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()

    def test_benchmark_instantiation(self, tsbs_benchmark):

        assert tsbs_benchmark.scale_factor == 0.1
        assert tsbs_benchmark.num_hosts == 5
        assert tsbs_benchmark._impl is not None

    def test_benchmark_info(self, tsbs_benchmark):

        info = tsbs_benchmark.get_benchmark_info()

        assert "name" in info
        assert "description" in info
        assert "scale_factor" in info
        assert "num_hosts" in info
        assert "duration_days" in info
        assert "num_queries" in info
        assert "query_categories" in info
        assert "tables" in info

        assert info["name"] == "TSBS DevOps"
        assert info["scale_factor"] == 0.1
        assert info["num_hosts"] == 5
        assert info["num_queries"] == 18

    def test_get_queries(self, tsbs_benchmark):

        queries = tsbs_benchmark.get_queries()

        assert len(queries) == 18

        for query_id, query_text in queries.items():
            assert isinstance(query_text, str), f"Query {query_id} should be a string"
            assert len(query_text.strip()) > 0, f"Query {query_id} should not be empty"
            assert "SELECT" in query_text.upper(), f"Query {query_id} should be a SELECT statement"

    def test_get_query_by_id(self, tsbs_benchmark):

        query = tsbs_benchmark.get_query("single-host-12-hr")
        assert isinstance(query, str)
        assert "SELECT" in query.upper()
        assert "cpu" in query.lower()

    def test_get_queries_by_category(self, tsbs_benchmark):

        aggregation_queries = tsbs_benchmark.get_queries_by_category("aggregation")
        assert len(aggregation_queries) > 0

        threshold_queries = tsbs_benchmark.get_queries_by_category("threshold")
        assert len(threshold_queries) > 0

        single_host_queries = tsbs_benchmark.get_queries_by_category("single-host")
        assert len(single_host_queries) > 0

    def test_query_info(self, tsbs_benchmark):

        info = tsbs_benchmark.get_query_info("cpu-max-all-1-hr")

        assert "id" in info
        assert "name" in info
        assert "category" in info
        assert info["category"] == "aggregation"

    def test_schema_creation(self, tsbs_benchmark, duckdb_conn):

        sql = tsbs_benchmark.get_create_tables_sql(dialect="duckdb")

        for statement in sql.strip().split(";"):
            stmt = statement.strip()
            if stmt and not stmt.startswith("--"):
                duckdb_conn.execute(stmt)

        tables_result = duckdb_conn.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name
        """).fetchall()

        table_names = [row[0].lower() for row in tables_result]

        assert "tags" in table_names, "tags table should exist"
        assert "cpu" in table_names, "cpu table should exist"
        assert "mem" in table_names, "mem table should exist"
        assert "disk" in table_names, "disk table should exist"
        assert "net" in table_names, "net table should exist"

    def test_schema_columns(self, tsbs_benchmark):

        schema = tsbs_benchmark.get_schema()

        assert "tags" in schema
        tags_columns = schema["tags"]["columns"]
        assert "hostname" in tags_columns
        assert "region" in tags_columns
        assert "datacenter" in tags_columns
        assert "service" in tags_columns

        assert "cpu" in schema
        cpu_columns = schema["cpu"]["columns"]
        assert "time" in cpu_columns
        assert "hostname" in cpu_columns
        assert "usage_user" in cpu_columns
        assert "usage_system" in cpu_columns
        assert "usage_idle" in cpu_columns

        assert "mem" in schema
        mem_columns = schema["mem"]["columns"]
        assert "time" in mem_columns
        assert "hostname" in mem_columns
        assert "total" in mem_columns
        assert "used" in mem_columns

    def test_query_sql_syntax_validity(self, tsbs_benchmark):

        queries = tsbs_benchmark.get_queries()

        for query_id, query_text in queries.items():
            upper_sql = query_text.upper()
            assert "SELECT" in upper_sql, f"Query {query_id} should have SELECT"
            assert "FROM" in upper_sql, f"Query {query_id} should have FROM"

            assert query_text.count("(") == query_text.count(")"), f"Query {query_id} should have balanced parentheses"

    def test_lastpoint_query_executes(self, tsbs_benchmark, duckdb_conn):
        duckdb_conn.execute("""
            CREATE TABLE cpu (
                time TIMESTAMP,
                hostname VARCHAR,
                usage_user DOUBLE,
                usage_system DOUBLE,
                usage_idle DOUBLE
            )
        """)
        duckdb_conn.execute("""
            INSERT INTO cpu VALUES
                ('2024-01-01 00:00:00', 'host_0', 10, 5, 85),
                ('2024-01-01 01:00:00', 'host_0', 20, 5, 75)
        """)

        rows = duckdb_conn.execute(tsbs_benchmark.get_query("lastpoint")).fetchall()

        assert rows == [("host_0", datetime(2024, 1, 1, 1, 0), 20.0, 5.0, 75.0)]

    def test_scale_factor_validation(self, temp_dir):

        with pytest.raises(ValueError, match="must be positive"):
            TSBSDevOps(scale_factor=0, output_dir=temp_dir)

        with pytest.raises(ValueError, match="must be positive"):
            TSBSDevOps(scale_factor=-1.0, output_dir=temp_dir)

    def test_generation_stats(self, tsbs_benchmark):

        stats = tsbs_benchmark.get_generation_stats()

        assert "num_hosts" in stats
        assert stats["num_hosts"] == 5
        assert "duration_days" in stats
        assert "interval_seconds" in stats
        assert "rows" in stats
        assert "total_rows" in stats


@pytest.mark.integration
@pytest.mark.duckdb
@pytest.mark.tsbs_devops
@pytest.mark.slow
class TestTSBSDevOpsDataGeneration:
    @pytest.fixture
    def tsbs_with_data(self, temp_dir):
        benchmark = TSBSDevOps(
            scale_factor=0.1,
            output_dir=temp_dir,
            num_hosts=3,
            duration_days=1,
            interval_seconds=3600,
        )
        benchmark.generate_data()
        return benchmark

    def test_data_generation_creates_files(self, tsbs_with_data):

        tables = tsbs_with_data.tables

        assert "tags" in tables
        assert "cpu" in tables
        assert "mem" in tables
        assert "disk" in tables
        assert "net" in tables

        for table_name, file_path in tables.items():
            assert file_path.exists(), f"{table_name} data file should exist"
            assert file_path.stat().st_size > 0, f"{table_name} data file should not be empty"

    def test_tags_data_structure(self, tsbs_with_data):

        import csv

        tags_file = tsbs_with_data.tables["tags"]

        with open(tags_file, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)

            expected_columns = [
                "hostname",
                "region",
                "datacenter",
                "rack",
                "os",
                "arch",
                "team",
                "service",
            ]

            for col in expected_columns:
                assert col in header, f"Column {col} should be in tags header"

            rows = list(reader)
            assert len(rows) == 3

    def test_cpu_data_structure(self, tsbs_with_data):

        import csv

        cpu_file = tsbs_with_data.tables["cpu"]

        with open(cpu_file, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)

            expected_columns = [
                "time",
                "hostname",
                "usage_user",
                "usage_system",
                "usage_idle",
            ]

            for col in expected_columns:
                assert col in header, f"Column {col} should be in cpu header"

            first_row = next(reader)
            assert len(first_row) == len(header)

    def test_data_load_to_duckdb(self, tsbs_with_data):

        conn = duckdb.connect(":memory:")

        try:
            sql = tsbs_with_data.get_create_tables_sql(dialect="duckdb")
            for statement in sql.strip().split(";"):
                stmt = statement.strip()
                if stmt and not stmt.startswith("--"):
                    conn.execute(stmt)

            for table_name, file_path in tsbs_with_data.tables.items():
                conn.execute(f"""
                    INSERT INTO {table_name}
                    SELECT * FROM read_csv('{file_path}', header=true, auto_detect=true)
                """)

            tags_count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
            assert tags_count == 3

            cpu_count = conn.execute("SELECT COUNT(*) FROM cpu").fetchone()[0]
            assert cpu_count > 0

            mem_count = conn.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
            assert mem_count > 0

        finally:
            conn.close()

    def test_queries_execute_on_data(self, tsbs_with_data):

        conn = duckdb.connect(":memory:")

        try:
            sql = tsbs_with_data.get_create_tables_sql(dialect="duckdb")
            for statement in sql.strip().split(";"):
                stmt = statement.strip()
                if stmt and not stmt.startswith("--"):
                    conn.execute(stmt)

            for table_name, file_path in tsbs_with_data.tables.items():
                conn.execute(f"""
                    INSERT INTO {table_name}
                    SELECT * FROM read_csv('{file_path}', header=true, auto_detect=true)
                """)

            test_queries = [
                "cpu-max-all-1-hr",
                "mem-by-host-1-hr",
            ]

            for query_id in test_queries:
                query_sql = tsbs_with_data.get_query(query_id)
                result = conn.execute(query_sql).fetchall()
                assert isinstance(result, list), f"Query {query_id} should return results"

        finally:
            conn.close()
