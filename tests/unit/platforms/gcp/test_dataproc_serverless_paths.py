# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from types import SimpleNamespace
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
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.GOOGLE_CLOUD_AVAILABLE", True),
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.dataproc_v1", MagicMock()),
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.storage", MagicMock()),
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        from benchbox.platforms.gcp import DataprocServerlessAdapter

        yield DataprocServerlessAdapter(
            project_id="proj-1",
            gcs_staging_dir="gs://bucket/prefix",
        )


def _benchmark():
    return SimpleNamespace(tables=["lineitem", "orders"])


class TestClientsAndConnection:
    def test_create_connection_lists_batches(self, adapter):
        client = MagicMock()
        adapter._batch_client = client
        result = adapter.create_connection()
        assert result["status"] == "connected"
        assert result["project_id"] == "proj-1"
        client.list_batches.assert_called_once()

    def test_google_missing_guard(self, monkeypatch):
        monkeypatch.setattr(
            "benchbox.platforms.gcp.dataproc_serverless_adapter.GOOGLE_CLOUD_AVAILABLE",
            False,
        )
        monkeypatch.setattr(
            "benchbox.platforms.gcp.dataproc_serverless_adapter.check_platform_dependencies",
            lambda *a, **k: (False, ["google-cloud-dataproc"]),
        )
        from benchbox.platforms.gcp import DataprocServerlessAdapter

        with pytest.raises(ConfigurationError):
            DataprocServerlessAdapter(project_id="p", gcs_staging_dir="gs://b/x")

    def test_staging_init_failure_warns(self, adapter, caplog):
        assert adapter._staging is not None
        with (
            patch("benchbox.platforms.gcp.dataproc_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.side_effect = RuntimeError("bad uri")
            from benchbox.platforms.gcp import DataprocServerlessAdapter

            with caplog.at_level("WARNING", logger="benchbox.platforms.gcp.dataproc_serverless_adapter"):
                fresh = DataprocServerlessAdapter(project_id="p", gcs_staging_dir="gs://b/x")
        assert fresh._staging is None
        assert any("Failed to initialize GCS staging" in r.getMessage() for r in caplog.records)


class TestBatchSubmission:
    def _client(self, adapter):
        client = MagicMock()
        operation = MagicMock()
        operation.result.return_value = SimpleNamespace(state=SimpleNamespace(name="SUCCEEDED"))
        client.create_batch.return_value = operation
        adapter._batch_client = client
        return client

    def test_quote_bearing_query_passed_as_data(self, adapter, tmp_path):
        import py_compile
        from pathlib import Path

        from benchbox.platforms.gcp import dataproc_serverless_adapter

        client = self._client(adapter)
        query = "SELECT 'it''s', \"q\" FROM t WHERE x = 'a\nb\\'"
        with patch.object(adapter, "_upload_to_gcs") as upload:
            adapter._submit_spark_sql_batch(query)
        source = Path(dataproc_serverless_adapter.__file__).with_name("_dataproc_query.py")
        assert upload.call_args[0][1] == source.read_text(encoding="utf-8")
        py_compile.compile(str(source), cfile=str(tmp_path / "dataproc_query.pyc"), doraise=True)
        args = client.create_batch.call_args.kwargs["request"]["batch"]["pyspark_batch"]["args"]
        assert args == ["USE benchbox", query, args[2]]
        assert args[2].startswith("gs://bucket/prefix/results/benchbox-")
        batch_id = client.create_batch.call_args.kwargs["request"]["batch_id"]
        assert args[2] == f"gs://bucket/prefix/results/{batch_id}"

    @pytest.mark.parametrize(
        "database, expected",
        [
            ('quoted"database', 'USE quoted"database'),
            ("line\nbreak", "USE line\nbreak"),
            ("slash\\tname", "USE slash\tname"),
        ],
    )
    def test_database_metadata_is_not_python_source(self, adapter, database, expected):
        adapter.database = database
        client = self._client(adapter)
        with patch.object(adapter, "_upload_to_gcs"):
            adapter._submit_spark_sql_batch("SELECT 1")
        args = client.create_batch.call_args.kwargs["request"]["batch"]["pyspark_batch"]["args"]
        assert args[0] == expected
        assert args[1] == "SELECT 1"

    def test_submit_builds_service_account_and_network(self, adapter):
        adapter.service_account = "sa@proj.iam.gserviceaccount.com"
        adapter.network_uri = "net"
        adapter.subnetwork_uri = "subnet"
        adapter._spark_config = {"spark.sql.shuffle.partitions": "42"}
        client = self._client(adapter)
        with patch.object(adapter, "_upload_to_gcs"):
            batch_id, state = adapter._submit_spark_sql_batch("SELECT 1")
        _, kwargs = client.create_batch.call_args
        batch = kwargs["request"]["batch"]
        assert batch["environment_config"]["execution_config"]["service_account"].startswith("sa@")
        assert batch["environment_config"]["execution_config"]["network_uri"] == "net"
        assert batch["environment_config"]["execution_config"]["subnetwork_uri"] == "subnet"
        assert batch["runtime_config"]["properties"] == {"spark.sql.shuffle.partitions": "42"}
        assert batch_id.startswith("benchbox-")
        assert state == "SUCCEEDED"

    def test_submit_no_wait_returns_pending(self, adapter):
        from benchbox.platforms.gcp.dataproc_serverless_adapter import DataprocBatchState

        client = MagicMock()
        client.create_batch.return_value = MagicMock()
        adapter._batch_client = client
        with patch.object(adapter, "_upload_to_gcs"):
            batch_id, state = adapter._submit_spark_sql_batch("SELECT 1", wait_for_completion=False)
        assert state == DataprocBatchState.PENDING
        assert batch_id.startswith("benchbox-")


class TestGcsIO:
    def test_upload_strips_gs_scheme(self, adapter):
        bucket, blob = MagicMock(), MagicMock()
        storage = MagicMock()
        storage.bucket.return_value = bucket
        bucket.blob.return_value = blob
        adapter._storage_client = storage
        adapter._upload_to_gcs("gs://bucket/dir/file.py", "print(1)")
        storage.bucket.assert_called_once_with("bucket")
        bucket.blob.assert_called_once_with("dir/file.py")
        blob.upload_from_string.assert_called_once_with("print(1)")

    def test_retrieve_parses_json_lines(self, adapter):
        blob = MagicMock()
        blob.name = "results/batch-1/part-0.json"
        blob.download_as_string.return_value = b'{"a": 1}\n{"a": 2}\n\n'
        other = MagicMock()
        other.name = "results/batch-1/_SUCCESS"
        bucket = MagicMock()
        bucket.list_blobs.return_value = [other, blob]
        storage = MagicMock()
        storage.bucket.return_value = bucket
        adapter._storage_client = storage
        assert adapter._retrieve_results("batch-1") == [{"a": 1}, {"a": 2}]


class TestLoadAndExecute:
    def test_create_schema_submits_database_batch(self, adapter):
        with patch.object(adapter, "_submit_spark_sql_batch", return_value=("b-1", "SUCCEEDED")) as submit:
            adapter.create_schema(_benchmark(), None)
        submit.assert_called_once_with("CREATE DATABASE IF NOT EXISTS benchbox", wait_for_completion=True)

    def test_load_data_registers_tables(self, adapter, tmp_path):
        source = tmp_path / "data"
        source.mkdir()
        adapter._staging = None
        with patch.object(adapter, "_submit_spark_sql_batch", return_value=("b-1", "SUCCEEDED")) as submit:
            counts, _, meta = adapter.load_data(_benchmark(), None, source)
        assert set(counts) == {"lineitem", "orders"}
        assert submit.call_count == 2
        assert set(meta["table_uris"]) == {"lineitem", "orders"}

    def test_load_data_missing_dir_raises(self, adapter, tmp_path):
        with pytest.raises(ConfigurationError, match="Source directory not found"):
            adapter.load_data(_benchmark(), None, tmp_path / "nope")

    def test_execute_success_envelope(self, adapter):
        with (
            patch.object(adapter, "_submit_spark_sql_batch", return_value=("b-1", "SUCCEEDED")),
            patch.object(adapter, "_retrieve_results", return_value=[{"n": 1}]),
        ):
            result = adapter.execute_query(None, "SELECT 1", "q1")
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 1
        assert result["error"] is None
        assert adapter._query_count == 1

    def test_execute_bad_state_envelope(self, adapter):
        with patch.object(adapter, "_submit_spark_sql_batch", return_value=("b-1", "FAILED")):
            result = adapter.execute_query(None, "SELECT 1", "q1")
        assert result["status"] == "FAILED"
        assert result["error_type"] == "RuntimeError"

    def test_execute_exception_envelope(self, adapter):
        with patch.object(adapter, "_submit_spark_sql_batch", side_effect=RuntimeError("down")):
            result = adapter.execute_query(None, "SELECT 1", "q1")
        assert result["status"] == "FAILED"
        assert result["error"] == "down"


class TestNetworkOnlyEnvironment:
    def test_network_without_service_account_builds_env(self, adapter):
        adapter.network_uri = "net"
        adapter.service_account = None
        client = MagicMock()
        operation = MagicMock()
        operation.result.return_value = SimpleNamespace(state=SimpleNamespace(name="SUCCEEDED"))
        client.create_batch.return_value = operation
        adapter._batch_client = client
        with patch.object(adapter, "_upload_to_gcs"):
            adapter._submit_spark_sql_batch("SELECT 1")
        _, kwargs = client.create_batch.call_args
        env = kwargs["request"]["batch"]["environment_config"]["execution_config"]
        assert env == {"network_uri": "net"}


class TestStagingUploadPath:
    def test_load_uploads_when_tables_missing(self, adapter, tmp_path):
        source = tmp_path / "data"
        source.mkdir()
        staging = MagicMock()
        staging.tables_exist.return_value = False
        adapter._staging = staging
        with patch.object(adapter, "_submit_spark_sql_batch", return_value=("b-1", "SUCCEEDED")) as submit:
            counts, _, meta = adapter.load_data(_benchmark(), None, source)
        staging.upload_tables.assert_called_once()
        assert submit.call_count == 2
        assert set(counts) == {"lineitem", "orders"}
        assert set(meta["table_uris"]) == {"lineitem", "orders"}


class TestFromConfigTuning:
    def test_tuning_keys_pass_through(self):
        from benchbox.platforms.gcp import DataprocServerlessAdapter

        with (
            patch("benchbox.platforms.gcp.dataproc_serverless_adapter.GOOGLE_CLOUD_AVAILABLE", True),
            patch("benchbox.platforms.gcp.dataproc_serverless_adapter.dataproc_v1", MagicMock()),
            patch("benchbox.platforms.gcp.dataproc_serverless_adapter.storage", MagicMock()),
            patch("benchbox.platforms.gcp.dataproc_serverless_adapter.CloudSparkStaging"),
        ):
            adapter = DataprocServerlessAdapter.from_config(
                {
                    "project_id": "p",
                    "gcs_staging_dir": "gs://b/x",
                    "tuning_enabled": False,
                    "tuning_source": "cli",
                }
            )
        assert adapter.tuning_enabled is False
        assert adapter.tuning_source == "cli"


class TestImportFallback:
    def test_reload_with_google_cloud_present(self):

        import importlib
        import sys
        import types

        import benchbox.platforms.gcp.dataproc_serverless_adapter as mod

        pkg = types.ModuleType("google.cloud")
        pkg.__path__ = []
        dataproc_v1 = types.ModuleType("google.cloud.dataproc_v1")
        dataproc_v1.BatchControllerClient = MagicMock(name="BatchControllerClient")
        storage = types.ModuleType("google.cloud.storage")
        storage.Client = MagicMock(name="Client")
        staged = {
            "google.cloud": pkg,
            "google.cloud.dataproc_v1": dataproc_v1,
            "google.cloud.storage": storage,
        }
        saved = {k: sys.modules[k] for k in staged if k in sys.modules}
        sys.modules.update(staged)
        try:
            importlib.reload(mod)
            assert mod.GOOGLE_CLOUD_AVAILABLE is True
        finally:
            for k in staged:
                sys.modules.pop(k, None)
            sys.modules.update(saved)
            importlib.reload(mod)


@pytest.mark.parametrize("failure", [None, "query", "write"])
def test_uploaded_driver_runs_arguments_and_preserves_failure_order(monkeypatch, failure):
    import sys

    from benchbox.platforms.gcp import _dataproc_query

    database_query = 'USE quoted"database'
    query = "SELECT 'literal\\value'"
    results_path = 'gs://bucket/quoted"prefix/results/batch'
    result = MagicMock()
    spark = MagicMock()
    spark.sql.side_effect = [MagicMock(), RuntimeError("query failed") if failure == "query" else result]
    result.write.mode.return_value = result.write
    if failure == "write":
        result.write.json.side_effect = RuntimeError("write failed")
    builder = MagicMock()
    builder.appName.return_value = builder
    builder.enableHiveSupport.return_value = builder
    builder.getOrCreate.return_value = spark
    monkeypatch.setitem(sys.modules, "pyspark", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pyspark.sql", SimpleNamespace(SparkSession=SimpleNamespace(builder=builder)))
    monkeypatch.setattr(sys, "argv", ["driver.py", database_query, query, results_path])

    if failure is None:
        _dataproc_query.main()
        spark.stop.assert_called_once_with()
        result.write.mode.assert_called_once_with("overwrite")
        result.write.json.assert_called_once_with(results_path)
    else:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            _dataproc_query.main()
        spark.stop.assert_not_called()
        if failure == "query":
            result.write.mode.assert_not_called()
        else:
            result.write.json.assert_called_once_with(results_path)
    assert [args[0][0] for args in spark.sql.call_args_list] == [database_query, query]
    builder.appName.assert_called_once_with("BenchBox Query")
    builder.enableHiveSupport.assert_called_once_with()


