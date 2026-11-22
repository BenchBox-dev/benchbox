from __future__ import annotations

import importlib
import threading
import time
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.platform_manifest import PLATFORM_MANIFEST
from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.platforms.base.remote_work import StartedRemoteWork
from benchbox.platforms.onehouse.onehouse_client import JobResult, JobState, OnehouseClient
from benchbox.utils import run_deadline
from benchbox.utils.run_deadline import RunDeadline

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _onehouse_client(**kwargs: Any) -> OnehouseClient:
    with (
        patch("benchbox.platforms.onehouse.onehouse_client.REQUESTS_AVAILABLE", True),
        patch("benchbox.platforms.onehouse.onehouse_client.requests", MagicMock()),
    ):
        return OnehouseClient(api_key="key", **kwargs)


def _stub_onehouse(client: OnehouseClient, finished: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    requests_made: list[tuple[str, str]] = []
    submitted = iter(["job-1", "job-2", "job-3", "job-4"])

    def request(method: str, path: str, data: Any = None) -> dict[str, Any]:
        requests_made.append((method, path))
        if (method, path) == ("POST", "/jobs"):
            return {"job_id": next(submitted)}
        if (method, path) == ("POST", "/clusters"):
            return {"cluster_id": "cluster-1"}
        return {}

    client._request = request
    client.get_job_status = lambda job_id: JobResult(
        job_id=job_id, state=JobState.SUCCEEDED if job_id in finished else JobState.RUNNING, duration_seconds=1.0
    )
    return requests_made


def _submit(client: OnehouseClient) -> str:
    from benchbox.platforms.onehouse.onehouse_client import TableFormat

    return client.submit_sql_job(sql="SELECT 1", database="db", table_format=TableFormat.ICEBERG)


def test_onehouse_cancels_the_jobs_this_run_started_and_still_running() -> None:
    client = _onehouse_client()
    requests_made = _stub_onehouse(client, finished={"job-1"})

    first = _submit(client)
    _submit(client)
    _submit(client)
    client.wait_for_job(first, timeout_minutes=1, poll_interval_seconds=0)

    client.cancel_started_work()

    cancels = [path for method, path in requests_made if path.endswith("/cancel")]
    assert cancels == ["/jobs/job-2/cancel", "/jobs/job-3/cancel"]


def test_onehouse_cancels_nothing_it_did_not_start() -> None:
    client = _onehouse_client()
    requests_made = _stub_onehouse(client)

    client.cancel_started_work()

    assert requests_made == []


def test_onehouse_stops_only_a_cluster_this_client_provisioned() -> None:
    inherited = _onehouse_client()
    inherited_requests = _stub_onehouse(inherited)
    inherited.cancel_started_work()
    assert ("DELETE", "/clusters/cluster-1") not in inherited_requests

    provisioned = _onehouse_client()
    provisioned_requests = _stub_onehouse(provisioned)
    provisioned.provision_cluster()
    provisioned.cancel_started_work()
    assert ("DELETE", "/clusters/cluster-1") in provisioned_requests


def test_onehouse_keeps_cancelling_after_one_cancel_fails_and_names_the_job() -> None:
    client = _onehouse_client()
    requests_made = _stub_onehouse(client)
    _submit(client)
    _submit(client)
    plain = client._request

    def request(method: str, path: str, data: Any = None) -> dict[str, Any]:
        if path == "/jobs/job-1/cancel":
            raise RuntimeError("API error: 503")
        return plain(method, path, data)

    client._request = request

    with pytest.raises(RuntimeError, match="job job-1: API error: 503"):
        client.cancel_started_work()

    assert ("POST", "/jobs/job-2/cancel") in requests_made


def test_onehouse_reports_a_failed_cluster_terminate_and_keeps_the_id_so_close_can_retry() -> None:
    client = _onehouse_client()
    requests_made = _stub_onehouse(client)
    client.provision_cluster()
    plain = client._request

    def request(method: str, path: str, data: Any = None) -> dict[str, Any]:
        if method == "DELETE":
            raise RuntimeError("API error: 503")
        return plain(method, path, data)

    client._request = request

    with pytest.raises(RuntimeError, match="cluster cluster-1: API error: 503"):
        client.cancel_started_work()

    assert client._cluster_id == "cluster-1"
    client._request = plain
    client.close()
    assert ("DELETE", "/clusters/cluster-1") in requests_made
    assert client._cluster_id is None


def test_quanton_hook_cancels_the_running_query_job_and_not_the_finished_one() -> None:
    with patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as staging:
        staging.from_uri.return_value = MagicMock()
        from benchbox.platforms.onehouse import QuantonAdapter

        adapter = QuantonAdapter(api_key="key", s3_staging_dir="s3://bucket/data")
    adapter._client = _onehouse_client()
    requests_made = _stub_onehouse(adapter._client, finished={"job-1"})
    adapter._client.get_job_results = lambda job_id: [{"a": 1}]

    first = adapter.execute_query(None, "SELECT 1", "Q1")
    adapter._client.wait_for_job = MagicMock(side_effect=RuntimeError("stopped waiting"))
    second = adapter.execute_query(None, "SELECT 2", "Q2")
    adapter.cancel_remote_work()

    assert first["status"] == "SUCCESS"
    assert second["status"] == "FAILED"
    assert [path for _, path in requests_made if path.endswith("/cancel")] == ["/jobs/job-2/cancel"]


def test_started_work_cancels_each_item_once_and_reports_every_failure() -> None:
    work = StartedRemoteWork()
    work.add("a", "handle-a")
    work.add("b", "handle-b")
    work.add("c", "handle-c")
    work.discard("c")
    cancelled: list[str] = []

    def cancel(key: str, handle: str) -> None:
        if key == "a":
            raise ValueError("denied")
        cancelled.append(handle)

    with pytest.raises(RuntimeError, match="Could not cancel Demo work started by this run: a: ValueError: denied"):
        work.cancel_all("Demo", cancel)

    assert cancelled == ["handle-b"]
    assert work.keys() == ["a"]


@pytest.fixture
def bigquery_adapter():
    with ExitStack() as stack:
        stack.enter_context(patch("benchbox.platforms.bigquery.bigquery", MagicMock()))
        stack.enter_context(patch("benchbox.platforms.bigquery.storage", MagicMock()))
        stack.enter_context(patch("benchbox.platforms.bigquery.service_account", MagicMock()))
        stack.enter_context(patch("benchbox.platforms.bigquery.google_auth", MagicMock()))
        stack.enter_context(patch("benchbox.platforms.bigquery.check_platform_dependencies", return_value=(True, [])))
        from benchbox.platforms.bigquery import BigQueryAdapter

        yield BigQueryAdapter(project_id="proj", dataset_id="ds")


def _query_job(job_id: str, result: Any) -> MagicMock:
    job = MagicMock()
    job.job_id = job_id
    job.result = result
    job.created = job.started = job.ended = None
    job.total_bytes_processed = job.total_bytes_billed = job.slot_millis = 0
    return job


def test_bigquery_cancels_the_query_job_in_flight_and_not_a_finished_one(bigquery_adapter) -> None:
    finished = _query_job("job-done", MagicMock(return_value=[(1,)]))
    stopped = threading.Event()
    started = threading.Event()

    def block() -> list[Any]:
        started.set()
        stopped.wait(10)
        raise RuntimeError("cancelled")

    running = _query_job("job-running", block)
    running.cancel.side_effect = stopped.set
    connection = MagicMock()
    connection.query.side_effect = [finished, running]

    bigquery_adapter.execute_query(connection, "SELECT 1", "Q1")
    worker = threading.Thread(target=bigquery_adapter.execute_query, args=(connection, "SELECT 2", "Q2"))
    worker.start()
    assert started.wait(10)
    bigquery_adapter.cancel_remote_work()
    worker.join(10)

    running.cancel.assert_called_once_with()
    finished.cancel.assert_not_called()
    assert bigquery_adapter._started_work.keys() == []


def test_bigquery_cancels_nothing_when_no_query_is_running(bigquery_adapter) -> None:
    finished = _query_job("job-done", MagicMock(return_value=[(1,)]))
    connection = MagicMock()
    connection.query.return_value = finished

    bigquery_adapter.execute_query(connection, "SELECT 1", "Q1")
    bigquery_adapter.cancel_remote_work()

    finished.cancel.assert_not_called()


@pytest.fixture
def emr_adapter():
    with (
        patch("benchbox.platforms.aws.emr_serverless_adapter.BOTO3_AVAILABLE", True),
        patch("benchbox.platforms.aws.emr_serverless_adapter.boto3", MagicMock()),
        patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as staging,
    ):
        staging.from_uri.return_value = MagicMock()
        from benchbox.platforms.aws import EMRServerlessAdapter

        yield EMRServerlessAdapter(
            application_id="app-123",
            s3_staging_dir="s3://bucket/prefix",
            execution_role_arn="arn:aws:iam::123:role/EMR",
        )


def _emr_client(adapter: Any) -> MagicMock:
    client = MagicMock()
    client.start_job_run.side_effect = [{"jobRunId": "run-1"}, {"jobRunId": "run-2"}]
    client.get_job_run.return_value = {"jobRun": {"state": "SUCCESS", "totalResourceUtilization": {}}}
    adapter._emr_serverless_client = client
    adapter._s3_client = MagicMock()
    return client


def test_emr_serverless_cancels_job_runs_in_flight_only(emr_adapter) -> None:
    client = _emr_client(emr_adapter)
    first = emr_adapter._submit_job_run("SELECT 1")
    emr_adapter._wait_for_job_run(first)
    emr_adapter._submit_job_run("SELECT 2")

    emr_adapter.cancel_remote_work()

    client.cancel_job_run.assert_called_once_with(applicationId="app-123", jobRunId="run-2")


def test_emr_serverless_stops_the_application_only_when_this_run_created_it(emr_adapter) -> None:
    client = _emr_client(emr_adapter)

    emr_adapter.cancel_remote_work()
    client.stop_application.assert_not_called()

    emr_adapter._application_created_by_us = True
    emr_adapter.cancel_remote_work()
    client.stop_application.assert_called_once_with(applicationId="app-123")
    assert emr_adapter._application_created_by_us is False


def test_emr_serverless_still_stops_its_application_when_a_job_cancel_fails(emr_adapter) -> None:
    client = _emr_client(emr_adapter)
    emr_adapter._submit_job_run("SELECT 1")
    emr_adapter._application_created_by_us = True
    client.cancel_job_run.side_effect = RuntimeError("throttled")

    with pytest.raises(RuntimeError, match="run-1: RuntimeError: throttled"):
        emr_adapter.cancel_remote_work()

    client.stop_application.assert_called_once()


def test_emr_serverless_waits_for_cancelled_runs_to_end_before_stopping_the_application(emr_adapter) -> None:
    client = _emr_client(emr_adapter)
    emr_adapter._submit_job_run("SELECT 1")
    emr_adapter._application_created_by_us = True
    states = iter(["CANCELLING", "CANCELLING", "CANCELLED"])
    calls: list[str] = []

    def poll(**_kwargs: Any) -> dict[str, Any]:
        calls.append("poll")
        return {"jobRun": {"state": next(states)}}

    client.get_job_run.side_effect = poll
    client.cancel_job_run.side_effect = lambda **_kwargs: calls.append("cancel")
    client.stop_application.side_effect = lambda **_kwargs: calls.append("stop")

    with patch("benchbox.platforms.aws.emr_serverless_adapter._pause"):
        emr_adapter.cancel_remote_work()

    assert calls == ["cancel", "poll", "poll", "poll", "stop"]


def test_emr_serverless_reports_runs_that_never_end_and_a_failed_application_stop(emr_adapter) -> None:
    client = _emr_client(emr_adapter)
    emr_adapter._submit_job_run("SELECT 1")
    emr_adapter._application_created_by_us = True
    client.get_job_run.return_value = {"jobRun": {"state": "CANCELLING"}}
    client.stop_application.side_effect = RuntimeError("application has running jobs")

    with (
        patch("benchbox.platforms.aws.emr_serverless_adapter.remote_cleanup_seconds_left", return_value=0.0),
        patch("benchbox.platforms.aws.emr_serverless_adapter._pause"),
        pytest.raises(RuntimeError) as excinfo,
    ):
        emr_adapter.cancel_remote_work()

    assert "run-1 had not ended" in str(excinfo.value)
    assert "application stop failed" in str(excinfo.value)
    assert emr_adapter._application_created_by_us is True


def test_emr_serverless_stops_the_application_inside_the_window_the_caller_waits(emr_adapter, monkeypatch) -> None:
    emr_module = importlib.import_module("benchbox.platforms.aws.emr_serverless_adapter")
    client = _emr_client(emr_adapter)
    emr_adapter._submit_job_run("SELECT 1")
    emr_adapter._application_created_by_us = True
    client.get_job_run.return_value = {"jobRun": {"state": "CANCELLING"}}
    stopped_at: list[float] = []
    client.stop_application.side_effect = lambda **_kwargs: stopped_at.append(time.monotonic())
    monkeypatch.setattr(run_deadline, "HARD_STOP_GRACE_SECONDS", 1.5)
    monkeypatch.setattr(run_deadline, "HARD_STOP_EXPORT_MARGIN_SECONDS", 0.5, raising=False)
    monkeypatch.setattr(emr_module, "EMR_SERVERLESS_STOP_RESERVE_SECONDS", 0.4, raising=False)
    monkeypatch.setattr(emr_module, "_pause", lambda _seconds: time.sleep(0.05), raising=False)
    deadline = RunDeadline(5)
    deadline.register_remote_cleanup(emr_adapter, emr_adapter.cancel_remote_work, "EMR Serverless")
    began = time.monotonic()

    errors = deadline.cancel_remote_work()

    assert len(stopped_at) == 1
    assert stopped_at[0] - began < 1.0
    assert len(errors) == 1
    assert "run-1 had not ended" in errors[0]
    assert emr_adapter._application_created_by_us is False


@pytest.fixture
def glue_adapter():
    with (
        patch("benchbox.platforms.aws.glue_adapter.BOTO3_AVAILABLE", True),
        patch("benchbox.platforms.aws.glue_adapter.boto3", MagicMock()),
        patch("benchbox.platforms.aws.glue_adapter.CloudSparkStaging") as staging,
    ):
        staging.from_uri.return_value = MagicMock()
        from benchbox.platforms.aws import AWSGlueAdapter

        yield AWSGlueAdapter(
            s3_staging_dir="s3://my-bucket/benchbox-data",
            job_role="arn:aws:iam::123456789012:role/GlueRole",
        )


def test_glue_stops_the_job_runs_in_flight_and_not_a_finished_one(glue_adapter) -> None:
    client = MagicMock()
    client.start_job_run.side_effect = [{"JobRunId": "jr-1"}, {"JobRunId": "jr-2"}]
    client.get_job_run.return_value = {"JobRun": {"JobRunState": "SUCCEEDED", "ExecutionTime": 1}}
    glue_adapter._glue_client = client
    glue_adapter._job_name = "benchbox-job"

    first = glue_adapter._submit_job_run("SELECT 1")
    glue_adapter._wait_for_job(first, poll_interval=0)
    glue_adapter._submit_job_run("SELECT 2")

    glue_adapter.cancel_remote_work()

    client.batch_stop_job_run.assert_called_once_with(JobName="benchbox-job", JobRunIds=["jr-2"])


def test_glue_reports_a_run_that_batch_stop_job_run_could_not_stop_and_keeps_tracking_it(glue_adapter) -> None:
    client = MagicMock()
    client.start_job_run.return_value = {"JobRunId": "jr-1"}
    client.batch_stop_job_run.return_value = {
        "SuccessfulSubmissions": [],
        "Errors": [{"JobRunId": "jr-1", "ErrorDetail": {"ErrorCode": "InternalServiceException"}}],
    }
    glue_adapter._glue_client = client
    glue_adapter._job_name = "benchbox-job"
    glue_adapter._submit_job_run("SELECT 1")

    with pytest.raises(RuntimeError, match="jr-1"):
        glue_adapter.cancel_remote_work()

    assert glue_adapter._started_work.keys() == ["jr-1"]


@pytest.fixture
def dataproc_adapter():
    with (
        patch("benchbox.platforms.gcp.dataproc_adapter.GOOGLE_CLOUD_AVAILABLE", True),
        patch("benchbox.platforms.gcp.dataproc_adapter.dataproc_v1", MagicMock()),
        patch("benchbox.platforms.gcp.dataproc_adapter.storage", MagicMock()),
        patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as staging,
    ):
        staging.from_uri.return_value = MagicMock()
        from benchbox.platforms.gcp import DataprocAdapter

        yield DataprocAdapter(project_id="proj", gcs_staging_dir="gs://bucket/path")


def _dataproc_client(adapter: Any) -> MagicMock:
    client = MagicMock()
    finished = MagicMock()
    finished.result.return_value = SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="DONE")))
    stuck = MagicMock()
    stuck.result.side_effect = TimeoutError()
    client.submit_job_as_operation.side_effect = [finished, stuck]
    adapter._job_client = client
    adapter._upload_to_gcs = MagicMock()
    return client


