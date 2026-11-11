# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import time
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.base.cloud_spark.session import (
    CloudSparkSessionManager,
    DatabricksConnectSessionManager,
    LivySessionManager,
    SessionConfig,
    SessionMetrics,
    SessionProtocol,
    SessionState,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSessionProtocol:
    def test_protocol_values(self):

        assert SessionProtocol.LIVY.value == "livy"
        assert SessionProtocol.SPARK_CONNECT.value == "spark_connect"
        assert SessionProtocol.DATABRICKS_CONNECT.value == "databricks_connect"
        assert SessionProtocol.NATIVE_SDK.value == "native_sdk"


class TestSessionState:
    def test_state_values(self):

        assert SessionState.NOT_STARTED.value == "not_started"
        assert SessionState.STARTING.value == "starting"
        assert SessionState.IDLE.value == "idle"
        assert SessionState.BUSY.value == "busy"
        assert SessionState.SHUTTING_DOWN.value == "shutting_down"
        assert SessionState.ERROR.value == "error"
        assert SessionState.DEAD.value == "dead"


class TestSessionConfig:
    def test_default_values(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )

        assert config.protocol == SessionProtocol.LIVY
        assert config.port == 443
        assert config.session_name == "benchbox-session"
        assert config.driver_memory == "4g"
        assert config.executor_memory == "4g"
        assert config.executor_cores == 2
        assert config.num_executors is None
        assert config.session_start_timeout == 300
        assert config.statement_timeout == 3600
        assert config.idle_timeout == 600
        assert config.track_cost is True
        assert config.cost_unit == "DBU"

    def test_custom_values(self):

        config = SessionConfig(
            protocol=SessionProtocol.DATABRICKS_CONNECT,
            endpoint="https://workspace.cloud.databricks.com",
            port=8443,
            session_name="my-benchmark",
            driver_memory="8g",
            executor_memory="16g",
            executor_cores=4,
            num_executors=10,
            spark_conf={"spark.sql.shuffle.partitions": "200"},
            session_start_timeout=600,
        )

        assert config.protocol == SessionProtocol.DATABRICKS_CONNECT
        assert config.port == 8443
        assert config.session_name == "my-benchmark"
        assert config.driver_memory == "8g"
        assert config.executor_memory == "16g"
        assert config.num_executors == 10
        assert config.spark_conf["spark.sql.shuffle.partitions"] == "200"


class TestSessionMetrics:
    def test_duration_not_started(self):

        metrics = SessionMetrics()
        assert metrics.duration_seconds == 0.0

    def test_duration_in_progress(self):
        from benchbox.utils.clock import mono_time

        metrics = SessionMetrics(start_time=mono_time() - 10)

        assert 9.5 <= metrics.duration_seconds <= 11.0

    def test_duration_completed(self):
        from benchbox.utils.clock import mono_time

        now = mono_time()
        metrics = SessionMetrics(start_time=now - 60, end_time=now - 30)

        assert metrics.duration_seconds == pytest.approx(30.0)

    def test_duration_fallback_for_legacy_wall_clock_values(self):
        start = time.time() - 45.0
        end = time.time() - 15.0

        metrics = SessionMetrics(start_time=start, end_time=end)

        assert 29.0 <= metrics.duration_seconds <= 31.0

    def test_default_counters(self):

        metrics = SessionMetrics()

        assert metrics.statements_executed == 0
        assert metrics.bytes_scanned == 0
        assert metrics.bytes_shuffled == 0
        assert metrics.cost_units == 0.0


class TestCloudSparkSessionManagerFactories:
    def test_for_emr(self):

        manager = CloudSparkSessionManager.for_emr(
            cluster_id="j-XXXXXXXXXXXXX",
            region="us-west-2",
        )

        assert isinstance(manager, LivySessionManager)
        assert manager.config.protocol == SessionProtocol.LIVY
        assert "j-XXXXXXXXXXXXX" in manager.config.endpoint
        assert "us-west-2" in manager.config.endpoint
        assert manager.config.credentials["region"] == "us-west-2"

    def test_for_dataproc(self):

        manager = CloudSparkSessionManager.for_dataproc(
            project_id="my-project",
            region="us-central1",
            cluster_name="benchmark-cluster",
        )

        assert isinstance(manager, LivySessionManager)
        assert manager.config.protocol == SessionProtocol.LIVY
        assert "benchmark-cluster" in manager.config.endpoint
        assert "my-project" in manager.config.endpoint
        assert manager.config.credentials["project_id"] == "my-project"

    def test_for_synapse(self):

        manager = CloudSparkSessionManager.for_synapse(
            workspace_name="my-workspace",
            spark_pool_name="benchmark-pool",
        )

        assert isinstance(manager, LivySessionManager)
        assert manager.config.protocol == SessionProtocol.LIVY
        assert "my-workspace" in manager.config.endpoint
        assert "benchmark-pool" in manager.config.endpoint
        assert "azuresynapse.net" in manager.config.endpoint

    def test_for_databricks(self):

        manager = CloudSparkSessionManager.for_databricks(
            host="https://workspace.cloud.databricks.com",
            cluster_id="1234-567890-abc123",
            token="dapi123456789",
        )

        assert isinstance(manager, DatabricksConnectSessionManager)
        assert manager.config.protocol == SessionProtocol.DATABRICKS_CONNECT
        assert manager.config.credentials["token"] == "dapi123456789"
        assert manager.config.credentials["cluster_id"] == "1234-567890-abc123"


class TestLivySessionManager:
    def test_initial_state(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )
        manager = LivySessionManager(config)

        assert manager.state == SessionState.NOT_STARTED
        assert not manager.is_active
        assert manager.metrics.session_id is None

    def test_create_session(self):
        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
            session_name="test-session",
        )
        manager = LivySessionManager(config)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.side_effect = [
            {"id": 42},
            {"state": "idle"},
        ]
        mock_response.raise_for_status = MagicMock()
        mock_client.request.return_value = mock_response
        manager._http_client = mock_client

        session_id = manager.create_session()

        assert session_id == 42
        assert manager._session_id == 42
        assert manager.state == SessionState.IDLE
        assert manager.metrics.session_id == "42"

    def test_execute_statement(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )
        manager = LivySessionManager(config)
        manager._session_id = 42
        manager._state = SessionState.IDLE

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.side_effect = [
            {"id": 1},
            {"state": "available", "output": {"data": "result"}},
        ]
        mock_response.raise_for_status = MagicMock()
        mock_client.request.return_value = mock_response
        manager._http_client = mock_client

        result = manager.execute_statement("SELECT 1")

        assert result == {"data": "result"}
        assert manager.metrics.statements_executed == 1
        assert manager.state == SessionState.IDLE

    def test_execute_statement_no_session(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )
        manager = LivySessionManager(config)

        with pytest.raises(RuntimeError, match="No active session"):
            manager.execute_statement("SELECT 1")

    def test_close_session(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )
        manager = LivySessionManager(config)
        manager._session_id = 42
        manager._state = SessionState.IDLE

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()
        mock_client.request.return_value = mock_response
        manager._http_client = mock_client

        manager.close_session()

        assert manager._session_id is None
        assert manager.state == SessionState.DEAD

    def test_get_session_info_no_session(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )
        manager = LivySessionManager(config)

        info = manager.get_session_info()

        assert info == {"state": "not_started"}

    def test_session_context_manager(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )
        manager = LivySessionManager(config)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.side_effect = [
            {"id": 42},
            {"state": "idle"},
            {},
        ]
        mock_response.raise_for_status = MagicMock()
        mock_client.request.return_value = mock_response
        manager._http_client = mock_client

        with manager.session() as session_id:
            assert session_id == 42
            assert manager.metrics.start_time is not None

        assert manager.state == SessionState.DEAD
        assert manager.metrics.end_time is not None


