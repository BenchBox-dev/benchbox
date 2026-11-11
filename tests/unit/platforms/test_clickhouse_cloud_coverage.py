from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import benchbox.platforms.clickhouse_cloud as cloud
from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter, _build_clickhouse_cloud_config

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_gcs_mocks():

    mock_blob = MagicMock()
    mock_gcs_bucket = MagicMock()
    mock_gcs_bucket.blob.return_value = mock_blob
    mock_gcs_client = MagicMock()
    mock_gcs_client.bucket.return_value = mock_gcs_bucket
    mock_gcs_storage = MagicMock()
    mock_gcs_storage.Client.return_value = mock_gcs_client
    mock_google_cloud = MagicMock()
    mock_google_cloud.storage = mock_gcs_storage
    sys_modules = {
        "google": MagicMock(cloud=mock_google_cloud),
        "google.cloud": mock_google_cloud,
        "google.cloud.storage": mock_gcs_storage,
    }
    return mock_blob, mock_gcs_bucket, mock_gcs_storage, mock_google_cloud, sys_modules


def test_from_config_maps_cloud_and_optional_fields() -> None:
    captured = {}

    def _fake_init(self, **kwargs):
        captured.update(kwargs)

    with patch.object(ClickHouseCloudAdapter, "__init__", _fake_init):
        ClickHouseCloudAdapter.from_config(
            {
                "host": "h",
                "password": "p",
                "username": "u",
                "database": "d",
                "region": "us-east-2",
                "cloud_provider": "aws",
                "service_id": "svc123",
                "service_name": "prod",
                "service_tier": "production",
                "compute_size": "large",
                "compression": "lz4",
                "benchmark": "tpch",
            }
        )

    assert captured["host"] == "h"
    assert captured["region"] == "us-east-2"
    assert captured["cloud_provider"] == "aws"
    assert captured["service_id"] == "svc123"
    assert captured["service_name"] == "prod"
    assert captured["service_tier"] == "production"
    assert captured["compute_size"] == "large"
    assert captured["compression"] == "lz4"
    assert captured["benchmark"] == "tpch"


def test_from_config_maps_oauth_token() -> None:

    captured = {}

    def _fake_init(self, **kwargs):
        captured.update(kwargs)

    with patch.object(ClickHouseCloudAdapter, "__init__", _fake_init):
        ClickHouseCloudAdapter.from_config(
            {
                "host": "h",
                "oauth_token": "my-token",
                "database": "d",
            }
        )

    assert captured["oauth_token"] == "my-token"
    assert captured["host"] == "h"


def test_from_config_maps_cloud_storage_staging() -> None:

    captured = {}

    def _fake_init(self, **kwargs):
        captured.update(kwargs)

    with patch.object(ClickHouseCloudAdapter, "__init__", _fake_init):
        ClickHouseCloudAdapter.from_config(
            {
                "host": "h",
                "password": "p",
                "s3_staging_url": "s3://bucket/prefix/",
                "s3_region": "us-east-1",
                "gcs_staging_url": "gs://bucket/prefix/",
            }
        )

    assert captured["s3_staging_url"] == "s3://bucket/prefix/"
    assert captured["s3_region"] == "us-east-1"
    assert captured["gcs_staging_url"] == "gs://bucket/prefix/"


def test_get_platform_info_adds_cloud_metadata() -> None:
    adapter = ClickHouseCloudAdapter(host="h", password="p")

    with patch(
        "benchbox.platforms.clickhouse_cloud.ClickHouseAdapter.get_platform_info", return_value={"configuration": {}}
    ):
        info = adapter.get_platform_info(connection=None)

    assert info["platform_type"] == "clickhouse-cloud"
    assert info["connection_mode"] == "cloud"
    assert info["configuration"]["deployment"] == "managed"
    assert info["configuration"]["auth_method"] == "password"
    assert info["configuration"]["host"] == "h"
    assert info["configuration"]["port"] == 8443
    assert info["configuration"]["secure"] is True


def test_get_platform_info_oauth_auth_method() -> None:

    adapter = ClickHouseCloudAdapter(host="h", oauth_token="tok")

    with patch(
        "benchbox.platforms.clickhouse_cloud.ClickHouseAdapter.get_platform_info", return_value={"configuration": {}}
    ):
        info = adapter.get_platform_info(connection=None)

    assert info["configuration"]["auth_method"] == "oauth"


def test_get_platform_info_includes_staging_urls() -> None:

    adapter = ClickHouseCloudAdapter(
        host="h",
        password="p",
        s3_staging_url="s3://b/p/",
        s3_region="us-east-1",
        gcs_staging_url="gs://b/p/",
    )

    with patch(
        "benchbox.platforms.clickhouse_cloud.ClickHouseAdapter.get_platform_info", return_value={"configuration": {}}
    ):
        info = adapter.get_platform_info(connection=None)

    assert info["configuration"]["s3_staging_url"] == "s3://b/p/"
    assert info["configuration"]["s3_region"] == "us-east-1"
    assert info["configuration"]["gcs_staging_url"] == "gs://b/p/"


def test_normalized_metadata_maps_full_clickhouse_cloud_config() -> None:
    adapter = ClickHouseCloudAdapter(
        host="svc123.us-east-2.aws.clickhouse.cloud",
        password="p",
        database="bench",
        service_id="configured-svc",
        service_name="prod",
        service_tier="production",
        compute_size="large",
        s3_staging_url="s3://bench-bucket/stage/",
        s3_region="us-east-2",
        disable_result_cache=False,
    )
    platform_info = adapter.get_platform_info(connection=None)
    platform_info["compute_configuration"] = {"system_settings": {"max_threads": "12"}}

    metadata = adapter.get_normalized_result_metadata(platform_info=platform_info)

    assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "managed_cloud"
    assert metadata["platform_deployment"]["deployment_type"] == "managed_cloud"
    assert metadata["platform_deployment"]["service_endpoint"] == "svc123.us-east-2.aws.clickhouse.cloud"
    assert metadata["platform_deployment"]["auth_method"] == "password"
    assert metadata["platform_cloud"]["provider"] == "aws"
    assert metadata["platform_cloud"]["region"] == "us-east-2"
    assert metadata["platform_cloud"]["workspace"] == "configured-svc"
    assert metadata["platform_cloud"]["region_collection_status"] == "available"
    assert metadata["platform_compute"]["service_id"] == "configured-svc"
    assert metadata["platform_compute"]["service_name"] == "prod"
    assert metadata["platform_compute"]["service_tier"] == "production"
    assert metadata["platform_compute"]["compute_size"] == "large"
    assert metadata["platform_compute"]["result_cache_enabled"] is True
    assert metadata["platform_compute"]["system_settings"] == {"max_threads": "12"}
    assert metadata["platform_compute"]["collection_status"] == "available"
    assert metadata["platform_storage"]["staging_url_type"] == "s3"
    assert metadata["platform_storage"]["bucket"] == "bench-bucket"
    assert metadata["platform_storage"]["prefix"] == "stage/"
    assert metadata["platform_storage"]["region"] == "us-east-2"


