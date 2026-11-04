# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import json

import pytest

from benchbox.platforms.postgresql import PostgreSQLAdapter

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_postgresql,
]


@pytest.fixture
def postgresql_adapter():
    skip_unless_docker_service("localhost", 5432, platform="PostgreSQL")
    adapter = PostgreSQLAdapter(
        host="localhost",
        port=5432,
        username="benchbox",
        password="benchbox",
        database="benchbox_test",
    )
    adapter.skip_database_management = True
    yield adapter


@pytest.fixture
def postgresql_adapter_with_capture():
    skip_unless_docker_service("localhost", 5432, platform="PostgreSQL")
    adapter = PostgreSQLAdapter(
        host="localhost",
        port=5432,
        username="benchbox",
        password="benchbox",
        database="benchbox_test",
        capture_plans=True,
    )
    adapter.skip_database_management = True
    yield adapter


class TestLivePostgreSQLConnection:
    def test_connection(self, postgresql_adapter):

        connection = postgresql_adapter.create_connection()
        try:
            assert connection is not None
        finally:
            postgresql_adapter.close_connection(connection)

    def test_platform_info(self, postgresql_adapter):

        info = postgresql_adapter.get_platform_info()
        assert info is not None
        assert info["platform_type"] == "postgresql"


class TestLivePostgreSQLQueryExecution:
    def test_create_schema(self, postgresql_adapter):

        connection = postgresql_adapter.create_connection()
        try:
            postgresql_adapter.execute_query(
                connection,
                "CREATE SCHEMA IF NOT EXISTS benchbox_test_schema",
                query_id="Q0",
                benchmark_type="tpch",
            )
            result = postgresql_adapter.execute_query(
                connection,
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'benchbox_test_schema'",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            postgresql_adapter.close_connection(connection)

    def test_execute_query(self, postgresql_adapter):
        connection = postgresql_adapter.create_connection()
        try:
            result = postgresql_adapter.execute_query(
                connection,
                "SELECT 1",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            postgresql_adapter.close_connection(connection)


class TestLivePostgreSQLQueryPlanCapture:
    def test_get_query_plan_returns_json(self, postgresql_adapter):
        connection = postgresql_adapter.create_connection()
        try:
            plan = postgresql_adapter.get_query_plan(connection, "SELECT 1")
            assert plan is not None
            assert len(plan) > 0
            parsed = json.loads(plan)
            assert isinstance(parsed, list)
            assert len(parsed) > 0
        finally:
            postgresql_adapter.close_connection(connection)

    def test_capture_query_plan_returns_dag(self, postgresql_adapter_with_capture):
        adapter = postgresql_adapter_with_capture
        connection = adapter.create_connection()
        try:
            plan, capture_ms = adapter.capture_query_plan(connection, "SELECT 1", "q_test")
            assert plan is not None, "Expected a QueryPlanDAG but got None"
            assert capture_ms >= 0.0
            assert plan.logical_root is not None
        finally:
            adapter.close_connection(connection)

    def test_capture_query_plan_has_fingerprint(self, postgresql_adapter_with_capture):
        adapter = postgresql_adapter_with_capture
        connection = adapter.create_connection()
        try:
            plan, _ = adapter.capture_query_plan(connection, "SELECT 1", "q_fp")
            assert plan is not None
            assert plan.plan_fingerprint
            assert len(plan.plan_fingerprint) == 64
        finally:
            adapter.close_connection(connection)

    def test_capture_query_plan_fingerprint_stable(self, postgresql_adapter_with_capture):
        adapter = postgresql_adapter_with_capture
        connection = adapter.create_connection()
        try:
            plan1, _ = adapter.capture_query_plan(connection, "SELECT 1", "q_fp_a")
            plan2, _ = adapter.capture_query_plan(connection, "SELECT 1", "q_fp_b")
            assert plan1 is not None
            assert plan2 is not None
            assert plan1.plan_fingerprint == plan2.plan_fingerprint
        finally:
            adapter.close_connection(connection)


@pytest.fixture
def refresh_schemas():
    import uuid

    skip_unless_docker_service("localhost", 5432, platform="PostgreSQL")
    suffix = uuid.uuid4().hex[:8]
    adapters = []
    connections = []
    for role in ("native", "values", "atomic"):
        adapter = PostgreSQLAdapter(
            host="localhost",
            port=5432,
            username="benchbox",
            password="benchbox",
            database="benchbox_test",
            schema=f"rf1_{role}_{suffix}",
        )
        adapter.skip_database_management = True
        connection = adapter.create_connection()
        adapters.append(adapter)
        connections.append(connection)
    yield dict(zip(("native", "values", "atomic"), zip(adapters, connections, strict=True), strict=True))
    for adapter, connection in zip(adapters, connections, strict=True):
        connection.rollback()
        connection.execute(f'DROP SCHEMA IF EXISTS "{adapter.schema}" CASCADE')
        connection.commit()
        adapter.close_connection(connection)


def _refresh_connection(adapter, connection, *, native: bool):
    from unittest.mock import MagicMock

    from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection

    return PlatformAdapterConnection(
        adapter.new_stream_connection(connection), adapter if native else MagicMock(), maintenance_mode=True
    )


@pytest.mark.slow
class TestLivePostgreSQLTPCHRefreshIngest:
    def test_native_rf1_matches_values_path_for_every_column(self, refresh_schemas, tmp_path):
        from tests.integration._tpch_refresh_utils import _check_native_rf1_matches_values, _load_sf001_base_postgres

        native = refresh_schemas["native"]
        values = refresh_schemas["values"]

        _check_native_rf1_matches_values(
            _refresh_connection(*native, native=True),
            _refresh_connection(*values, native=False),
            tmp_path,
            _load_sf001_base_postgres,
        )

    def test_native_rf1_bad_row_leaves_no_rows_and_connection_usable(self, refresh_schemas, tmp_path):
        from tests.integration._tpch_refresh_utils import _check_rf1_bad_row_is_atomic, _load_sf001_base_postgres

        refresh_connection = _refresh_connection(*refresh_schemas["atomic"], native=True)

        _check_rf1_bad_row_is_atomic(refresh_connection, tmp_path, _load_sf001_base_postgres)

        assert refresh_connection.execute("SELECT 1").fetchone()[0] == 1
