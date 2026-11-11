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


class TestDataprocAdapterInitialization:
    def test_missing_project_id_raises_error(self):

        from benchbox.platforms.gcp import DataprocAdapter

        with pytest.raises(ConfigurationError, match="project_id"):
            DataprocAdapter(
                gcs_staging_dir="gs://my-bucket/benchbox-data",
            )

    def test_missing_gcs_staging_dir_raises_error(self):

        from benchbox.platforms.gcp import DataprocAdapter

        with pytest.raises(ConfigurationError, match="gcs_staging_dir"):
            DataprocAdapter(
                project_id="my-project",
            )

    def test_invalid_gcs_path_raises_error(self):

        from benchbox.platforms.gcp import DataprocAdapter

        with pytest.raises(ConfigurationError, match="Invalid GCS"):
            DataprocAdapter(
                project_id="my-project",
                gcs_staging_dir="/local/path",
            )

    def test_valid_configuration(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="my-project",
                region="us-west1",
                cluster_name="my-cluster",
                gcs_staging_dir="gs://my-bucket/benchbox-data",
                database="my_benchmark_db",
            )

            assert adapter.gcs_bucket == "my-bucket"
            assert adapter.gcs_prefix == "benchbox-data"
            assert adapter.project_id == "my-project"
            assert adapter.region == "us-west1"
            assert adapter.cluster_name == "my-cluster"
            assert adapter.database == "my_benchmark_db"

    def test_default_values(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="my-project",
                gcs_staging_dir="gs://my-bucket/data",
            )

            assert adapter.region == "us-central1"
            assert adapter.database == "benchbox"
            assert adapter.master_machine_type == "n2-standard-4"
            assert adapter.worker_machine_type == "n2-standard-4"
            assert adapter.num_workers == 2
            assert adapter.use_preemptible_workers is False
            assert adapter.timeout_minutes == 60
            assert adapter.create_ephemeral_cluster is False


class TestDataprocTableFormat:
    def test_table_format_default_parquet(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="proj",
                gcs_staging_dir="gs://bucket/data",
            )
            assert adapter.table_format == "parquet"

    def test_table_format_delta(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="proj",
                gcs_staging_dir="gs://bucket/data",
                table_format="delta",
            )
            assert adapter.table_format == "delta"

    def test_table_format_from_config(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter.from_config(
                {
                    "project_id": "proj",
                    "gcs_staging_dir": "gs://bucket/data",
                    "table_format": "iceberg",
                }
            )
            assert adapter.table_format == "iceberg"


class TestDataprocAdapterPlatformInfo:
    def test_get_platform_info(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="my-project",
                region="europe-west1",
                cluster_name="test-cluster",
                gcs_staging_dir="gs://my-bucket/data",
            )

            info = adapter.get_platform_info()

            assert info["platform"] == "dataproc"
            assert info["display_name"] == "Google Cloud Dataproc"
            assert info["vendor"] == "Google Cloud"
            assert info["type"] == "managed_spark"
            assert info["project_id"] == "my-project"
            assert info["region"] == "europe-west1"
            assert info["cluster_name"] == "test-cluster"
            assert info["supports_sql"] is True
            assert info["supports_dataframe"] is True

    def test_get_dialect(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="my-project",
                gcs_staging_dir="gs://my-bucket/data",
            )

            assert adapter.get_target_dialect() == "spark"


class TestDataprocAdapterConnection:
    def test_create_connection_success(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.dataproc_v1") as mock_dataproc,
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_cluster_client = MagicMock()
            mock_cluster = MagicMock()
            mock_cluster.status.state.name = "RUNNING"
            mock_cluster.config.worker_config.num_instances = 4
            mock_cluster_client.get_cluster.return_value = mock_cluster
            mock_dataproc.ClusterControllerClient.return_value = mock_cluster_client

            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="my-project",
                cluster_name="my-cluster",
                gcs_staging_dir="gs://my-bucket/data",
            )

            result = adapter.create_connection()

            assert result["status"] == "connected"
            assert result["cluster_name"] == "my-cluster"
            assert result["cluster_state"] == "RUNNING"


class TestDataprocAdapterDataLoading:
    def test_load_data_existing_tables(self, tmp_path):

        source_dir = tmp_path / "test_data"
        source_dir.mkdir()

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging_instance = MagicMock()
            mock_staging_instance.tables_exist.return_value = True
            mock_staging_instance.get_table_uri.side_effect = lambda t: f"gs://bucket/tables/{t}"
            mock_staging.from_uri.return_value = mock_staging_instance

            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="my-project",
                gcs_staging_dir="gs://bucket/data",
            )

            mock_benchmark = SimpleNamespace(tables=["lineitem", "orders"])
            with patch.object(adapter, "_ensure_cluster_exists"):
                result_dict, _, _ = adapter.load_data(mock_benchmark, None, source_dir)

            assert "lineitem" in result_dict
            assert "orders" in result_dict
            mock_staging_instance.upload_tables.assert_not_called()


