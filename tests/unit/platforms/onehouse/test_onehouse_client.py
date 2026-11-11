# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestOnehouseClientInitialization:
    def test_default_values(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests", MagicMock()),
        ):
            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")

            assert client.api_key == "test-key"
            assert client.region == "us-east-1"
            assert client.api_endpoint == "api.onehouse.ai"
            assert client.timeout_seconds == 30

    def test_custom_values(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests", MagicMock()),
        ):
            from benchbox.platforms.onehouse import ClusterConfig, OnehouseClient

            cluster_config = ClusterConfig(
                cluster_size="large",
                min_workers=2,
                max_workers=20,
            )

            client = OnehouseClient(
                api_key="custom-key",
                region="eu-west-1",
                api_endpoint="api.custom.onehouse.ai",
                timeout_seconds=60,
                cluster_config=cluster_config,
            )

            assert client.api_key == "custom-key"
            assert client.region == "eu-west-1"
            assert client.api_endpoint == "api.custom.onehouse.ai"
            assert client.timeout_seconds == 60
            assert client.cluster_config.cluster_size == "large"


class TestJobState:
    def test_terminal_states(self):

        from benchbox.platforms.onehouse import JobState

        assert JobState.SUCCEEDED.is_terminal is True
        assert JobState.FAILED.is_terminal is True
        assert JobState.CANCELLED.is_terminal is True
        assert JobState.RUNNING.is_terminal is False
        assert JobState.PENDING.is_terminal is False
        assert JobState.QUEUED.is_terminal is False

    def test_success_state(self):

        from benchbox.platforms.onehouse import JobState

        assert JobState.SUCCEEDED.is_success is True
        assert JobState.FAILED.is_success is False
        assert JobState.CANCELLED.is_success is False


class TestTableFormat:
    def test_table_formats(self):

        from benchbox.platforms.onehouse import TableFormat

        assert TableFormat.HUDI.value == "hudi"
        assert TableFormat.ICEBERG.value == "iceberg"
        assert TableFormat.DELTA.value == "delta"


class TestClusterConfig:
    def test_default_values(self):

        from benchbox.platforms.onehouse import ClusterConfig

        config = ClusterConfig()

        assert config.cluster_size == "small"
        assert config.spark_version == "3.5"
        assert config.auto_scaling is True
        assert config.min_workers == 1
        assert config.max_workers == 10
        assert config.idle_timeout_minutes == 15

    def test_custom_values(self):

        from benchbox.platforms.onehouse import ClusterConfig

        config = ClusterConfig(
            cluster_size="xlarge",
            spark_version="3.4",
            auto_scaling=False,
            min_workers=5,
            max_workers=50,
            idle_timeout_minutes=30,
        )

        assert config.cluster_size == "xlarge"
        assert config.spark_version == "3.4"
        assert config.auto_scaling is False
        assert config.min_workers == 5
        assert config.max_workers == 50
        assert config.idle_timeout_minutes == 30