def test_normalized_metadata_marks_sparse_clickhouse_cloud_facets_unavailable() -> None:
    adapter = ClickHouseCloudAdapter(host="svc.clickhouse.cloud", password="p")

    metadata = adapter.get_normalized_result_metadata(platform_info=adapter.get_platform_info(connection=None))

    assert "provider" not in metadata["platform_cloud"]
    assert "region" not in metadata["platform_cloud"]
    assert metadata["platform_cloud"]["region_collection_status"] == "unavailable"
    assert metadata["platform_cloud"]["source"] == "requested"
    assert metadata["platform_compute"]["collection_status"] == "partial"
    assert metadata["platform_storage"]["table_format"] == "MergeTree"
    assert metadata["platform_storage"]["staging_url_type_status"] == "unavailable"
    assert metadata["platform_storage"]["collection_status"] == "partial"


def test_build_clickhouse_cloud_config_merges_saved_options(monkeypatch: pytest.MonkeyPatch) -> None:
    cred_manager = MagicMock()
    cred_manager.get_platform_credentials.return_value = {"host": "saved", "username": "saved_user"}
    info = type("Info", (), {"display_name": "CH Cloud", "driver_package": "clickhouse-connect"})
    with patch("benchbox.security.credentials.CredentialManager", return_value=cred_manager):
        config = _build_clickhouse_cloud_config(
            "clickhouse-cloud",
            {"host": "option-host", "password": "option-pass"},
            {"database": "db1", "benchmark": "tpch", "scale_factor": 0.01},
            info,
        )

    assert config.type == "clickhouse-cloud"

    assert config.host == "saved"
    assert config.password == "option-pass"
    assert config.options["username"] == "saved_user"


def test_build_config_includes_oauth_and_staging() -> None:

    cred_manager = MagicMock()
    cred_manager.get_platform_credentials.return_value = {}
    info = type("Info", (), {"display_name": "CH Cloud", "driver_package": "clickhouse-connect"})
    with patch("benchbox.security.credentials.CredentialManager", return_value=cred_manager):
        config = _build_clickhouse_cloud_config(
            "clickhouse-cloud",
            {
                "host": "h",
                "oauth_token": "tok",
                "region": "us-west-2",
                "cloud_provider": "aws",
                "service_id": "svc123",
                "service_name": "prod",
                "service_tier": "production",
                "compute_size": "large",
                "s3_staging_url": "s3://b/",
                "s3_region": "us-west-2",
                "gcs_staging_url": "gs://b/",
            },
            {},
            info,
        )

    assert config.options["oauth_token"] == "tok"
    assert config.options["region"] == "us-west-2"
    assert config.options["cloud_provider"] == "aws"
    assert config.options["service_id"] == "svc123"
    assert config.options["service_name"] == "prod"
    assert config.options["service_tier"] == "production"
    assert config.options["compute_size"] == "large"
    assert config.options["s3_staging_url"] == "s3://b/"
    assert config.options["s3_region"] == "us-west-2"
    assert config.options["gcs_staging_url"] == "gs://b/"


def test_adapter_init_with_oauth_token_instead_of_password() -> None:

    adapter = ClickHouseCloudAdapter(host="h", oauth_token="my-token")
    assert adapter.oauth_token == "my-token"
    assert adapter.host == "h"


def test_adapter_init_with_oauth_token_env(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setenv("CLICKHOUSE_CLOUD_HOST", "cloud-host")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_OAUTH_TOKEN", "env-token")
    adapter = ClickHouseCloudAdapter()
    assert adapter.oauth_token == "env-token"
    assert adapter.host == "cloud-host"


def test_apply_cloud_defaults_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setenv("CLICKHOUSE_CLOUD_HOST", "env-host")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_PASSWORD", "env-pass")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_OAUTH_TOKEN", "env-oauth")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_USER", "env-user")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_S3_STAGING_URL", "s3://env-bucket/")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_S3_REGION", "eu-west-1")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_GCS_STAGING_URL", "gs://env-bucket/")

    config: dict = {}

    ClickHouseCloudAdapter._apply_cloud_defaults(None, config)

    assert config["host"] == "env-host"
    assert config["password"] == "env-pass"
    assert config["oauth_token"] == "env-oauth"
    assert config["username"] == "env-user"
    assert config["s3_staging_url"] == "s3://env-bucket/"
    assert config["s3_region"] == "eu-west-1"
    assert config["gcs_staging_url"] == "gs://env-bucket/"


def test_add_cli_arguments_includes_new_options() -> None:

    parser = argparse.ArgumentParser()
    ClickHouseCloudAdapter.add_cli_arguments(parser)

    args = parser.parse_args([])

    assert getattr(args, "clickhouse_cloud_oauth_token", "MISSING") is None
    assert getattr(args, "clickhouse_cloud_s3_staging_url", "MISSING") is None
    assert getattr(args, "clickhouse_cloud_s3_region", "MISSING") is None
    assert getattr(args, "clickhouse_cloud_gcs_staging_url", "MISSING") is None


def test_add_cli_arguments_parses_values() -> None:

    parser = argparse.ArgumentParser()
    ClickHouseCloudAdapter.add_cli_arguments(parser)

    args = parser.parse_args(
        [
            "--clickhouse-cloud-oauth-token",
            "my-tok",
            "--clickhouse-cloud-s3-staging-url",
            "s3://b/p/",
            "--clickhouse-cloud-s3-region",
            "us-east-1",
            "--clickhouse-cloud-gcs-staging-url",
            "gs://b/p/",
        ]
    )
    assert args.clickhouse_cloud_oauth_token == "my-tok"
    assert args.clickhouse_cloud_s3_staging_url == "s3://b/p/"
    assert args.clickhouse_cloud_s3_region == "us-east-1"
    assert args.clickhouse_cloud_gcs_staging_url == "gs://b/p/"


def test_parse_s3_url() -> None:
    assert ClickHouseCloudAdapter._parse_s3_url("s3://my-bucket/staging/") == ("my-bucket", "staging/")
    assert ClickHouseCloudAdapter._parse_s3_url("s3://my-bucket/") == ("my-bucket", "")
    assert ClickHouseCloudAdapter._parse_s3_url("s3://bucket") == ("bucket", "")


