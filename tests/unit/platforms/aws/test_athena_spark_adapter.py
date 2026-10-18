# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestAthenaSparkAdapterInitialization:
    def test_missing_workgroup_raises_error(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
        ):
            from benchbox.platforms.aws import AthenaSparkAdapter

            with pytest.raises(ConfigurationError, match="workgroup"):
                AthenaSparkAdapter(
                    s3_staging_dir="s3://my-bucket/benchbox-data",
                )

    def test_missing_s3_staging_dir_raises_error(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
        ):
            from benchbox.platforms.aws import AthenaSparkAdapter

            with pytest.raises(ConfigurationError, match="s3_staging_dir"):
                AthenaSparkAdapter(
                    workgroup="spark-workgroup",
                )

    def test_invalid_s3_path_raises_error(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
        ):
            from benchbox.platforms.aws import AthenaSparkAdapter

            with pytest.raises(ConfigurationError, match="Invalid S3"):
                AthenaSparkAdapter(
                    workgroup="spark-workgroup",
                    s3_staging_dir="/local/path",
                )

    def test_valid_configuration(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-workgroup",
                s3_staging_dir="s3://my-bucket/benchbox-data",
                region="us-west-2",
                database="my_benchmark_db",
            )

            assert adapter.workgroup == "spark-workgroup"
            assert adapter.s3_bucket == "my-bucket"
            assert adapter.s3_prefix == "benchbox-data"
            assert adapter.region == "us-west-2"
            assert adapter.database == "my_benchmark_db"

    def test_default_values(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-workgroup",
                s3_staging_dir="s3://my-bucket/data",
            )

            assert adapter.region == "us-east-1"
            assert adapter.database == "benchbox"
            assert adapter.session_idle_timeout_minutes == 15
            assert adapter.coordinator_dpu_size == 1
            assert adapter.max_concurrent_dpus == 20
            assert adapter.default_executor_dpu_size == 1
            assert adapter.timeout_minutes == 60

    def test_optional_session_configuration(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-workgroup",
                s3_staging_dir="s3://my-bucket/data",
                coordinator_dpu_size=2,
                max_concurrent_dpus=40,
                session_idle_timeout_minutes=30,
            )

            assert adapter.coordinator_dpu_size == 2
            assert adapter.max_concurrent_dpus == 40
            assert adapter.session_idle_timeout_minutes == 30