class TestOnehouseClientConnection:
    def test_test_connection_success(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_response.content = b"{}"
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            result = client.test_connection()

            assert result is True

    def test_test_connection_failure(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_session.request.side_effect = Exception("Connection failed")
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            result = client.test_connection()

            assert result is False


class TestOnehouseClientJobs:
    def test_submit_sql_job(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"job_id": "job-12345"}
            mock_response.content = b'{"job_id": "job-12345"}'
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient, TableFormat

            client = OnehouseClient(api_key="test-key")
            job_id = client.submit_sql_job(
                sql="SELECT * FROM test_table",
                database="test_db",
                table_format=TableFormat.ICEBERG,
            )

            assert job_id == "job-12345"

    def test_get_job_status(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "state": "RUNNING",
                "duration_seconds": 10.5,
            }
            mock_response.content = b'{"state": "RUNNING"}'
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import JobState, OnehouseClient

            client = OnehouseClient(api_key="test-key")
            result = client.get_job_status("job-12345")

            assert result.job_id == "job-12345"
            assert result.state == JobState.RUNNING
            assert result.duration_seconds == 10.5

    def test_cancel_job(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_response.content = b"{}"
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            client.cancel_job("job-12345")

            calls = mock_session.request.call_args_list
            assert any("cancel" in str(call) for call in calls)


class TestOnehouseClientCluster:
    def test_provision_cluster(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"cluster_id": "cluster-abc123"}
            mock_response.content = b'{"cluster_id": "cluster-abc123"}'
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            cluster_id = client.provision_cluster()

            assert cluster_id == "cluster-abc123"
            assert client._cluster_id == "cluster-abc123"

    def test_terminate_cluster(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_response.content = b"{}"
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            client._cluster_id = "cluster-abc123"

            client.terminate_cluster()

            assert client._cluster_id is None


class TestOnehouseClientErrors:
    def test_auth_error(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_requests.exceptions = real_requests.exceptions

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="invalid-key")

            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_session.request.return_value = mock_response
            client._session = mock_session

            with pytest.raises(ConfigurationError, match="Invalid"):
                client._request("GET", "/test")

    def test_permission_error(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_requests.exceptions = real_requests.exceptions

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")

            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_session.request.return_value = mock_response
            client._session = mock_session

            with pytest.raises(ConfigurationError, match="permission"):
                client._request("GET", "/test")


class TestOnehouseClientClose:
    def test_close_terminates_cluster(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_response.content = b"{}"
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            client._cluster_id = "cluster-abc123"

            client.close()

            assert client._cluster_id is None
            mock_session.close.assert_called_once()


class TestOnehouseClientPySparkJobs:
    def test_submit_pyspark_job_basic(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"job_id": "pyspark-job-123"}
            mock_response.content = b'{"job_id": "pyspark-job-123"}'
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient, TableFormat

            client = OnehouseClient(api_key="test-key")
            job_id = client.submit_pyspark_job(
                script="spark.read.parquet('s3://bucket/data')",
                database="test_db",
                table_format=TableFormat.HUDI,
            )

            assert job_id == "pyspark-job-123"

    def test_submit_pyspark_job_with_all_options(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"job_id": "full-pyspark-job"}
            mock_response.content = b'{"job_id": "full-pyspark-job"}'
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient, TableFormat

            client = OnehouseClient(api_key="test-key")
            client._cluster_id = "cluster-123"

            job_id = client.submit_pyspark_job(
                script="df = spark.read.parquet('s3://bucket/data')",
                script_args=["--input", "data.csv"],
                database="prod_db",
                table_format=TableFormat.DELTA,
                output_location="s3://bucket/output",
                spark_config={"spark.sql.shuffle.partitions": "200"},
                job_name="custom-job-name",
            )

            assert job_id == "full-pyspark-job"

            call_args = mock_session.request.call_args_list[-1]
            assert "jobs" in call_args[1]["url"]


class TestOnehouseClientWaitForJob:
    def test_wait_for_job_success(self):

        mock_clock = Mock(side_effect=[0, 5, 10])
        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
            patch("benchbox.platforms.onehouse.onehouse_client.time") as mock_time,
            patch("benchbox.platforms.onehouse.onehouse_client.mono_time", mock_clock),
            patch("benchbox.utils.clock.mono_time", mock_clock),
        ):
            mock_session = MagicMock()

            mock_response_running = MagicMock()
            mock_response_running.status_code = 200
            mock_response_running.json.return_value = {"state": "RUNNING"}
            mock_response_running.content = b'{"state": "RUNNING"}'

            mock_response_success = MagicMock()
            mock_response_success.status_code = 200
            mock_response_success.json.return_value = {
                "state": "SUCCEEDED",
                "duration_seconds": 15.5,
            }
            mock_response_success.content = b'{"state": "SUCCEEDED"}'

            mock_session.request.side_effect = [mock_response_running, mock_response_success]
            mock_requests.Session.return_value = mock_session

            mock_time.sleep = MagicMock()

            from benchbox.platforms.onehouse import JobState, OnehouseClient

            client = OnehouseClient(api_key="test-key")
            result = client.wait_for_job("job-123", timeout_minutes=5)

            assert result.state == JobState.SUCCEEDED
            assert result.duration_seconds == 15.5

    def test_wait_for_job_failure(self):

        mock_clock = Mock(side_effect=[0, 5])
        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
            patch("benchbox.platforms.onehouse.onehouse_client.time"),
            patch("benchbox.platforms.onehouse.onehouse_client.mono_time", mock_clock),
            patch("benchbox.utils.clock.mono_time", mock_clock),
        ):
            mock_session = MagicMock()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "state": "FAILED",
                "error_message": "Out of memory",
            }
            mock_response.content = b'{"state": "FAILED"}'

            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")

            with pytest.raises(RuntimeError, match="Out of memory"):
                client.wait_for_job("job-123")

    def test_wait_for_job_timeout(self):

        mock_clock = Mock(side_effect=[0, 3700])
        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
            patch("benchbox.platforms.onehouse.onehouse_client.time") as mock_time,
            patch("benchbox.platforms.onehouse.onehouse_client.mono_time", mock_clock),
            patch("benchbox.utils.clock.mono_time", mock_clock),
        ):
            mock_session = MagicMock()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"state": "RUNNING"}
            mock_response.content = b'{"state": "RUNNING"}'

            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            mock_time.sleep = MagicMock()

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")

            with pytest.raises(RuntimeError, match="timed out"):
                client.wait_for_job("job-123", timeout_minutes=1)

    def test_wait_for_job_cancelled_state(self):

        mock_clock = Mock(side_effect=[0, 5])
        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
            patch("benchbox.platforms.onehouse.onehouse_client.time"),
            patch("benchbox.platforms.onehouse.onehouse_client.mono_time", mock_clock),
            patch("benchbox.utils.clock.mono_time", mock_clock),
        ):
            mock_session = MagicMock()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"state": "CANCELLED"}
            mock_response.content = b'{"state": "CANCELLED"}'

            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")

            with pytest.raises(RuntimeError, match="failed"):
                client.wait_for_job("job-123")


