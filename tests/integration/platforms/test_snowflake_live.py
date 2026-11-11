# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import os

import pytest

from benchbox import TPCH

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_snowflake,
    pytest.mark.skipif(
        not os.getenv("SNOWFLAKE_PASSWORD"),
        reason="Requires SNOWFLAKE_PASSWORD environment variable. See .env.example for setup.",
    ),
]


class TestLiveSnowflakeConnection:
    def test_snowflake_live_connection(self, live_snowflake_adapter):

        connection = live_snowflake_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1 as test")
            result = cursor.fetchone()
            assert result[0] == 1

        finally:
            live_snowflake_adapter.close_connection(connection)

    def test_snowflake_live_version_info(self, live_snowflake_adapter):

        connection = live_snowflake_adapter.create_connection()
        try:
            metadata = live_snowflake_adapter.get_platform_info(connection)

            assert metadata["platform_name"] == "Snowflake"
            assert "version" in metadata
            assert metadata["connection_type"] in ["snowflake", "data_cloud"]

        finally:
            live_snowflake_adapter.close_connection(connection)

    def test_snowflake_live_warehouse_access(self, live_snowflake_adapter):

        connection = live_snowflake_adapter.create_connection()
        try:
            cursor = connection.cursor()

            cursor.execute("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA()")
            result = cursor.fetchone()

            assert result is not None
            assert len(result) == 2
            print(f"Connected to database: {result[0]}, schema: {result[1]}")

        finally:
            live_snowflake_adapter.close_connection(connection)


class TestLiveSnowflakeSchemaManagement:
    def test_snowflake_live_schema_creation(self, live_snowflake_adapter, unique_test_schema, cleanup_test_schema):
        connection = live_snowflake_adapter.create_connection()
        cleanup_test_schema(live_snowflake_adapter, unique_test_schema)

        try:
            cursor = connection.cursor()

            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {unique_test_schema}")

            cursor.execute("SHOW SCHEMAS")
            schemas = [row[1] for row in cursor.fetchall()]
            assert unique_test_schema.upper() in [s.upper() for s in schemas]

        finally:
            live_snowflake_adapter.close_connection(connection)


class TestLiveSnowflakeDataLoading:
    def test_snowflake_live_tpch_data_load(
        self, live_snowflake_adapter, unique_test_schema, test_scale_factor, test_output_dir, cleanup_test_schema
    ):
        cleanup_test_schema(live_snowflake_adapter, unique_test_schema)

        tpch = TPCH(scale_factor=test_scale_factor, output_dir=test_output_dir, verbose=False)

        data_files = tpch.generate_data()
        assert len(data_files) > 0, "No data files generated"

        connection = live_snowflake_adapter.create_connection()
        try:
            cursor = connection.cursor()

            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {unique_test_schema}")
            cursor.execute(f"USE SCHEMA {unique_test_schema}")

            create_sql = tpch.get_create_tables_sql(dialect="snowflake")
            for statement in create_sql.split(";"):
                if statement.strip():
                    cursor.execute(statement)

            stats, errors, _ = live_snowflake_adapter.load_data(tpch, connection, test_output_dir)

            assert len(stats) > 0, "No tables loaded"
            assert all(count > 0 for count in stats.values()), "Some tables have zero rows"

            cursor.execute(f"SELECT COUNT(*) FROM {unique_test_schema}.LINEITEM")
            lineitem_count = cursor.fetchone()[0]
            assert lineitem_count > 0, "LINEITEM table is empty"

        finally:
            live_snowflake_adapter.close_connection(connection)


class TestLiveSnowflakeQueryExecution:
    def test_snowflake_live_simple_query(self, live_snowflake_adapter, unique_test_schema):
        connection = live_snowflake_adapter.create_connection()
        try:
            cursor = connection.cursor()

            cursor.execute("SELECT COUNT(*) as cnt, SUM(1) as total FROM (SELECT 1 UNION ALL SELECT 2)")
            result = cursor.fetchone()

            assert result[0] == 2
            assert result[1] == 2

        finally:
            live_snowflake_adapter.close_connection(connection)

    def test_snowflake_live_tpch_query_execution(
        self, live_snowflake_adapter, unique_test_schema, test_scale_factor, test_output_dir
    ):

        tpch = TPCH(scale_factor=test_scale_factor, output_dir=test_output_dir, verbose=False)

        connection = live_snowflake_adapter.create_connection()
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

            query1_snowflake = query1.replace("LINEITEM", f"{unique_test_schema}.LINEITEM")

            cursor.execute(query1_snowflake)
            results = cursor.fetchall()

            assert len(results) > 0, "Query 1 returned no results"
            print(f"Query 1 returned {len(results)} rows")

        finally:
            live_snowflake_adapter.close_connection(connection)


