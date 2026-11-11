# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

botocore_exceptions = pytest.importorskip("botocore.exceptions")
ClientError = botocore_exceptions.ClientError

from benchbox.core.exceptions import ConfigurationError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


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


def _benchmark():
    return SimpleNamespace(tables=["lineitem", "orders"])


class TestApplicationLifecycle:
    def test_create_application_with_initial_capacity(self, adapter):
        client = MagicMock()
        client.create_application.return_value = {"applicationId": "app-9"}
        adapter._emr_serverless_client = client
        adapter.initial_capacity = {"Driver": {"CPU": "2 vCPU"}}
        with patch.object(adapter, "_wait_for_application_state", return_value="CREATED"):
            assert adapter._create_emr_application() == "app-9"
        _, kwargs = client.create_application.call_args
        assert kwargs["initialCapacity"] == {"Driver": {"CPU": "2 vCPU"}}
        assert adapter._application_created_by_us is True

    def test_wait_timeout_raises(self, adapter):
        client = MagicMock()
        client.get_application.return_value = {"application": {"state": "STARTING"}}
        adapter._emr_serverless_client = client

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.time.sleep"),
            patch("benchbox.platforms.aws.emr_serverless_adapter.mono_time", return_value=0.0),
            patch("benchbox.utils.clock.mono_time", side_effect=[0.5, 10.0]),
            pytest.raises(ConfigurationError, match="Timeout waiting"),
        ):
            adapter._wait_for_application_state("app-123", ["STARTED"], timeout_seconds=1)
        assert client.get_application.call_count == 1

    def test_ensure_started_creates_when_requested(self, adapter):
        adapter.application_id = None
        adapter.create_application = True
        client = MagicMock()
        client.get_application.return_value = {"application": {"state": "CREATED"}}
        adapter._emr_serverless_client = client
        with (
            patch.object(adapter, "_create_emr_application", return_value="app-new") as create,
            patch.object(adapter, "_wait_for_application_state") as waiter,
        ):
            adapter._ensure_application_started()
        create.assert_called_once()
        client.start_application.assert_called_once_with(applicationId="app-new")
        waiter.assert_called_once_with("app-new", ["STARTED"])
        assert adapter.application_id == "app-new"

    def test_ensure_started_without_app_or_create_raises(self, adapter):
        adapter.application_id = None
        adapter.create_application = False
        with pytest.raises(ConfigurationError, match="No application_id"):
            adapter._ensure_application_started()


class TestConnectionBranches:
    def test_pending_when_create_deferred(self, adapter):
        adapter.application_id = None
        adapter.create_application = True
        adapter._emr_serverless_client = MagicMock()
        assert adapter.create_connection()["status"] == "pending"

    def test_neither_app_nor_create_raises(self, adapter):
        adapter.application_id = None
        adapter.create_application = False
        adapter._emr_serverless_client = MagicMock()
        with pytest.raises(ConfigurationError, match="No application_id"):
            adapter.create_connection()

    def test_client_error_wraps(self, adapter):
        client = MagicMock()
        client.get_application.side_effect = _client_error("AccessDenied")
        adapter._emr_serverless_client = client
        with pytest.raises(ConfigurationError, match="Failed to connect"):
            adapter.create_connection()