def test_parse_gcs_url() -> None:
    assert ClickHouseCloudAdapter._parse_gcs_url("gs://my-bucket/staging/") == ("my-bucket", "staging/")
    assert ClickHouseCloudAdapter._parse_gcs_url("gs://my-bucket/") == ("my-bucket", "")
    assert ClickHouseCloudAdapter._parse_gcs_url("gs://bucket") == ("bucket", "")


def test_invalid_s3_url_raises_error() -> None:

    from benchbox.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="Invalid S3 staging URL"):
        ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="http://not-s3/")


def test_invalid_gcs_url_raises_error() -> None:

    from benchbox.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="Invalid GCS staging URL"):
        ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="http://not-gcs/")


def test_s3_url_gets_trailing_slash() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/prefix")
    assert adapter.s3_staging_url == "s3://bucket/prefix/"


def test_gcs_url_gets_trailing_slash() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://bucket/prefix")
    assert adapter.gcs_staging_url == "gs://bucket/prefix/"


def test_load_data_dispatches_to_s3_when_configured() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://b/p/")
    mock_result = ({"t": 10}, 1.0, {"loading_method": "s3_staging"})

    benchmark, connection = MagicMock(), MagicMock()
    with patch.object(adapter, "_load_data_via_s3", return_value=mock_result) as mock_s3:
        result = adapter.load_data(benchmark, connection, Path("/tmp"))

    mock_s3.assert_called_once_with(benchmark, connection, Path("/tmp"))
    assert result[2]["loading_method"] == "s3_staging"


def test_load_data_dispatches_to_gcs_when_configured() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://b/p/")
    mock_result = ({"t": 10}, 1.0, {"loading_method": "gcs_staging"})

    benchmark, connection = MagicMock(), MagicMock()
    with patch.object(adapter, "_load_data_via_gcs", return_value=mock_result) as mock_gcs:
        result = adapter.load_data(benchmark, connection, Path("/tmp"))

    mock_gcs.assert_called_once_with(benchmark, connection, Path("/tmp"))
    assert result[2]["loading_method"] == "gcs_staging"


def test_load_data_falls_back_to_default_when_no_staging() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p")
    mock_result = ({"t": 10}, 1.0, None)

    benchmark, connection = MagicMock(), MagicMock()
    with patch(
        "benchbox.platforms.clickhouse.workload.ClickHouseWorkloadMixin.load_data",
        return_value=mock_result,
    ) as mock_parent:
        result = adapter.load_data(benchmark, connection, Path("/tmp"))

    mock_parent.assert_called_once_with(benchmark, connection, Path("/tmp"))
    assert result[2] is None


def test_external_mode_requires_staging_url() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p")
    assert adapter.supports_external_tables is True

    with pytest.raises(ValueError, match="requires cloud staging URL"):
        adapter.validate_external_table_requirements()


def test_create_external_tables_dispatches_to_s3() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")
    mock_result = ({"orders": 100}, 1.0, {"loading_method": "s3_external_views"})

    benchmark, connection = MagicMock(), MagicMock()
    with patch.object(adapter, "_create_external_tables_via_s3", return_value=mock_result) as mock_s3:
        result = adapter.create_external_tables(benchmark, connection, Path("/tmp"))

    mock_s3.assert_called_once_with(benchmark, connection, Path("/tmp"))
    assert result[2]["loading_method"] == "s3_external_views"


