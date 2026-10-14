# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path

import pytest

from benchbox.utils.cloud_storage import (
    DatabricksPath,
    create_path_handler,
    format_cloud_usage_guide,
    get_cloud_path_info,
    is_databricks_path,
    validate_cloud_credentials,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDatabricksPathDetection:
    def test_is_databricks_path_valid_uc_volumes(self):
        assert is_databricks_path("dbfs:/Volumes/workspace/benchbox/data")
        assert is_databricks_path("dbfs:/Volumes/catalog/schema/volume")
        assert is_databricks_path("dbfs:/Volumes/catalog/schema/volume/subpath")

    def test_is_databricks_path_valid_dbfs(self):
        assert is_databricks_path("dbfs:/tmp/test")
        assert is_databricks_path("dbfs:/FileStore/data")
        assert is_databricks_path("dbfs://path/to/data")

    def test_is_databricks_path_invalid(self):
        assert not is_databricks_path("s3://bucket/path")
        assert not is_databricks_path("gs://bucket/path")
        assert not is_databricks_path("/local/path")
        assert not is_databricks_path("C:\\Windows\\path")
        assert not is_databricks_path("")
        assert not is_databricks_path(None)
        assert not is_databricks_path(123)

    def test_is_databricks_path_with_path_objects(self):
        assert not is_databricks_path(Path("/local/path"))


class TestDatabricksPathClass:
    def test_databricks_path_creation(self):
        local_path = "/tmp/test"
        dbfs_target = "dbfs:/Volumes/workspace/benchbox/data"

        db_path = DatabricksPath(local_path, dbfs_target)

        assert str(db_path) == str(Path(local_path))
        assert db_path.dbfs_target == dbfs_target

    def test_databricks_path_with_path_object(self):
        local_path = Path("/tmp/test")
        dbfs_target = "dbfs:/Volumes/workspace/benchbox/data"

        db_path = DatabricksPath(local_path, dbfs_target)

        assert str(db_path) == str(local_path)
        assert db_path.dbfs_target == dbfs_target

    def test_databricks_path_fspath_protocol(self):
        local_path = "/tmp/test"
        dbfs_target = "dbfs:/Volumes/workspace/benchbox/data"

        db_path = DatabricksPath(local_path, dbfs_target)

        import os

        fspath = os.fspath(db_path)
        assert fspath == str(Path(local_path))

    def test_databricks_path_delegation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dbfs_target = "dbfs:/Volumes/workspace/benchbox/data"
            db_path = DatabricksPath(temp_dir, dbfs_target)

            assert db_path.exists()
            assert db_path.is_dir()
            assert not db_path.is_file()
            assert db_path.name == Path(temp_dir).name

    def test_databricks_path_equality(self):
        local_path = "/tmp/test"
        dbfs_target1 = "dbfs:/Volumes/workspace/benchbox/data1"
        dbfs_target2 = "dbfs:/Volumes/workspace/benchbox/data2"

        db_path1 = DatabricksPath(local_path, dbfs_target1)
        db_path2 = DatabricksPath(local_path, dbfs_target1)
        db_path3 = DatabricksPath(local_path, dbfs_target2)

        assert db_path1 == db_path2

        assert db_path1 != db_path3

        assert db_path1 == local_path
        assert db_path1 == Path(local_path)

    def test_databricks_path_repr(self):
        local_path = "/tmp/test"
        dbfs_target = "dbfs:/Volumes/workspace/benchbox/data"

        db_path = DatabricksPath(local_path, dbfs_target)

        repr_str = repr(db_path)
        assert "DatabricksPath" in repr_str
        assert local_path in repr_str
        assert dbfs_target in repr_str

    def test_databricks_path_joining(self):
        local_path = "/tmp/test"
        dbfs_target = "dbfs:/Volumes/workspace/benchbox/data"

        db_path = DatabricksPath(local_path, dbfs_target)
        joined = db_path / "subpath"

        assert isinstance(joined, Path)
        assert str(joined) == str(Path(local_path) / "subpath")

    def test_databricks_path_hash_and_path_properties(self, tmp_path):
        local_path = tmp_path / "cache"
        db_path = DatabricksPath(local_path, "dbfs:/Volumes/workspace/benchbox/data")
        db_path.mkdir()
        file_path = local_path / "part.tbl"
        file_path.write_text("data")

        assert hash(db_path) == hash(local_path)
        assert db_path != object()
        assert list(db_path.iterdir()) == [file_path]
        assert list(db_path.glob("*.tbl")) == [file_path]
        assert db_path.parent == local_path.parent
        assert db_path.parts == local_path.parts
        assert db_path.as_posix() == local_path.as_posix()
        assert db_path.resolve() == local_path.resolve()


class TestDatabricksPathHandler:
    def test_create_path_handler_dbfs_valid_uc_volume(self):
        import shutil

        dbfs_path = "dbfs:/Volumes/workspace/benchbox/data"
        handler = create_path_handler(dbfs_path)

        try:
            assert isinstance(handler, DatabricksPath)

            assert handler.dbfs_target == dbfs_path

            assert handler.exists()
            assert handler.is_dir()
            assert "benchbox_dbfs_" in str(handler)

            assert "dbfs:" not in str(handler)

        finally:
            if handler.exists():
                shutil.rmtree(str(handler))

    def test_create_path_handler_dbfs_invalid_path(self):
        invalid_paths = [
            "dbfs:/tmp/data",
            "dbfs:/FileStore/data",
            "dbfs://data",
        ]

        for invalid_path in invalid_paths:
            with pytest.raises(ValueError, match="Invalid dbfs:// path"):
                create_path_handler(invalid_path)


class TestDatabricksCredentialValidation:
    def test_validate_cloud_credentials_dbfs(self):
        result = validate_cloud_credentials("dbfs:/Volumes/workspace/benchbox/data")

        assert result["valid"] is True
        assert result["provider"] == "dbfs"
        assert result["error"] is None
        assert "DATABRICKS_HOST" in result["env_vars"]
        assert "DATABRICKS_HTTP_PATH" not in result["env_vars"]
        assert "DATABRICKS_TOKEN" in result["env_vars"]


class TestDatabricksPathInfo:
    def test_get_cloud_path_info_dbfs_uc_volume(self):
        info = get_cloud_path_info("dbfs:/Volumes/workspace/benchbox/data")

        assert info["is_cloud"] is True
        assert info["provider"] == "dbfs"
        assert info["bucket"] is None
        assert info["path"] == "/Volumes/workspace/benchbox/data"
        assert info["credentials_valid"] is True

        volume_info = info["volume_info"]
        assert volume_info["catalog"] == "workspace"
        assert volume_info["schema"] == "benchbox"
        assert volume_info["volume"] == "data"

    def test_get_cloud_path_info_dbfs_with_subpath(self):
        info = get_cloud_path_info("dbfs:/Volumes/workspace/benchbox/data/tpch/sf01")

        assert info["is_cloud"] is True
        assert info["provider"] == "dbfs"
        assert info["path"] == "/Volumes/workspace/benchbox/data/tpch/sf01"

        volume_info = info["volume_info"]
        assert volume_info["catalog"] == "workspace"
        assert volume_info["schema"] == "benchbox"
        assert volume_info["volume"] == "data"

    def test_get_cloud_path_info_dbfs_invalid_format(self):
        info = get_cloud_path_info("dbfs:/tmp/data")

        assert info["is_cloud"] is True
        assert info["provider"] == "dbfs"
        assert info["path"] == "/tmp/data"
        volume_info = info.get("volume_info", {})
        assert not volume_info or volume_info.get("catalog") is None


class TestDatabricksUsageGuide:
    def test_format_cloud_usage_guide_dbfs(self):
        guide = format_cloud_usage_guide("dbfs")

        assert "Databricks DBFS" in guide or "Unity Catalog Volumes" in guide
        assert "DATABRICKS_HOST" in guide
        assert "DATABRICKS_HTTP_PATH" not in guide
        assert "DATABRICKS_TOKEN" in guide
        assert "dbfs:/Volumes/" in guide
        assert "databricks-sdk" in guide