class TestOnehouseClientJobResults:
    def test_get_job_results(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"rows": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]}
            mock_response.content = b'{"rows": [{"id": 1}, {"id": 2}]}'
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            results = client.get_job_results("job-123")

            assert len(results) == 2
            assert results[0]["id"] == 1


class TestOnehouseClientDatabase:
    def test_create_database(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_response.content = b"{}"
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            client.create_database("test_db", location="s3://bucket/databases/test_db")

            calls = mock_session.request.call_args_list
            assert any("databases" in str(call) for call in calls)

    def test_create_database_no_location(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_response.content = b"{}"
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            client.create_database("simple_db")

            calls = mock_session.request.call_args_list
            assert any("databases" in str(call) for call in calls)


class TestOnehouseClientClusterStatus:
    def test_get_cluster_status_no_cluster(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests", MagicMock()),
        ):
            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            status = client.get_cluster_status()

            assert status["status"] == "no_cluster"
            assert "No cluster provisioned" in status["message"]

    def test_get_cluster_status_with_cluster(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"status": "RUNNING", "workers": 4}
            mock_response.content = b'{"status": "RUNNING"}'
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            client._cluster_id = "cluster-123"

            status = client.get_cluster_status()

            assert status["status"] == "RUNNING"


class TestOnehouseClientProvisionErrors:
    def test_provision_cluster_missing_id(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_response.content = b"{}"
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")

            with pytest.raises(RuntimeError, match="cluster_id"):
                client.provision_cluster()


class TestOnehouseClientTerminateErrors:
    def test_terminate_cluster_handles_exception(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_session.request.side_effect = Exception("Network error")
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")
            client._cluster_id = "cluster-123"

            client.terminate_cluster()

            assert client._cluster_id is None


class TestOnehouseClientConnectionErrors:
    def test_connection_error(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_requests.exceptions = real_requests.exceptions

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")

            mock_session = MagicMock()
            mock_session.request.side_effect = real_requests.exceptions.ConnectionError("No route to host")
            client._session = mock_session

            with pytest.raises(ConfigurationError, match="Failed to connect"):
                client._request("GET", "/test")

    def test_timeout_error(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_requests.exceptions = real_requests.exceptions

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")

            mock_session = MagicMock()
            mock_session.request.side_effect = real_requests.exceptions.Timeout("Request timed out")
            client._session = mock_session

            with pytest.raises(RuntimeError, match="timeout"):
                client._request("GET", "/test")

    def test_http_error_with_json_message(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_requests.exceptions = real_requests.exceptions

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")

            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.json.return_value = {"message": "Internal server error"}
            mock_response.raise_for_status.side_effect = real_requests.exceptions.HTTPError(response=mock_response)
            mock_session.request.return_value = mock_response
            client._session = mock_session

            with pytest.raises(RuntimeError, match="Internal server error"):
                client._request("GET", "/test")

    def test_http_error_without_json_message(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_requests.exceptions = real_requests.exceptions

            from benchbox.platforms.onehouse import OnehouseClient

            client = OnehouseClient(api_key="test-key")

            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.json.side_effect = ValueError("No JSON")
            mock_response.raise_for_status.side_effect = real_requests.exceptions.HTTPError(response=mock_response)
            mock_session.request.return_value = mock_response
            client._session = mock_session

            with pytest.raises(RuntimeError, match="API error"):
                client._request("GET", "/test")


class TestOnehouseClientJobStatusFallback:
    def test_unknown_job_state_defaults_to_pending(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"state": "UNKNOWN_STATE"}
            mock_response.content = b'{"state": "UNKNOWN_STATE"}'
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import JobState, OnehouseClient

            client = OnehouseClient(api_key="test-key")
            result = client.get_job_status("job-123")

            assert result.state == JobState.PENDING


class TestOnehouseClientSqlJobOptions:
    def test_submit_sql_job_with_output_location(self):

        with (
            patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.onehouse.onehouse_client.requests") as mock_requests,
        ):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"job_id": "job-with-output"}
            mock_response.content = b'{"job_id": "job-with-output"}'
            mock_session.request.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            from benchbox.platforms.onehouse import OnehouseClient, TableFormat

            client = OnehouseClient(api_key="test-key")
            client._cluster_id = "cluster-123"

            job_id = client.submit_sql_job(
                sql="SELECT * FROM test",
                database="test_db",
                table_format=TableFormat.ICEBERG,
                output_location="s3://bucket/output",
                spark_config={"spark.executor.memory": "4g"},
                job_name="named-job",
            )

            assert job_id == "job-with-output"