class TestLiveSnowflakeSpecificFeatures:
    def test_snowflake_live_put_copy(
        self, live_snowflake_adapter, unique_test_schema, test_output_dir, cleanup_test_schema
    ):

        cleanup_test_schema(live_snowflake_adapter, unique_test_schema)

        connection = live_snowflake_adapter.create_connection()
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
            live_snowflake_adapter.close_connection(connection)

    def test_snowflake_live_cleanup(self, live_snowflake_adapter, unique_test_schema):

        connection = live_snowflake_adapter.create_connection()
        try:
            cursor = connection.cursor()

            cursor.execute(f"DROP SCHEMA IF EXISTS {unique_test_schema} CASCADE")

            cursor.execute("SHOW SCHEMAS")
            schemas = [row[1].upper() for row in cursor.fetchall()]
            assert unique_test_schema.upper() not in schemas

        finally:
            live_snowflake_adapter.close_connection(connection)


@pytest.fixture
def live_snowflake_adapter_with_capture(snowflake_credentials):
    from benchbox.platforms.snowflake import SnowflakeAdapter

    adapter = SnowflakeAdapter(**{**snowflake_credentials, "capture_plans": True})
    yield adapter


class TestLiveSnowflakeQueryPlanCapture:
    def test_get_query_plan_returns_json(self, live_snowflake_adapter):
        connection = live_snowflake_adapter.create_connection()
        try:
            plan = live_snowflake_adapter.get_query_plan(connection, "SELECT 1")
            assert plan is not None
            assert len(plan) > 0
            parsed = json.loads(plan)
            assert parsed is not None
        finally:
            live_snowflake_adapter.close_connection(connection)

    def test_capture_query_plan_returns_dag(self, live_snowflake_adapter_with_capture):
        adapter = live_snowflake_adapter_with_capture
        connection = adapter.create_connection()
        try:
            plan, _ = adapter.capture_query_plan(connection, "SELECT 1", "q_test")
            assert plan is not None, "Expected a QueryPlanDAG but got None"
            assert plan.logical_root is not None
        finally:
            adapter.close_connection(connection)

    def test_capture_query_plan_has_fingerprint(self, live_snowflake_adapter_with_capture):
        adapter = live_snowflake_adapter_with_capture
        connection = adapter.create_connection()
        try:
            plan, _ = adapter.capture_query_plan(connection, "SELECT 1", "q_fp")
            assert plan is not None
            assert plan.plan_fingerprint
            assert len(plan.plan_fingerprint) == 64
        finally:
            adapter.close_connection(connection)

    def test_capture_query_plan_fingerprint_stable(self, live_snowflake_adapter_with_capture):
        adapter = live_snowflake_adapter_with_capture
        connection = adapter.create_connection()
        try:
            plan1, _ = adapter.capture_query_plan(connection, "SELECT 1", "q_fp_a")
            plan2, _ = adapter.capture_query_plan(connection, "SELECT 1", "q_fp_b")
            assert plan1 is not None
            assert plan2 is not None
            assert plan1.plan_fingerprint == plan2.plan_fingerprint
        finally:
            adapter.close_connection(connection)

    def test_execute_query_attaches_plan(self, live_snowflake_adapter_with_capture):
        adapter = live_snowflake_adapter_with_capture
        connection = adapter.create_connection()
        try:
            result = adapter.execute_query(connection, "SELECT 1", "q_exec", validate_row_count=False)
            assert result["status"] == "SUCCESS"
            assert result["query_plan"] is not None
            assert result["plan_fingerprint"] == result["query_plan"].plan_fingerprint
            assert result["plan_capture_time_ms"] is not None
        finally:
            adapter.close_connection(connection)


