# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.utils.cloud_storage import (
    CloudPathAdapter,
    CloudStagingPath,
    CloudStorageGeneratorMixin,
    DatabricksPath,
    DatabricksVolumeAdapter,
    create_path_handler,
    ensure_cloud_directory,
    get_cloud_path_info,
    get_remote_fs_adapter,
    is_cloud_path,
    validate_cloud_credentials,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCloudPathAdapter:
    def test_cloud_path_adapter_local(self):
        adapter = CloudPathAdapter("/tmp/test")
        assert not adapter.is_cloud
        assert adapter.path_info["is_cloud"] is False
        assert adapter.path_info["provider"] == "local"

    def test_cloud_path_adapter_cloud_without_lib(self):
        with patch("benchbox.utils.cloud_storage.CloudPath", None), pytest.raises(ImportError):
            CloudPathAdapter("s3://bucket/path")

    def test_cloud_path_adapter_str_representation(self):
        adapter = CloudPathAdapter("/tmp/test")
        assert str(Path("/tmp/test")) in str(adapter)

    def test_cloud_path_adapter_path_joining(self):
        adapter = CloudPathAdapter("/tmp/test")
        new_adapter = adapter / "subpath"
        assert isinstance(new_adapter, CloudPathAdapter)
        assert "subpath" in str(new_adapter)

    def test_cloud_path_adapter_handles_exists_errors(self):
        adapter = CloudPathAdapter("/tmp/path")
        adapter.path_handler = Mock()
        adapter.path_handler.exists.side_effect = OSError("boom")

        assert adapter.exists() is False

    def test_cloud_path_adapter_cloud_join_mkdir_name_and_parent(self):
        path_handler = MagicMock()
        path_handler.__truediv__.return_value = "s3://bucket/base/child"
        path_handler.name = "base"
        path_handler.parent = "s3://bucket"
        joined_handler = MagicMock()
        parent_handler = MagicMock()

        with (
            patch("benchbox.utils.cloud_storage.is_cloud_path", return_value=True),
            patch(
                "benchbox.utils.cloud_storage.create_path_handler",
                side_effect=[path_handler, joined_handler, parent_handler],
            ),
            patch(
                "benchbox.utils.cloud_storage.get_cloud_path_info",
                return_value={"is_cloud": True, "provider": "s3"},
            ),
        ):
            adapter = CloudPathAdapter("s3://bucket/base")
            adapter.mkdir(parents=False, exist_ok=False)
            joined = adapter / "child"
            parent = adapter.parent

        path_handler.mkdir.assert_called_once_with(parents=False, exist_ok=False)
        assert adapter.path_info["provider"] == "s3"
        assert adapter.name == "base"
        assert isinstance(joined, CloudPathAdapter)
        assert isinstance(parent, CloudPathAdapter)
        assert joined.path_handler is joined_handler
        assert parent.path_handler is parent_handler


