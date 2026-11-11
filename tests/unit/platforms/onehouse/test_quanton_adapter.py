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


def _benchmark(*tables: str) -> SimpleNamespace:
    return SimpleNamespace(get_table_names=lambda: list(tables))


class TestQuantonAdapterInitialization:
    def test_missing_api_key_raises_error(self):

        with (
            patch.dict("os.environ", {}, clear=True),
        ):
            from benchbox.platforms.onehouse import QuantonAdapter

            with pytest.raises(ConfigurationError, match="API key"):
                QuantonAdapter(
                    s3_staging_dir="s3://my-bucket/benchbox-data",
                )

    def test_missing_s3_staging_dir_raises_error(self):
        from benchbox.platforms.onehouse import QuantonAdapter

        with pytest.raises(ConfigurationError, match="s3_staging_dir"):
            QuantonAdapter(
                api_key="test-api-key",
            )

    def test_invalid_s3_path_raises_error(self):
        from benchbox.platforms.onehouse import QuantonAdapter

        with pytest.raises(ConfigurationError, match="Invalid S3"):
            QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="/local/path",
            )

    def test_invalid_table_format_raises_error(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            with pytest.raises(ConfigurationError, match="Invalid table_format"):
                QuantonAdapter(
                    api_key="test-api-key",
                    s3_staging_dir="s3://my-bucket/data",
                    table_format="parquet",
                )

    def test_valid_configuration(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://my-bucket/benchbox-data",
                region="us-west-2",
                database="my_benchmark_db",
                table_format="iceberg",
                cluster_size="medium",
            )

            assert adapter.s3_bucket == "my-bucket"
            assert adapter.s3_prefix == "benchbox-data"
            assert adapter.region == "us-west-2"
            assert adapter.database == "my_benchmark_db"
            assert adapter.table_format_str == "iceberg"
            assert adapter.cluster_size == "medium"

    def test_api_key_from_environment(self):

        with (
            patch.dict("os.environ", {"ONEHOUSE_API_KEY": "env-api-key"}, clear=False),
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                s3_staging_dir="s3://my-bucket/data",
            )

            assert adapter.api_key == "env-api-key"

    def test_default_values(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://my-bucket/data",
            )

            assert adapter.region == "us-east-1"
            assert adapter.database == "benchbox"
            assert adapter.table_format_str == "iceberg"
            assert adapter.cluster_size == "small"
            assert adapter.timeout_minutes == 60


class TestQuantonAdapterPlatformInfo:
    def test_get_platform_info(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://my-bucket/data",
                region="eu-west-1",
                table_format="hudi",
            )

            info = adapter.get_platform_info()

            assert info["platform"] == "quanton"
            assert info["display_name"] == "Onehouse Quanton"
            assert info["vendor"] == "Onehouse"
            assert info["type"] == "managed_spark"
            assert info["region"] == "eu-west-1"
            assert info["table_format"] == "hudi"
            assert info["supports_sql"] is True
            assert info["supports_dataframe"] is True
            assert info["supports_hudi"] is True
            assert info["supports_iceberg"] is True
            assert info["supports_delta"] is True

    def test_get_dialect(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://my-bucket/data",
            )

            assert adapter.get_target_dialect() == "spark"


class TestQuantonAdapterConnection:
    def test_create_connection_success(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()
            mock_client.test_connection.return_value = True
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://my-bucket/data",
                region="us-west-2",
            )

            result = adapter.create_connection()

            assert result["status"] == "connected"
            assert result["region"] == "us-west-2"

    def test_create_connection_failure(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()
            mock_client.test_connection.return_value = False
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://my-bucket/data",
            )

            with pytest.raises(ConfigurationError, match="Failed to connect"):
                adapter.create_connection()


class TestQuantonAdapterDataLoading:
    def test_load_data_existing_tables(self, tmp_path):

        source_dir = tmp_path / "test_data"
        source_dir.mkdir()

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging_instance = MagicMock()
            mock_staging_instance.tables_exist.return_value = True
            mock_staging_instance.get_table_uri.side_effect = lambda t: f"s3://bucket/tables/{t}"
            mock_staging.from_uri.return_value = mock_staging_instance

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            stats, _, metadata = adapter.load_data(_benchmark("lineitem", "orders"), None, source_dir)

            assert "lineitem" in stats
            assert "orders" in stats
            assert metadata is not None
            assert "lineitem" in metadata["table_uris"]
            mock_staging_instance.upload_tables.assert_not_called()


class TestQuantonAdapterRegistry:
    def test_platform_metadata_exists(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        assert "quanton" in all_metadata

    def test_platform_metadata_content(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        assert "quanton" in all_metadata
        quanton_meta = all_metadata["quanton"]

        assert quanton_meta["display_name"] == "Onehouse Quanton"
        assert quanton_meta["category"] == "cloud"
        assert quanton_meta["capabilities"]["supports_sql"] is True
        assert quanton_meta["capabilities"]["supports_dataframe"] is True


class TestQuantonAdapterTuning:
    def test_apply_platform_optimizations(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            result = adapter.apply_platform_optimizations(MagicMock())
            assert result == []

    def test_apply_primary_keys(self):
        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            config = MagicMock()
            config.enabled = True
            result = adapter.apply_primary_keys(config)
            assert result == []

    def test_configure_for_benchmark(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.base.cloud_spark.mixins.SparkConfigOptimizer") as mock_optimizer,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_config = MagicMock()
            mock_config.to_dict.return_value = {"spark.sql.shuffle.partitions": "200"}
            mock_optimizer.for_tpch.return_value = mock_config

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            adapter.configure_for_benchmark(None, "tpch")

            assert adapter._benchmark_type == "tpch"
            mock_optimizer.for_tpch.assert_called_once()


class TestQuantonAdapterCLI:
    def test_add_cli_arguments(self):

        from benchbox.platforms.onehouse import QuantonAdapter

        parser = MagicMock()
        parser.add_argument_group.return_value = MagicMock()

        QuantonAdapter.add_cli_arguments(parser)

        parser.add_argument_group.assert_called_once_with("Onehouse Quanton Options")


class TestQuantonAdapterFromConfig:
    def test_from_config_basic(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            config = {
                "api_key": "test-api-key",
                "s3_staging_dir": "s3://test-bucket/data",
                "region": "ap-southeast-1",
                "database": "test_db",
                "table_format": "delta",
                "cluster_size": "large",
            }

            adapter = QuantonAdapter.from_config(config)

            assert adapter.region == "ap-southeast-1"
            assert adapter.database == "test_db"
            assert adapter.table_format_str == "delta"
            assert adapter.cluster_size == "large"


class TestQuantonAdapterTableFormats:
    def test_iceberg_format(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
                table_format="iceberg",
            )

            assert adapter.table_format_str == "iceberg"

    def test_hudi_format(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
                table_format="hudi",
            )

            assert adapter.table_format_str == "hudi"

    def test_delta_format(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
                table_format="delta",
            )

            assert adapter.table_format_str == "delta"


class TestQuantonAdapterClose:
    def test_close_logs_metrics(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.logger") as mock_logger,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )
            adapter._query_count = 5
            adapter._total_job_duration_seconds = 123.5

            adapter.close()

            mock_logger.info.assert_called()

    def test_close_handles_client_error(self):
        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()
            mock_client.close.side_effect = Exception("Client error")
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            adapter.close()


class TestQuantonAdapterTestConnection:
    def test_test_connection_success(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()
            mock_client.test_connection.return_value = True
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            assert adapter.test_connection() is True

    def test_test_connection_failure(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()
            mock_client.test_connection.side_effect = Exception("Network error")
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            assert adapter.test_connection() is False


class TestQuantonAdapterCreateSchema:
    def test_create_schema_success(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            adapter.database = "test_schema"
            adapter.create_schema(_benchmark(), None)

            mock_client.create_database.assert_called_once()
            call_args = mock_client.create_database.call_args
            assert call_args[0][0] == "test_schema"

    def test_create_schema_uses_default_database(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
                database="my_db",
            )

            adapter.create_schema(_benchmark(), None)

            call_args = mock_client.create_database.call_args
            assert call_args[0][0] == "my_db"

    def test_create_schema_already_exists(self):
        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()
            mock_client.create_database.side_effect = Exception("Database already exists")
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            adapter.database = "existing_db"
            adapter.create_schema(_benchmark(), None)

    def test_create_schema_other_error(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
            patch("benchbox.platforms.onehouse.quanton_adapter.logger") as mock_logger,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()
            mock_client.create_database.side_effect = Exception("Network error")
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            adapter.database = "new_db"
            adapter.create_schema(_benchmark(), None)

            mock_logger.warning.assert_called()


class TestQuantonAdapterDDLGeneration:
    def test_generate_iceberg_ddl(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
                table_format="iceberg",
            )

            ddl = adapter._generate_create_table_ddl("test_table", "s3://bucket/tables/test_table")

            assert "USING ICEBERG" in ddl
            assert "test_table" in ddl
            assert "s3://bucket/tables/test_table" in ddl

    def test_generate_hudi_ddl_with_record_key(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
                table_format="hudi",
                record_key="order_key",
                precombine_field="order_date",
                hudi_table_type="MERGE_ON_READ",
            )

            ddl = adapter._generate_create_table_ddl("orders", "s3://bucket/tables/orders")

            assert "USING HUDI" in ddl
            assert "hoodie.table.name" in ddl
            assert "order_key" in ddl
            assert "order_date" in ddl
            assert "MERGE_ON_READ" in ddl

    def test_generate_hudi_ddl_without_record_key(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.logger") as mock_logger,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
                table_format="hudi",
            )

            ddl = adapter._generate_create_table_ddl("lineitem", "s3://bucket/tables/lineitem")

            assert "USING HUDI" in ddl
            assert "lineitem_key" in ddl
            mock_logger.warning.assert_called()

    def test_generate_delta_ddl(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
                table_format="delta",
            )

            ddl = adapter._generate_create_table_ddl("test_table", "s3://bucket/tables/test_table")

            assert "USING DELTA" in ddl
            assert "test_table" in ddl


class TestQuantonAdapterExecuteQuery:
    def test_execute_query_success(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()

            mock_client.submit_sql_job.return_value = "job-123"
            mock_job_result = MagicMock()
            mock_job_result.duration_seconds = 5.5
            mock_client.wait_for_job.return_value = mock_job_result
            mock_client.get_job_results.return_value = [{"id": 1}, {"id": 2}]

            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            result = adapter.execute_query(None, "SELECT * FROM test", "q1")

            assert result["status"] == "SUCCESS"
            assert len(result["results"]) == 2
            assert adapter._query_count == 1
            assert adapter._total_job_duration_seconds == 5.5

    def test_execute_query_falls_back_to_s3(self):
        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.boto3") as mock_boto3,
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_client = MagicMock()

            mock_client.submit_sql_job.return_value = "job-123"
            mock_job_result = MagicMock()
            mock_job_result.duration_seconds = 3.0
            mock_client.wait_for_job.return_value = mock_job_result
            mock_client.get_job_results.side_effect = Exception("API error")

            mock_client_class.return_value = mock_client

            mock_s3_client = MagicMock()
            mock_s3_client.list_objects_v2.return_value = {"Contents": [{"Key": "results/data.json"}]}
            mock_s3_client.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"id": 1}\n{"id": 2}')}
            mock_session = MagicMock()
            mock_session.client.return_value = mock_s3_client
            mock_boto3.Session.return_value = mock_session

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            result = adapter.execute_query(None, "SELECT * FROM test", "q1")

            assert result["status"] == "SUCCESS"
            assert len(result["results"]) == 2


class TestQuantonAdapterLoadDataFull:
    def test_load_data_uploads_and_creates_tables(self, tmp_path):

        source_dir = tmp_path / "test_data"
        source_dir.mkdir()
        (source_dir / "lineitem.parquet").write_bytes(b"fake parquet")

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
        ):
            mock_staging_instance = MagicMock()
            mock_staging_instance.tables_exist.return_value = False
            mock_staging_instance.get_table_uri.side_effect = lambda t: f"s3://bucket/tables/{t}"
            mock_staging.from_uri.return_value = mock_staging_instance

            mock_client = MagicMock()
            mock_client.submit_sql_job.return_value = "ddl-job"
            mock_client.wait_for_job.return_value = MagicMock()
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            stats, _, metadata = adapter.load_data(_benchmark("lineitem"), None, source_dir)

            assert "lineitem" in stats
            assert metadata is not None
            assert "lineitem" in metadata["table_uris"]
            mock_staging_instance.upload_tables.assert_called_once()

    def test_load_data_source_not_found(self, tmp_path):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            with pytest.raises(ConfigurationError, match="not found"):
                adapter.load_data(_benchmark("lineitem"), None, tmp_path / "nonexistent")

    def test_load_data_table_creation_error(self, tmp_path):

        source_dir = tmp_path / "test_data"
        source_dir.mkdir()

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.OnehouseClient") as mock_client_class,
            patch("benchbox.platforms.onehouse.quanton_adapter.logger") as mock_logger,
        ):
            mock_staging_instance = MagicMock()
            mock_staging_instance.tables_exist.return_value = False
            mock_staging.from_uri.return_value = mock_staging_instance

            mock_client = MagicMock()
            mock_client.submit_sql_job.side_effect = Exception("DDL error")
            mock_client_class.return_value = mock_client

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            stats, _, metadata = adapter.load_data(_benchmark("lineitem"), None, source_dir)

            assert "lineitem" in stats
            assert metadata is not None
            assert "lineitem" in metadata["table_uris"]
            mock_logger.warning.assert_called()


class TestQuantonAdapterApplyTuning:
    def test_apply_tuning_with_scale_factor(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            config = MagicMock()
            config.scale_factor = 10.0
            config.primary_keys = None
            config.foreign_keys = None
            config.platform = None

            adapter.apply_tuning_configuration(config)

            assert adapter._scale_factor == 10.0

    def test_apply_tuning_with_all_options(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            config = MagicMock()
            config.scale_factor = 1.0
            config.primary_keys = MagicMock(enabled=True)
            config.foreign_keys = MagicMock(enabled=True)
            config.platform = MagicMock()

            result = adapter.apply_tuning_configuration(config)

            assert "primary_keys" in result
            assert "foreign_keys" in result
            assert "platform_optimizations" in result


class TestQuantonAdapterFromConfigExtended:
    def test_from_config_with_hudi_options(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            config = {
                "api_key": "test-api-key",
                "s3_staging_dir": "s3://bucket/data",
                "table_format": "hudi",
                "record_key": "order_key",
                "precombine_field": "order_date",
                "hudi_table_type": "MERGE_ON_READ",
                "force_recreate": True,
                "show_query_plans": True,
            }

            adapter = QuantonAdapter.from_config(config)

            assert adapter.table_format_str == "hudi"
            assert adapter.record_key == "order_key"
            assert adapter.precombine_field == "order_date"
            assert adapter.hudi_table_type == "MERGE_ON_READ"

    def test_from_config_with_onehouse_api_key(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.onehouse import QuantonAdapter

            config = {
                "onehouse_api_key": "aliased-api-key",
                "s3_staging_dir": "s3://bucket/data",
            }

            adapter = QuantonAdapter.from_config(config)

            assert adapter.api_key == "aliased-api-key"


class TestQuantonAdapterStagingInitialization:
    def test_staging_initialization_failure_warning(self):

        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.onehouse.quanton_adapter.logger") as mock_logger,
        ):
            mock_staging.from_uri.side_effect = Exception("S3 init error")

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
            )

            assert adapter is not None
            mock_logger.warning.assert_called()


class TestQuantonAdapterS3Client:
    def test_get_s3_client_creates_client(self):
        with (
            patch("benchbox.platforms.onehouse.quanton_adapter.boto3") as mock_boto3,
            patch("benchbox.platforms.onehouse.quanton_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_s3_client = MagicMock()
            mock_session = MagicMock()
            mock_session.client.return_value = mock_s3_client
            mock_boto3.Session.return_value = mock_session

            from benchbox.platforms.onehouse import QuantonAdapter

            adapter = QuantonAdapter(
                api_key="test-api-key",
                s3_staging_dir="s3://bucket/data",
                region="eu-west-1",
            )

            client = adapter._get_s3_client()

            assert client is mock_s3_client
            mock_boto3.Session.assert_called_with(region_name="eu-west-1")


