# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_client_error(code: str) -> Exception:

    class _MockClientError(Exception):
        def __init__(self) -> None:
            self.response = {"Error": {"Code": code}}
            super().__init__(code)

    return _MockClientError()


def _adapter(**extra):
    from benchbox.platforms.aws import AWSGlueAdapter

    with (
        patch("benchbox.platforms.aws.glue_adapter.BOTO3_AVAILABLE", True),
        patch("benchbox.platforms.aws.glue_adapter.boto3", MagicMock()),
        patch("benchbox.platforms.aws.glue_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        return AWSGlueAdapter(
            s3_staging_dir="s3://my-bucket/benchbox-data",
            job_role="arn:aws:iam::123456789012:role/GlueRole",
            **extra,
        )


def _benchmark(*tables: str) -> SimpleNamespace:
    return SimpleNamespace(get_table_names=lambda: list(tables))


class TestGetBotoSession:
    def test_profile_branch(self) -> None:
        adapter = _adapter(aws_profile="my-profile")

        mock_boto3 = MagicMock()
        with patch("benchbox.platforms.aws.glue_adapter.boto3", mock_boto3):
            adapter._get_boto_session()

        mock_boto3.Session.assert_called_once_with(profile_name="my-profile", region_name="us-east-1")

    def test_explicit_keys_branch(self) -> None:
        adapter = _adapter(
            aws_access_key_id="AKID123",
            aws_secret_access_key="secret456",
        )

        mock_boto3 = MagicMock()
        with patch("benchbox.platforms.aws.glue_adapter.boto3", mock_boto3):
            adapter._get_boto_session()

        mock_boto3.Session.assert_called_once_with(
            aws_access_key_id="AKID123",
            aws_secret_access_key="secret456",
            region_name="us-east-1",
        )

    def test_default_credential_chain(self) -> None:
        adapter = _adapter()

        mock_boto3 = MagicMock()
        with patch("benchbox.platforms.aws.glue_adapter.boto3", mock_boto3):
            adapter._get_boto_session()

        mock_boto3.Session.assert_called_once_with(region_name="us-east-1")


class TestClientCaching:
    def test_glue_client_is_cached(self) -> None:
        adapter = _adapter()
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client

        with patch("benchbox.platforms.aws.glue_adapter.boto3") as mock_boto3:
            mock_boto3.Session.return_value = mock_session
            c1 = adapter._get_glue_client()
            c2 = adapter._get_glue_client()

        assert c1 is c2
        assert mock_session.client.call_count == 1

    def test_s3_client_is_cached(self) -> None:
        adapter = _adapter()
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client

        with patch("benchbox.platforms.aws.glue_adapter.boto3") as mock_boto3:
            mock_boto3.Session.return_value = mock_session
            c1 = adapter._get_s3_client()
            c2 = adapter._get_s3_client()

        assert c1 is c2
        assert mock_session.client.call_count == 1


class TestCreateConnectionErrors:
    def test_access_denied_raises_configuration_error(self) -> None:
        adapter = _adapter()

        mock_glue = MagicMock()
        access_denied = _make_client_error("AccessDeniedException")

        with patch("benchbox.platforms.aws.glue_adapter.ClientError", type(access_denied)):
            mock_glue.get_databases.side_effect = access_denied
            with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
                with pytest.raises(ConfigurationError, match="Access denied"):
                    adapter.create_connection()

    def test_unknown_client_error_raises_configuration_error(self) -> None:
        adapter = _adapter()

        mock_glue = MagicMock()
        other_error = _make_client_error("ServiceUnavailable")

        with patch("benchbox.platforms.aws.glue_adapter.ClientError", type(other_error)):
            mock_glue.get_databases.side_effect = other_error
            with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
                with pytest.raises(ConfigurationError, match="Failed to connect"):
                    adapter.create_connection()


class TestCreateSchemaNonEntityError:
    def test_non_entity_error_is_reraised(self) -> None:
        adapter = _adapter()
        mock_glue = MagicMock()
        other_error = _make_client_error("AccessDeniedException")

        with patch("benchbox.platforms.aws.glue_adapter.ClientError", type(other_error)):
            mock_glue.get_database.side_effect = other_error
            with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
                with pytest.raises(type(other_error)):
                    adapter.create_schema(_benchmark(), None)


class TestCreateCatalogTable:
    def test_parquet_format_uses_parquet_serde(self) -> None:
        adapter = _adapter()
        mock_glue = MagicMock()
        with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
            adapter._create_catalog_table("orders", "parquet", "s3://b/orders/")
        call_kwargs = mock_glue.create_table.call_args[1]
        serde = call_kwargs["TableInput"]["StorageDescriptor"]["SerdeInfo"]["SerializationLibrary"]
        assert "ParquetHiveSerDe" in serde

    def test_csv_format_uses_csv_serde(self) -> None:
        adapter = _adapter()
        mock_glue = MagicMock()
        with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
            adapter._create_catalog_table("orders", "csv", "s3://b/orders/")
        call_kwargs = mock_glue.create_table.call_args[1]
        serde = call_kwargs["TableInput"]["StorageDescriptor"]["SerdeInfo"]["SerializationLibrary"]
        assert "OpenCSVSerde" in serde

    def test_unknown_format_defaults_to_parquet(self) -> None:
        adapter = _adapter()
        mock_glue = MagicMock()
        with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
            adapter._create_catalog_table("orders", "orc", "s3://b/orders/")
        call_kwargs = mock_glue.create_table.call_args[1]
        serde = call_kwargs["TableInput"]["StorageDescriptor"]["SerdeInfo"]["SerializationLibrary"]
        assert "ParquetHiveSerDe" in serde

    def test_already_exists_exception_is_swallowed(self) -> None:
        adapter = _adapter()
        mock_glue = MagicMock()
        already_exists = _make_client_error("AlreadyExistsException")

        with patch("benchbox.platforms.aws.glue_adapter.ClientError", type(already_exists)):
            mock_glue.create_table.side_effect = already_exists
            with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
                adapter._create_catalog_table("orders", "parquet", "s3://b/orders/")


class TestLoadDataStagingNone:
    def test_load_data_with_no_staging_returns_empty_upload(self) -> None:
        adapter = _adapter()
        adapter._staging = None

        mock_glue = MagicMock()
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(adapter, "_get_glue_client", return_value=mock_glue),
        ):
            stats, _, metadata = adapter.load_data(_benchmark("lineitem"), None, tmpdir)

        assert stats == {"lineitem": 0}
        assert metadata == {"table_uris": {}}

    def test_load_data_missing_source_dir_raises(self) -> None:
        adapter = _adapter()
        with pytest.raises(ConfigurationError, match="Source directory not found"):
            adapter.load_data(_benchmark("lineitem"), None, "/nonexistent/path")


