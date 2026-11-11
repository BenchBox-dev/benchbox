from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def adapter():
    with (
        patch("benchbox.platforms.aws.emr_serverless_adapter.BOTO3_AVAILABLE", True),
        patch("benchbox.platforms.aws.emr_serverless_adapter.boto3", MagicMock()),
        patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        from benchbox.platforms.aws import EMRServerlessAdapter

        yield EMRServerlessAdapter(
            application_id="app-123",
            s3_staging_dir="s3://bucket/prefix",
            execution_role_arn="arn:aws:iam::123:role/EMR",
        )


class TestEMRServerlessCoverage:
    def test_wait_for_application_state_paths(self, adapter):
        client = MagicMock()
        client.get_application.return_value = {"application": {"state": "CREATED"}}
        adapter._emr_serverless_client = client

        state = adapter._wait_for_application_state("app-123", ["CREATED"], timeout_seconds=1)
        assert state == "CREATED"

        client.get_application.return_value = {"application": {"state": "STOPPED"}}
        with pytest.raises(ConfigurationError, match="STOPPED"):
            adapter._wait_for_application_state("app-123", ["STARTED"], timeout_seconds=1)

    def test_ensure_application_started_branches(self, adapter):
        client = MagicMock()
        adapter._emr_serverless_client = client

        client.get_application.return_value = {"application": {"state": "CREATED"}}
        with patch.object(adapter, "_wait_for_application_state") as waiter:
            adapter._ensure_application_started()
            client.start_application.assert_called_once_with(applicationId="app-123")
            waiter.assert_called_once_with("app-123", ["STARTED"])

        client.get_application.return_value = {"application": {"state": "STARTED"}}
        adapter._ensure_application_started()

        client.get_application.return_value = {"application": {"state": "BROKEN"}}
        with pytest.raises(ConfigurationError, match="unexpected state"):
            adapter._ensure_application_started()

    def test_submit_job_run_builds_script_and_parameters(self, adapter):
        emr_client = MagicMock()
        emr_client.start_job_run.return_value = {"jobRunId": "job-1"}
        s3_client = MagicMock()
        adapter._emr_serverless_client = emr_client
        adapter._s3_client = s3_client
        adapter.spark_submit_parameters = "--conf a=b"
        adapter._spark_config = {"spark.sql.shuffle.partitions": "8"}

        job_id = adapter._submit_job_run("SELECT 1")

        assert job_id == "job-1"
        assert s3_client.put_object.called
        payload = s3_client.put_object.call_args.kwargs["Body"].decode()
        assert 'spark.sql("USE benchbox")' in payload
        assert "SELECT 1" in payload

    def test_submit_job_run_escapes_quote_bearing_query(self, adapter):
        import json

        emr_client = MagicMock()
        emr_client.start_job_run.return_value = {"jobRunId": "job-1"}
        s3_client = MagicMock()
        adapter._emr_serverless_client = emr_client
        adapter._s3_client = s3_client

        query = "SELECT 'it''s' FROM t WHERE x = 'a\nb\\'"
        adapter._submit_job_run(query)

        payload = s3_client.put_object.call_args.kwargs["Body"].decode()
        compile(payload, "<generated>", "exec")
        assert json.dumps(query) in payload

    def test_wait_for_job_run_and_retrieve_results(self, adapter):
        emr_client = MagicMock()
        emr_client.get_job_run.return_value = {
            "jobRun": {"state": "SUCCESS", "totalResourceUtilization": {"vCPUHour": 1.5, "memoryGBHour": 2.0}}
        }
        adapter._emr_serverless_client = emr_client

        state, usage = adapter._wait_for_job_run("job-1")
        assert state == "SUCCESS"
        assert usage["vCPUHour"] == 1.5

        s3_client = MagicMock()
        s3_client.list_objects_v2.return_value = {"Contents": [{"Key": "prefix/results/job-1/part-000.json"}]}
        s3_client.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"a":1}\n{"a":2}\n')}
        adapter._s3_client = s3_client

        rows = adapter._retrieve_results("job-1")
        assert rows == [{"a": 1}, {"a": 2}]

    def test_execute_query_updates_metrics(self, adapter):
        with (
            patch.object(adapter, "_ensure_application_started"),
            patch.object(adapter, "_submit_job_run", return_value="job-2"),
            patch.object(
                adapter, "_wait_for_job_run", return_value=("SUCCESS", {"vCPUHour": 0.5, "memoryGBHour": 0.25})
            ),
            patch.object(adapter, "_retrieve_results", return_value=[{"x": 1}]),
        ):
            result = adapter.execute_query(None, "SELECT 1", "q1")

        assert result["status"] == "SUCCESS"
        assert result["results"] == [{"x": 1}]
        assert adapter._query_count == 1
        assert adapter._total_vcpu_hours == 0.5
        assert adapter._total_memory_gb_hours == 0.25

    def test_close_stops_application_if_created_by_us(self, adapter):
        client = MagicMock()
        adapter._emr_serverless_client = client
        adapter._application_created_by_us = True

        adapter.close()
        client.stop_application.assert_called_once_with(applicationId="app-123")

    def test_create_schema_and_create_connection_branches(self, adapter):
        glue = MagicMock()
        adapter._glue_client = glue
        glue.get_database.side_effect = Exception("missing")
        with patch("benchbox.platforms.aws.emr_serverless_adapter.ClientError", Exception):
            pass

        glue = MagicMock()
        adapter._glue_client = glue
        glue.get_database.return_value = {"Database": {"Name": "benchbox"}}
        adapter.create_schema(None, None)

        emr = MagicMock()
        emr.get_application.return_value = {"application": {"state": "STARTED", "releaseLabel": "emr-7.0.0"}}
        adapter._emr_serverless_client = emr
        status = adapter.create_connection()
        assert status["status"] == "connected"


