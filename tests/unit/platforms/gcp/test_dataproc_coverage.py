from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.gcp import DataprocAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _adapter(**kwargs) -> DataprocAdapter:
    with (
        patch("benchbox.platforms.gcp.dataproc_adapter.GOOGLE_CLOUD_AVAILABLE", True),
        patch("benchbox.platforms.gcp.dataproc_adapter.dataproc_v1", MagicMock()),
        patch("benchbox.platforms.gcp.dataproc_adapter.storage", MagicMock()),
        patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        return DataprocAdapter(project_id="proj", gcs_staging_dir="gs://bucket/path", **kwargs)


def _benchmark(*tables: str) -> SimpleNamespace:
    return SimpleNamespace(get_table_names=lambda: list(tables))


def test_client_helpers_cache_clients() -> None:
    adapter = _adapter()

    cluster_client = MagicMock()
    job_client = MagicMock()
    storage_client = MagicMock()

    with (
        patch("benchbox.platforms.gcp.dataproc_adapter.dataproc_v1") as mock_dp,
        patch("benchbox.platforms.gcp.dataproc_adapter.storage") as mock_storage,
    ):
        mock_dp.ClusterControllerClient.return_value = cluster_client
        mock_dp.JobControllerClient.return_value = job_client
        mock_storage.Client.return_value = storage_client

        assert adapter._get_cluster_client() is adapter._get_cluster_client()
        assert adapter._get_job_client() is adapter._get_job_client()
        assert adapter._get_storage_client() is adapter._get_storage_client()


def test_create_connection_returns_pending_for_missing_ephemeral_cluster() -> None:
    adapter = _adapter(create_ephemeral_cluster=True)
    client = MagicMock()
    client.get_cluster.side_effect = RuntimeError("404 NotFound")

    with patch.object(adapter, "_get_cluster_client", return_value=client):
        result = adapter.create_connection()

    assert result["status"] == "pending"
    assert "will be created" in result["message"]


def test_close_deletes_cluster_when_created_by_us() -> None:
    adapter = _adapter(create_ephemeral_cluster=True)
    adapter._cluster_created_by_us = True

    with patch.object(adapter, "_delete_cluster") as mock_delete:
        adapter.close()

    mock_delete.assert_called_once_with()


def test_apply_tuning_configuration_collects_results() -> None:
    adapter = _adapter()
    adapter.apply_primary_keys = MagicMock(return_value={"pk": 1})
    adapter.apply_foreign_keys = MagicMock(return_value={"fk": 1})
    adapter.apply_platform_optimizations = MagicMock(return_value={"opt": 1})

    config = SimpleNamespace(
        scale_factor=5.0,
        primary_keys=["a"],
        foreign_keys=["b"],
        platform_optimizations={"x": 1},
    )
    result = adapter.apply_tuning_configuration(config)

    assert adapter._scale_factor == 5.0
    assert result["primary_keys"] == {"pk": 1}
    assert result["foreign_keys"] == {"fk": 1}
    assert result["platform_optimizations"] == {"opt": 1}


def test_dataproc_job_state_constants_defined() -> None:
    from benchbox.platforms.gcp.dataproc_adapter import DataprocJobState

    assert DataprocJobState.PENDING == "PENDING"
    assert DataprocJobState.RUNNING == "RUNNING"
    assert DataprocJobState.DONE == "DONE"
    assert DataprocJobState.ERROR == "ERROR"
    assert DataprocJobState.CANCELLED == "CANCELLED"


def test_adapter_stores_cluster_config_defaults() -> None:
    adapter = _adapter()
    assert adapter.master_machine_type == "n2-standard-4"
    assert adapter.worker_machine_type == "n2-standard-4"
    assert adapter.num_workers == 2
    assert adapter.region == "us-central1"


def test_adapter_custom_cluster_config() -> None:
    adapter = _adapter(
        cluster_name="my-cluster",
        master_machine_type="n2-highmem-4",
        worker_machine_type="n2-highmem-8",
        num_workers=4,
        region="us-east1",
    )
    assert adapter.cluster_name == "my-cluster"
    assert adapter.master_machine_type == "n2-highmem-4"
    assert adapter.num_workers == 4
    assert adapter.region == "us-east1"


def test_upload_to_gcs_calls_storage_upload() -> None:
    adapter = _adapter()
    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_storage.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    with patch.object(adapter, "_get_storage_client", return_value=mock_storage):
        adapter._upload_to_gcs("gs://my-bucket/scripts/job.py", "spark code here")

    mock_storage.bucket.assert_called_once_with("my-bucket")
    mock_blob.upload_from_string.assert_called_once_with("spark code here")


def test_get_platform_info_basic_fields() -> None:
    adapter = _adapter()
    info = adapter.get_platform_info()
    assert info["platform"] == "dataproc"
    assert info["vendor"] == "Google Cloud"
    assert info["project_id"] == "proj"


def test_retrieve_results_parses_json_lines() -> None:
    adapter = _adapter()
    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.name = "results/job-abc/part-0.json"
    mock_blob.download_as_string.return_value = b'{"a": 1}\n{"a": 2}\n'
    mock_storage.bucket.return_value = mock_bucket
    mock_bucket.list_blobs.return_value = [mock_blob]

    with patch.object(adapter, "_get_storage_client", return_value=mock_storage):
        results = adapter._retrieve_results("job-abc")

    assert len(results) == 2
    assert results[0]["a"] == 1


def test_create_cluster_calls_client_and_waits() -> None:
    adapter = _adapter()
    client = MagicMock()
    operation = MagicMock()
    operation.result.return_value = SimpleNamespace(cluster_name="benchbox-cluster")
    client.create_cluster.return_value = operation

    with patch.object(adapter, "_get_cluster_client", return_value=client):
        adapter._create_cluster()

    client.create_cluster.assert_called_once()
    operation.result.assert_called_once()
    assert adapter._cluster_created_by_us is True


def test_create_cluster_includes_preemptible_workers() -> None:
    adapter = _adapter(use_preemptible_workers=True, num_preemptible_workers=2)
    client = MagicMock()
    operation = MagicMock()
    operation.result.return_value = SimpleNamespace(cluster_name="benchbox-cluster")
    client.create_cluster.return_value = operation

    with patch.object(adapter, "_get_cluster_client", return_value=client):
        adapter._create_cluster()

    call_kwargs = client.create_cluster.call_args[1]
    assert "secondary_worker_config" in call_kwargs["cluster"]["config"]


def test_ensure_cluster_exists_running_returns_immediately() -> None:
    adapter = _adapter()
    client = MagicMock()
    cluster = SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="RUNNING")))
    client.get_cluster.return_value = cluster

    with patch.object(adapter, "_get_cluster_client", return_value=client):
        adapter._ensure_cluster_exists()