class TestExecuteQuery:
    def _setup_execute_mock(self, adapter, status="SUCCEEDED", results=None):
        if results is None:
            results = [{"col": "val"}]

        adapter._job_name = "benchbox-test-job"

        mock_glue = MagicMock()
        mock_glue.start_job_run.return_value = {"JobRunId": "jr-abc123"}
        mock_glue.get_job_run.return_value = {
            "JobRun": {
                "JobRunState": status,
                "ExecutionTime": 120,
            }
        }

        mock_s3 = MagicMock()
        json_lines = "\n".join(json.dumps(r) for r in results)
        mock_body = MagicMock()
        mock_body.read.return_value = json_lines.encode("utf-8")
        mock_s3.list_objects_v2.return_value = {"Contents": [{"Key": "benchbox-data/results/jr-abc123/part-0.json"}]}
        mock_s3.get_object.return_value = {"Body": mock_body}

        return mock_glue, mock_s3

    def test_execute_query_success_returns_results(self) -> None:
        adapter = _adapter()
        mock_glue, mock_s3 = self._setup_execute_mock(adapter)

        with (
            patch.object(adapter, "_get_glue_client", return_value=mock_glue),
            patch.object(adapter, "_get_s3_client", return_value=mock_s3),
            patch.object(adapter, "_ensure_job_exists", return_value="benchbox-test-job"),
        ):
            result = adapter.execute_query(None, "SELECT 1", "q1")

        assert result["status"] == "SUCCESS"
        assert result["results"] == [{"col": "val"}]
        assert adapter._query_count == 1

    def test_execute_query_failed_job_raises_runtime_error(self) -> None:
        adapter = _adapter()
        mock_glue, mock_s3 = self._setup_execute_mock(adapter, status="FAILED")

        with (
            patch.object(adapter, "_get_glue_client", return_value=mock_glue),
            patch.object(adapter, "_get_s3_client", return_value=mock_s3),
            patch.object(adapter, "_ensure_job_exists", return_value="benchbox-test-job"),
        ):
            result = adapter.execute_query(None, "SELECT 1", "q1")

        assert result["status"] == "FAILED"
        assert "Glue job failed" in result["error"]

    def test_execute_query_increments_query_count(self) -> None:
        adapter = _adapter()
        mock_glue, mock_s3 = self._setup_execute_mock(adapter)

        with (
            patch.object(adapter, "_get_glue_client", return_value=mock_glue),
            patch.object(adapter, "_get_s3_client", return_value=mock_s3),
            patch.object(adapter, "_ensure_job_exists", return_value="benchbox-test-job"),
        ):
            adapter.execute_query(None, "SELECT 1", "q1")
            adapter.execute_query(None, "SELECT 2", "q2")

        assert adapter._query_count == 2


