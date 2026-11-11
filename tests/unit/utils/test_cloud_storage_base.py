# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.utils.cloud_storage import (
    create_path_handler,
    format_cloud_usage_guide,
    get_cloud_path_info,
    is_cloud_path,
    validate_cloud_credentials,
    validate_cloud_path_support,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCloudPathDetection:
    def test_is_cloud_path_s3(self):
        assert is_cloud_path("s3://bucket/path")
        assert is_cloud_path("s3://bucket/path/file.txt")
        assert not is_cloud_path("/local/path")
        assert not is_cloud_path("C:\\Windows\\path")
        assert not is_cloud_path("")

    def test_is_cloud_path_gcs(self):
        assert is_cloud_path("gs://bucket/path")
        assert is_cloud_path("gcs://bucket/path")
        assert is_cloud_path("gs://bucket/path/file.txt")

    def test_is_cloud_path_azure(self):
        assert is_cloud_path("abfss://container@account.dfs.core.windows.net/path")
        assert is_cloud_path("azure://container@account/path")

    def test_is_cloud_path_path_objects(self):
        assert not is_cloud_path(Path("/local/path"))
        assert not is_cloud_path(Path.cwd())

    def test_is_cloud_path_invalid_input(self):
        assert not is_cloud_path(None)
        assert not is_cloud_path(123)
        assert not is_cloud_path([])

    def test_is_cloud_path_dbfs(self):
        assert is_cloud_path("dbfs:/Volumes/workspace/benchbox/data")
        assert is_cloud_path("dbfs:/tmp/test")
        assert is_cloud_path("dbfs://path/to/data")


class TestCloudPathSupport:
    @patch("benchbox.utils.cloud_storage.CloudPath", None)
    def test_validate_cloud_path_support_missing(self):
        assert not validate_cloud_path_support()

    def test_validate_cloud_path_support_available(self):
        try:
            import cloudpathlib

            assert validate_cloud_path_support()
        except ImportError:
            pytest.skip("cloudpathlib not available in test environment")


class TestPathHandlerCreation:
    def test_create_path_handler_local(self):
        local_path = "/tmp/local"
        handler = create_path_handler(local_path)
        assert isinstance(handler, Path)
        assert handler == Path(local_path)

    def test_create_path_handler_local_path_object(self):
        local_path = Path("/tmp/local")
        handler = create_path_handler(local_path)
        assert isinstance(handler, Path)
        assert handler == local_path

    @patch("benchbox.utils.cloud_storage.CloudPath", None)
    def test_create_path_handler_cloud_missing_lib(self):
        with pytest.raises(ImportError, match="cloudpathlib is required"):
            create_path_handler("s3://bucket/path")

    @patch("benchbox.utils.cloud_storage.CloudPath")
    def test_create_path_handler_cloud_success(self, mock_cloud_path):
        mock_instance = MagicMock()
        mock_cloud_path.return_value = mock_instance

        result = create_path_handler("s3://bucket/path")

        mock_cloud_path.assert_called_once_with("s3://bucket/path")
        assert result == mock_instance

    @patch("benchbox.utils.cloud_storage.CloudPath")
    def test_create_path_handler_cloud_invalid_format(self, mock_cloud_path):
        mock_cloud_path.side_effect = Exception("Invalid format")

        with pytest.raises(ValueError, match="Invalid cloud path format"):
            create_path_handler("s3://invalid-format")