class TestCloudStagingPath:
    def test_initialization_with_str_paths(self):
        local_path = "/tmp/data"
        cloud_target = "gs://my-bucket/data"

        staging_path = CloudStagingPath(local_path, cloud_target)

        assert staging_path._path == Path(local_path)
        assert staging_path._cloud_target == cloud_target

    def test_initialization_with_path_objects(self):
        local_path = Path("/tmp/data")
        cloud_target = "s3://my-bucket/data"

        staging_path = CloudStagingPath(local_path, cloud_target)

        assert staging_path._path == local_path
        assert staging_path._cloud_target == cloud_target

    def test_fspath_protocol(self):
        local_path = "/tmp/benchmark_data"
        staging_path = CloudStagingPath(local_path, "gs://bucket/path")

        assert os.fspath(staging_path) == str(Path(local_path))

    def test_str_returns_local_path(self):
        local_path = "/tmp/data"
        staging_path = CloudStagingPath(local_path, "gs://bucket/data")

        assert str(staging_path) == str(Path(local_path))

    def test_repr_shows_both_paths(self):
        local_path = "/tmp/data"
        cloud_target = "gs://bucket/data"
        staging_path = CloudStagingPath(local_path, cloud_target)

        repr_str = repr(staging_path)
        assert "CloudStagingPath" in repr_str
        assert "/tmp/data" in repr_str
        assert "gs://bucket/data" in repr_str

    def test_cloud_target_property(self):
        cloud_target = "s3://my-bucket/benchmark-data"
        staging_path = CloudStagingPath("/local/path", cloud_target)

        assert staging_path.cloud_target == cloud_target

    def test_path_joining_with_truediv(self):
        staging_path = CloudStagingPath("/tmp/data", "gs://bucket/data")

        joined = staging_path / "subdir" / "file.txt"

        assert isinstance(joined, Path)
        assert joined == Path("/tmp/data/subdir/file.txt")

    def test_equality_comparison(self):
        path1 = CloudStagingPath("/tmp/data", "gs://bucket/data")
        path2 = CloudStagingPath("/tmp/data", "gs://bucket/data")
        path3 = CloudStagingPath("/tmp/other", "gs://bucket/data")
        path4 = CloudStagingPath("/tmp/data", "gs://other-bucket/data")

        assert path1 == path2
        assert path1 != path3
        assert path1 != path4

    def test_equality_with_path_objects(self):
        staging_path = CloudStagingPath("/tmp/data", "gs://bucket/data")

        assert staging_path == Path("/tmp/data")
        assert staging_path == "/tmp/data"
        assert staging_path != Path("/tmp/other")

    def test_hash_based_on_local_path(self):
        path1 = CloudStagingPath("/tmp/data", "gs://bucket/data")
        path2 = CloudStagingPath("/tmp/data", "gs://bucket/data")
        path3 = CloudStagingPath("/tmp/data", "gs://other-bucket/data")

        assert hash(path1) == hash(path2) == hash(path3)

        path_set = {path1, path2}
        assert len(path_set) == 1

        path_set2 = {path1, path3}
        assert len(path_set2) == 2

    def test_path_operations_delegate_to_local(self, tmp_path):
        staging_path = CloudStagingPath(str(tmp_path), "gs://bucket/data")

        staging_path.mkdir(parents=True, exist_ok=True)
        assert tmp_path.exists()

        assert staging_path.is_dir()

        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        files = list(staging_path.iterdir())
        assert len(files) == 1

        matches = list(staging_path.glob("*.txt"))
        assert len(matches) == 1

    def test_path_properties(self, tmp_path):
        local_path = tmp_path / "subdir" / "file.txt"
        staging_path = CloudStagingPath(str(local_path), "s3://bucket/data")

        assert staging_path.name == "file.txt"
        assert staging_path.parent == local_path.parent
        assert staging_path.parts == local_path.parts

    def test_as_posix_returns_local_posix(self):
        staging_path = CloudStagingPath("/tmp/data/file.txt", "gs://bucket/data")

        assert staging_path.as_posix() == "/tmp/data/file.txt"

    def test_resolve_returns_absolute_local_path(self):
        staging_path = CloudStagingPath("relative/path", "s3://bucket/data")

        resolved = staging_path.resolve()
        assert resolved.is_absolute()

    def test_with_different_cloud_providers(self):
        gcs_path = CloudStagingPath("/tmp/data", "gs://gcs-bucket/path")
        s3_path = CloudStagingPath("/tmp/data", "s3://s3-bucket/path")
        azure_path = CloudStagingPath("/tmp/data", "abfss://container@account.dfs.core.windows.net/path")

        assert gcs_path.cloud_target.startswith("gs://")
        assert s3_path.cloud_target.startswith("s3://")
        assert azure_path.cloud_target.startswith("abfss://")

        assert os.fspath(gcs_path) == os.fspath(s3_path) == os.fspath(azure_path)

    def test_inequality_with_unrelated_objects(self):
        staging_path = CloudStagingPath("/tmp/data", "gs://bucket/data")

        assert staging_path != object()