class TestDatabricksConnectSessionManager:
    def test_initial_state(self):

        config = SessionConfig(
            protocol=SessionProtocol.DATABRICKS_CONNECT,
            endpoint="https://workspace.cloud.databricks.com",
            credentials={"token": "test-token", "cluster_id": "test-cluster"},
        )
        manager = DatabricksConnectSessionManager(config)

        assert manager.state == SessionState.NOT_STARTED
        assert not manager.is_active
        assert manager._spark is None

    def test_create_session_import_error(self):

        config = SessionConfig(
            protocol=SessionProtocol.DATABRICKS_CONNECT,
            endpoint="https://workspace.cloud.databricks.com",
            credentials={"token": "test-token", "cluster_id": "test-cluster"},
        )
        manager = DatabricksConnectSessionManager(config)

        with patch.dict("sys.modules", {"databricks.connect": None}):
            with pytest.raises(ImportError, match="databricks-connect required"):
                manager.create_session()

    def test_create_session_success(self):
        config = SessionConfig(
            protocol=SessionProtocol.DATABRICKS_CONNECT,
            endpoint="https://workspace.cloud.databricks.com",
            credentials={"token": "test-token", "cluster_id": "test-cluster"},
        )
        manager = DatabricksConnectSessionManager(config)

        mock_spark = MagicMock()
        mock_builder = MagicMock()
        mock_builder.host.return_value = mock_builder
        mock_builder.token.return_value = mock_builder
        mock_builder.clusterId.return_value = mock_builder
        mock_builder.getOrCreate.return_value = mock_spark

        with patch(
            "benchbox.platforms.base.cloud_spark.session.DatabricksConnectSessionManager.create_session"
        ) as mock_create:
            mock_create.return_value = mock_spark
            result = manager.create_session()
            assert result == mock_spark

    def test_execute_sql_statement(self):

        config = SessionConfig(
            protocol=SessionProtocol.DATABRICKS_CONNECT,
            endpoint="https://workspace.cloud.databricks.com",
            credentials={"token": "test-token", "cluster_id": "test-cluster"},
        )
        manager = DatabricksConnectSessionManager(config)
        manager._state = SessionState.IDLE

        mock_spark = MagicMock()
        mock_result = [MagicMock(asDict=lambda: {"col1": 1})]
        mock_spark.sql.return_value.collect.return_value = mock_result
        manager._spark = mock_spark

        result = manager.execute_statement("SELECT 1 AS col1")

        assert "data" in result
        mock_spark.sql.assert_called_once_with("SELECT 1 AS col1")
        assert manager.metrics.statements_executed == 1

    def test_execute_statement_no_session(self):

        config = SessionConfig(
            protocol=SessionProtocol.DATABRICKS_CONNECT,
            endpoint="https://workspace.cloud.databricks.com",
            credentials={"token": "test-token", "cluster_id": "test-cluster"},
        )
        manager = DatabricksConnectSessionManager(config)

        with pytest.raises(RuntimeError, match="No active session"):
            manager.execute_statement("SELECT 1")

    def test_close_session(self):

        config = SessionConfig(
            protocol=SessionProtocol.DATABRICKS_CONNECT,
            endpoint="https://workspace.cloud.databricks.com",
            credentials={"token": "test-token", "cluster_id": "test-cluster"},
        )
        manager = DatabricksConnectSessionManager(config)
        manager._state = SessionState.IDLE

        mock_spark = MagicMock()
        manager._spark = mock_spark

        manager.close_session()

        mock_spark.stop.assert_called_once()
        assert manager._spark is None
        assert manager.state == SessionState.DEAD
        assert manager.metrics.end_time is not None

    def test_get_session_info_no_session(self):

        config = SessionConfig(
            protocol=SessionProtocol.DATABRICKS_CONNECT,
            endpoint="https://workspace.cloud.databricks.com",
            credentials={"token": "test-token", "cluster_id": "test-cluster"},
        )
        manager = DatabricksConnectSessionManager(config)

        info = manager.get_session_info()

        assert info == {"state": "not_started"}

    def test_get_session_info_active_session(self):

        config = SessionConfig(
            protocol=SessionProtocol.DATABRICKS_CONNECT,
            endpoint="https://workspace.cloud.databricks.com",
            credentials={"token": "test-token", "cluster_id": "test-cluster"},
        )
        manager = DatabricksConnectSessionManager(config)
        manager._state = SessionState.IDLE
        manager._metrics.session_id = "databricks-connect"

        mock_spark = MagicMock()
        mock_spark.version = "14.3"
        manager._spark = mock_spark

        info = manager.get_session_info()

        assert info["state"] == "idle"
        assert info["protocol"] == "databricks_connect"
        assert info["spark_version"] == "14.3"


class TestSessionManagerIsActive:
    def test_is_active_idle(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )
        manager = LivySessionManager(config)
        manager._state = SessionState.IDLE

        assert manager.is_active is True

    def test_is_active_busy(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )
        manager = LivySessionManager(config)
        manager._state = SessionState.BUSY

        assert manager.is_active is True

    def test_is_not_active_not_started(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )
        manager = LivySessionManager(config)

        assert manager.is_active is False

    def test_is_not_active_dead(self):

        config = SessionConfig(
            protocol=SessionProtocol.LIVY,
            endpoint="https://cluster.example.com",
        )
        manager = LivySessionManager(config)
        manager._state = SessionState.DEAD

        assert manager.is_active is False