def test_ensure_cluster_exists_creating_waits_for_running() -> None:
    adapter = _adapter()
    client = MagicMock()
    creating = SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="CREATING")))
    running = SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="RUNNING")))
    client.get_cluster.side_effect = [creating, running]

    with (
        patch.object(adapter, "_get_cluster_client", return_value=client),
        patch("benchbox.platforms.gcp.dataproc_adapter.time.sleep"),
    ):
        adapter._ensure_cluster_exists()

    assert client.get_cluster.call_count == 2


def test_ensure_cluster_exists_creating_raises_on_error_state() -> None:
    adapter = _adapter()
    client = MagicMock()
    creating = SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="CREATING")))
    error = SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="ERROR")))
    client.get_cluster.side_effect = [creating, error]

    with (
        patch.object(adapter, "_get_cluster_client", return_value=client),
        patch("benchbox.platforms.gcp.dataproc_adapter.time.sleep"),
        pytest.raises(ConfigurationError, match="ERROR"),
    ):
        adapter._ensure_cluster_exists()


def test_ensure_cluster_exists_not_found_creates_ephemeral() -> None:
    adapter = _adapter(create_ephemeral_cluster=True)
    client = MagicMock()
    client.get_cluster.side_effect = RuntimeError("404 NotFound")

    with (
        patch.object(adapter, "_get_cluster_client", return_value=client),
        patch.object(adapter, "_create_cluster") as mock_create,
    ):
        adapter._ensure_cluster_exists()

    mock_create.assert_called_once()


def test_ensure_cluster_exists_not_found_raises_without_ephemeral() -> None:
    adapter = _adapter(create_ephemeral_cluster=False)
    client = MagicMock()
    client.get_cluster.side_effect = RuntimeError("404 NotFound")

    with (
        patch.object(adapter, "_get_cluster_client", return_value=client),
        pytest.raises(RuntimeError, match="NotFound"),
    ):
        adapter._ensure_cluster_exists()


def test_delete_cluster_calls_client() -> None:
    adapter = _adapter()
    adapter._cluster_created_by_us = True
    client = MagicMock()
    operation = MagicMock()
    client.delete_cluster.return_value = operation

    with patch.object(adapter, "_get_cluster_client", return_value=client):
        adapter._delete_cluster()

    client.delete_cluster.assert_called_once()
    operation.result.assert_called_once()


def test_delete_cluster_logs_warning_on_error() -> None:
    adapter = _adapter()
    adapter._cluster_created_by_us = True
    client = MagicMock()
    client.delete_cluster.side_effect = RuntimeError("forbidden")

    with patch.object(adapter, "_get_cluster_client", return_value=client):
        adapter._delete_cluster()


def test_delete_cluster_skips_when_not_created_by_us() -> None:
    adapter = _adapter()
    adapter._cluster_created_by_us = False
    client = MagicMock()

    with patch.object(adapter, "_get_cluster_client", return_value=client):
        adapter._delete_cluster()

    client.delete_cluster.assert_not_called()


def test_create_schema_submits_spark_sql_job() -> None:
    adapter = _adapter()
    with (
        patch.object(adapter, "_ensure_cluster_exists"),
        patch.object(adapter, "_submit_spark_sql_job", return_value=("job-1", "DONE")) as mock_submit,
    ):
        adapter.database = "my_db"
        adapter.create_schema(_benchmark(), None)

    mock_submit.assert_called_once()
    assert "my_db" in mock_submit.call_args[0][0]