class TestDatabricksVolumeAdapter:
    def _sdk_modules(self, workspace_client_cls):
        databricks_module = ModuleType("databricks")
        sdk_module = ModuleType("databricks.sdk")
        sdk_module.WorkspaceClient = workspace_client_cls
        databricks_module.sdk = sdk_module
        return {"databricks": databricks_module, "databricks.sdk": sdk_module}

    def test_uses_provided_workspace_client(self):
        workspace_client_cls = Mock()
        workspace_client = Mock()

        with patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)):
            adapter = DatabricksVolumeAdapter(workspace_client=workspace_client)

        assert adapter._ws is workspace_client
        workspace_client_cls.assert_not_called()

    def test_constructs_workspace_client_with_host_and_token(self):
        workspace_client = Mock()
        workspace_client_cls = Mock(return_value=workspace_client)

        with patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)):
            adapter = DatabricksVolumeAdapter(host="workspace.cloud.databricks.com", token="tok")

        assert adapter._ws is workspace_client
        workspace_client_cls.assert_called_once_with(host="https://workspace.cloud.databricks.com", token="tok")

    def test_file_exists_returns_true_when_sdk_finds_file(self):
        workspace_client = Mock()
        workspace_client.files.get.return_value = {"path": "/Volumes/catalog/schema/volume/file.txt"}
        workspace_client_cls = Mock()

        with patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)):
            adapter = DatabricksVolumeAdapter(workspace_client=workspace_client)

        assert adapter.file_exists("dbfs:/Volumes/catalog/schema/volume/file.txt") is True
        workspace_client.files.get.assert_called_once_with("/Volumes/catalog/schema/volume/file.txt")

    def test_file_exists_returns_false_on_sdk_errors(self):
        workspace_client = Mock()
        workspace_client.files.get.side_effect = RuntimeError("missing")
        workspace_client_cls = Mock()

        with patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)):
            adapter = DatabricksVolumeAdapter(workspace_client=workspace_client)

        assert adapter.file_exists("dbfs:/Volumes/catalog/schema/volume/file.txt") is False

    def test_read_file_supports_bytes_and_stream_results(self):
        workspace_client = Mock()
        workspace_client.files.download.side_effect = [b"raw", MagicMock(read=Mock(return_value=b"stream"))]
        workspace_client_cls = Mock()

        with patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)):
            adapter = DatabricksVolumeAdapter(workspace_client=workspace_client)

        assert adapter.read_file("dbfs:/Volumes/catalog/schema/volume/raw.txt") == b"raw"
        assert adapter.read_file("dbfs:/Volumes/catalog/schema/volume/stream.txt") == b"stream"

    def test_read_file_wraps_sdk_errors(self):
        workspace_client = Mock()
        workspace_client.files.download.side_effect = RuntimeError("boom")
        workspace_client_cls = Mock()

        with patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)):
            adapter = DatabricksVolumeAdapter(workspace_client=workspace_client)

        with pytest.raises(RuntimeError, match="Failed to read remote file"):
            adapter.read_file("dbfs:/Volumes/catalog/schema/volume/file.txt")

    def test_write_file_uploads_bytes_and_wraps_errors(self):
        workspace_client = Mock()
        workspace_client_cls = Mock()

        with patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)):
            adapter = DatabricksVolumeAdapter(workspace_client=workspace_client)

        adapter.write_file("dbfs:/Volumes/catalog/schema/volume/file.txt", b"payload")

        call_args = workspace_client.files.upload.call_args
        assert call_args.args[0] == "/Volumes/catalog/schema/volume/file.txt"
        assert call_args.args[1].getvalue() == b"payload"
        assert call_args.kwargs["overwrite"] is True

        workspace_client.files.upload.side_effect = RuntimeError("nope")
        with pytest.raises(RuntimeError, match="Failed to write remote file"):
            adapter.write_file("dbfs:/Volumes/catalog/schema/volume/file.txt", b"payload")

    def test_list_files_filters_names_and_returns_empty_on_errors(self):
        workspace_client = Mock()
        workspace_client.files.list.return_value = [
            SimpleNamespace(path="/Volumes/catalog/schema/volume/part-000.tbl"),
            SimpleNamespace(file_path="/Volumes/catalog/schema/volume/part-001.txt"),
            "/Volumes/catalog/schema/volume/part-002.tbl",
        ]
        workspace_client_cls = Mock()

        with patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)):
            adapter = DatabricksVolumeAdapter(workspace_client=workspace_client)

        matches = adapter.list_files("dbfs:/Volumes/catalog/schema/volume", pattern="*.tbl")
        assert matches == [
            "/Volumes/catalog/schema/volume/part-000.tbl",
            "/Volumes/catalog/schema/volume/part-002.tbl",
        ]

        workspace_client.files.list.side_effect = RuntimeError("list failed")
        assert adapter.list_files("dbfs:/Volumes/catalog/schema/volume", pattern="*.tbl") == []