class TestEnsureJobExists:
    def test_returns_cached_job_name_if_set(self) -> None:
        adapter = _adapter()
        adapter._job_name = "existing-job"
        result = adapter._ensure_job_exists()
        assert result == "existing-job"

    def test_creates_job_when_not_set(self) -> None:
        adapter = _adapter()
        adapter._job_name = None

        mock_glue = MagicMock()

        with (
            patch.object(adapter, "_get_glue_client", return_value=mock_glue),
        ):
            job_name = adapter._ensure_job_exists()

        assert job_name is not None
        assert "benchbox" in job_name
        mock_glue.create_job.assert_called_once()
        assert adapter._job_name == job_name

    def test_handles_already_exists_exception(self) -> None:
        adapter = _adapter()
        adapter._job_name = None

        mock_glue = MagicMock()
        already_exists = _make_client_error("AlreadyExistsException")

        with patch("benchbox.platforms.aws.glue_adapter.ClientError", type(already_exists)):
            mock_glue.create_job.side_effect = already_exists
            with (
                patch.object(adapter, "_get_glue_client", return_value=mock_glue),
                patch.object(adapter, "_upload_job_script", return_value="s3://bucket/script.py"),
            ):
                job_name = adapter._ensure_job_exists()

        assert job_name is not None
        assert adapter._job_name == job_name


class TestUploadJobScript:
    def test_uploads_script_and_returns_s3_path(self) -> None:
        adapter = _adapter()
        mock_s3 = MagicMock()

        with patch.object(adapter, "_get_s3_client", return_value=mock_s3):
            path = adapter._upload_job_script()

        assert path.startswith("s3://my-bucket/")
        assert path.endswith(".py")
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "my-bucket"
        assert call_kwargs["ContentType"] == "text/x-python"


class TestSubmitJobRun:
    def test_submit_job_run_returns_run_id_and_tracks(self) -> None:
        adapter = _adapter()
        adapter._job_name = "benchbox-test-job"
        mock_glue = MagicMock()
        mock_glue.start_job_run.return_value = {"JobRunId": "jr-xyz789"}

        with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
            run_id = adapter._submit_job_run("SELECT 1")

        assert run_id == "jr-xyz789"
        assert "jr-xyz789" in adapter._job_runs
        mock_glue.start_job_run.assert_called_once_with(
            JobName="benchbox-test-job",
            Arguments={"--query": "SELECT 1"},
        )