def test_create_external_tables_via_s3_builds_view_sql(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")

    parquet_file = tmp_path / "orders.parquet"
    parquet_file.write_bytes(b"PAR1")

    benchmark = MagicMock()
    benchmark.tables = {"orders": [str(parquet_file)]}

    connection = MagicMock()

    def _execute(sql: str):
        if sql.startswith("SELECT COUNT(*)"):
            return [(17,)]
        return []

    connection.execute.side_effect = _execute

    mock_s3_client = MagicMock()
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_s3_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        stats, _, metadata = adapter._create_external_tables_via_s3(benchmark, connection, tmp_path)

    assert stats["orders"] == 17
    assert metadata["loading_method"] == "s3_external_views"
    upload_args = mock_s3_client.upload_file.call_args
    assert upload_args is not None, "upload_file should have been called"
    assert "bucket" in str(upload_args), "Upload should target the configured bucket"

    executed_sql = " ".join(call.args[0] for call in connection.execute.call_args_list)
    assert "CREATE OR REPLACE VIEW orders AS SELECT * FROM s3(" in executed_sql
    assert "'Parquet'" in executed_sql


def test_create_external_tables_via_s3_builds_iceberg_view_sql(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")
    iceberg_dir = tmp_path / "orders"
    (iceberg_dir / "metadata").mkdir(parents=True)
    (iceberg_dir / "metadata" / "v1.metadata.json").write_text("{}")

    benchmark = MagicMock()
    benchmark.tables = {"orders": [str(iceberg_dir)]}
    connection = MagicMock()

    def _execute(sql: str):
        if sql.startswith("SELECT COUNT(*)"):
            return [(4,)]
        return []

    connection.execute.side_effect = _execute

    mock_s3_client = MagicMock()
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_s3_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        stats, _, metadata = adapter._create_external_tables_via_s3(benchmark, connection, tmp_path)

    assert stats["orders"] == 4
    assert metadata["loading_method"] == "s3_external_views"
    executed_sql = " ".join(call.args[0] for call in connection.execute.call_args_list)
    assert "CREATE OR REPLACE VIEW orders AS SELECT * FROM iceberg(" in executed_sql


def test_load_data_via_s3_boto3_import_error() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://b/p/")

    with patch.dict("sys.modules", {"boto3": None}):
        with pytest.raises(ImportError, match="boto3 is required"):
            adapter._load_data_via_s3(MagicMock(), MagicMock(), Path("/tmp"))


def test_load_data_via_s3_uploads_and_ingests(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")

    data_file = tmp_path / "test_table.csv"
    data_file.write_text("col1,col2\n1,a\n2,b\n")

    benchmark = MagicMock()
    benchmark.tables = {"test_table": [str(data_file)]}

    connection = MagicMock()
    connection.execute.return_value = [(100,)]

    mock_s3_client = MagicMock()
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_s3_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        table_stats, total_time, metadata = adapter._load_data_via_s3(benchmark, connection, tmp_path)

    assert "test_table" in table_stats
    assert metadata["loading_method"] == "s3_staging"
    assert metadata["s3_staging_url"] == "s3://bucket/staging/"

    assert mock_s3_client.upload_file.called, "upload_file should have been called"
    uploaded_path = mock_s3_client.upload_file.call_args[0][0]
    assert uploaded_path == str(data_file), f"Upload should include {data_file}"

    execute_calls = [str(c) for c in connection.execute.call_args_list]
    assert any("s3(" in str(c) for c in execute_calls)


def test_load_data_via_s3_uses_single_glob_insert_for_many_files(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")

    chunk_a = tmp_path / "lineitem_0.csv"
    chunk_b = tmp_path / "lineitem_1.csv"
    chunk_a.write_text("col1,col2\n1,a\n")
    chunk_b.write_text("col1,col2\n2,b\n")

    benchmark = MagicMock()
    benchmark.tables = {"lineitem": [str(chunk_a), str(chunk_b)]}

    connection = MagicMock()
    connection.execute.return_value = [(3,)]

    mock_s3_client = MagicMock()
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_s3_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        table_stats, _, metadata = adapter._load_data_via_s3(benchmark, connection, tmp_path)

    assert table_stats == {"lineitem": 3}
    assert metadata["loading_method"] == "s3_staging"
    assert mock_s3_client.upload_file.call_count == 2
    uploaded_keys = [c.args[2] for c in mock_s3_client.upload_file.call_args_list]
    tokens = {key.split("/")[2] for key in uploaded_keys}
    assert len(tokens) == 1, "both uploads of one load must share a single per-load prefix"
    token = tokens.pop()
    assert re.fullmatch(r"[0-9a-f]{12}", token)
    inserts = [c.args[0] for c in connection.execute.call_args_list if "INSERT INTO" in c.args[0]]
    assert len(inserts) == 1
    assert f"s3('s3://bucket/staging/lineitem/{token}/*'" in inserts[0]
    counts = [c.args[0] for c in connection.execute.call_args_list if "COUNT(*)" in c.args[0]]
    assert len(counts) == 1


def test_load_data_via_s3_glob_tokens_differ_between_loads(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")

    first = tmp_path / "lineitem_0.csv"
    first.write_text("col1\n1\n")
    second = tmp_path / "lineitem_1.csv"
    second.write_text("col1\n2\n")

    mock_s3_client = MagicMock()
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_s3_client

    globs: list[str] = []
    for data_file in (first, second):
        benchmark = MagicMock()
        benchmark.tables = {"lineitem": [str(data_file)]}
        connection = MagicMock()
        connection.execute.return_value = [(1,)]
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            adapter._load_data_via_s3(benchmark, connection, tmp_path)
        inserts = [c.args[0] for c in connection.execute.call_args_list if "INSERT INTO" in c.args[0]]
        assert len(inserts) == 1
        globs.append(inserts[0])

    assert globs[0] != globs[1], "each load must ingest through its own unique prefix"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows file names cannot contain '*', which this test needs to exercise glob-metacharacter handling",
)
def test_load_data_via_s3_falls_back_per_file_on_glob_syntax(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")

    odd_file = tmp_path / "line*item.csv"
    odd_file.write_text("col1\n1\n")

    benchmark = MagicMock()
    benchmark.tables = {"lineitem": [str(odd_file)]}

    connection = MagicMock()
    connection.execute.return_value = [(1,)]

    mock_s3_client = MagicMock()
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_s3_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        table_stats, _, _ = adapter._load_data_via_s3(benchmark, connection, tmp_path)

    assert table_stats == {"lineitem": 1}
    inserts = [c.args[0] for c in connection.execute.call_args_list if "INSERT INTO" in c.args[0]]
    assert len(inserts) == 1
    assert "line*item.csv" in inserts[0]
    assert "*" not in inserts[0].split("s3('", 1)[1].split("'", 1)[0].replace("line*item.csv", "")


def test_load_data_via_gcs_uses_single_glob_insert(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://bucket/staging/")

    chunk_a = tmp_path / "orders_0.csv"
    chunk_b = tmp_path / "orders_1.csv"
    chunk_a.write_text("col1\n1\n")
    chunk_b.write_text("col1\n2\n")

    benchmark = MagicMock()
    benchmark.tables = {"orders": [str(chunk_a), str(chunk_b)]}

    connection = MagicMock()
    connection.execute.return_value = [(2,)]

    _, mock_gcs_bucket, _, _, sys_modules = _make_gcs_mocks()

    with patch.dict("sys.modules", sys_modules):
        table_stats, _, metadata = adapter._load_data_via_gcs(benchmark, connection, tmp_path)

    assert table_stats == {"orders": 2}
    assert metadata["loading_method"] == "gcs_staging"
    assert mock_gcs_bucket.blob.call_count == 2
    blob_names = [c.args[0] for c in mock_gcs_bucket.blob.call_args_list]
    tokens = {name.split("/")[2] for name in blob_names}
    assert len(tokens) == 1, "both uploads of one load must share a single per-load prefix"
    token = tokens.pop()
    assert re.fullmatch(r"[0-9a-f]{12}", token)
    inserts = [c.args[0] for c in connection.execute.call_args_list if "INSERT INTO" in c.args[0]]
    assert len(inserts) == 1
    assert f"gcs('https://storage.googleapis.com/bucket/staging/orders/{token}/*'" in inserts[0]


def test_load_data_via_gcs_import_error() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://b/p/")

    with patch.dict("sys.modules", {"google.cloud": None, "google.cloud.storage": None, "google": None}):
        with pytest.raises(ImportError, match="google-cloud-storage is required"):
            adapter._load_data_via_gcs(MagicMock(), MagicMock(), Path("/tmp"))


def test_cloud_client_uses_access_token() -> None:

    mock_cc = MagicMock()
    mock_client = MagicMock()
    mock_cc.get_client.return_value = mock_client

    with patch("benchbox.platforms.clickhouse._dependencies.clickhouse_connect", mock_cc):
        from benchbox.platforms.clickhouse.client import ClickHouseCloudClient

        ClickHouseCloudClient(
            host="h",
            access_token="my-token",
        )

    call_kwargs = mock_cc.get_client.call_args[1]
    assert call_kwargs["access_token"] == "my-token"
    assert "password" not in call_kwargs
    assert "username" not in call_kwargs


def test_cloud_client_uses_password_when_no_token() -> None:

    mock_cc = MagicMock()
    mock_client = MagicMock()
    mock_cc.get_client.return_value = mock_client

    with patch("benchbox.platforms.clickhouse._dependencies.clickhouse_connect", mock_cc):
        from benchbox.platforms.clickhouse.client import ClickHouseCloudClient

        ClickHouseCloudClient(
            host="h",
            user="testuser",
            password="testpass",
        )

    call_kwargs = mock_cc.get_client.call_args[1]
    assert call_kwargs["username"] == "testuser"
    assert call_kwargs["password"] == "testpass"
    assert "access_token" not in call_kwargs


def test_setup_cloud_mode_requires_auth() -> None:

    with pytest.raises(ValueError, match="requires authentication"):
        ClickHouseCloudAdapter(host="h")


def test_setup_cloud_mode_with_oauth_only() -> None:

    adapter = ClickHouseCloudAdapter(host="h", oauth_token="tok")
    assert adapter.oauth_token == "tok"
    assert adapter.password is None or adapter.password == ""


def test_create_cloud_connection_passes_oauth_token() -> None:

    adapter = ClickHouseCloudAdapter(host="h", oauth_token="tok")

    mock_client = MagicMock()
    mock_client.execute.return_value = [(1,)]

    with patch("benchbox.platforms.clickhouse.setup.ClickHouseCloudClient", return_value=mock_client) as MockClient:
        adapter._create_cloud_connection()

    call_kwargs = MockClient.call_args[1]
    assert call_kwargs["access_token"] == "tok"


def test_resolve_cloud_data_files_from_benchmark_tables(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p")
    benchmark = MagicMock()
    benchmark.tables = {"orders": [str(tmp_path / "orders.csv")]}

    result = adapter._resolve_cloud_data_files(benchmark, tmp_path)
    assert "orders" in result
    assert result["orders"][0] == tmp_path / "orders.csv"


def test_resolve_cloud_data_files_from_manifest(tmp_path: Path) -> None:

    import json

    manifest = {
        "tables": {
            "lineitem": [{"path": "lineitem.csv"}],
        }
    }
    (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))

    adapter = ClickHouseCloudAdapter(host="h", password="p")
    benchmark = MagicMock(spec=[])

    result = adapter._resolve_cloud_data_files(benchmark, tmp_path)
    assert "lineitem" in result


def test_resolve_cloud_data_files_raises_when_no_data(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p")
    benchmark = MagicMock(spec=[])

    with pytest.raises(ValueError, match="No data files found"):
        adapter._resolve_cloud_data_files(benchmark, tmp_path)


def test_normalize_external_file_inputs_splits_cloud_and_local(tmp_path: Path) -> None:

    local_file = tmp_path / "data.parquet"
    local_file.write_bytes(b"\x00" * 10)
    inputs = [str(local_file), "s3://bucket/data.parquet", "gs://bucket/data.parquet"]
    local_paths, cloud_uris = ClickHouseCloudAdapter._normalize_external_file_inputs(inputs)
    assert len(local_paths) == 1
    assert len(cloud_uris) == 2
    assert str(local_file) == str(local_paths[0])


def test_s3_external_credential_warning_logged(tmp_path: Path, caplog) -> None:

    import sys

    adapter = ClickHouseCloudAdapter(
        host="h",
        password="p",
        s3_staging_url="s3://test-bucket/prefix/",
    )
    parquet_file = tmp_path / "lineitem.parquet"
    parquet_file.write_bytes(b"\x00" * 10)

    benchmark = MagicMock()
    benchmark.tables = {"lineitem": [str(parquet_file)]}

    mock_conn = MagicMock()
    mock_conn.execute.return_value = [(42,)]

    env_patch = {
        "AWS_ACCESS_KEY_ID": "AKIATEST",
        "AWS_SECRET_ACCESS_KEY": "secret123",
    }

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = MagicMock()

    with (
        patch.dict(os.environ, env_patch),
        patch.dict(sys.modules, {"boto3": mock_boto3}),
        caplog.at_level(logging.WARNING),
    ):
        adapter._create_external_tables_via_s3(benchmark, mock_conn, tmp_path)

    assert any("credentials will be embedded" in r.message.lower() for r in caplog.records)


def _make_ch_adapter(**kwargs):

    mock_driver = MagicMock()
    mock_driver.__version__ = "22.8.0"
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        return ClickHouseAdapter(deployment_mode="server", host="localhost", password="pass", **kwargs)


def test_diagnostics_get_platform_info_server_mode() -> None:

    mock_driver = MagicMock()
    mock_driver.__version__ = "22.8.0"
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", password="pass")

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = ("22.8.5.29",)
    mock_cursor.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        info = adapter.get_platform_info(connection=mock_conn)

    assert info["platform_type"] == "clickhouse"
    assert info["connection_mode"] == "server"
    assert info["platform_version"] == "22.8.5.29"
    assert "client_library_version" in info


def test_diagnostics_get_platform_info_server_mode_no_connection() -> None:

    mock_driver = MagicMock()
    mock_driver.__version__ = "22.8.0"
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", password="pass")

    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        info = adapter.get_platform_info(connection=None)

    assert info["platform_type"] == "clickhouse"
    assert info["connection_mode"] == "server"


def test_diagnostics_get_platform_info_server_version_error_silenced() -> None:

    mock_driver = MagicMock()
    mock_driver.__version__ = "22.8.0"
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", password="pass")

    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = RuntimeError("connection lost")
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        info = adapter.get_platform_info(connection=mock_conn)

    assert info["platform_type"] == "clickhouse"


def test_diagnostics_get_platform_metadata_captures_settings() -> None:

    mock_driver = MagicMock()
    mock_driver.__version__ = "22.8.0"
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", password="pass")

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [
        [("22.8.5",)],
        [("max_memory_usage", "10000000000"), ("max_threads", "8")],
        [("benchbox", 1024 * 1024, 5)],
    ]

    metadata = adapter._get_platform_metadata(mock_conn)

    assert metadata["clickhouse_version"] == "22.8.5"
    assert "current_settings" in metadata
    assert "database_stats" in metadata


def test_diagnostics_get_platform_metadata_error_captured() -> None:

    mock_driver = MagicMock()
    mock_driver.__version__ = "22.8.0"
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", password="pass")

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = RuntimeError("network error")

    metadata = adapter._get_platform_metadata(mock_conn)
    assert "metadata_error" in metadata
    assert "network error" in metadata["metadata_error"]


def test_diagnostics_check_server_database_exists_true() -> None:

    mock_driver = MagicMock()
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", password="pass")
        adapter.database = "benchbox"

    mock_client = MagicMock()
    mock_client.execute.return_value = [("default",), ("benchbox",), ("system",)]

    with patch.object(adapter, "_create_admin_client", return_value=mock_client):
        result = adapter.check_server_database_exists(database="benchbox")

    assert result is True


def test_diagnostics_check_server_database_exists_false() -> None:

    mock_driver = MagicMock()
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", password="pass")
        adapter.database = "benchbox"

    mock_client = MagicMock()
    mock_client.execute.return_value = [("default",), ("system",)]

    with patch.object(adapter, "_create_admin_client", return_value=mock_client):
        result = adapter.check_server_database_exists(database="benchbox")

    assert result is False


def test_diagnostics_check_server_database_exists_exception_returns_false() -> None:

    mock_driver = MagicMock()
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", password="pass")

    with patch.object(adapter, "_create_admin_client", side_effect=ConnectionError("refused")):
        result = adapter.check_server_database_exists(database="benchbox")

    assert result is False


def _make_ch_server_adapter():
    mock_driver = MagicMock()
    mock_driver.__version__ = "22.8.0"
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter(
            deployment_mode="server",
            host="localhost",
            port=9000,
            password="pass",
            database="bench",
            max_memory_usage=1024**3,
            max_threads=4,
        )
    return adapter


def _make_ch_local_adapter():
    mock_driver = MagicMock()
    with patch.dict("sys.modules", {"clickhouse_driver": mock_driver}):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter(
            deployment_mode="local",
            data_path="/tmp/chdb",
            memory_limit="4GB",
        )
    return adapter


def test_diagnostics_drop_database_server_mode() -> None:

    adapter = _make_ch_server_adapter()
    mock_client = MagicMock()
    adapter.database = "bench"
    with patch.object(adapter, "_create_admin_client", return_value=mock_client):
        adapter.drop_database(database="bench")
    mock_client.execute.assert_called_with("DROP DATABASE IF EXISTS bench")


def test_diagnostics_drop_database_local_is_noop() -> None:

    if importlib.util.find_spec("chdb") is None:
        pytest.skip("chDB not installed")
    adapter = _make_ch_local_adapter()
    mock_client = MagicMock()
    with patch.object(adapter, "_create_admin_client", return_value=mock_client):
        adapter.drop_database()
    mock_client.execute.assert_not_called()


def test_diagnostics_drop_database_exception_raises_runtime() -> None:

    adapter = _make_ch_server_adapter()
    with patch.object(adapter, "_create_admin_client", side_effect=RuntimeError("refused")):
        with pytest.raises(RuntimeError, match="Failed to drop ClickHouse database"):
            adapter.drop_database()


def test_diagnostics_get_table_info_returns_schema_and_stats() -> None:

    adapter = _make_ch_server_adapter()
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [
        [("id", "UInt64"), ("name", "String")],
        [(1000, 512000, 128000)],
    ]

    result = adapter.get_table_info(mock_conn, "lineitem")

    assert result["columns"] == [("id", "UInt64"), ("name", "String")]
    assert result["row_count"] == 1000
    assert result["bytes_on_disk"] == 512000


def test_diagnostics_get_table_info_error_returns_dict() -> None:

    adapter = _make_ch_server_adapter()
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = RuntimeError("table missing")

    result = adapter.get_table_info(mock_conn, "orders")
    assert "error" in result
    assert "table missing" in result["error"]


def test_diagnostics_optimize_table_calls_final() -> None:

    adapter = _make_ch_server_adapter()
    mock_conn = MagicMock()
    adapter.optimize_table(mock_conn, "lineitem")
    mock_conn.execute.assert_called_with("OPTIMIZE TABLE lineitem FINAL")


def test_diagnostics_optimize_table_exception_silenced() -> None:

    adapter = _make_ch_server_adapter()
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = RuntimeError("optimize failed")
    adapter.optimize_table(mock_conn, "lineitem")


def test_build_ctas_sort_sql_returns_none_when_mode_off() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p")
    with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("off", "auto")):
        result = adapter._build_ctas_sort_sql("orders", [])
    assert result is None


def test_build_ctas_sort_sql_raises_when_mode_on() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p")
    with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("force", "ctas")):
        with pytest.raises(ValueError, match="does not support post-load sorted ingestion"):
            adapter._build_ctas_sort_sql("orders", [])


def test_build_ctas_sort_sql_raises_on_resolve_error() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p")
    with patch.object(adapter, "resolve_sorted_ingestion_strategy", side_effect=ValueError("unsupported")):
        with pytest.raises(ValueError, match="does not support post-load sorted ingestion"):
            adapter._build_ctas_sort_sql("orders", [])


def test_create_external_tables_dispatches_to_gcs() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://bucket/staging/")
    mock_result = ({"orders": 50}, 0.5, {"loading_method": "gcs_external_views"})

    benchmark, connection = MagicMock(), MagicMock()
    with patch.object(adapter, "_create_external_tables_via_gcs", return_value=mock_result) as mock_gcs:
        result = adapter.create_external_tables(benchmark, connection, Path("/tmp"))

    mock_gcs.assert_called_once_with(benchmark, connection, Path("/tmp"))
    assert result[2]["loading_method"] == "gcs_external_views"


def test_create_external_tables_via_gcs_import_error() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://b/p/")

    with patch.dict(
        "sys.modules",
        {"google": None, "google.cloud": None, "google.cloud.storage": None},
    ):
        with pytest.raises(ImportError, match="google-cloud-storage is required"):
            adapter._create_external_tables_via_gcs(MagicMock(), MagicMock(), Path("/tmp"))


def test_create_external_tables_via_gcs_parquet_locals(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://gcs-bucket/staging/")

    parquet_file = tmp_path / "lineitem.parquet"
    parquet_file.write_bytes(b"PAR1")

    benchmark = MagicMock()
    benchmark.tables = {"lineitem": [str(parquet_file)]}

    connection = MagicMock()

    def _execute(sql: str):
        if sql.startswith("SELECT COUNT(*)"):
            return [(99,)]
        return []

    connection.execute.side_effect = _execute

    _, _, _, _, sys_modules = _make_gcs_mocks()
    with patch.dict("sys.modules", sys_modules):
        stats, _, metadata = adapter._create_external_tables_via_gcs(benchmark, connection, tmp_path)

    assert stats["lineitem"] == 99
    assert metadata["loading_method"] == "gcs_external_views"
    assert metadata["gcs_staging_url"] == "gs://gcs-bucket/staging/"

    executed_sqls = [call.args[0] for call in connection.execute.call_args_list]
    view_sql = next(s for s in executed_sqls if "CREATE OR REPLACE VIEW" in s)
    assert "lineitem" in view_sql
    assert "gcs(" in view_sql
    assert "'Parquet'" in view_sql


def test_create_external_tables_via_gcs_iceberg_local(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://gcs-bucket/staging/")

    iceberg_dir = tmp_path / "orders"
    (iceberg_dir / "metadata").mkdir(parents=True)
    (iceberg_dir / "metadata" / "v1.metadata.json").write_text("{}")

    benchmark = MagicMock()
    benchmark.tables = {"orders": [str(iceberg_dir)]}

    connection = MagicMock()
    connection.execute.return_value = [(7,)]

    _, _, _, _, sys_modules = _make_gcs_mocks()
    with patch.dict("sys.modules", sys_modules):
        stats, _, metadata = adapter._create_external_tables_via_gcs(benchmark, connection, tmp_path)

    assert stats["orders"] == 7
    executed_sqls = [call.args[0] for call in connection.execute.call_args_list]
    view_sql = next(s for s in executed_sqls if "CREATE OR REPLACE VIEW" in s)
    assert "iceberg(" in view_sql
    assert "storage.googleapis.com" in view_sql


def test_create_external_tables_via_gcs_parquet_cloud_uris(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://gcs-bucket/staging/")

    connection = MagicMock()
    connection.execute.return_value = [(11,)]

    _, _, _, _, sys_modules = _make_gcs_mocks()
    with (
        patch.object(
            adapter,
            "_resolve_cloud_data_files",
            return_value={"orders": ["gs://gcs-bucket/data/orders.parquet"]},
        ),
        patch.dict("sys.modules", sys_modules),
    ):
        stats, _, metadata = adapter._create_external_tables_via_gcs(MagicMock(), connection, tmp_path)

    assert stats["orders"] == 11
    executed_sqls = [call.args[0] for call in connection.execute.call_args_list]
    view_sql = next(s for s in executed_sqls if "CREATE OR REPLACE VIEW" in s)
    assert "gcs(" in view_sql
    assert "storage.googleapis.com" in view_sql


def test_create_external_tables_via_gcs_iceberg_cloud_uris(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://gcs-bucket/staging/")

    connection = MagicMock()
    connection.execute.return_value = [(3,)]

    _, _, _, _, sys_modules = _make_gcs_mocks()
    with (
        patch.object(
            adapter,
            "_resolve_cloud_data_files",
            return_value={"orders": ["gs://gcs-bucket/data/orders/metadata/v1.json"]},
        ),
        patch.dict("sys.modules", sys_modules),
    ):
        stats, _, metadata = adapter._create_external_tables_via_gcs(MagicMock(), connection, tmp_path)

    assert stats["orders"] == 3
    executed_sqls = [call.args[0] for call in connection.execute.call_args_list]
    view_sql = next(s for s in executed_sqls if "CREATE OR REPLACE VIEW" in s)
    assert "iceberg(" in view_sql


def test_create_external_tables_via_gcs_no_sources_raises(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://gcs-bucket/staging/")

    connection = MagicMock()

    _, _, _, _, sys_modules = _make_gcs_mocks()

    with (
        patch.object(
            adapter,
            "_resolve_cloud_data_files",
            return_value={"orders": ["s3://wrong-scheme/data.parquet"]},
        ),
        patch.dict("sys.modules", sys_modules),
    ):
        with pytest.raises(ValueError, match="requires Iceberg directories or Parquet files"):
            adapter._create_external_tables_via_gcs(MagicMock(), connection, tmp_path)


def test_create_external_tables_via_gcs_credential_warning(tmp_path: Path, caplog) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://gcs-bucket/staging/")

    parquet_file = tmp_path / "lineitem.parquet"
    parquet_file.write_bytes(b"PAR1")

    benchmark = MagicMock()
    benchmark.tables = {"lineitem": [str(parquet_file)]}

    connection = MagicMock()
    connection.execute.return_value = [(5,)]

    _, _, _, _, sys_modules = _make_gcs_mocks()
    env_patch = {"GCS_HMAC_ACCESS_KEY": "hmackey", "GCS_HMAC_SECRET": "hmacsecret"}

    with (
        patch.dict(os.environ, env_patch),
        patch.dict("sys.modules", sys_modules),
        caplog.at_level(logging.WARNING),
    ):
        adapter._create_external_tables_via_gcs(benchmark, connection, tmp_path)

    assert any("credentials will be embedded" in r.message.lower() for r in caplog.records)


def test_load_data_via_gcs_uploads_and_ingests(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://gcs-bucket/staging/")

    data_file = tmp_path / "orders.csv"
    data_file.write_text("col1,col2\n1,a\n2,b\n")

    benchmark = MagicMock()
    benchmark.tables = {"orders": [str(data_file)]}

    connection = MagicMock()
    connection.execute.return_value = [(200,)]

    mock_blob, _, _, _, sys_modules = _make_gcs_mocks()
    with patch.dict("sys.modules", sys_modules):
        table_stats, total_time, metadata = adapter._load_data_via_gcs(benchmark, connection, tmp_path)

    assert "orders" in table_stats
    assert table_stats["orders"] == 200
    assert metadata["loading_method"] == "gcs_staging"
    assert metadata["gcs_staging_url"] == "gs://gcs-bucket/staging/"

    mock_blob.upload_from_filename.assert_called_once()
    execute_calls = [call.args[0] for call in connection.execute.call_args_list]
    assert any("gcs(" in s for s in execute_calls)
    assert any("CSVWithNames" in s for s in execute_calls)


def test_load_data_via_gcs_skips_empty_files(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", gcs_staging_url="gs://gcs-bucket/staging/")

    benchmark = MagicMock()
    benchmark.tables = {"orders": [str(tmp_path / "nonexistent.csv")]}

    connection = MagicMock()

    _, _, _, _, sys_modules = _make_gcs_mocks()
    with patch.dict("sys.modules", sys_modules):
        table_stats, _, metadata = adapter._load_data_via_gcs(benchmark, connection, tmp_path)

    assert table_stats["orders"] == 0
    connection.execute.assert_not_called()


def test_create_external_tables_via_s3_parquet_cloud_uris(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")

    connection = MagicMock()
    connection.execute.return_value = [(55,)]

    mock_s3_client = MagicMock()
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_s3_client

    with (
        patch.object(
            adapter,
            "_resolve_cloud_data_files",
            return_value={"orders": ["s3://bucket/data/orders.parquet"]},
        ),
        patch.dict("sys.modules", {"boto3": mock_boto3}),
    ):
        stats, _, metadata = adapter._create_external_tables_via_s3(MagicMock(), connection, tmp_path)

    assert stats["orders"] == 55
    executed_sqls = [call.args[0] for call in connection.execute.call_args_list]
    view_sql = next(s for s in executed_sqls if "CREATE OR REPLACE VIEW" in s)
    assert "s3(" in view_sql
    assert "'Parquet'" in view_sql

    mock_s3_client.upload_file.assert_not_called()


def test_create_external_tables_via_s3_parquet_cloud_uris_multiple_dirs(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")

    connection = MagicMock()
    connection.execute.return_value = [(22,)]

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = MagicMock()

    with (
        patch.object(
            adapter,
            "_resolve_cloud_data_files",
            return_value={
                "orders": [
                    "s3://bucket/data/2024/orders.parquet",
                    "s3://bucket/data/2025/orders.parquet",
                ]
            },
        ),
        patch.dict("sys.modules", {"boto3": mock_boto3}),
    ):
        stats, _, _ = adapter._create_external_tables_via_s3(MagicMock(), connection, tmp_path)

    assert stats["orders"] == 22
    executed_sqls = [call.args[0] for call in connection.execute.call_args_list]
    view_sql = next(s for s in executed_sqls if "CREATE OR REPLACE VIEW" in s)
    assert "**" in view_sql or "*.parquet" in view_sql


def test_create_external_tables_via_s3_iceberg_cloud_uris(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")

    connection = MagicMock()
    connection.execute.return_value = [(9,)]

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = MagicMock()

    with (
        patch.object(
            adapter,
            "_resolve_cloud_data_files",
            return_value={"orders": ["s3://bucket/data/orders/metadata/v1.json"]},
        ),
        patch.dict("sys.modules", {"boto3": mock_boto3}),
    ):
        stats, _, _ = adapter._create_external_tables_via_s3(MagicMock(), connection, tmp_path)

    assert stats["orders"] == 9
    executed_sqls = [call.args[0] for call in connection.execute.call_args_list]
    view_sql = next(s for s in executed_sqls if "CREATE OR REPLACE VIEW" in s)
    assert "iceberg(" in view_sql


def test_create_external_tables_via_s3_no_sources_raises(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")

    connection = MagicMock()
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = MagicMock()

    with (
        patch.object(
            adapter,
            "_resolve_cloud_data_files",
            return_value={"orders": ["gs://wrong-bucket/data.parquet"]},
        ),
        patch.dict("sys.modules", {"boto3": mock_boto3}),
    ):
        with pytest.raises(ValueError, match="requires Iceberg directories or Parquet files"):
            adapter._create_external_tables_via_s3(MagicMock(), connection, tmp_path)


def test_load_data_via_s3_skips_empty_files(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p", s3_staging_url="s3://bucket/staging/")

    benchmark = MagicMock()
    benchmark.tables = {"orders": [str(tmp_path / "nonexistent.csv")]}

    connection = MagicMock()
    mock_s3_client = MagicMock()
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_s3_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        table_stats, _, metadata = adapter._load_data_via_s3(benchmark, connection, tmp_path)

    assert table_stats["orders"] == 0
    mock_s3_client.upload_file.assert_not_called()
    connection.execute.assert_not_called()


def test_load_data_via_s3_with_s3_region(tmp_path: Path) -> None:

    adapter = ClickHouseCloudAdapter(
        host="h", password="p", s3_staging_url="s3://bucket/staging/", s3_region="eu-central-1"
    )

    data_file = tmp_path / "orders.csv"
    data_file.write_text("id\n1\n")

    benchmark = MagicMock()
    benchmark.tables = {"orders": [str(data_file)]}

    connection = MagicMock()
    connection.execute.return_value = [(5,)]

    mock_boto3 = MagicMock()
    mock_s3_client = MagicMock()
    mock_boto3.client.return_value = mock_s3_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        adapter._load_data_via_s3(benchmark, connection, tmp_path)

    boto3_call_kwargs = mock_boto3.client.call_args[1]
    assert boto3_call_kwargs.get("region_name") == "eu-central-1"


def test_platform_name_returns_clickhouse_cloud() -> None:

    adapter = ClickHouseCloudAdapter(host="h", password="p")
    assert adapter.platform_name == "ClickHouse Cloud"


def test_build_config_with_none_info() -> None:

    cred_manager = MagicMock()
    cred_manager.get_platform_credentials.return_value = {}
    with patch("benchbox.security.credentials.CredentialManager", return_value=cred_manager):
        config = _build_clickhouse_cloud_config(
            "clickhouse-cloud",
            {"host": "h", "password": "p"},
            {},
            None,
        )

    assert config.type == "clickhouse-cloud"
    assert config.name == "ClickHouse Cloud"
    assert config.driver_package == "clickhouse-connect"


def test_build_config_driver_version_from_overrides() -> None:

    cred_manager = MagicMock()
    cred_manager.get_platform_credentials.return_value = {}
    info = type("Info", (), {"display_name": "CH Cloud", "driver_package": "clickhouse-connect"})
    with patch("benchbox.security.credentials.CredentialManager", return_value=cred_manager):
        config = _build_clickhouse_cloud_config(
            "clickhouse-cloud",
            {"driver_version": "0.7.0"},
            {"driver_version": "0.8.0"},
            info,
        )

    assert config.driver_version == "0.8.0"


class TestResolveCloudDataFiles:
    def test_raises_when_resolver_returns_none(self, tmp_path) -> None:

        adapter = ClickHouseCloudAdapter(host="h", password="p")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = None

            with pytest.raises(ValueError, match="No data files found"):
                adapter._resolve_cloud_data_files(MagicMock(), tmp_path)

    def test_raises_when_tables_empty(self, tmp_path) -> None:

        adapter = ClickHouseCloudAdapter(host="h", password="p")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = MagicMock(tables={})

            with pytest.raises(ValueError, match="No data files found"):
                adapter._resolve_cloud_data_files(MagicMock(), tmp_path)

    def test_returns_path_mapping_from_resolver(self, tmp_path) -> None:

        adapter = ClickHouseCloudAdapter(host="h", password="p")
        raw_tables = {"hits": [str(tmp_path / "hits.csv")]}

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = MagicMock(tables=raw_tables)

            result = adapter._resolve_cloud_data_files(MagicMock(), tmp_path)

        assert "hits" in result
        assert all(isinstance(p, Path) for p in result["hits"])

    def test_passes_platform_name_and_table_mode_to_resolver(self, tmp_path) -> None:

        adapter = ClickHouseCloudAdapter(host="h", password="p")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = MagicMock(tables={"t": ["f"]})

            adapter._resolve_cloud_data_files(MagicMock(), tmp_path)

        _, kwargs = mock_cls.call_args
        assert kwargs.get("platform_name") == adapter.platform_name
        assert "table_mode" in kwargs