class TestCloudStorageEdgeCases:
    def _sdk_modules(self, workspace_client_cls):
        databricks_module = ModuleType("databricks")
        sdk_module = ModuleType("databricks.sdk")
        sdk_module.WorkspaceClient = workspace_client_cls
        databricks_module.sdk = sdk_module
        return {"databricks": databricks_module, "databricks.sdk": sdk_module}

    def test_create_path_handler_passthroughs_existing_specialized_paths(self, tmp_path):
        databricks_path = DatabricksPath(tmp_path, "dbfs:/Volumes/catalog/schema/volume")
        assert create_path_handler(databricks_path) is databricks_path

        class FakeCloudPath:
            pass

        fake_cloud_path = FakeCloudPath()
        with patch("benchbox.utils.cloud_storage.CloudPath", FakeCloudPath):
            assert create_path_handler(fake_cloud_path) is fake_cloud_path

    def test_get_remote_fs_adapter_returns_databricks_adapter(self):
        workspace_client = Mock()
        workspace_client_cls = Mock(return_value=workspace_client)

        with (
            patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)),
            patch("benchbox.utils.cloud_storage.DatabricksVolumeAdapter", return_value="adapter") as adapter_cls,
        ):
            result = get_remote_fs_adapter("dbfs:/Volumes/catalog/schema/volume")

        assert result == "adapter"
        workspace_client_cls.assert_called_once_with()
        adapter_cls.assert_called_once_with(workspace_client)

    def test_get_remote_fs_adapter_wraps_import_errors(self):
        workspace_client_cls = Mock(return_value=Mock())

        with (
            patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)),
            patch("benchbox.utils.cloud_storage.DatabricksVolumeAdapter", side_effect=ImportError("missing")),
            pytest.raises(ImportError, match="databricks-sdk"),
        ):
            get_remote_fs_adapter("dbfs:/Volumes/catalog/schema/volume")

    def test_get_remote_fs_adapter_wraps_runtime_errors(self):
        workspace_client_cls = Mock(return_value=Mock())

        with (
            patch.dict("sys.modules", self._sdk_modules(workspace_client_cls)),
            patch("benchbox.utils.cloud_storage.DatabricksVolumeAdapter", side_effect=RuntimeError("bad auth")),
            pytest.raises(RuntimeError, match="Ensure DATABRICKS_HOST and DATABRICKS_TOKEN"),
        ):
            get_remote_fs_adapter("dbfs:/Volumes/catalog/schema/volume")

    def test_get_remote_fs_adapter_rejects_unsupported_paths(self):
        with pytest.raises(ValueError, match="No RemoteFileSystemAdapter available"):
            get_remote_fs_adapter("s3://bucket/path")

    @patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/key.json"}, clear=True)
    def test_validate_cloud_credentials_handles_missing_credentials_error(self):

        class FakeMissingCredentialsError(Exception):
            pass

        with (
            patch("benchbox.utils.cloud_storage.MissingCredentialsError", FakeMissingCredentialsError),
            patch("benchbox.utils.cloud_storage.CloudPath", side_effect=FakeMissingCredentialsError("denied")),
        ):
            result = validate_cloud_credentials("gs://bucket/path")

        assert result["valid"] is False
        assert result["provider"] == "gs"
        assert "Credential validation failed: denied" in result["error"]

    @patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/key.json"}, clear=True)
    def test_validate_cloud_credentials_handles_generic_cloudpath_errors(self):
        with patch("benchbox.utils.cloud_storage.CloudPath", side_effect=RuntimeError("network down")):
            result = validate_cloud_credentials("gs://bucket/path")

        assert result["valid"] is False
        assert result["provider"] == "gs"
        assert "Cloud path validation failed: network down" in result["error"]

    def test_ensure_cloud_directory_uses_exists_only_handlers(self):
        handler = Mock()
        del handler.mkdir
        handler.exists.return_value = False

        with patch("benchbox.utils.cloud_storage.logger.info") as info_log:
            result = ensure_cloud_directory(handler)

        assert result is handler
        handler.exists.assert_called_once_with()
        info_log.assert_called_once()

    def test_ensure_cloud_directory_reraises_creation_errors(self):
        handler = Mock()
        handler.mkdir.side_effect = OSError("permission denied")

        with (
            patch("benchbox.utils.cloud_storage.logger.error") as error_log,
            pytest.raises(OSError, match="permission denied"),
        ):
            ensure_cloud_directory(handler)

        error_log.assert_called_once()


class DummyCloudGenerator(CloudStorageGeneratorMixin):
    pass


class TestCloudStorageGeneratorMixin:
    def test_is_cloud_output_distinguishes_uri_strings(self):
        generator = DummyCloudGenerator()

        assert generator._is_cloud_output("s3://bucket/path") is True
        assert generator._is_cloud_output("/tmp/local") is False

    def test_handle_cloud_or_local_generation_delegates_to_generator(self):
        generator = DummyCloudGenerator()
        output_dir = CloudStagingPath("/tmp/cache", "gs://bucket/path")
        generate = Mock(return_value={"orders": "/tmp/cache/orders.tbl"})

        result = generator._handle_cloud_or_local_generation(output_dir, generate, verbose=True)

        assert result == {"orders": "/tmp/cache/orders.tbl"}
        generate.assert_called_once_with(output_dir)