class TestWaitForJob:
    def test_returns_immediately_on_succeeded(self) -> None:
        adapter = _adapter()
        adapter._job_name = "benchbox-test-job"
        mock_glue = MagicMock()
        mock_glue.get_job_run.return_value = {"JobRun": {"JobRunState": "SUCCEEDED", "ExecutionTime": 60}}

        with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
            status = adapter._wait_for_job("jr-abc", poll_interval=0)

        assert status == "SUCCEEDED"
        assert adapter._total_dpu_hours > 0

    def test_polls_until_terminal_state(self) -> None:
        adapter = _adapter()
        adapter._job_name = "benchbox-test-job"
        mock_glue = MagicMock()
        mock_glue.get_job_run.side_effect = [
            {"JobRun": {"JobRunState": "RUNNING", "ExecutionTime": 0}},
            {"JobRun": {"JobRunState": "RUNNING", "ExecutionTime": 0}},
            {"JobRun": {"JobRunState": "SUCCEEDED", "ExecutionTime": 120}},
        ]

        with (
            patch.object(adapter, "_get_glue_client", return_value=mock_glue),
            patch("benchbox.platforms.aws.glue_adapter.time.sleep"),
        ):
            status = adapter._wait_for_job("jr-abc", poll_interval=1)

        assert status == "SUCCEEDED"
        assert mock_glue.get_job_run.call_count == 3

    def test_returns_failed_status(self) -> None:
        adapter = _adapter()
        adapter._job_name = "benchbox-test-job"
        mock_glue = MagicMock()
        mock_glue.get_job_run.return_value = {"JobRun": {"JobRunState": "FAILED", "ExecutionTime": 10}}

        with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
            status = adapter._wait_for_job("jr-abc", poll_interval=0)

        assert status == "FAILED"

    def test_returns_timeout_status(self) -> None:
        adapter = _adapter()
        adapter._job_name = "benchbox-test-job"
        mock_glue = MagicMock()
        mock_glue.get_job_run.return_value = {"JobRun": {"JobRunState": "TIMEOUT", "ExecutionTime": 3600}}

        with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
            status = adapter._wait_for_job("jr-abc", poll_interval=0)

        assert status == "TIMEOUT"

    def test_returns_stopped_status(self) -> None:
        adapter = _adapter()
        adapter._job_name = "benchbox-test-job"
        mock_glue = MagicMock()
        mock_glue.get_job_run.return_value = {"JobRun": {"JobRunState": "STOPPED", "ExecutionTime": 30}}

        with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
            status = adapter._wait_for_job("jr-abc", poll_interval=0)

        assert status == "STOPPED"

    def test_returns_error_status(self) -> None:
        adapter = _adapter()
        adapter._job_name = "benchbox-test-job"
        mock_glue = MagicMock()
        mock_glue.get_job_run.return_value = {"JobRun": {"JobRunState": "ERROR", "ExecutionTime": 5}}

        with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
            status = adapter._wait_for_job("jr-abc", poll_interval=0)

        assert status == "ERROR"

    def test_dpu_hours_accumulate(self) -> None:
        adapter = _adapter()
        adapter._job_name = "benchbox-test-job"
        adapter.number_of_workers = 4
        mock_glue = MagicMock()
        mock_glue.get_job_run.return_value = {"JobRun": {"JobRunState": "SUCCEEDED", "ExecutionTime": 3600}}

        with patch.object(adapter, "_get_glue_client", return_value=mock_glue):
            adapter._wait_for_job("jr-abc", poll_interval=0)

        assert adapter._total_dpu_hours == pytest.approx(4.0)


