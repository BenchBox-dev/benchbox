# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.base.cloud_spark.staging import (
    AzureADLSStaging,
    AzureBlobStaging,
    CloudProvider,
    CloudSparkStaging,
    DBFSStaging,
    GCSStaging,
    LocalStaging,
    S3Staging,
    StagingConfig,
    UploadProgress,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCloudProvider:
    def test_provider_values(self):

        assert CloudProvider.AWS_S3.value == "s3"
        assert CloudProvider.GCS.value == "gs"
        assert CloudProvider.AZURE_ADLS.value == "abfss"
        assert CloudProvider.DBFS.value == "dbfs"
        assert CloudProvider.LOCAL.value == "file"


class TestUploadProgress:
    def test_percent_complete(self):

        progress = UploadProgress(
            table_name="lineitem",
            file_name="lineitem.parquet",
            bytes_uploaded=50,
            total_bytes=100,
            files_completed=1,
            total_files=5,
        )
        assert progress.percent_complete == 50.0

    def test_percent_complete_zero_total(self):

        progress = UploadProgress(
            table_name="empty",
            file_name="empty.parquet",
            bytes_uploaded=0,
            total_bytes=0,
            files_completed=0,
            total_files=0,
        )
        assert progress.percent_complete == 100.0


class TestCloudSparkStagingFromUri:
    def test_from_uri_s3(self):
        staging = CloudSparkStaging.from_uri("s3://my-bucket/path/to/data")

        assert isinstance(staging, S3Staging)
        assert staging.config.provider == CloudProvider.AWS_S3
        assert staging.config.bucket == "my-bucket"
        assert staging.config.prefix == "path/to/data"

    def test_from_uri_s3a(self):
        staging = CloudSparkStaging.from_uri("s3a://my-bucket/data")

        assert isinstance(staging, S3Staging)
        assert staging.config.provider == CloudProvider.AWS_S3

    def test_from_uri_gcs(self):

        result = CloudSparkStaging.from_uri("gs://my-bucket/data")
        assert isinstance(result, GCSStaging)
        assert result.config.provider == CloudProvider.GCS
        assert result.config.bucket == "my-bucket"

    def test_from_uri_azure_adls(self):

        uri = "abfss://container@account.dfs.core.windows.net/path"
        result = CloudSparkStaging.from_uri(uri)
        assert isinstance(result, AzureADLSStaging)
        assert result.config.provider == CloudProvider.AZURE_ADLS
        assert "container@account" in result.config.bucket

    def test_from_uri_dbfs(self):

        result = CloudSparkStaging.from_uri("dbfs:/Volumes/catalog/schema/volume/data")
        assert isinstance(result, DBFSStaging)
        assert result.config.provider == CloudProvider.DBFS
        assert "Volumes" in result.config.prefix

    def test_from_uri_local(self):

        staging = CloudSparkStaging.from_uri("file:///tmp/data")

        assert isinstance(staging, LocalStaging)
        assert staging.config.provider == CloudProvider.LOCAL

    def test_from_uri_unsupported_scheme(self):

        with pytest.raises(ValueError, match="Unsupported URI scheme"):
            CloudSparkStaging.from_uri("hdfs://cluster/data")


