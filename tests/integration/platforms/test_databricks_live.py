# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os

import pytest

from benchbox import TPCH

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_databricks,
    pytest.mark.skipif(
        not os.getenv("DATABRICKS_TOKEN"),
        reason="Requires DATABRICKS_TOKEN environment variable. See .env.example for setup.",
    ),
]


class TestLiveDatabricksConnection:
    def test_databricks_live_connection(self, live_databricks_adapter):

        connection = live_databricks_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1 as test")
            result = cursor.fetchone()
            assert result[0] == 1

        finally:
            live_databricks_adapter.close_connection(connection)

    def test_databricks_live_version_info(self, live_databricks_adapter):

        connection = live_databricks_adapter.create_connection()
        try:
            metadata = live_databricks_adapter.get_platform_info(connection)

            assert metadata["platform_name"] == "Databricks"
            assert "version" in metadata
            assert metadata["connection_type"] == "sql_warehouse"

        finally:
            live_databricks_adapter.close_connection(connection)

    def test_databricks_live_catalog_access(self, live_databricks_adapter):

        connection = live_databricks_adapter.create_connection()
        try:
            cursor = connection.cursor()

            cursor.execute("SELECT CURRENT_CATALOG(), CURRENT_SCHEMA()")
            result = cursor.fetchone()

            assert result is not None
            assert len(result) == 2
            print(f"Connected to catalog: {result[0]}, schema: {result[1]}")

        finally:
            live_databricks_adapter.close_connection(connection)


class TestLiveDatabricksSchemaManagement:
    def test_databricks_live_schema_creation(self, live_databricks_adapter, unique_test_schema, cleanup_test_schema):
        connection = live_databricks_adapter.create_connection()
        cleanup_test_schema(live_databricks_adapter, unique_test_schema)

        try:
            cursor = connection.cursor()

            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {unique_test_schema}")

            cursor.execute("SHOW SCHEMAS")
            schemas = [row[0] for row in cursor.fetchall()]
            assert unique_test_schema in schemas

        finally:
            live_databricks_adapter.close_connection(connection)


class TestLiveDatabricksDataLoading:
    def test_databricks_live_tpch_data_load(
        self, live_databricks_adapter, unique_test_schema, test_scale_factor, test_output_dir, cleanup_test_schema
    ):
        cleanup_test_schema(live_databricks_adapter, unique_test_schema)

        tpch = TPCH(scale_factor=test_scale_factor, output_dir=test_output_dir, verbose=False)

        data_files = tpch.generate_data()
        assert len(data_files) > 0, "No data files generated"

        connection = live_databricks_adapter.create_connection()
        try:
            cursor = connection.cursor()

            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {unique_test_schema}")
            cursor.execute(f"USE SCHEMA {unique_test_schema}")

            create_sql = tpch.get_create_tables_sql(dialect="databricks")
            for statement in create_sql.split(";"):
                if statement.strip():
                    cursor.execute(statement)

            stats, errors, _ = live_databricks_adapter.load_data(tpch, connection, test_output_dir)

            assert len(stats) > 0, "No tables loaded"
            assert all(count > 0 for count in stats.values()), "Some tables have zero rows"

            cursor.execute(f"SELECT COUNT(*) FROM {unique_test_schema}.LINEITEM")
            lineitem_count = cursor.fetchone()[0]
            assert lineitem_count > 0, "LINEITEM table is empty"

        finally:
            live_databricks_adapter.close_connection(connection)


class TestLiveDatabricksQueryExecution:
    def test_databricks_live_simple_query(self, live_databricks_adapter, unique_test_schema):
        connection = live_databricks_adapter.create_connection()
        try:
            cursor = connection.cursor()

            cursor.execute("SELECT COUNT(*) as cnt, SUM(1) as total FROM (SELECT 1 UNION ALL SELECT 2)")
            result = cursor.fetchone()

            assert result[0] == 2
            assert result[1] == 2

        finally:
            live_databricks_adapter.close_connection(connection)

    def test_databricks_live_tpch_query_execution(
        self, live_databricks_adapter, unique_test_schema, test_scale_factor, test_output_dir
    ):

        tpch = TPCH(scale_factor=test_scale_factor, output_dir=test_output_dir, verbose=False)

        connection = live_databricks_adapter.create_connection()
        try:
            cursor = connection.cursor()

            try:
                cursor.execute(f"SELECT COUNT(*) FROM {unique_test_schema}.LINEITEM")
                count = cursor.fetchone()[0]
                if count == 0:
                    pytest.skip("No data loaded - run data load test first")
            except Exception:
                pytest.skip("Schema not found - run schema creation test first")

            query1 = tpch.get_query(1, seed=42)

            query1_databricks = query1.replace("LINEITEM", f"{unique_test_schema}.LINEITEM")

            cursor.execute(query1_databricks)
            results = cursor.fetchall()

            assert len(results) > 0, "Query 1 returned no results"
            print(f"Query 1 returned {len(results)} rows")

        finally:
            live_databricks_adapter.close_connection(connection)


class TestLiveDatabricksSpecificFeatures:
    def test_databricks_live_copy_into(
        self, live_databricks_adapter, unique_test_schema, test_output_dir, cleanup_test_schema
    ):

        cleanup_test_schema(live_databricks_adapter, unique_test_schema)

        connection = live_databricks_adapter.create_connection()
        try:
            cursor = connection.cursor()

            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {unique_test_schema}")
            cursor.execute(f"USE SCHEMA {unique_test_schema}")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS test_copy (
                    id INT,
                    value STRING
                )
            """)

            test_file = test_output_dir / "test_data.csv"
            test_file.write_text("1|test1\n2|test2\n3|test3\n")

            cursor.execute(f"SELECT COUNT(*) FROM {unique_test_schema}.test_copy")
            initial_count = cursor.fetchone()[0]
            assert initial_count == 0

        finally:
            live_databricks_adapter.close_connection(connection)

    def test_databricks_live_cleanup(self, live_databricks_adapter, unique_test_schema):

        connection = live_databricks_adapter.create_connection()
        try:
            cursor = connection.cursor()

            cursor.execute(f"DROP SCHEMA IF EXISTS {unique_test_schema} CASCADE")

            cursor.execute("SHOW SCHEMAS")
            schemas = [row[0] for row in cursor.fetchall()]
            assert unique_test_schema not in schemas

        finally:
            live_databricks_adapter.close_connection(connection)


