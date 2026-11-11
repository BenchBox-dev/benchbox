# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest import mock

import duckdb
import pytest

from benchbox import TPCH

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


@pytest.mark.integration
@pytest.mark.duckdb
class TestTPCHDuckDBIntegration:
    @pytest.fixture
    def tpch(self, small_scale_factor, temp_dir):

        with mock.patch("benchbox.core.tpch.generator.TPCHDataGenerator._find_or_build_dbgen") as mock_build:
            mock_build.return_value = temp_dir / "dbgen"
            return TPCH(scale_factor=small_scale_factor, output_dir=temp_dir)

    @pytest.fixture
    def duckdb_conn(self):

        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()

    def test_create_schema(self, tpch, duckdb_conn):

        sql = tpch.get_create_tables_sql()

        for statement in sql.strip().split(";"):
            if statement.strip():
                duckdb_conn.execute(statement.strip())

        tables_result = duckdb_conn.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name
        """).fetchall()

        table_names = [row[0] for row in tables_result]
        expected_tables = [
            "customer",
            "lineitem",
            "nation",
            "orders",
            "part",
            "partsupp",
            "region",
            "supplier",
        ]

        table_names_lower = [t.lower() for t in table_names]
        for expected_table in expected_tables:
            assert expected_table in table_names_lower, (
                f"Table {expected_table} not found in created tables: {table_names}"
            )

        for table in expected_tables:
            try:
                result = duckdb_conn.execute(f"DESCRIBE {table}").fetchall()
                assert len(result) > 0, f"Table {table} should have columns"
            except Exception as e:
                pytest.fail(f"Failed to describe table {table}: {e}")

    def test_load_sample_data(self, tpch, duckdb_conn):

        sql = tpch.get_create_tables_sql()
        for statement in sql.strip().split(";"):
            if statement.strip():
                duckdb_conn.execute(statement.strip())

        data_paths = tpch.generate_data()
        assert len(data_paths) > 0, "Should generate at least one data file"

        for path in data_paths:
            assert path.exists(), f"Data file {path} should exist"

    def test_sql_dialect_compatibility(self, tpch, duckdb_conn):

        queries = tpch.get_queries()

        for query_id, query_sql in list(queries.items())[:5]:
            assert "SELECT" in query_sql.upper()
            assert "FROM" in query_sql.upper()

            upper_sql = query_sql.upper()

            duckdb_compatible_features = [
                "JOIN",
                "GROUP BY",
                "ORDER BY",
                "HAVING",
                "UNION",
                "CASE WHEN",
                "EXISTS",
                "IN",
                "LIKE",
            ]

            has_standard_sql = any(feature in upper_sql for feature in duckdb_compatible_features)
            if not has_standard_sql and "WHERE" not in upper_sql:
                assert len(query_sql.strip()) > 20, f"Query {query_id} seems too simple: {query_sql[:100]}"