class TestLocalStaging:
    def test_upload_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            staging_dir = Path(tmpdir) / "staging"
            source_dir.mkdir()

            test_file = source_dir / "test.parquet"
            test_file.write_text("test data")

            config = StagingConfig(
                uri=f"file://{staging_dir}",
                provider=CloudProvider.LOCAL,
                bucket="",
                prefix=str(staging_dir),
            )
            staging = LocalStaging(config)

            uri = staging.upload_file(test_file, "table/test.parquet")

            assert "test.parquet" in uri
            assert (staging_dir / "table" / "test.parquet").exists()

    def test_file_exists(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            staging_dir = Path(tmpdir)
            (staging_dir / "existing.txt").write_text("data")

            config = StagingConfig(
                uri=f"file://{staging_dir}",
                provider=CloudProvider.LOCAL,
                bucket="",
                prefix=str(staging_dir),
            )
            staging = LocalStaging(config)

            assert staging.file_exists("existing.txt")
            assert not staging.file_exists("nonexistent.txt")

    def test_list_files(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            staging_dir = Path(tmpdir)
            table_dir = staging_dir / "lineitem"
            table_dir.mkdir()

            (table_dir / "part1.parquet").write_text("data1")
            (table_dir / "part2.parquet").write_text("data2")

            config = StagingConfig(
                uri=f"file://{staging_dir}",
                provider=CloudProvider.LOCAL,
                bucket="",
                prefix=str(staging_dir),
            )
            staging = LocalStaging(config)

            files = staging.list_files("lineitem/")
            assert len(files) == 2
            assert any("part1.parquet" in f for f in files)

    def test_delete_path(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            staging_dir = Path(tmpdir)
            test_file = staging_dir / "to_delete.txt"
            test_file.write_text("delete me")

            config = StagingConfig(
                uri=f"file://{staging_dir}",
                provider=CloudProvider.LOCAL,
                bucket="",
                prefix=str(staging_dir),
            )
            staging = LocalStaging(config)

            assert test_file.exists()
            staging.delete_path("to_delete.txt")
            assert not test_file.exists()

    def test_upload_tables(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            staging_dir = Path(tmpdir) / "staging"
            source_dir.mkdir()

            (source_dir / "lineitem.parquet").write_text("lineitem data")
            (source_dir / "orders.parquet").write_text("orders data")

            config = StagingConfig(
                uri=f"file://{staging_dir}",
                provider=CloudProvider.LOCAL,
                bucket="",
                prefix=str(staging_dir),
            )
            staging = LocalStaging(config)

            uploaded = staging.upload_tables(
                tables=["lineitem", "orders"],
                source_dir=source_dir,
                file_format="parquet",
            )

            assert "lineitem" in uploaded
            assert "orders" in uploaded
            assert (staging_dir / "lineitem").exists()
            assert (staging_dir / "orders").exists()

    def test_upload_data_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            staging_dir = Path(tmpdir) / "staging"
            source_dir.mkdir()

            lineitem = source_dir / "lineitem.parquet"
            orders_part1 = source_dir / "orders.parquet.1"
            orders_part2 = source_dir / "orders.parquet.2"
            lineitem.write_text("lineitem data")
            orders_part1.write_text("orders part 1")
            orders_part2.write_text("orders part 2")

            config = StagingConfig(
                uri=f"file://{staging_dir}",
                provider=CloudProvider.LOCAL,
                bucket="",
                prefix=str(staging_dir),
            )
            staging = LocalStaging(config)

            uploaded = staging.upload_data_files(
                {
                    "lineitem": lineitem,
                    "orders": [orders_part1, orders_part2],
                }
            )

            assert uploaded["lineitem"].endswith("/lineitem/")
            assert uploaded["orders"].endswith("/orders/")
            assert (staging_dir / "lineitem" / "lineitem.parquet").exists()
            assert (staging_dir / "orders" / "orders.parquet.1").exists()
            assert (staging_dir / "orders" / "orders.parquet.2").exists()

    def test_upload_data_files_preserves_nested_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            staging_dir = Path(tmpdir) / "staging"
            asia_dir = source_dir / "orders" / "region=ASIA"
            europe_dir = source_dir / "orders" / "region=EUROPE"
            asia_dir.mkdir(parents=True)
            europe_dir.mkdir(parents=True)

            asia_part = asia_dir / "part-00000.parquet"
            europe_part = europe_dir / "part-00000.parquet"
            asia_part.write_text("asia orders")
            europe_part.write_text("europe orders")

            config = StagingConfig(
                uri=f"file://{staging_dir}",
                provider=CloudProvider.LOCAL,
                bucket="",
                prefix=str(staging_dir),
            )
            staging = LocalStaging(config)

            staging.upload_data_files({"orders": [asia_part, europe_part]})

            assert (staging_dir / "orders" / "region=ASIA" / "part-00000.parquet").read_text() == "asia orders"
            assert (staging_dir / "orders" / "region=EUROPE" / "part-00000.parquet").read_text() == "europe orders"

    def test_tables_exist(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            staging_dir = Path(tmpdir)

            (staging_dir / "lineitem").mkdir()
            (staging_dir / "lineitem" / "data.parquet").write_text("data")

            config = StagingConfig(
                uri=f"file://{staging_dir}",
                provider=CloudProvider.LOCAL,
                bucket="",
                prefix=str(staging_dir),
            )
            staging = LocalStaging(config)

            assert staging.tables_exist(["lineitem"])
            assert not staging.tables_exist(["lineitem", "orders"])

    def test_fingerprint_gates_reuse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            staging_dir = Path(tmpdir) / "staging"
            source_dir.mkdir()
            (source_dir / "lineitem.parquet").write_text("lineitem data")

            config = StagingConfig(
                uri=f"file://{staging_dir}",
                provider=CloudProvider.LOCAL,
                bucket="",
                prefix=str(staging_dir),
            )
            staging = LocalStaging(config)

            uploaded = staging.upload_tables(
                tables=["lineitem"],
                source_dir=source_dir,
                file_format="parquet",
                fingerprint="abc123",
            )
            assert "lineitem" in uploaded

            assert staging.tables_exist(["lineitem"], "parquet", "abc123")
            assert not staging.tables_exist(["lineitem"], "parquet", "other-fp")
            assert staging.tables_exist(["lineitem"])

    def test_get_table_uri(self):

        config = StagingConfig(
            uri="file:///tmp/staging",
            provider=CloudProvider.LOCAL,
            bucket="",
            prefix="/tmp/staging",
        )
        staging = LocalStaging(config)

        uri = staging.get_table_uri("lineitem")
        assert uri == "file:///tmp/staging/lineitem/"


class TestS3Staging:
    def test_s3_upload_file(self):
        config = StagingConfig(
            uri="s3://my-bucket/data",
            provider=CloudProvider.AWS_S3,
            bucket="my-bucket",
            prefix="data",
        )
        staging = S3Staging(config)

        mock_client = MagicMock()
        staging._client = mock_client

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            tmp.write(b"test data")
            tmp.flush()

            uri = staging.upload_file(Path(tmp.name), "table/file.parquet")

            mock_client.upload_file.assert_called_once()
            assert uri == "s3://my-bucket/data/table/file.parquet"

    def test_s3_file_exists_true(self):
        config = StagingConfig(
            uri="s3://my-bucket/data",
            provider=CloudProvider.AWS_S3,
            bucket="my-bucket",
            prefix="data",
        )
        staging = S3Staging(config)

        mock_client = MagicMock()
        mock_client.head_object.return_value = {}
        mock_client.exceptions = MagicMock()
        staging._client = mock_client

        assert staging.file_exists("table/file.parquet")
        mock_client.head_object.assert_called_with(Bucket="my-bucket", Key="data/table/file.parquet")

    def test_s3_list_files(self):
        config = StagingConfig(
            uri="s3://my-bucket/data",
            provider=CloudProvider.AWS_S3,
            bucket="my-bucket",
            prefix="data",
        )
        staging = S3Staging(config)

        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"Contents": [{"Key": "data/table/file1.parquet"}, {"Key": "data/table/file2.parquet"}]}
        ]
        mock_client.get_paginator.return_value = mock_paginator
        staging._client = mock_client

        files = staging.list_files("table/")

        assert len(files) == 2
        assert "data/table/file1.parquet" in files

    def test_s3_delete_recursive(self):
        config = StagingConfig(
            uri="s3://my-bucket/data",
            provider=CloudProvider.AWS_S3,
            bucket="my-bucket",
            prefix="data",
        )
        staging = S3Staging(config)

        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{"Contents": [{"Key": "data/table/file1.parquet"}]}]
        mock_client.get_paginator.return_value = mock_paginator
        staging._client = mock_client

        staging.delete_path("table/", recursive=True)

        mock_client.delete_objects.assert_called_once()


class TestStagingConfig:
    def test_default_values(self):

        config = StagingConfig(
            uri="s3://bucket/path",
            provider=CloudProvider.AWS_S3,
            bucket="bucket",
            prefix="path",
        )

        assert config.parallel_uploads == 4
        assert config.chunk_size == 8 * 1024 * 1024
        assert config.compression is None
        assert config.region is None

    def test_custom_values(self):

        config = StagingConfig(
            uri="s3://bucket/path",
            provider=CloudProvider.AWS_S3,
            bucket="bucket",
            prefix="path",
            region="us-west-2",
            compression="zstd",
            parallel_uploads=8,
        )

        assert config.region == "us-west-2"
        assert config.compression == "zstd"
        assert config.parallel_uploads == 8


class TestUploadProgressBoundaries:
    def test_percent_complete_exactly_full(self):
        progress = UploadProgress(
            table_name="t",
            file_name="f.parquet",
            bytes_uploaded=1024,
            total_bytes=1024,
            files_completed=1,
            total_files=1,
        )
        assert progress.percent_complete == 100.0

    def test_percent_complete_zero_uploaded(self):
        progress = UploadProgress(
            table_name="t",
            file_name="f.parquet",
            bytes_uploaded=0,
            total_bytes=512,
            files_completed=0,
            total_files=1,
        )
        assert progress.percent_complete == 0.0


class TestCloudSparkStagingFromUriExtra:
    def test_from_uri_wasbs(self):
        result = CloudSparkStaging.from_uri("wasbs://container@account.blob.core.windows.net/path")
        assert isinstance(result, AzureBlobStaging)
        assert result.config.provider == CloudProvider.AZURE_BLOB

    def test_from_uri_bare_local_path(self):
        staging = CloudSparkStaging.from_uri("/tmp/benchbox/data")
        assert isinstance(staging, LocalStaging)
        assert staging.config.provider == CloudProvider.LOCAL

    def test_parse_uri_s3_bucket_and_prefix(self):
        bucket, prefix = CloudSparkStaging._parse_uri("s3://my-bucket/path/to/prefix", CloudProvider.AWS_S3)
        assert bucket == "my-bucket"
        assert prefix == "path/to/prefix"

    def test_parse_uri_gcs_bucket_and_prefix(self):
        bucket, prefix = CloudSparkStaging._parse_uri("gs://gcs-bucket/benchbox/data", CloudProvider.GCS)
        assert bucket == "gcs-bucket"
        assert prefix == "benchbox/data"


def _s3_config(prefix: str | None = "data") -> StagingConfig:
    from benchbox.platforms.base.cloud_spark.staging import CloudProvider, StagingConfig

    return StagingConfig(
        uri="s3://my-bucket/data",
        provider=CloudProvider.AWS_S3,
        bucket="my-bucket",
        prefix=prefix or "",
    )


class TestS3StagingMethods:
    def _provider(self, prefix=None):
        from benchbox.platforms.base.cloud_spark.staging import S3Staging

        s = S3Staging(_s3_config(prefix))
        s._client = MagicMock()
        return s

    def test_upload_file(self, tmp_path):
        p = self._provider()
        f = tmp_path / "lineitem.parquet"
        f.write_bytes(b"data")
        uri = p.upload_file(f, "lineitem/lineitem.parquet")
        p._client.upload_file.assert_called_once()
        assert "my-bucket" in uri

    def test_upload_file_gzip_encoding(self, tmp_path):
        from benchbox.platforms.base.cloud_spark.staging import S3Staging

        config = _s3_config()
        config.compression = "gzip"
        s = S3Staging(config)
        s._client = MagicMock()
        f = tmp_path / "data.parquet"
        f.write_bytes(b"data")
        s.upload_file(f, "tables/data.parquet")
        call_kwargs = s._client.upload_file.call_args[1]
        assert call_kwargs.get("ExtraArgs") == {"ContentEncoding": "gzip"}

    def test_file_exists_found(self):
        p = self._provider()
        p._client.head_object.return_value = {}
        p._client.exceptions.ClientError = Exception
        assert p.file_exists("lineitem/lineitem.parquet") is True

    def test_file_exists_not_found(self):
        p = self._provider()
        p._client.exceptions.ClientError = Exception
        p._client.head_object.side_effect = p._client.exceptions.ClientError
        assert p.file_exists("missing.parquet") is False

    def test_delete_path_single_file(self):
        p = self._provider(prefix=None)
        p.delete_path("tables/data.parquet", recursive=False)
        p._client.delete_object.assert_called_once_with(Bucket="my-bucket", Key="tables/data.parquet")

    def test_full_key_no_prefix(self):
        p = self._provider(prefix=None)
        assert p._full_key("tables/data") == "tables/data"


class TestGCSStagingMethods:
    def _provider(self, prefix="data"):
        from benchbox.platforms.base.cloud_spark.staging import CloudProvider, GCSStaging, StagingConfig

        config = StagingConfig(
            uri="gs://gcs-bucket/data", provider=CloudProvider.GCS, bucket="gcs-bucket", prefix=prefix
        )
        s = GCSStaging(config)
        s._client = MagicMock()
        return s

    def test_upload_file(self, tmp_path):
        p = self._provider()
        mock_blob = MagicMock()
        p._client.bucket.return_value.blob.return_value = mock_blob
        f = tmp_path / "data.parquet"
        f.write_bytes(b"data")
        uri = p.upload_file(f, "tables/data.parquet")
        mock_blob.upload_from_filename.assert_called_once()
        assert "gcs-bucket" in uri

    def test_file_exists(self):
        p = self._provider()
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        p._client.bucket.return_value.blob.return_value = mock_blob
        assert p.file_exists("tables/data.parquet") is True

    def test_list_files(self):
        p = self._provider()
        mock_blobs = [MagicMock(name="data/tables/a.parquet"), MagicMock(name="data/tables/b.parquet")]
        p._client.bucket.return_value.list_blobs.return_value = mock_blobs
        files = p.list_files("tables/")
        assert len(files) == 2

    def test_delete_path_recursive(self):
        p = self._provider()
        blob1, blob2 = MagicMock(), MagicMock()
        p._client.bucket.return_value.list_blobs.return_value = [blob1, blob2]
        p.delete_path("tables/", recursive=True)
        blob1.delete.assert_called_once()
        blob2.delete.assert_called_once()

    def test_delete_path_single(self):
        p = self._provider()
        mock_blob = MagicMock()
        p._client.bucket.return_value.blob.return_value = mock_blob
        p.delete_path("tables/data.parquet", recursive=False)
        mock_blob.delete.assert_called_once()

    def test_get_client_caches(self):
        p = self._provider()
        client = p._get_client()
        assert client is p._client


class TestAzureADLSStagingMethods:
    def _provider(self):
        from benchbox.platforms.base.cloud_spark.staging import AzureADLSStaging, CloudProvider, StagingConfig

        config = StagingConfig(
            uri="abfss://container@account.dfs.core.windows.net/prefix",
            provider=CloudProvider.AZURE_ADLS,
            bucket="container@account.dfs.core.windows.net",
            prefix="prefix",
        )
        s = AzureADLSStaging(config)
        s._client = MagicMock()
        return s

    def test_full_path_with_prefix(self):
        p = self._provider()
        assert p._full_path("tables/data") == "prefix/tables/data"

    def test_upload_file(self, tmp_path):
        p = self._provider()
        mock_file_client = MagicMock()
        p._client.get_file_client.return_value = mock_file_client
        f = tmp_path / "data.parquet"
        f.write_bytes(b"data")
        p.upload_file(f, "tables/data.parquet")
        mock_file_client.upload_data.assert_called_once()

    def test_file_exists_found(self):
        p = self._provider()
        mock_fc = MagicMock()
        mock_fc.get_file_properties.return_value = {}
        p._client.get_file_client.return_value = mock_fc
        assert p.file_exists("tables/data.parquet") is True

    def test_file_exists_not_found(self):
        p = self._provider()
        mock_fc = MagicMock()
        mock_fc.get_file_properties.side_effect = Exception("not found")
        p._client.get_file_client.return_value = mock_fc
        assert p.file_exists("tables/data.parquet") is False

    def test_list_files(self):
        p = self._provider()
        paths = [
            MagicMock(name="prefix/tables/a.parquet", is_directory=False),
            MagicMock(is_directory=True),
        ]
        p._client.get_paths.return_value = paths
        files = p.list_files("tables/")
        assert len(files) == 1

    def test_delete_path_recursive(self):
        p = self._provider()
        mock_dir = MagicMock()
        p._client.get_directory_client.return_value = mock_dir
        p.delete_path("tables/", recursive=True)
        mock_dir.delete_directory.assert_called_once()

    def test_delete_path_single(self):
        p = self._provider()
        mock_fc = MagicMock()
        p._client.get_file_client.return_value = mock_fc
        p.delete_path("tables/data.parquet", recursive=False)
        mock_fc.delete_file.assert_called_once()


class TestAzureBlobStagingMethods:
    def _provider(self):
        from benchbox.platforms.base.cloud_spark.staging import AzureBlobStaging, CloudProvider, StagingConfig

        config = StagingConfig(
            uri="wasbs://container@account.blob.core.windows.net/prefix",
            provider=CloudProvider.AZURE_BLOB,
            bucket="container@account.blob.core.windows.net",
            prefix="prefix",
        )
        s = AzureBlobStaging(config)
        s._client = MagicMock()
        return s

    def test_full_path_with_prefix(self):
        p = self._provider()
        assert p._full_path("tables/data") == "prefix/tables/data"

    def test_upload_file(self, tmp_path):
        p = self._provider()
        mock_blob = MagicMock()
        p._client.get_blob_client.return_value = mock_blob
        f = tmp_path / "data.parquet"
        f.write_bytes(b"data")
        p.upload_file(f, "tables/data.parquet")
        mock_blob.upload_blob.assert_called_once()

    def test_file_exists(self):
        p = self._provider()
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        p._client.get_blob_client.return_value = mock_blob
        assert p.file_exists("tables/data.parquet") is True

    def test_list_files(self):
        p = self._provider()
        p._client.list_blobs.return_value = [MagicMock(name="prefix/tables/a.parquet")]
        files = p.list_files("tables/")
        assert len(files) == 1

    def test_delete_path_recursive(self):
        p = self._provider()
        p._client.list_blobs.return_value = [MagicMock(name="prefix/tables/a.parquet")]
        p.delete_path("tables/", recursive=True)
        p._client.delete_blob.assert_called_once()

    def test_delete_path_single(self):
        p = self._provider()
        p.delete_path("tables/data.parquet", recursive=False)
        p._client.delete_blob.assert_called_once_with("prefix/tables/data.parquet")


class TestDBFSStagingMethods:
    def _provider(self, prefix="benchbox"):
        from benchbox.platforms.base.cloud_spark.staging import CloudProvider, DBFSStaging, StagingConfig

        config = StagingConfig(
            uri="dbfs:/benchbox",
            provider=CloudProvider.DBFS,
            bucket="",
            prefix=prefix,
        )
        s = DBFSStaging(config)
        s._client = MagicMock()
        return s

    def test_full_path_with_prefix(self):
        p = self._provider()
        assert p._full_path("tables/data") == "/benchbox/tables/data"

    def test_full_path_no_prefix(self):
        p = self._provider(prefix="")
        assert p._full_path("tables/data") == "/tables/data"

    def test_upload_file(self, tmp_path):
        p = self._provider()
        f = tmp_path / "data.parquet"
        f.write_bytes(b"data")
        uri = p.upload_file(f, "tables/data.parquet")
        p._client.dbfs.upload.assert_called_once()
        assert "dbfs:" in uri

    def test_file_exists_found(self):
        p = self._provider()
        p._client.dbfs.get_status.return_value = {}
        assert p.file_exists("tables/data.parquet") is True

    def test_file_exists_not_found(self):
        p = self._provider()
        p._client.dbfs.get_status.side_effect = Exception("not found")
        assert p.file_exists("tables/data.parquet") is False

    def test_list_files(self):
        p = self._provider()
        files = [MagicMock(path="/benchbox/tables/a.parquet", is_dir=False), MagicMock(is_dir=True)]
        p._client.dbfs.list.return_value = files
        result = p.list_files("tables/")
        assert len(result) == 1

    def test_list_files_empty(self):
        p = self._provider()
        p._client.dbfs.list.side_effect = Exception("no such path")
        result = p.list_files("tables/")
        assert result == []

    def test_delete_path(self):
        p = self._provider()
        p.delete_path("tables/", recursive=True)
        p._client.dbfs.delete.assert_called_once_with("/benchbox/tables/", recursive=True)