def test_dataproc_cancels_the_job_that_outlived_its_wait_and_not_the_finished_one(dataproc_adapter) -> None:
    client = _dataproc_client(dataproc_adapter)
    dataproc_adapter._submit_spark_sql_job("SELECT 1")
    with pytest.raises(TimeoutError):
        dataproc_adapter._submit_spark_sql_job("SELECT 2")
    stuck_job_id = client.submit_job_as_operation.call_args.kwargs["job"]["reference"]["job_id"]

    dataproc_adapter.cancel_remote_work()

    client.cancel_job.assert_called_once_with(project_id="proj", region=dataproc_adapter.region, job_id=stuck_job_id)


def test_dataproc_does_not_track_a_job_that_failed(dataproc_adapter) -> None:
    client = _dataproc_client(dataproc_adapter)
    client.submit_job_as_operation.side_effect = None
    failing = MagicMock()
    failing.result.side_effect = RuntimeError("job failed")
    client.submit_job_as_operation.return_value = failing

    with pytest.raises(RuntimeError):
        dataproc_adapter._submit_spark_sql_job("SELECT 1")
    dataproc_adapter.cancel_remote_work()

    client.cancel_job.assert_not_called()


def test_dataproc_deletes_only_a_cluster_this_run_created(dataproc_adapter) -> None:
    client = _dataproc_client(dataproc_adapter)
    cluster_client = MagicMock()
    dataproc_adapter._cluster_client = cluster_client

    dataproc_adapter.cancel_remote_work()
    cluster_client.delete_cluster.assert_not_called()

    dataproc_adapter._cluster_created_by_us = True
    dataproc_adapter.create_ephemeral_cluster = True
    dataproc_adapter.cancel_remote_work()
    cluster_client.delete_cluster.assert_called_once()
    assert client.cancel_job.call_count == 0