class TestSchemaAndLoadPaths:
    def test_create_schema_missing_database(self, adapter):
        client = MagicMock()
        client.get_database.side_effect = _client_error("EntityNotFoundException")
        adapter._glue_client = client
        adapter.create_schema(_benchmark(), None)
        client.create_database.assert_called_once()

    def test_create_schema_unexpected_error_reraises(self, adapter):
        client = MagicMock()
        client.get_database.side_effect = _client_error("Boom")
        adapter._glue_client = client
        with pytest.raises(ClientError):
            adapter.create_schema(_benchmark(), None)

    def test_load_data_missing_dir_raises(self, adapter, tmp_path):
        with pytest.raises(ConfigurationError, match="Source directory not found"):
            adapter.load_data(_benchmark(), None, tmp_path / "nope")

    def test_load_data_upload_and_parquet_tables(self, adapter, tmp_path):
        source = tmp_path / "data"
        source.mkdir()
        staging = MagicMock()
        staging.tables_exist.return_value = False
        adapter._staging = staging
        glue = MagicMock()
        glue.get_table.side_effect = _client_error("EntityNotFoundException")
        adapter._glue_client = glue

        counts, _, meta = adapter.load_data(_benchmark(), None, source)

        assert set(counts) == {"lineitem", "orders"}
        staging.upload_tables.assert_called_once()
        assert glue.create_table.call_count == 2
        assert set(meta["table_uris"]) == {"lineitem", "orders"}

    def test_load_data_non_parquet_registers_via_job(self, adapter, tmp_path):
        source = tmp_path / "data"
        source.mkdir()
        adapter._staging = None
        adapter.table_format = "delta"
        glue = MagicMock()
        glue.get_table.side_effect = _client_error("EntityNotFoundException")
        adapter._glue_client = glue
        with patch.object(adapter, "_submit_job_run", return_value="job-1") as submit:
            adapter.load_data(_benchmark(), None, source)
        assert submit.call_count == 2
        assert "USING DELTA" in submit.call_args[0][0]

    def test_load_data_unexpected_glue_error_reraises(self, adapter, tmp_path):
        source = tmp_path / "data"
        source.mkdir()
        adapter._staging = None
        glue = MagicMock()
        glue.get_table.side_effect = _client_error("Boom")
        adapter._glue_client = glue
        with pytest.raises(ClientError):
            adapter.load_data(_benchmark(), None, source)


class TestExecuteAndClose:
    def test_job_failed_state_raises(self, adapter):
        client = MagicMock()
        client.get_job_run.return_value = {"jobRun": {"state": "FAILED", "stateDetails": "bad"}}
        adapter._emr_serverless_client = client
        with pytest.raises(RuntimeError, match="Job failed"):
            adapter._wait_for_job_run("job-1")

    def test_job_timeout_raises(self, adapter):
        client = MagicMock()
        client.get_job_run.return_value = {"jobRun": {"state": "RUNNING"}}
        adapter._emr_serverless_client = client
        adapter.timeout_minutes = 0
        with pytest.raises(RuntimeError, match="timed out"):
            adapter._wait_for_job_run("job-1")

    def test_execute_failure_envelope(self, adapter):
        with (
            patch.object(adapter, "_ensure_application_started"),
            patch.object(adapter, "_submit_job_run", side_effect=RuntimeError("nope")),
        ):
            result = adapter.execute_query(None, "SELECT 1", "q1")
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "nope"
        assert result["error_type"] == "RuntimeError"

    def test_close_stop_failure_warns(self, adapter, caplog):
        adapter._application_created_by_us = True
        client = MagicMock()
        client.stop_application.side_effect = RuntimeError("denied")
        adapter._emr_serverless_client = client
        with caplog.at_level("WARNING", logger="benchbox.platforms.aws.emr_serverless_adapter"):
            adapter.close()
        assert any("Failed to stop application" in r.getMessage() for r in caplog.records)

    def test_boto3_missing_guard(self, monkeypatch):
        monkeypatch.setattr("benchbox.platforms.aws.emr_serverless_adapter.BOTO3_AVAILABLE", False)
        monkeypatch.setattr(
            "benchbox.platforms.aws.emr_serverless_adapter.check_platform_dependencies",
            lambda *a, **k: (False, ["boto3"]),
        )
        from benchbox.platforms.aws import EMRServerlessAdapter

        with pytest.raises(ConfigurationError):
            EMRServerlessAdapter(
                application_id="app-123",
                s3_staging_dir="s3://bucket/prefix",
                execution_role_arn="arn:aws:iam::123:role/EMR",
            )

    def test_staging_init_failure_warns(self, caplog):
        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.emr_serverless_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.side_effect = RuntimeError("bad uri")
            from benchbox.platforms.aws import EMRServerlessAdapter

            with caplog.at_level("WARNING", logger="benchbox.platforms.aws.emr_serverless_adapter"):
                adapter = EMRServerlessAdapter(
                    application_id="app-123",
                    s3_staging_dir="s3://bucket/prefix",
                    execution_role_arn="arn:aws:iam::123:role/EMR",
                )
        assert adapter._staging is None
        assert any("Failed to initialize S3 staging" in r.getMessage() for r in caplog.records)