def test_submit_spark_sql_job_with_wait() -> None:
    adapter = _adapter()
    client = MagicMock()
    operation = MagicMock()
    result = SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="DONE")))
    operation.result.return_value = result
    client.submit_job_as_operation.return_value = operation

    with (
        patch.object(adapter, "_get_job_client", return_value=client),
        patch.object(adapter, "_upload_to_gcs"),
    ):
        job_id, state = adapter._submit_spark_sql_job("SELECT 1", wait_for_completion=True)

    assert state == "DONE"
    assert job_id.startswith("benchbox-")


def test_submit_spark_sql_job_without_wait() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.submit_job_as_operation.return_value = MagicMock()

    with (
        patch.object(adapter, "_get_job_client", return_value=client),
        patch.object(adapter, "_upload_to_gcs"),
    ):
        job_id, state = adapter._submit_spark_sql_job("SELECT 1", wait_for_completion=False)

    assert state == "PENDING"


def test_load_data_skips_when_tables_exist(tmp_path) -> None:
    adapter = _adapter()
    adapter._staging = MagicMock()
    adapter._staging.tables_exist.return_value = True
    adapter._staging.get_table_uri.side_effect = lambda t: f"gs://bucket/{t}"

    stats, _, metadata = adapter.load_data(_benchmark("lineitem"), None, tmp_path)

    assert "lineitem" in stats
    assert metadata is not None
    assert "lineitem" in metadata["table_uris"]
    adapter._staging.upload_tables.assert_not_called()


def test_load_data_uploads_and_creates_tables(tmp_path) -> None:
    adapter = _adapter()
    adapter._staging = MagicMock()
    adapter._staging.tables_exist.return_value = False

    with (
        patch.object(adapter, "_ensure_cluster_exists"),
        patch.object(adapter, "_submit_spark_sql_job", return_value=("j1", "DONE")),
    ):
        stats, _, metadata = adapter.load_data(_benchmark("lineitem"), None, tmp_path)

    assert "lineitem" in stats
    assert metadata is not None
    assert "lineitem" in metadata["table_uris"]
    adapter._staging.upload_tables.assert_called_once()


def test_load_data_raises_on_missing_source_dir() -> None:
    adapter = _adapter()

    with pytest.raises(ConfigurationError, match="Source directory not found"):
        adapter.load_data(_benchmark("lineitem"), None, "/nonexistent/path")


def test_execute_query_returns_results() -> None:
    adapter = _adapter()
    with (
        patch.object(adapter, "_ensure_cluster_exists"),
        patch.object(adapter, "_submit_spark_sql_job", return_value=("job-1", "DONE")),
        patch.object(adapter, "_retrieve_results", return_value=[{"x": 1}]),
    ):
        result = adapter.execute_query(None, "SELECT 1", "q1")

    assert result["status"] == "SUCCESS"
    assert result["results"] == [{"x": 1}]
    assert adapter._query_count == 1


def test_execute_query_raises_on_failed_state() -> None:
    adapter = _adapter()
    with (
        patch.object(adapter, "_ensure_cluster_exists"),
        patch.object(adapter, "_submit_spark_sql_job", return_value=("job-1", "ERROR")),
    ):
        result = adapter.execute_query(None, "SELECT 1", "q1")

    assert result["status"] == "FAILED"
    assert "job failed" in result["error"]


def test_close_logs_total_job_time() -> None:
    adapter = _adapter()
    adapter._total_job_time_seconds = 42.5
    adapter.close()


def test_add_cli_arguments_registers_options() -> None:
    parser = MagicMock()
    group = MagicMock()
    parser.add_argument_group.return_value = group
    DataprocAdapter.add_cli_arguments(parser)
    assert group.add_argument.call_count >= 6


def test_from_config_generates_cluster_name_when_missing() -> None:
    config = {
        "project_id": "my-project",
        "gcs_staging_dir": "gs://bucket/path",
        "benchmark": "tpch",
        "scale_factor": 1,
    }
    adapter = DataprocAdapter.from_config(config)
    assert adapter.cluster_name.startswith("benchbox-tpch-sf1-")


def test_from_config_uses_provided_cluster_name() -> None:
    config = {
        "project_id": "my-project",
        "gcs_staging_dir": "gs://bucket/path",
        "cluster_name": "my-cluster",
    }
    adapter = DataprocAdapter.from_config(config)
    assert adapter.cluster_name == "my-cluster"


def test_apply_tuning_configuration_sets_scale_factor() -> None:
    adapter = _adapter()
    config = SimpleNamespace(
        scale_factor=5.0,
        primary_keys=None,
        foreign_keys=None,
        platform_optimizations=None,
    )
    result = adapter.apply_tuning_configuration(config)
    assert adapter._scale_factor == 5.0
    assert result == {}


def test_get_target_dialect_returns_spark() -> None:
    adapter = _adapter()
    assert adapter.get_target_dialect() == "spark"