class TestAthenaSparkTableFormat:
    def test_table_format_default_parquet(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-wg",
                s3_staging_dir="s3://bucket/data",
            )
            assert adapter.table_format == "parquet"

    def test_table_format_delta(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-wg",
                s3_staging_dir="s3://bucket/data",
                table_format="delta",
            )
            assert adapter.table_format == "delta"

    def test_table_format_from_config(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter.from_config(
                {
                    "workgroup": "spark-wg",
                    "s3_staging_dir": "s3://bucket/data",
                    "table_format": "iceberg",
                }
            )
            assert adapter.table_format == "iceberg"


class TestAthenaSparkAdapterPlatformInfo:
    def test_get_platform_info(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-workgroup",
                s3_staging_dir="s3://my-bucket/data",
                region="eu-west-1",
            )

            info = adapter.get_platform_info()

            assert info["platform"] == "athena-spark"
            assert info["display_name"] == "Amazon Athena for Apache Spark"
            assert info["vendor"] == "AWS"
            assert info["type"] == "interactive_spark"
            assert info["region"] == "eu-west-1"
            assert info["workgroup"] == "spark-workgroup"
            assert info["supports_sql"] is True
            assert info["supports_dataframe"] is True
            assert info["billing_model"] == "DPU-hour"

    def test_get_dialect(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-workgroup",
                s3_staging_dir="s3://my-bucket/data",
            )

            assert adapter.get_target_dialect() == "spark"


class TestAthenaSparkAdapterConnection:
    def test_create_connection_starts_session(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3") as mock_boto3,
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_athena_client = MagicMock()
            mock_athena_client.start_session.return_value = {
                "SessionId": "session-123",
                "State": "CREATING",
            }
            mock_athena_client.get_session_status.return_value = {"Status": {"State": "IDLE"}}
            mock_boto3.client.return_value = mock_athena_client

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-workgroup",
                s3_staging_dir="s3://my-bucket/data",
            )

            result = adapter.create_connection()

            assert result["status"] == "connected"
            assert result["session_id"] == "session-123"
            mock_athena_client.start_session.assert_called_once()

    def test_create_connection_handles_error(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3") as mock_boto3,
            patch("benchbox.platforms.aws.athena_spark_adapter.ClientError", Exception),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_athena_client = MagicMock()
            error_response = {"Error": {"Code": "InvalidRequestException", "Message": "Not a Spark workgroup"}}
            mock_athena_client.start_session.side_effect = Exception("Invalid workgroup")
            mock_athena_client.start_session.side_effect.response = error_response
            mock_boto3.client.return_value = mock_athena_client

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="not-spark-workgroup",
                s3_staging_dir="s3://my-bucket/data",
            )

            with pytest.raises(Exception):
                adapter.create_connection()


class TestAthenaSparkSessionState:
    def test_session_state_values(self):

        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkSessionState

        assert AthenaSparkSessionState.CREATING == "CREATING"
        assert AthenaSparkSessionState.IDLE == "IDLE"
        assert AthenaSparkSessionState.BUSY == "BUSY"
        assert AthenaSparkSessionState.TERMINATED == "TERMINATED"
        assert AthenaSparkSessionState.FAILED == "FAILED"

    def test_ready_states(self):

        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkSessionState

        assert AthenaSparkSessionState.IDLE in AthenaSparkSessionState.READY_STATES
        assert AthenaSparkSessionState.CREATED in AthenaSparkSessionState.READY_STATES
        assert AthenaSparkSessionState.BUSY not in AthenaSparkSessionState.READY_STATES

    def test_terminal_states(self):

        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkSessionState

        assert AthenaSparkSessionState.TERMINATED in AthenaSparkSessionState.TERMINAL_STATES
        assert AthenaSparkSessionState.FAILED in AthenaSparkSessionState.TERMINAL_STATES
        assert AthenaSparkSessionState.IDLE not in AthenaSparkSessionState.TERMINAL_STATES


class TestAthenaSparkCalculationState:
    def test_calculation_state_values(self):

        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkCalculationState

        assert AthenaSparkCalculationState.CREATING == "CREATING"
        assert AthenaSparkCalculationState.RUNNING == "RUNNING"
        assert AthenaSparkCalculationState.COMPLETED == "COMPLETED"
        assert AthenaSparkCalculationState.FAILED == "FAILED"
        assert AthenaSparkCalculationState.CANCELED == "CANCELED"

    def test_terminal_states(self):

        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkCalculationState

        assert AthenaSparkCalculationState.COMPLETED in AthenaSparkCalculationState.TERMINAL_STATES
        assert AthenaSparkCalculationState.FAILED in AthenaSparkCalculationState.TERMINAL_STATES
        assert AthenaSparkCalculationState.CANCELED in AthenaSparkCalculationState.TERMINAL_STATES
        assert AthenaSparkCalculationState.RUNNING not in AthenaSparkCalculationState.TERMINAL_STATES


class TestAthenaSparkAdapterRegistry:
    def test_platform_metadata_exists(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        assert "athena-spark" in all_metadata

    def test_platform_metadata_content(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        assert "athena-spark" in all_metadata
        spark_meta = all_metadata["athena-spark"]

        assert spark_meta["display_name"] == "Amazon Athena for Apache Spark"
        assert spark_meta["category"] == "cloud"
        assert spark_meta["capabilities"]["supports_sql"] is True
        assert spark_meta["capabilities"]["supports_dataframe"] is True
        assert "interactive" in spark_meta["supports"]


class TestAthenaSparkAdapterTuning:
    def test_apply_platform_optimizations(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-workgroup",
                s3_staging_dir="s3://my-bucket/data",
            )

            result = adapter.apply_platform_optimizations(MagicMock())
            assert result == []

    def test_apply_primary_keys(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-workgroup",
                s3_staging_dir="s3://my-bucket/data",
            )

            config = MagicMock()
            config.enabled = True
            result = adapter.apply_primary_keys(config)
            assert result == []

    def test_configure_for_benchmark(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.base.cloud_spark.mixins.SparkConfigOptimizer") as mock_optimizer,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_config = MagicMock()
            mock_config.to_dict.return_value = {"spark.sql.shuffle.partitions": "200"}
            mock_optimizer.for_tpch.return_value = mock_config

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-workgroup",
                s3_staging_dir="s3://my-bucket/data",
            )

            adapter.configure_for_benchmark(None, "tpch")

            assert adapter._benchmark_type == "tpch"
            mock_optimizer.for_tpch.assert_called_once()


class TestAthenaSparkAdapterCLI:
    def test_add_cli_arguments(self):

        from benchbox.platforms.aws import AthenaSparkAdapter

        parser = MagicMock()
        parser.add_argument_group.return_value = MagicMock()

        AthenaSparkAdapter.add_cli_arguments(parser)

        parser.add_argument_group.assert_called_once_with("Athena Spark Options")


class TestAthenaSparkAdapterFromConfig:
    def test_from_config_basic(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.aws import AthenaSparkAdapter

            config = {
                "workgroup": "test-spark-workgroup",
                "s3_staging_dir": "s3://test-bucket/data",
                "region": "ap-southeast-1",
                "database": "test_db",
                "coordinator_dpu_size": 2,
            }

            adapter = AthenaSparkAdapter.from_config(config)

            assert adapter.workgroup == "test-spark-workgroup"
            assert adapter.region == "ap-southeast-1"
            assert adapter.database == "test_db"
            assert adapter.coordinator_dpu_size == 2


class TestAthenaSparkAdapterClose:
    def test_close_terminates_session(self):

        with (
            patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
            patch("benchbox.platforms.aws.athena_spark_adapter.boto3") as mock_boto3,
            patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_athena_client = MagicMock()
            mock_boto3.client.return_value = mock_athena_client

            from benchbox.platforms.aws import AthenaSparkAdapter

            adapter = AthenaSparkAdapter(
                workgroup="spark-workgroup",
                s3_staging_dir="s3://my-bucket/data",
            )
            adapter._session_id = "session-456"
            adapter._query_count = 5
            adapter._total_execution_time_seconds = 120.5

            adapter.close()

            mock_athena_client.terminate_session.assert_called_once_with(SessionId="session-456")
            assert adapter._session_id is None