def test_dataproc_reports_a_failed_cluster_delete_and_keeps_the_flag_so_close_can_retry(dataproc_adapter) -> None:
    _dataproc_client(dataproc_adapter)
    cluster_client = MagicMock()
    cluster_client.delete_cluster.return_value.result.side_effect = RuntimeError("quota service down")
    dataproc_adapter._cluster_client = cluster_client
    dataproc_adapter._cluster_created_by_us = True
    dataproc_adapter.create_ephemeral_cluster = True

    with pytest.raises(RuntimeError, match="quota service down"):
        dataproc_adapter.cancel_remote_work()

    assert dataproc_adapter._cluster_created_by_us is True
    cluster_client.delete_cluster.return_value.result.side_effect = None
    dataproc_adapter.close()
    assert cluster_client.delete_cluster.call_count == 2
    assert dataproc_adapter._cluster_created_by_us is False


def test_the_base_adapter_hook_does_nothing() -> None:
    assert PlatformAdapter.cancel_remote_work(SimpleNamespace()) is None


def _cloud_entries() -> list[Any]:
    return [entry for entry in PLATFORM_MANIFEST if entry.metadata_dict().get("category") == "cloud"]


def _adapter_class(entry: Any) -> type:
    return getattr(importlib.import_module(entry.adapter.module), entry.adapter.class_name)


@pytest.mark.parametrize("entry", _cloud_entries(), ids=lambda entry: entry.key)
def test_the_manifest_declares_cleanup_exactly_where_the_adapter_implements_it(entry: Any) -> None:
    implemented = _adapter_class(entry).cancel_remote_work is not PlatformAdapter.cancel_remote_work

    assert bool(entry.capabilities.get("run_timeout_cleanup", False)) is implemented


def test_the_adapters_with_cleanup_are_the_ones_the_survey_found_workable() -> None:
    declared = {entry.key for entry in PLATFORM_MANIFEST if entry.capabilities.get("run_timeout_cleanup")}

    assert declared == {"quanton", "bigquery", "emr-serverless", "glue", "dataproc"}
