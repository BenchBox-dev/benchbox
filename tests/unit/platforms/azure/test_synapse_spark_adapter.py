# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_synapse_spark_load_data_propagates_table_creation_failure(tmp_path):
    with (
        patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
        patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
        patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
        patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
    ):
        staging = MagicMock()
        staging.tables_exist.return_value = True
        mock_staging.from_uri.return_value = staging

        from benchbox.platforms.azure import SynapseSparkAdapter

        adapter = SynapseSparkAdapter(
            workspace_name="workspace",
            spark_pool_name="pool",
            storage_account="account",
            storage_container="container",
        )
        adapter._execute_statement = MagicMock(side_effect=RuntimeError("table create failed"))
        benchmark = MagicMock(tables={"orders": None})

        with pytest.raises(RuntimeError, match="Failed to create Synapse Spark table orders"):
            adapter.load_data(benchmark, None, tmp_path)


class TestSynapseSparkAdapterInitialization:
    def test_missing_workspace_name_raises_error(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import SynapseSparkAdapter

            with pytest.raises(ConfigurationError, match="workspace_name"):
                SynapseSparkAdapter(
                    spark_pool_name="sparkpool1",
                    storage_account="mystorageaccount",
                    storage_container="benchbox",
                )

    def test_missing_spark_pool_raises_error(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import SynapseSparkAdapter

            with pytest.raises(ConfigurationError, match="spark_pool_name"):
                SynapseSparkAdapter(
                    workspace_name="my-synapse-workspace",
                    storage_account="mystorageaccount",
                    storage_container="benchbox",
                )

    def test_missing_storage_account_raises_error(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import SynapseSparkAdapter

            with pytest.raises(ConfigurationError, match="storage_account"):
                SynapseSparkAdapter(
                    workspace_name="my-synapse-workspace",
                    spark_pool_name="sparkpool1",
                    storage_container="benchbox",
                )

    def test_missing_storage_container_raises_error(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import SynapseSparkAdapter

            with pytest.raises(ConfigurationError, match="storage_container"):
                SynapseSparkAdapter(
                    workspace_name="my-synapse-workspace",
                    spark_pool_name="sparkpool1",
                    storage_account="mystorageaccount",
                )

    def test_valid_configuration(self):
        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="my-synapse-workspace",
                spark_pool_name="sparkpool1",
                storage_account="mystorageaccount",
                storage_container="benchbox",
                tenant_id="tenant-123",
            )

            assert adapter.workspace_name == "my-synapse-workspace"
            assert adapter.spark_pool_name == "sparkpool1"
            assert adapter.storage_account == "mystorageaccount"
            assert adapter.storage_container == "benchbox"
            assert adapter.tenant_id == "tenant-123"

    def test_default_values(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="my-synapse-workspace",
                spark_pool_name="sparkpool1",
                storage_account="mystorageaccount",
                storage_container="benchbox",
            )

            assert adapter.timeout_minutes == 60
            assert adapter.storage_path == "benchbox"
            assert adapter.tenant_id is None

    def test_derived_livy_endpoint(self):
        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="my-workspace",
                spark_pool_name="my-pool",
                storage_account="mystorageaccount",
                storage_container="benchbox",
            )

            assert "my-workspace" in adapter.livy_endpoint
            assert "my-pool" in adapter.livy_endpoint
            assert "dev.azuresynapse.net" in adapter.livy_endpoint

    def test_adls_uri_format(self):
        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="my-workspace",
                spark_pool_name="my-pool",
                storage_account="mystorageaccount",
                storage_container="mycontainer",
                storage_path="data/benchbox",
            )

            assert adapter.adls_uri == "abfss://mycontainer@mystorageaccount.dfs.core.windows.net/data/benchbox"


class TestSynapseSparkAdapterPlatformInfo:
    def test_get_platform_info(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="test-workspace",
                spark_pool_name="test-pool",
                storage_account="teststorage",
                storage_container="testcontainer",
            )

            info = adapter.get_platform_info()

            assert info["platform"] == "synapse-spark"
            assert info["display_name"] == "Azure Synapse Analytics Spark"
            assert info["vendor"] == "Microsoft"
            assert info["type"] == "managed_spark"
            assert info["workspace_name"] == "test-workspace"
            assert info["spark_pool"] == "test-pool"
            assert info["storage_account"] == "teststorage"
            assert info["supports_sql"] is True
            assert info["supports_dataframe"] is True
            assert info["storage"] == "ADLS Gen2"

    def test_get_dialect(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="test-workspace",
                spark_pool_name="test-pool",
                storage_account="teststorage",
                storage_container="testcontainer",
            )

            assert adapter.get_target_dialect() == "spark"


class TestSynapseSparkAdapterConnection:
    def test_create_connection_success(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential") as mock_cred_class,
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_credential = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "test-token"
            mock_token.expires_on = 9999999999
            mock_credential.get_token.return_value = mock_token
            mock_cred_class.return_value = mock_credential

            with patch("benchbox.platforms.azure.synapse_spark_adapter.requests") as mock_requests:
                mock_requests.exceptions = real_requests.exceptions

                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "sparkVersion": "3.3",
                    "nodeSize": "Medium",
                    "nodeCount": 5,
                }
                mock_requests.get.return_value = mock_response

                from benchbox.platforms.azure import SynapseSparkAdapter

                adapter = SynapseSparkAdapter(
                    workspace_name="test-workspace",
                    spark_pool_name="test-pool",
                    storage_account="teststorage",
                    storage_container="testcontainer",
                )

                result = adapter.create_connection()

                assert result["status"] == "connected"
                assert result["workspace_name"] == "test-workspace"
                assert result["spark_pool"] == "test-pool"
                assert result["spark_version"] == "3.3"

    def test_create_connection_auth_failure(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential") as mock_cred_class,
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_credential = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "test-token"
            mock_token.expires_on = 9999999999
            mock_credential.get_token.return_value = mock_token
            mock_cred_class.return_value = mock_credential

            with patch("benchbox.platforms.azure.synapse_spark_adapter.requests") as mock_requests:
                mock_requests.exceptions = real_requests.exceptions

                mock_response = MagicMock()
                mock_response.status_code = 401
                mock_requests.get.return_value = mock_response

                from benchbox.platforms.azure import SynapseSparkAdapter

                adapter = SynapseSparkAdapter(
                    workspace_name="test-workspace",
                    spark_pool_name="test-pool",
                    storage_account="teststorage",
                    storage_container="testcontainer",
                )

                with pytest.raises(ConfigurationError, match="Authentication failed"):
                    adapter.create_connection()

    def test_create_connection_pool_not_found(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential") as mock_cred_class,
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_credential = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "test-token"
            mock_token.expires_on = 9999999999
            mock_credential.get_token.return_value = mock_token
            mock_cred_class.return_value = mock_credential

            with patch("benchbox.platforms.azure.synapse_spark_adapter.requests") as mock_requests:
                mock_requests.exceptions = real_requests.exceptions

                mock_response = MagicMock()
                mock_response.status_code = 404
                mock_requests.get.return_value = mock_response

                from benchbox.platforms.azure import SynapseSparkAdapter

                adapter = SynapseSparkAdapter(
                    workspace_name="test-workspace",
                    spark_pool_name="nonexistent-pool",
                    storage_account="teststorage",
                    storage_container="testcontainer",
                )

                with pytest.raises(ConfigurationError, match="not found"):
                    adapter.create_connection()


class TestSynapseSparkAdapterTuning:
    def test_configure_for_tpch(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.azure.synapse_spark_adapter.SparkConfigOptimizer") as mock_optimizer,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_config = MagicMock()
            mock_config.to_dict.return_value = {"spark.sql.adaptive.enabled": "true"}
            mock_optimizer.for_tpch.return_value = mock_config

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="test-workspace",
                spark_pool_name="test-pool",
                storage_account="teststorage",
                storage_container="testcontainer",
            )

            adapter.configure_for_benchmark("tpch", scale_factor=10.0)

            assert adapter._benchmark_type == "tpch"
            assert adapter._scale_factor == 10.0
            mock_optimizer.for_tpch.assert_called_once()

    def test_configure_for_tpcds(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.azure.synapse_spark_adapter.SparkConfigOptimizer") as mock_optimizer,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_config = MagicMock()
            mock_config.to_dict.return_value = {}
            mock_optimizer.for_tpcds.return_value = mock_config

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="test-workspace",
                spark_pool_name="test-pool",
                storage_account="teststorage",
                storage_container="testcontainer",
            )

            adapter.configure_for_benchmark("tpcds", scale_factor=100.0)

            assert adapter._benchmark_type == "tpcds"
            assert adapter._scale_factor == 100.0
            mock_optimizer.for_tpcds.assert_called_once()


class TestSynapseSparkAdapterCLI:
    def test_add_cli_arguments(self):

        from argparse import ArgumentParser

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
        ):
            from benchbox.platforms.azure import SynapseSparkAdapter

            parser = ArgumentParser()
            SynapseSparkAdapter.add_cli_arguments(parser)

            args = parser.parse_args(
                [
                    "--workspace-name",
                    "test-workspace",
                    "--spark-pool",
                    "test-pool",
                    "--storage-account",
                    "teststorage",
                    "--storage-container",
                    "testcontainer",
                    "--storage-path",
                    "data/benchbox",
                    "--tenant-id",
                    "test-tenant",
                    "--timeout",
                    "120",
                ]
            )

            assert args.workspace_name == "test-workspace"
            assert args.spark_pool_name == "test-pool"
            assert args.storage_account == "teststorage"
            assert args.storage_container == "testcontainer"
            assert args.storage_path == "data/benchbox"
            assert args.tenant_id == "test-tenant"
            assert args.timeout_minutes == 120

    def test_from_config(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import SynapseSparkAdapter

            config = {
                "workspace_name": "config-workspace",
                "spark_pool_name": "config-pool",
                "storage_account": "configstorage",
                "storage_container": "configcontainer",
                "storage_path": "config/path",
                "tenant_id": "config-tenant",
                "timeout_minutes": 90,
            }

            adapter = SynapseSparkAdapter.from_config(config)

            assert adapter.workspace_name == "config-workspace"
            assert adapter.spark_pool_name == "config-pool"
            assert adapter.storage_account == "configstorage"
            assert adapter.storage_container == "configcontainer"
            assert adapter.storage_path == "config/path"
            assert adapter.tenant_id == "config-tenant"
            assert adapter.timeout_minutes == 90

    def test_from_config_reads_nested_options_toggle(self):
        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import SynapseSparkAdapter

            base = {
                "workspace_name": "config-workspace",
                "spark_pool_name": "config-pool",
                "storage_account": "configstorage",
                "storage_container": "configcontainer",
            }

            adapter = SynapseSparkAdapter.from_config({**base, "options": {"adaptive_enabled": False}})
            assert adapter.adaptive_enabled is False

            adapter = SynapseSparkAdapter.from_config(dict(base))
            assert adapter.adaptive_enabled is True


class TestSynapseSparkAdapterRegistry:
    def test_platform_registered_in_registry(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()

        assert "synapse-spark" in all_metadata
        metadata = all_metadata["synapse-spark"]

        assert metadata["display_name"] == "Azure Synapse Analytics Spark"
        assert metadata["category"] == "cloud"
        assert "azure-identity" in str(metadata.get("libraries", []))

    def test_platform_capabilities(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        metadata = all_metadata["synapse-spark"]

        capabilities = metadata.get("capabilities", {})
        assert capabilities.get("supports_sql") is True
        assert capabilities.get("supports_dataframe") is True


class TestSynapseLivyStateConstants:
    def test_synapse_livy_session_states(self):

        from benchbox.platforms.azure.synapse_spark_adapter import SynapseLivySessionState

        assert SynapseLivySessionState.NOT_STARTED == "not_started"
        assert SynapseLivySessionState.STARTING == "starting"
        assert SynapseLivySessionState.IDLE == "idle"
        assert SynapseLivySessionState.BUSY == "busy"
        assert SynapseLivySessionState.ERROR == "error"
        assert SynapseLivySessionState.DEAD == "dead"

    def test_table_format_default_parquet(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="ws",
                spark_pool_name="pool",
                storage_account="sa",
                storage_container="c",
            )
            assert adapter.table_format == "parquet"

    def test_table_format_delta(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="ws",
                spark_pool_name="pool",
                storage_account="sa",
                storage_container="c",
                table_format="delta",
            )
            assert adapter.table_format == "delta"

    def test_table_format_from_config(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter.from_config(
                {
                    "workspace_name": "ws",
                    "spark_pool_name": "pool",
                    "storage_account": "sa",
                    "storage_container": "c",
                    "table_format": "iceberg",
                }
            )
            assert adapter.table_format == "iceberg"

    def test_adaptive_enabled_from_config(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter.from_config(
                {
                    "workspace_name": "ws",
                    "spark_pool_name": "pool",
                    "storage_account": "sa",
                    "storage_container": "c",
                    "adaptive_enabled": False,
                }
            )
            assert adapter.adaptive_enabled is False

            defaulted = SynapseSparkAdapter.from_config(
                {
                    "workspace_name": "ws",
                    "spark_pool_name": "pool",
                    "storage_account": "sa",
                    "storage_container": "c",
                }
            )
            assert defaulted.adaptive_enabled is True

    def test_session_config_delta_extensions(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.azure.synapse_spark_adapter.requests") as mock_requests,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 1}
            mock_requests.post.return_value = mock_response

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="ws",
                spark_pool_name="pool",
                storage_account="sa",
                storage_container="c",
                table_format="delta",
            )
            adapter._wait_for_session_state = MagicMock()
            adapter._create_session()

            call_kwargs = mock_requests.post.call_args
            session_conf = call_kwargs.kwargs["json"]["conf"]
            assert "io.delta.sql.DeltaSparkSessionExtension" in session_conf["spark.sql.extensions"]
            assert "DeltaCatalog" in session_conf["spark.sql.catalog.spark_catalog"]

    def test_session_config_iceberg_extensions(self):

        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.azure.synapse_spark_adapter.requests") as mock_requests,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 1}
            mock_requests.post.return_value = mock_response

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="ws",
                spark_pool_name="pool",
                storage_account="sa",
                storage_container="c",
                table_format="iceberg",
            )
            adapter._wait_for_session_state = MagicMock()
            adapter._create_session()

            call_kwargs = mock_requests.post.call_args
            session_conf = call_kwargs.kwargs["json"]["conf"]
            assert "IcebergSparkSessionExtensions" in session_conf["spark.sql.extensions"]
            assert "SparkSessionCatalog" in session_conf["spark.sql.catalog.spark_catalog"]
            assert session_conf["spark.sql.catalog.spark_catalog.type"] == "hive"

    def test_session_config_parquet_no_extensions(self):
        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.azure.synapse_spark_adapter.requests") as mock_requests,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 1}
            mock_requests.post.return_value = mock_response

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="ws",
                spark_pool_name="pool",
                storage_account="sa",
                storage_container="c",
            )
            adapter._wait_for_session_state = MagicMock()
            adapter._create_session()

            call_kwargs = mock_requests.post.call_args
            session_conf = call_kwargs.kwargs["json"]["conf"]
            assert "spark.sql.extensions" not in session_conf

    def test_synapse_livy_statement_states(self):
        from benchbox.platforms.azure.spark_execution_utils import LivyStatementState

        assert LivyStatementState.WAITING == "waiting"
        assert LivyStatementState.RUNNING == "running"
        assert LivyStatementState.AVAILABLE == "available"
        assert LivyStatementState.ERROR == "error"
        assert LivyStatementState.CANCELLED == "cancelled"

    def test_user_spark_config_wins_over_benchmark_config(self):
        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.azure.synapse_spark_adapter.requests") as mock_requests,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 1}
            mock_requests.post.return_value = mock_response

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="ws",
                spark_pool_name="pool",
                storage_account="sa",
                storage_container="c",
                spark_config={"spark.sql.adaptive.enabled": "false"},
            )
            adapter._spark_config = {"spark.sql.adaptive.enabled": "true"}
            adapter._wait_for_session_state = MagicMock()
            adapter._create_session()

            call_kwargs = mock_requests.post.call_args
            session_conf = call_kwargs.kwargs["json"]["conf"]
            assert session_conf["spark.sql.adaptive.enabled"] == "false"


def _create_synapse_session_conf(**adapter_kwargs):
    with (
        patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
        patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
        patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
        patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        patch("benchbox.platforms.azure.synapse_spark_adapter.requests") as mock_requests,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 1}
        mock_requests.post.return_value = mock_response

        from benchbox.platforms.azure import SynapseSparkAdapter

        adapter = SynapseSparkAdapter(
            workspace_name="ws",
            spark_pool_name="pool",
            storage_account="sa",
            storage_container="c",
            **adapter_kwargs,
        )
        adapter._wait_for_session_state = MagicMock()
        adapter._create_session()

        return mock_requests.post.call_args.kwargs["json"]["conf"]


class TestSynapseAdaptiveToggle:
    AQE_KEYS = (
        "spark.sql.adaptive.enabled",
        "spark.sql.adaptive.coalescePartitions.enabled",
        "spark.sql.adaptive.skewJoin.enabled",
    )

    def test_toggle_off_renders_all_false(self):
        conf = _create_synapse_session_conf(adaptive_enabled=False)
        assert [conf[key] for key in self.AQE_KEYS] == ["false"] * 3

    def test_default_renders_all_true(self):
        conf = _create_synapse_session_conf()
        assert [conf[key] for key in self.AQE_KEYS] == ["true"] * 3

    def test_user_override_beats_toggle_off(self):
        conf = _create_synapse_session_conf(
            adaptive_enabled=False,
            spark_config={"spark.sql.adaptive.enabled": "true"},
        )
        assert conf["spark.sql.adaptive.enabled"] == "true"
        assert conf["spark.sql.adaptive.coalescePartitions.enabled"] == "false"

    def test_toggle_flows_into_optimizer_config(self):
        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter(
                workspace_name="ws",
                spark_pool_name="pool",
                storage_account="sa",
                storage_container="c",
                adaptive_enabled=False,
            )
            adapter.configure_for_benchmark("tpch")

            assert adapter._spark_config["spark.sql.adaptive.enabled"] == "false"
            assert adapter._spark_config["spark.sql.adaptive.skewJoin.enabled"] == "false"