class TestCredentialValidation:
    def test_validate_cloud_credentials_local_path(self):
        result = validate_cloud_credentials("/local/path")
        assert result["valid"] is True
        assert result["provider"] == "local"
        assert result["error"] is None

    @patch("benchbox.utils.cloud_storage.CloudPath", None)
    def test_validate_cloud_credentials_missing_lib(self):
        result = validate_cloud_credentials("s3://bucket/path")
        assert result["valid"] is False
        assert result["provider"] == "unknown"
        assert "cloudpathlib not installed" in result["error"]

    @patch.dict(os.environ, {}, clear=True)
    @patch("os.path.expanduser", return_value="/nonexistent/path")
    def test_validate_cloud_credentials_s3_missing_vars(self, mock_expanduser):
        result = validate_cloud_credentials("s3://bucket/path")
        assert result["valid"] is False
        assert result["provider"] in ["s3", "unknown"]
        if result["provider"] == "s3":
            assert "No AWS credentials found" in result["error"]
            assert result["env_vars"] == ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
        else:
            assert "cloudpathlib not installed" in result["error"]

    @patch.dict(
        os.environ,
        {"AWS_ACCESS_KEY_ID": "test-key", "AWS_SECRET_ACCESS_KEY": "test-secret"},
        clear=True,
    )
    @patch("benchbox.utils.cloud_storage.CloudPath")
    def test_validate_cloud_credentials_s3_success(self, mock_cloud_path):
        mock_instance = MagicMock()
        mock_instance.exists.return_value = True
        mock_cloud_path.return_value = mock_instance

        result = validate_cloud_credentials("s3://bucket/path")
        assert result["valid"] is True
        assert result["provider"] == "s3"
        assert result["error"] is None

    @patch.dict(os.environ, {}, clear=True)
    def test_validate_cloud_credentials_gcs_missing_vars(self):
        result = validate_cloud_credentials("gs://bucket/path")
        assert result["valid"] is False
        assert result["provider"] in ["gs", "unknown"]
        if result["provider"] == "gs":
            assert "Missing environment variables" in result["error"]
            assert "GOOGLE_APPLICATION_CREDENTIALS" in result["error"]
        else:
            assert "cloudpathlib not installed" in result["error"]

    @patch.dict(os.environ, {}, clear=True)
    def test_validate_cloud_credentials_azure_missing_vars(self):
        result = validate_cloud_credentials("abfss://container@account.dfs.core.windows.net/path")
        assert result["valid"] is False
        assert result["provider"] in ["abfss", "unknown"]
        if result["provider"] == "abfss":
            assert "Missing environment variables" in result["error"]
            assert "AZURE_STORAGE_ACCOUNT_NAME" in result["error"]
        else:
            assert "cloudpathlib not installed" in result["error"]


class TestPathInfo:
    def test_get_cloud_path_info_local(self):
        info = get_cloud_path_info("/local/path")
        assert info["is_cloud"] is False
        assert info["provider"] == "local"
        assert info["bucket"] is None
        assert info["path"] == "/local/path"
        assert info["credentials_valid"] is True

    def test_get_cloud_path_info_s3(self):
        info = get_cloud_path_info("s3://my-bucket/path/to/data")
        assert info["is_cloud"] is True
        assert info["provider"] == "s3"
        assert info["bucket"] == "my-bucket"
        assert info["path"] == "path/to/data"

    def test_get_cloud_path_info_gcs(self):
        info = get_cloud_path_info("gs://my-bucket/path/to/data")
        assert info["is_cloud"] is True
        assert info["provider"] == "gs"
        assert info["bucket"] == "my-bucket"
        assert info["path"] == "path/to/data"

    def test_get_cloud_path_info_azure(self):
        info = get_cloud_path_info("abfss://container@account.dfs.core.windows.net/path/to/data")
        assert info["is_cloud"] is True
        assert info["provider"] == "abfss"
        assert info["bucket"] == "container"
        assert info["account"] == "account"
        assert info["path"] == "path/to/data"


class TestUsageGuides:
    def test_format_cloud_usage_guide_s3(self):
        guide = format_cloud_usage_guide("s3")
        assert "AWS S3 Setup:" in guide
        assert "AWS_ACCESS_KEY_ID" in guide
        assert "AWS_SECRET_ACCESS_KEY" in guide
        assert "s3://your-bucket" in guide

    def test_format_cloud_usage_guide_gs(self):
        guide = format_cloud_usage_guide("gs")
        assert "Google Cloud Storage Setup:" in guide
        assert "GOOGLE_APPLICATION_CREDENTIALS" in guide
        assert "gs://your-bucket" in guide

    def test_format_cloud_usage_guide_azure(self):
        guide = format_cloud_usage_guide("azure")
        assert "Azure Blob Storage Setup:" in guide
        assert "AZURE_STORAGE_ACCOUNT_NAME" in guide
        assert "AZURE_STORAGE_ACCOUNT_KEY" in guide
        assert "abfss://" in guide

    def test_format_cloud_usage_guide_unknown(self):
        guide = format_cloud_usage_guide("unknown")
        assert "No setup guide available" in guide


class TestCloudPathIntegration:
    @pytest.mark.skipif(not validate_cloud_path_support(), reason="cloudpathlib not available")
    def test_cloud_path_creation_integration(self):
        try:
            handler = create_path_handler("s3://test-bucket/test-path")
            assert handler is not None
            assert hasattr(handler, "exists")
        except Exception as e:
            pytest.skip(f"CloudPath creation failed: {e}")


