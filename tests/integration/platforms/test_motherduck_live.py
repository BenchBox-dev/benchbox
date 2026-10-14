# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os
import re

import pytest

from benchbox import TPCH

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_motherduck,
    pytest.mark.skipif(
        not os.getenv("MOTHERDUCK_TOKEN"),
        reason="Requires MOTHERDUCK_TOKEN environment variable. See docs/testing/live-integration-tests.md for setup.",
    ),
]


@pytest.fixture(scope="module")
def tpch_data(live_motherduck_adapter, test_scale_factor, test_output_dir):
    schema = f"benchbox_data_{os.getpid()}"
    tpch = TPCH(scale_factor=test_scale_factor, output_dir=test_output_dir, verbose=False)
    data_files = tpch.generate_data()
    assert len(data_files) > 0, "No data files generated"

    connection = live_motherduck_adapter.create_connection()
    try:
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        connection.execute(f"SET search_path = '{schema}'")
        live_motherduck_adapter.create_schema(tpch, connection)
        stats, _errors, _ = live_motherduck_adapter.load_data(tpch, connection, test_output_dir)
        yield tpch, stats, schema
    finally:
        try:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        except Exception:
            pass
        live_motherduck_adapter.close_connection(connection)


class TestLiveMotherDuckConnection:
    def test_connection(self, live_motherduck_adapter):

        connection = live_motherduck_adapter.create_connection()
        try:
            result = connection.execute("SELECT 1").fetchone()
            assert result[0] == 1
        finally:
            live_motherduck_adapter.close_connection(connection)

    def test_platform_info(self, live_motherduck_adapter):

        info = live_motherduck_adapter.get_platform_info()
        assert info is not None
        assert info.get("platform_type") == "motherduck"
        assert info.get("platform_name")
        assert "configuration" in info


class TestLiveMotherDuckQueryExecution:
    def test_create_and_query_table(self, live_motherduck_adapter):
        connection = live_motherduck_adapter.create_connection()
        try:
            connection.execute("DROP TABLE IF EXISTS benchbox_smoke_test")
            connection.execute(
                "CREATE TABLE benchbox_smoke_test AS "
                "SELECT * FROM (VALUES "
                "  (1, 'alpha', 10.5), "
                "  (2, 'beta', 20.3), "
                "  (3, 'gamma', 30.1)"
                ") AS t(id, name, value)"
            )
            result = connection.execute("SELECT COUNT(*) FROM benchbox_smoke_test").fetchone()
            assert result[0] == 3
        finally:
            try:
                connection.execute("DROP TABLE IF EXISTS benchbox_smoke_test")
            except Exception:
                pass
            live_motherduck_adapter.close_connection(connection)

    def test_aggregation_query(self, live_motherduck_adapter):
        connection = live_motherduck_adapter.create_connection()
        try:
            result = connection.execute(
                "SELECT COUNT(*) AS cnt, SUM(x) AS total FROM (VALUES (1), (2), (3)) AS t(x)"
            ).fetchone()
            assert result[0] == 3
            assert result[1] == 6
        finally:
            live_motherduck_adapter.close_connection(connection)

    def test_tpch_query_1(self, live_motherduck_adapter, tpch_data):
        tpch, _stats, schema = tpch_data
        connection = live_motherduck_adapter.create_connection()
        try:
            query1 = tpch.get_query(1, seed=42)
            query1_md = re.sub(r"\blineitem\b", f"{schema}.lineitem", query1)
            results = connection.execute(query1_md).fetchall()
            assert len(results) > 0, "TPC-H Query 1 returned no results"
        finally:
            live_motherduck_adapter.close_connection(connection)


class TestLiveMotherDuckSchemaManagement:
    def test_schema_creation(self, live_motherduck_adapter, unique_test_schema):
        connection = live_motherduck_adapter.create_connection()
        try:
            connection.execute(f"CREATE SCHEMA IF NOT EXISTS {unique_test_schema}")
            result = connection.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ?",
                [unique_test_schema],
            ).fetchone()
            assert result is not None, f"Schema {unique_test_schema} not found in information_schema"
        finally:
            try:
                connection.execute(f"DROP SCHEMA IF EXISTS {unique_test_schema} CASCADE")
            except Exception:
                pass
            live_motherduck_adapter.close_connection(connection)


class TestLiveMotherDuckDataLoading:
    def test_tpch_data_load(self, live_motherduck_adapter, tpch_data):

        _tpch, stats, schema = tpch_data
        assert len(stats) > 0, "No tables loaded"
        assert all(count > 0 for count in stats.values()), "Some tables have zero rows"
        connection = live_motherduck_adapter.create_connection()
        try:
            row = connection.execute(f"SELECT COUNT(*) FROM {schema}.lineitem").fetchone()
            assert row[0] > 0, "lineitem table is empty"
        finally:
            live_motherduck_adapter.close_connection(connection)


class TestLiveMotherDuckSpecificFeatures:
    def test_duckdb_extensions(self, live_motherduck_adapter):

        connection = live_motherduck_adapter.create_connection()
        try:
            result = connection.execute(
                "SELECT extension_name FROM duckdb_extensions() WHERE loaded = true LIMIT 5"
            ).fetchall()
            assert len(result) > 0
        finally:
            live_motherduck_adapter.close_connection(connection)

    def test_cloud_databases(self, live_motherduck_adapter):

        connection = live_motherduck_adapter.create_connection()
        try:
            result = connection.execute("SHOW DATABASES").fetchall()
            assert len(result) > 0, "Expected at least one database in SHOW DATABASES"
        finally:
            live_motherduck_adapter.close_connection(connection)


@pytest.fixture(scope="module")
def capture_adapter(motherduck_credentials):
    from benchbox.platforms.motherduck import MotherDuckAdapter

    return MotherDuckAdapter(capture_plans=True, **motherduck_credentials)


class TestLiveMotherDuckQueryPlanCapture:
    def test_get_query_plan_returns_text(self, capture_adapter):
        connection = capture_adapter.create_connection()
        try:
            plan = capture_adapter.get_query_plan(connection, "SELECT 1")
            assert plan is not None
            assert isinstance(plan, str)
            assert len(plan.strip()) > 0
        finally:
            capture_adapter.close_connection(connection)

    def test_capture_query_plan_returns_dag(self, capture_adapter):
        connection = capture_adapter.create_connection()
        try:
            plan, capture_ms = capture_adapter.capture_query_plan(connection, "SELECT 1", "q_test")
            assert plan is not None, "Expected a QueryPlanDAG but got None"
            assert capture_ms >= 0.0
            assert plan.logical_root is not None
        finally:
            capture_adapter.close_connection(connection)

    def test_capture_query_plan_has_fingerprint(self, capture_adapter):
        connection = capture_adapter.create_connection()
        try:
            plan, _ = capture_adapter.capture_query_plan(connection, "SELECT 1", "q_fp")
            assert plan is not None
            assert plan.plan_fingerprint
            assert len(plan.plan_fingerprint) == 64
        finally:
            capture_adapter.close_connection(connection)

    def test_capture_query_plan_fingerprint_stable(self, capture_adapter):
        connection = capture_adapter.create_connection()
        try:
            plan1, _ = capture_adapter.capture_query_plan(connection, "SELECT 1", "q_stable")
            plan2, _ = capture_adapter.capture_query_plan(connection, "SELECT 1", "q_stable")
            assert plan1 is not None and plan2 is not None
            assert plan1.plan_fingerprint == plan2.plan_fingerprint
        finally:
            capture_adapter.close_connection(connection)
