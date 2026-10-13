# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os

import pytest
from trino.exceptions import TrinoUserError

from benchbox import TPCH

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_starburst,
    pytest.mark.skipif(
        not os.getenv("STARBURST_HOST"),
        reason="Requires STARBURST_HOST environment variable. See docs/testing/live-integration-tests.md for setup.",
    ),
]


@pytest.fixture(scope="module")
def starburst_writable_catalog():
    catalog = os.getenv("STARBURST_WRITABLE_CATALOG")
    if not catalog:
        pytest.skip("Requires STARBURST_WRITABLE_CATALOG for write tests (tpch catalog is read-only)")
    return catalog


class TestLiveStarburstConnection:
    def test_connection(self, live_starburst_adapter):

        connection = live_starburst_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1
        finally:
            live_starburst_adapter.close_connection(connection)

    def test_platform_info(self, live_starburst_adapter):

        info = live_starburst_adapter.get_platform_info()
        assert info is not None
        assert info.get("platform_type") == "starburst"
        assert info.get("platform_name")
        assert "configuration" in info


class TestLiveStarburstQueryExecution:
    def test_tpch_query(self, live_starburst_adapter):
        connection = live_starburst_adapter.create_connection()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM tpch.tiny.lineitem")
                result = cursor.fetchone()
                assert result[0] > 0
            except TrinoUserError:
                cursor.execute("SELECT COUNT(*) AS cnt, SUM(x) AS total FROM (VALUES 1, 2, 3) AS t(x)")
                result = cursor.fetchone()
                assert result[0] == 3
                assert result[1] == 6
        finally:
            live_starburst_adapter.close_connection(connection)

    def test_aggregation_query(self, live_starburst_adapter):
        connection = live_starburst_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt, SUM(x) AS total FROM (VALUES 1, 2, 3) AS t(x)")
            result = cursor.fetchone()
            assert result[0] == 3
            assert result[1] == 6
        finally:
            live_starburst_adapter.close_connection(connection)

    def test_tpch_builtin_query(self, live_starburst_adapter):
        connection = live_starburst_adapter.create_connection()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT returnflag, COUNT(*) AS cnt FROM tpch.tiny.lineitem GROUP BY returnflag ORDER BY returnflag"
                )
                results = cursor.fetchall()
                assert len(results) > 0, "Expected rows from tpch.tiny.lineitem GROUP BY"
            except TrinoUserError:
                pytest.skip("tpch catalog not available in this Starburst cluster")
        finally:
            live_starburst_adapter.close_connection(connection)


class TestLiveStarburstSchemaManagement:
    def test_schema_creation(self, live_starburst_adapter, starburst_writable_catalog, unique_test_schema):
        connection = live_starburst_adapter.create_connection()
        schema_ref = f"{starburst_writable_catalog}.{unique_test_schema}"
        try:
            cursor = connection.cursor()
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_ref}")
            cursor.execute(f"SHOW SCHEMAS FROM {starburst_writable_catalog}")
            schemas = [row[0] for row in cursor.fetchall()]
            assert unique_test_schema in schemas, (
                f"Schema {unique_test_schema} not found in {starburst_writable_catalog}"
            )
        finally:
            try:
                cursor = connection.cursor()
                cursor.execute(f"DROP SCHEMA IF EXISTS {schema_ref}")
            except Exception:
                pass
            live_starburst_adapter.close_connection(connection)


class TestLiveStarburstDataLoading:
    def test_tpch_data_load(
        self,
        live_starburst_adapter,
        starburst_writable_catalog,
        unique_test_schema,
        test_scale_factor,
        test_output_dir,
    ):
        tpch = TPCH(scale_factor=test_scale_factor, output_dir=test_output_dir, verbose=False)
        data_files = tpch.generate_data()
        assert len(data_files) > 0, "No data files generated"

        schema_ref = f"{starburst_writable_catalog}.{unique_test_schema}"
        connection = live_starburst_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_ref}")
            cursor.execute(f"USE {schema_ref}")
            live_starburst_adapter.create_schema(tpch, connection)
            stats, _errors, _ = live_starburst_adapter.load_data(tpch, connection, test_output_dir)
            assert len(stats) > 0, "No tables loaded"
            assert all(count > 0 for count in stats.values()), "Some tables have zero rows"
        finally:
            try:
                cursor = connection.cursor()
                cursor.execute(f"DROP SCHEMA IF EXISTS {schema_ref} CASCADE")
            except Exception:
                pass
            live_starburst_adapter.close_connection(connection)


class TestLiveStarburstSpecificFeatures:
    def test_show_catalogs(self, live_starburst_adapter):

        connection = live_starburst_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SHOW CATALOGS")
            catalogs = [row[0] for row in cursor.fetchall()]
            assert len(catalogs) > 0
        finally:
            live_starburst_adapter.close_connection(connection)

    def test_information_schema(self, live_starburst_adapter):

        connection = live_starburst_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT table_schema, table_name FROM tpch.information_schema.tables LIMIT 5")
            results = cursor.fetchall()
            assert len(results) > 0, "Expected rows from tpch.information_schema.tables"
        finally:
            live_starburst_adapter.close_connection(connection)