class TestDataprocJobState:
    def test_job_state_values(self):

        from benchbox.platforms.gcp.dataproc_adapter import DataprocJobState

        assert DataprocJobState.PENDING == "PENDING"
        assert DataprocJobState.RUNNING == "RUNNING"
        assert DataprocJobState.DONE == "DONE"
        assert DataprocJobState.ERROR == "ERROR"
        assert DataprocJobState.CANCELLED == "CANCELLED"


class TestDataprocAdapterRegistry:
    def test_platform_metadata_exists(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        assert "dataproc" in all_metadata

    def test_platform_metadata_content(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        assert "dataproc" in all_metadata
        dataproc_meta = all_metadata["dataproc"]

        assert dataproc_meta["display_name"] == "Google Cloud Dataproc"
        assert dataproc_meta["category"] == "cloud"
        assert dataproc_meta["capabilities"]["supports_sql"] is True
        assert dataproc_meta["capabilities"]["supports_dataframe"] is True


class TestDataprocAdapterTuning:
    def test_apply_platform_optimizations(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="my-project",
                gcs_staging_dir="gs://bucket/data",
            )

            result = adapter.apply_platform_optimizations(MagicMock())
            assert result == []

    def test_apply_primary_keys(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="my-project",
                gcs_staging_dir="gs://bucket/data",
            )

            config = MagicMock()
            config.enabled = True
            result = adapter.apply_primary_keys(config)
            assert result == []

    def test_configure_for_benchmark(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.base.cloud_spark.mixins.SparkConfigOptimizer") as mock_optimizer,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_config = MagicMock()
            mock_config.to_dict.return_value = {"spark.sql.shuffle.partitions": "200"}
            mock_optimizer.for_tpch.return_value = mock_config

            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="my-project",
                gcs_staging_dir="gs://bucket/data",
            )

            adapter.configure_for_benchmark(None, "tpch")

            assert adapter._benchmark_type == "tpch"
            mock_optimizer.for_tpch.assert_called_once()


class TestDataprocAdapterCLI:
    def test_add_cli_arguments(self):

        from benchbox.platforms.gcp import DataprocAdapter

        parser = MagicMock()
        parser.add_argument_group.return_value = MagicMock()

        DataprocAdapter.add_cli_arguments(parser)

        parser.add_argument_group.assert_called_once_with("Dataproc Options")


class TestDataprocAdapterFromConfig:
    def test_from_config_basic(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.gcp import DataprocAdapter

            config = {
                "project_id": "test-project",
                "region": "asia-east1",
                "cluster_name": "test-cluster",
                "gcs_staging_dir": "gs://test-bucket/data",
                "database": "test_db",
            }

            adapter = DataprocAdapter.from_config(config)

            assert adapter.project_id == "test-project"
            assert adapter.region == "asia-east1"
            assert adapter.cluster_name == "test-cluster"
            assert adapter.database == "test_db"

    def test_from_config_generates_cluster_name(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.gcp import DataprocAdapter

            config = {
                "project_id": "test-project",
                "gcs_staging_dir": "gs://test-bucket/data",
                "benchmark": "tpch",
                "scale_factor": 10,
            }

            adapter = DataprocAdapter.from_config(config)

            assert adapter.cluster_name.startswith("benchbox-tpch-sf10-")


class TestDataprocAdapterClose:
    def test_close_logs_metrics(self):

        with (
            patch("benchbox.platforms.gcp.dataproc_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.gcp.dataproc_adapter.logger") as mock_logger,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.gcp import DataprocAdapter

            adapter = DataprocAdapter(
                project_id="my-project",
                gcs_staging_dir="gs://bucket/data",
            )
            adapter._query_count = 5
            adapter._total_job_time_seconds = 120.5

            adapter.close()

            mock_logger.info.assert_called()


