from __future__ import annotations

import os

import pytest

VELOX_ENDPOINT = os.environ.get("VELOX_ENDPOINT", "sc://localhost:50051")
VELOX_GLUTEN_JAR = os.environ.get("VELOX_GLUTEN_JAR", "")
VELOX_DEPLOYMENT = os.environ.get("VELOX_DEPLOYMENT", "remote")

_LIVE_ENABLED = bool(os.environ.get("VELOX_LIVE_TESTS"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_velox,
    pytest.mark.skipif(
        not _LIVE_ENABLED,
        reason="Skipping Velox live tests: set VELOX_LIVE_TESTS=1 to enable",
    ),
]

try:
    from pyspark.sql import SparkSession

    PYSPARK_AVAILABLE = True
except ImportError:
    PYSPARK_AVAILABLE = False


@pytest.mark.skipif(not PYSPARK_AVAILABLE, reason="PySpark not installed")
class TestVeloxSQLSmoke:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.velox import VeloxAdapter

        kwargs: dict = {"database": "benchbox_velox_smoke", "deployment": VELOX_DEPLOYMENT}
        if VELOX_DEPLOYMENT == "remote":
            kwargs["endpoint"] = VELOX_ENDPOINT
        else:
            kwargs["gluten_jar_path"] = VELOX_GLUTEN_JAR

        adapter = VeloxAdapter(**kwargs)
        yield adapter
        try:
            if adapter._spark_session:
                adapter.close_connection(adapter._spark_session)
        except Exception:
            pass

    def test_connection(self, adapter):
        connection = adapter.create_connection()
        adapter.close_connection(connection)

    def test_select_one(self, adapter):
        connection = adapter.create_connection()
        result = adapter.execute_query(connection, "SELECT 1 AS value", query_id="Q0", benchmark_type="tpch")
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 1
        adapter.close_connection(connection)

    def test_platform_info_fields(self, adapter):
        connection = adapter.create_connection()
        info = adapter.get_platform_info(connection=connection)
        adapter.close_connection(connection)

        assert info["platform_type"] == "velox"
        assert info["platform_name"] == "Apache Gluten + Velox"
        assert "gluten_version" in info
        assert "offheap_size" in info
        assert "client_library_version" in info
        assert "platform_version" in info

    def test_tpch_q1_velox_active(self, adapter):
        connection = adapter.create_connection()
        try:
            info = adapter.get_platform_info(connection=connection)
            assert info.get("velox_active") is True, (
                f"velox_active is not True - Gluten plugin may not be loaded. "
                f"Probe plan: {info.get('velox_probe_plan', '(not captured)')}"
            )

            connection.sql(
                "CREATE TABLE IF NOT EXISTS velox_smoke_t "
                "(l_quantity DOUBLE, l_extendedprice DOUBLE, l_discount DOUBLE) "
                "USING PARQUET"
            )
            connection.sql("INSERT INTO velox_smoke_t VALUES (1.0, 100.0, 0.1), (2.0, 200.0, 0.05)")
            plan = adapter.get_query_plan(
                connection,
                "SELECT sum(l_quantity), sum(l_extendedprice * (1 - l_discount)) FROM velox_smoke_t",
            )
            assert "Velox native execution: YES" in plan, (
                f"Expected VeloxColumnar nodes in plan but got:\n{plan[:1000]}"
            )
        finally:
            try:
                connection.sql("DROP TABLE IF EXISTS velox_smoke_t")
            except Exception:
                pass
            adapter.close_connection(connection)

    def test_query_plan_annotated(self, adapter):
        connection = adapter.create_connection()
        plan = adapter.get_query_plan(connection, "SELECT 1 AS n")
        adapter.close_connection(connection)

        assert isinstance(plan, str)
        assert len(plan) > 0
        assert "Velox native execution" in plan