class TestRetrieveResults:
    def test_retrieves_json_lines_from_s3(self) -> None:
        adapter = _adapter()
        mock_s3 = MagicMock()

        rows = [{"a": 1}, {"a": 2}]
        content = "\n".join(json.dumps(r) for r in rows)
        mock_body = MagicMock()
        mock_body.read.return_value = content.encode("utf-8")

        mock_s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "benchbox-data/results/jr-001/part-0.json"},
                {"Key": "benchbox-data/results/jr-001/_SUCCESS"},
            ]
        }
        mock_s3.get_object.return_value = {"Body": mock_body}

        with patch.object(adapter, "_get_s3_client", return_value=mock_s3):
            results = adapter._retrieve_results("jr-001")

        assert results == rows
        assert mock_s3.get_object.call_count == 1

    def test_returns_empty_list_when_no_contents(self) -> None:
        adapter = _adapter()
        mock_s3 = MagicMock()
        mock_s3.list_objects_v2.return_value = {}

        with patch.object(adapter, "_get_s3_client", return_value=mock_s3):
            results = adapter._retrieve_results("jr-empty")

        assert results == []


class TestApplyTuning:
    def test_apply_tuning_with_platform_config(self) -> None:
        adapter = _adapter()
        adapter.apply_platform_optimizations = MagicMock(return_value=["opt1"])

        config = SimpleNamespace(platform={"spark.sql.shuffle.partitions": 200})
        result = adapter.apply_tuning(config)

        adapter.apply_platform_optimizations.assert_called_once()
        assert result["platform_optimizations"] == ["opt1"]

    def test_apply_tuning_without_platform_config(self) -> None:
        adapter = _adapter()
        config = SimpleNamespace(platform=None)
        result = adapter.apply_tuning(config)
        assert result["platform_optimizations"] == []


class TestGetTargetDialect:
    def test_returns_spark(self) -> None:
        adapter = _adapter()
        assert adapter.get_target_dialect() == "spark"


class TestFromConfigDatabaseProvided:
    def test_from_config_uses_explicit_database(self) -> None:
        from benchbox.platforms.aws import AWSGlueAdapter

        with patch("benchbox.platforms.aws.glue_adapter.CloudSparkStaging") as mock_staging:
            mock_staging.from_uri.return_value = MagicMock()
            adapter = AWSGlueAdapter.from_config(
                {
                    "benchmark": "tpch",
                    "scale_factor": 1.0,
                    "s3_staging_dir": "s3://my-bucket/data",
                    "job_role": "arn:aws:iam::123456789012:role/GlueRole",
                    "database": "explicit_db_name",
                }
            )

        assert adapter.database == "explicit_db_name"


class TestGlueJobStatusExtra:
    def test_all_status_constants(self) -> None:
        from benchbox.platforms.aws.glue_adapter import GlueJobStatus

        assert GlueJobStatus.STARTING == "STARTING"
        assert GlueJobStatus.STOPPING == "STOPPING"
        assert GlueJobStatus.WAITING == "WAITING"
        assert GlueJobStatus.ERROR == "ERROR"


class TestS3PrefixFallback:
    def test_s3_staging_dir_without_path_uses_default_prefix(self) -> None:
        from benchbox.platforms.aws import AWSGlueAdapter

        with patch("benchbox.platforms.aws.glue_adapter.CloudSparkStaging") as mock_staging:
            mock_staging.from_uri.return_value = MagicMock()
            adapter = AWSGlueAdapter(
                s3_staging_dir="s3://my-bucket",
                job_role="arn:aws:iam::123456789012:role/GlueRole",
            )

        assert adapter.s3_bucket == "my-bucket"
        assert adapter.s3_prefix == "benchbox-data"


class TestRegionAlias:
    def test_aws_region_alias_is_used(self) -> None:
        from benchbox.platforms.aws import AWSGlueAdapter

        with patch("benchbox.platforms.aws.glue_adapter.CloudSparkStaging") as mock_staging:
            mock_staging.from_uri.return_value = MagicMock()
            adapter = AWSGlueAdapter(
                s3_staging_dir="s3://my-bucket/data",
                job_role="arn:aws:iam::123456789012:role/GlueRole",
                aws_region="ap-southeast-1",
            )

        assert adapter.region == "ap-southeast-1"


