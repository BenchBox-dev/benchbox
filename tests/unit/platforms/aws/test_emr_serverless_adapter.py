# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestEMRServerlessAdapterInitialization:
    def test_missing_s3_staging_dir_raises_error(self):
        from benchbox.platforms.aws import EMRServerlessAdapter

        with pytest.raises(ConfigurationError, match="s3_staging_dir"):
            EMRServerlessAdapter(
                application_id="00f123",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )

    def test_missing_execution_role_raises_error(self):

        from benchbox.platforms.aws import EMRServerlessAdapter

        with pytest.raises(ConfigurationError, match="execution_role_arn"):
            EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="s3://my-bucket/benchbox-data",
            )

    def test_missing_application_id_without_create_raises_error(self):

        from benchbox.platforms.aws import EMRServerlessAdapter

        with pytest.raises(ConfigurationError, match="application_id"):
            EMRServerlessAdapter(
                s3_staging_dir="s3://my-bucket/benchbox-data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )

    def test_invalid_s3_path_raises_error(self):
        from benchbox.platforms.aws import EMRServerlessAdapter

        with pytest.raises(ConfigurationError, match="Invalid S3"):
            EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="/local/path",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )

    def test_valid_configuration_with_application_id(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f12345abc67890",
                s3_staging_dir="s3://my-bucket/benchbox-data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
                region="us-west-2",
                database="my_benchmark_db",
            )

            assert adapter.s3_bucket == "my-bucket"
            assert adapter.s3_prefix == "benchbox-data"
            assert adapter.application_id == "00f12345abc67890"
            assert adapter.region == "us-west-2"
            assert adapter.database == "my_benchmark_db"

    def test_valid_configuration_with_create_application(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                s3_staging_dir="s3://my-bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
                create_application=True,
                application_name="my-app",
            )

            assert adapter.application_id is None
            assert adapter.create_application is True
            assert adapter.application_name == "my-app"

    def test_default_values(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="s3://my-bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )

            assert adapter.region == "us-east-1"
            assert adapter.database == "benchbox"
            assert adapter.release_label == "emr-7.0.0"
            assert adapter.timeout_minutes == 60
            assert adapter.create_application is False


class TestEMRServerlessTableFormat:
    def test_table_format_default_parquet(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="app-123",
                s3_staging_dir="s3://bucket/data",
                execution_role_arn="arn:aws:iam::123:role/R",
            )
            assert adapter.table_format == "parquet"

    def test_table_format_delta(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="app-123",
                s3_staging_dir="s3://bucket/data",
                execution_role_arn="arn:aws:iam::123:role/R",
                table_format="delta",
            )
            assert adapter.table_format == "delta"

    def test_table_format_from_config(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter.from_config(
                {
                    "application_id": "app-123",
                    "s3_staging_dir": "s3://bucket/data",
                    "execution_role_arn": "arn:aws:iam::123:role/R",
                    "table_format": "iceberg",
                }
            )
            assert adapter.table_format == "iceberg"


class TestEMRServerlessAdapterPlatformInfo:
    def test_get_platform_info(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f12345abc67890",
                s3_staging_dir="s3://my-bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
                region="eu-west-1",
            )

            info = adapter.get_platform_info()

            assert info["platform"] == "emr-serverless"
            assert info["display_name"] == "Amazon EMR Serverless"
            assert info["vendor"] == "AWS"
            assert info["type"] == "managed_spark"
            assert info["application_id"] == "00f12345abc67890"
            assert info["region"] == "eu-west-1"
            assert info["supports_sql"] is True
            assert info["supports_dataframe"] is True

    def test_get_dialect(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="s3://my-bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )

            assert adapter.get_target_dialect() == "spark"


class TestEMRServerlessAdapterConnection:
    def test_create_connection_success(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.boto3") as mock_boto3,
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_session = MagicMock()
            mock_emr_client = MagicMock()
            mock_emr_client.get_application.return_value = {
                "application": {
                    "applicationId": "00f123",
                    "state": "STARTED",
                    "releaseLabel": "emr-7.0.0",
                }
            }
            mock_session.client.return_value = mock_emr_client
            mock_boto3.Session.return_value = mock_session

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="s3://my-bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )

            result = adapter.create_connection()

            assert result["status"] == "connected"
            assert result["application_id"] == "00f123"
            assert result["application_state"] == "STARTED"


class TestEMRServerlessAdapterDataLoading:
    def test_load_data_existing_tables(self, tmp_path):

        source_dir = tmp_path / "test_data"
        source_dir.mkdir()

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging_instance = MagicMock()
            mock_staging_instance.tables_exist.return_value = True
            mock_staging_instance.get_table_uri.side_effect = lambda t: f"s3://bucket/tables/{t}"
            mock_staging.from_uri.return_value = mock_staging_instance

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="s3://bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )

            mock_benchmark = SimpleNamespace(tables=["lineitem", "orders"])
            result_dict, _, _ = adapter.load_data(mock_benchmark, None, source_dir)

            assert "lineitem" in result_dict
            assert "orders" in result_dict
            mock_staging_instance.upload_tables.assert_not_called()


class TestEMRServerlessJobState:
    def test_job_state_values(self):

        from benchbox.platforms.aws.emr_serverless_adapter import EMRServerlessJobState

        assert EMRServerlessJobState.SUBMITTED == "SUBMITTED"
        assert EMRServerlessJobState.PENDING == "PENDING"
        assert EMRServerlessJobState.RUNNING == "RUNNING"
        assert EMRServerlessJobState.SUCCESS == "SUCCESS"
        assert EMRServerlessJobState.FAILED == "FAILED"
        assert EMRServerlessJobState.CANCELLED == "CANCELLED"


class TestEMRServerlessAdapterRegistry:
    def test_platform_metadata_exists(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        assert "emr-serverless" in all_metadata

    def test_platform_metadata_content(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        assert "emr-serverless" in all_metadata
        emr_meta = all_metadata["emr-serverless"]

        assert emr_meta["display_name"] == "Amazon EMR Serverless"
        assert emr_meta["category"] == "cloud"
        assert emr_meta["capabilities"]["supports_sql"] is True
        assert emr_meta["capabilities"]["supports_dataframe"] is True


class TestEMRServerlessAdapterTuning:
    def test_apply_platform_optimizations(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="s3://bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )

            result = adapter.apply_platform_optimizations(MagicMock())
            assert result == []

    def test_apply_primary_keys(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="s3://bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )

            config = MagicMock()
            config.enabled = True
            result = adapter.apply_primary_keys(config)
            assert result == []

    def test_configure_for_benchmark(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.base.cloud_spark.mixins.SparkConfigOptimizer") as mock_optimizer,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_config = MagicMock()
            mock_config.to_dict.return_value = {"spark.sql.shuffle.partitions": "200"}
            mock_optimizer.for_tpch.return_value = mock_config

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="s3://bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )

            adapter.configure_for_benchmark(None, "tpch")

            assert adapter._benchmark_type == "tpch"
            mock_optimizer.for_tpch.assert_called_once()


class TestEMRServerlessAdapterCLI:
    def test_add_cli_arguments(self):

        from benchbox.platforms.aws import EMRServerlessAdapter

        parser = MagicMock()
        parser.add_argument_group.return_value = MagicMock()

        EMRServerlessAdapter.add_cli_arguments(parser)

        parser.add_argument_group.assert_called_once_with("EMR Serverless Options")


class TestEMRServerlessAdapterFromConfig:
    def test_from_config_basic(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import EMRServerlessAdapter

            config = {
                "application_id": "00f12345",
                "s3_staging_dir": "s3://test-bucket/data",
                "execution_role_arn": "arn:aws:iam::123456789012:role/EMRRole",
                "region": "ap-southeast-1",
                "database": "test_db",
            }

            adapter = EMRServerlessAdapter.from_config(config)

            assert adapter.application_id == "00f12345"
            assert adapter.region == "ap-southeast-1"
            assert adapter.database == "test_db"


class TestEMRServerlessAdapterClose:
    def test_close_logs_metrics(self):

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.aws.emr_serverless_adapter.logger") as mock_logger,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="s3://bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )
            adapter._query_count = 5
            adapter._total_vcpu_hours = 0.5
            adapter._total_memory_gb_hours = 2.0

            adapter.close()

            mock_logger.info.assert_called()


class TestLoadDataRequestedFormat:
    def test_requested_delta_registers_delta(self, tmp_path):
        ClientError = pytest.importorskip("botocore.exceptions").ClientError
        source_dir = tmp_path / "test_data"
        source_dir.mkdir()

        with (
            patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging_instance = MagicMock()
            mock_staging_instance.tables_exist.return_value = False
            mock_staging_instance.upload_tables.return_value = {}
            mock_staging.from_uri.return_value = mock_staging_instance

            from benchbox.platforms.aws import EMRServerlessAdapter

            adapter = EMRServerlessAdapter(
                application_id="00f123",
                s3_staging_dir="s3://bucket/data",
                execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
            )
            adapter.requested_table_format = "delta"
            assert adapter.table_format == "parquet"

            glue = MagicMock()
            glue.get_table.side_effect = ClientError({"Error": {"Code": "EntityNotFoundException"}}, "GetTable")
            mock_benchmark = SimpleNamespace(tables=["lineitem"])
            with (
                patch.object(adapter, "_get_glue_client", return_value=glue),
                patch.object(adapter, "_submit_job_run", return_value="job-1") as submit,
            ):
                adapter.load_data(mock_benchmark, None, source_dir)

        sql = submit.call_args[0][0]
        assert "USING DELTA" in sql