class TestLazyClients:
    def test_glue_and_s3_clients_created_and_cached(self, adapter):
        session = MagicMock()
        with patch("benchbox.platforms.aws.emr_serverless_adapter.boto3") as mock_boto3:
            mock_boto3.Session.return_value = session
            glue = adapter._get_glue_client()
            s3 = adapter._get_s3_client()
        session.client.assert_any_call("glue")
        session.client.assert_any_call("s3")
        assert adapter._get_glue_client() is glue
        assert adapter._get_s3_client() is s3

    def test_ensure_started_starting_branch_waits(self, adapter):
        client = MagicMock()
        client.get_application.return_value = {"application": {"state": "STARTING"}}
        adapter._emr_serverless_client = client
        with patch.object(adapter, "_wait_for_application_state") as waiter:
            adapter._ensure_application_started()
        waiter.assert_called_once_with(adapter.application_id, ["STARTED"])
        client.start_application.assert_not_called()


class TestGlueTableExists:
    def test_existing_table_skips_create(self, adapter, tmp_path):
        source = tmp_path / "data"
        source.mkdir()
        adapter._staging = None
        glue = MagicMock()
        glue.get_table.return_value = {"Table": {"Name": "lineitem"}}
        adapter._glue_client = glue
        counts, _, meta = adapter.load_data(SimpleNamespace(tables=["lineitem"]), None, source)
        assert set(counts) == {"lineitem"}
        glue.create_table.assert_not_called()
        assert meta["table_uris"] == {"lineitem": f"{adapter.s3_staging_dir}/tables/lineitem"}


class TestJobPollSleep:
    def test_running_then_success(self, adapter):
        client = MagicMock()
        client.get_job_run.side_effect = [
            {"jobRun": {"state": "RUNNING"}},
            {"jobRun": {"state": "SUCCESS", "totalResourceUtilization": {}}},
        ]
        adapter._emr_serverless_client = client
        with patch("benchbox.platforms.aws.emr_serverless_adapter.time.sleep") as nap:
            state, _ = adapter._wait_for_job_run("job-1")
        assert state == "SUCCESS"
        nap.assert_called_once()


class TestFromConfigTuning:
    def test_tuning_keys_pass_through(self):
        from benchbox.platforms.aws import EMRServerlessAdapter

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.emr_serverless_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging"),
        ):
            adapter = EMRServerlessAdapter.from_config(
                {
                    "application_id": "app-1",
                    "s3_staging_dir": "s3://b/x",
                    "execution_role_arn": "arn:aws:iam::1:role/R",
                    "tuning_enabled": True,
                    "tuning_source": "cli",
                }
            )
        assert adapter.tuning_enabled is True
        assert adapter.tuning_source == "cli"


class TestImportFallback:
    def test_reload_without_boto3(self):
        import importlib
        import sys

        import benchbox.platforms.aws.emr_serverless_adapter as mod

        saved = {k: sys.modules[k] for k in ("boto3", "botocore.exceptions") if k in sys.modules}
        sys.modules["boto3"] = None
        try:
            importlib.reload(mod)
            assert mod.BOTO3_AVAILABLE is False
            assert mod.boto3 is None
            assert mod.ClientError is Exception
        finally:
            for k in ("boto3", "botocore.exceptions"):
                sys.modules.pop(k, None)
            sys.modules.update(saved)
            importlib.reload(mod)


