# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestFabricSparkAdapterInitialization:
    def test_missing_workspace_id_raises_error(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import FabricSparkAdapter

            with pytest.raises(ConfigurationError, match="workspace_id"):
                FabricSparkAdapter(
                    lakehouse_id="lakehouse-123",
                )

    def test_missing_lakehouse_id_raises_error(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import FabricSparkAdapter

            with pytest.raises(ConfigurationError, match="lakehouse_id"):
                FabricSparkAdapter(
                    workspace_id="workspace-abc",
                )

    def test_valid_configuration(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="workspace-abc-123",
                lakehouse_id="lakehouse-xyz-456",
                tenant_id="tenant-789",
                spark_pool_name="my-pool",
            )

            assert adapter.workspace_id == "workspace-abc-123"
            assert adapter.lakehouse_id == "lakehouse-xyz-456"
            assert adapter.tenant_id == "tenant-789"
            assert adapter.spark_pool_name == "my-pool"

    def test_default_values(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="workspace-abc",
                lakehouse_id="lakehouse-xyz",
            )

            assert adapter.timeout_minutes == 60
            assert adapter.tenant_id is None
            assert adapter.spark_pool_name is None

    def test_derived_livy_endpoint(self):
        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="my-workspace",
                lakehouse_id="my-lakehouse",
            )

            assert "my-workspace" in adapter.livy_endpoint
            assert "my-lakehouse" in adapter.livy_endpoint
            assert "livyApi" in adapter.livy_endpoint

    def test_custom_livy_endpoint(self):
        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import FabricSparkAdapter

            custom_endpoint = "https://custom-livy.example.com/sessions"
            adapter = FabricSparkAdapter(
                workspace_id="my-workspace",
                lakehouse_id="my-lakehouse",
                livy_endpoint=custom_endpoint,
            )

            assert adapter.livy_endpoint == custom_endpoint


class TestFabricSparkAdapterPlatformInfo:
    def test_get_platform_info(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="workspace-abc",
                lakehouse_id="lakehouse-xyz",
                spark_pool_name="my-pool",
            )

            info = adapter.get_platform_info()

            assert info["platform"] == "fabric-spark"
            assert info["display_name"] == "Microsoft Fabric Spark"
            assert info["vendor"] == "Microsoft"
            assert info["type"] == "managed_spark"
            assert info["workspace_id"] == "workspace-abc"
            assert info["lakehouse_id"] == "lakehouse-xyz"
            assert info["spark_pool"] == "my-pool"
            assert info["supports_sql"] is True
            assert info["supports_dataframe"] is True
            assert info["storage"] == "OneLake"

    def test_get_dialect(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="workspace-abc",
                lakehouse_id="lakehouse-xyz",
            )

            assert adapter.get_target_dialect() == "spark"


class TestFabricSparkAdapterConnection:
    def test_create_connection_success(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential") as mock_cred_class,
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.requests") as mock_requests,
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_credential = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "test-token"
            mock_token.expires_on = 9999999999
            mock_credential.get_token.return_value = mock_token
            mock_cred_class.return_value = mock_credential

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "displayName": "My Workspace",
            }
            mock_requests.get.return_value = mock_response

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="workspace-abc",
                lakehouse_id="lakehouse-xyz",
            )

            result = adapter.create_connection()

            assert result["status"] == "connected"
            assert result["workspace_id"] == "workspace-abc"
            assert result["workspace_name"] == "My Workspace"
            assert result["lakehouse_id"] == "lakehouse-xyz"

    def test_create_connection_auth_failure(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential") as mock_cred_class,
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_credential = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "test-token"
            mock_token.expires_on = 9999999999
            mock_credential.get_token.return_value = mock_token
            mock_cred_class.return_value = mock_credential

            with patch("benchbox.platforms.azure.fabric_spark_adapter.requests") as mock_requests:
                mock_requests.exceptions = real_requests.exceptions

                mock_response = MagicMock()
                mock_response.status_code = 401
                mock_requests.get.return_value = mock_response

                from benchbox.platforms.azure import FabricSparkAdapter

                adapter = FabricSparkAdapter(
                    workspace_id="workspace-abc",
                    lakehouse_id="lakehouse-xyz",
                )

                with pytest.raises(ConfigurationError, match="Authentication failed"):
                    adapter.create_connection()

    def test_create_connection_workspace_not_found(self):

        import requests as real_requests

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential") as mock_cred_class,
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_credential = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "test-token"
            mock_token.expires_on = 9999999999
            mock_credential.get_token.return_value = mock_token
            mock_cred_class.return_value = mock_credential

            with patch("benchbox.platforms.azure.fabric_spark_adapter.requests") as mock_requests:
                mock_requests.exceptions = real_requests.exceptions

                mock_response = MagicMock()
                mock_response.status_code = 404
                mock_requests.get.return_value = mock_response

                from benchbox.platforms.azure import FabricSparkAdapter

                adapter = FabricSparkAdapter(
                    workspace_id="nonexistent-workspace",
                    lakehouse_id="lakehouse-xyz",
                )

                with pytest.raises(ConfigurationError, match="not found"):
                    adapter.create_connection()


class TestFabricSparkAdapterDataLoading:
    def test_load_data_propagates_table_creation_failure(self, tmp_path):
        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            staging = MagicMock()
            staging.tables_exist.return_value = True
            mock_staging.from_uri.return_value = staging

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(workspace_id="workspace", lakehouse_id="lakehouse")
            adapter._execute_statement = MagicMock(side_effect=RuntimeError("table create failed"))
            benchmark = MagicMock(tables={"orders": None})

            with pytest.raises(RuntimeError, match="Failed to create Fabric Spark table orders"):
                adapter.load_data(benchmark, None, tmp_path)

    def test_load_data_skips_when_exists(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.requests") as mock_requests,
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging_instance = MagicMock()
            mock_staging_instance.tables_exist.return_value = True
            mock_staging.from_uri.return_value = mock_staging_instance

            mock_cred = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "test-token"
            mock_token.expires_on = 9999999999
            mock_cred.get_token.return_value = mock_token

            mock_session_response = MagicMock()
            mock_session_response.status_code = 201
            mock_session_response.json.return_value = {"id": 1, "state": "idle"}

            mock_statement_response = MagicMock()
            mock_statement_response.status_code = 201
            mock_statement_response.json.return_value = {"id": 1, "state": "available", "output": {"status": "ok"}}

            mock_requests.post.return_value = mock_session_response
            mock_requests.get.return_value = mock_statement_response

            from benchbox.platforms.azure import FabricSparkAdapter

            with patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", return_value=mock_cred):
                adapter = FabricSparkAdapter(
                    workspace_id="workspace-abc",
                    lakehouse_id="lakehouse-xyz",
                )

                import tempfile

                mock_benchmark = MagicMock()
                mock_benchmark.tables = {"lineitem": None, "orders": None}

                with tempfile.TemporaryDirectory() as tmpdir:
                    from pathlib import Path

                    with patch.object(adapter, "_execute_statement", return_value={}):
                        adapter.load_data(mock_benchmark, None, Path(tmpdir))

                    mock_staging_instance.upload_tables.assert_not_called()

    def test_load_data_falls_back_to_table_discovery_for_placeholder_tables(self):
        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.requests") as mock_requests,
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging_instance = MagicMock()
            mock_staging_instance.tables_exist.return_value = False
            mock_staging.from_uri.return_value = mock_staging_instance

            mock_cred = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "test-token"
            mock_token.expires_on = 9999999999
            mock_cred.get_token.return_value = mock_token

            mock_session_response = MagicMock()
            mock_session_response.status_code = 201
            mock_session_response.json.return_value = {"id": 1, "state": "idle"}

            mock_statement_response = MagicMock()
            mock_statement_response.status_code = 201
            mock_statement_response.json.return_value = {"id": 1, "state": "available", "output": {"status": "ok"}}

            mock_requests.post.return_value = mock_session_response
            mock_requests.get.return_value = mock_statement_response

            from benchbox.platforms.azure import FabricSparkAdapter

            with patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", return_value=mock_cred):
                adapter = FabricSparkAdapter(
                    workspace_id="workspace-abc",
                    lakehouse_id="lakehouse-xyz",
                )

                import tempfile

                mock_benchmark = MagicMock()
                mock_benchmark.tables = {"lineitem": None, "orders": None}

                with tempfile.TemporaryDirectory() as tmpdir:
                    from pathlib import Path

                    with patch.object(adapter, "_execute_statement", return_value={}):
                        adapter.load_data(mock_benchmark, None, Path(tmpdir))

                    mock_staging_instance.upload_tables.assert_called_once_with(
                        tables=["lineitem", "orders"],
                        source_dir=Path(tmpdir),
                        file_format="parquet",
                    )
                    mock_staging_instance.upload_data_files.assert_not_called()


class TestFabricSparkAdapterTuning:
    def test_configure_for_tpch(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.azure.fabric_spark_adapter.SparkConfigOptimizer") as mock_optimizer,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_config = MagicMock()
            mock_config.to_dict.return_value = {"spark.sql.adaptive.enabled": "true"}
            mock_optimizer.for_tpch.return_value = mock_config

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="workspace-abc",
                lakehouse_id="lakehouse-xyz",
            )

            adapter.configure_for_benchmark(None, "tpch", scale_factor=10.0)

            assert adapter._benchmark_type == "tpch"
            assert adapter._scale_factor == 10.0
            mock_optimizer.for_tpch.assert_called_once()

    def test_configure_for_tpcds(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.azure.fabric_spark_adapter.SparkConfigOptimizer") as mock_optimizer,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            mock_config = MagicMock()
            mock_config.to_dict.return_value = {}
            mock_optimizer.for_tpcds.return_value = mock_config

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="workspace-abc",
                lakehouse_id="lakehouse-xyz",
            )

            adapter.configure_for_benchmark(None, "tpcds", scale_factor=100.0)

            assert adapter._benchmark_type == "tpcds"
            assert adapter._scale_factor == 100.0
            mock_optimizer.for_tpcds.assert_called_once()


class TestFabricSparkAdapterCLI:
    def test_add_cli_arguments(self):

        from argparse import ArgumentParser

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
        ):
            from benchbox.platforms.azure import FabricSparkAdapter

            parser = ArgumentParser()
            FabricSparkAdapter.add_cli_arguments(parser)

            args = parser.parse_args(
                [
                    "--workspace-id",
                    "test-workspace",
                    "--lakehouse-id",
                    "test-lakehouse",
                    "--tenant-id",
                    "test-tenant",
                    "--spark-pool",
                    "test-pool",
                    "--timeout",
                    "120",
                ]
            )

            assert args.workspace_id == "test-workspace"
            assert args.lakehouse_id == "test-lakehouse"
            assert args.tenant_id == "test-tenant"
            assert args.spark_pool_name == "test-pool"
            assert args.timeout_minutes == 120

    def test_from_config(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import FabricSparkAdapter

            config = {
                "workspace_id": "config-workspace",
                "lakehouse_id": "config-lakehouse",
                "tenant_id": "config-tenant",
                "spark_pool_name": "config-pool",
                "timeout_minutes": 90,
            }

            adapter = FabricSparkAdapter.from_config(config)

            assert adapter.workspace_id == "config-workspace"
            assert adapter.lakehouse_id == "config-lakehouse"
            assert adapter.tenant_id == "config-tenant"
            assert adapter.spark_pool_name == "config-pool"
            assert adapter.timeout_minutes == 90

    def test_from_config_reads_nested_options_toggle(self):
        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import FabricSparkAdapter

            base = {
                "workspace_id": "config-workspace",
                "lakehouse_id": "config-lakehouse",
                "tenant_id": "config-tenant",
                "spark_pool_name": "config-pool",
            }

            adapter = FabricSparkAdapter.from_config({**base, "options": {"adaptive_enabled": False}})
            assert adapter.adaptive_enabled is False

            adapter = FabricSparkAdapter.from_config({**base, "adaptive_enabled": False})
            assert adapter.adaptive_enabled is False

            adapter = FabricSparkAdapter.from_config(dict(base))
            assert adapter.adaptive_enabled is True


class TestFabricSparkAdapterRegistry:
    def test_platform_registered_in_registry(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()

        assert "fabric-spark" in all_metadata
        metadata = all_metadata["fabric-spark"]

        assert metadata["display_name"] == "Microsoft Fabric Spark"
        assert metadata["category"] == "cloud"
        assert "azure-identity" in str(metadata.get("libraries", []))

    def test_platform_capabilities(self):

        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        metadata = all_metadata["fabric-spark"]

        capabilities = metadata.get("capabilities", {})
        assert capabilities.get("supports_sql") is True
        assert capabilities.get("supports_dataframe") is True


class TestFabricSparkLivySessionConstants:
    def test_livy_session_states(self):

        from benchbox.platforms.azure.fabric_spark_adapter import LivySessionState

        assert LivySessionState.NOT_STARTED == "not_started"
        assert LivySessionState.STARTING == "starting"
        assert LivySessionState.IDLE == "idle"
        assert LivySessionState.BUSY == "busy"
        assert LivySessionState.ERROR == "error"
        assert LivySessionState.DEAD == "dead"

    def test_table_format_default_delta(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="ws-123",
                lakehouse_id="lh-456",
            )
            assert adapter.table_format == "delta"

    def test_table_format_iceberg(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="ws-123",
                lakehouse_id="lh-456",
                table_format="iceberg",
            )
            assert adapter.table_format == "iceberg"

    def test_table_format_from_config(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter.from_config(
                {
                    "workspace_id": "ws-123",
                    "lakehouse_id": "lh-456",
                    "table_format": "parquet",
                }
            )
            assert adapter.table_format == "parquet"

    def test_adaptive_enabled_from_config(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter.from_config(
                {
                    "workspace_id": "ws-123",
                    "lakehouse_id": "lh-456",
                    "adaptive_enabled": False,
                }
            )
            assert adapter.adaptive_enabled is False

            defaulted = FabricSparkAdapter.from_config(
                {
                    "workspace_id": "ws-123",
                    "lakehouse_id": "lh-456",
                }
            )
            assert defaulted.adaptive_enabled is True

    def test_session_config_iceberg_extensions(self):

        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.requests") as mock_requests,
        ):
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 1}
            mock_requests.post.return_value = mock_response

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="ws-123",
                lakehouse_id="lh-456",
                table_format="iceberg",
            )
            adapter._wait_for_session_state = MagicMock()
            adapter._create_session()

            call_kwargs = mock_requests.post.call_args
            session_conf = call_kwargs.kwargs["json"]["conf"]
            assert "IcebergSparkSessionExtensions" in session_conf["spark.sql.extensions"]
            assert "SparkSessionCatalog" in session_conf["spark.sql.catalog.spark_catalog"]

    def test_session_config_delta_no_extensions(self):
        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.requests") as mock_requests,
        ):
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 1}
            mock_requests.post.return_value = mock_response

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="ws-123",
                lakehouse_id="lh-456",
            )
            adapter._wait_for_session_state = MagicMock()
            adapter._create_session()

            call_kwargs = mock_requests.post.call_args
            session_conf = call_kwargs.kwargs["json"]["conf"]
            assert "spark.sql.extensions" not in session_conf

    def test_livy_statement_states(self):

        from benchbox.platforms.azure.spark_execution_utils import LivyStatementState

        assert LivyStatementState.WAITING == "waiting"
        assert LivyStatementState.RUNNING == "running"
        assert LivyStatementState.AVAILABLE == "available"
        assert LivyStatementState.ERROR == "error"
        assert LivyStatementState.CANCELLED == "cancelled"

    def test_user_spark_config_wins_over_benchmark_config(self):
        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
            patch("benchbox.platforms.azure.fabric_spark_adapter.requests") as mock_requests,
        ):
            mock_staging.from_uri.return_value = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 1}
            mock_requests.post.return_value = mock_response

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="ws-123",
                lakehouse_id="lh-456",
                spark_config={"spark.sql.adaptive.enabled": "false"},
            )
            adapter._spark_config = {"spark.sql.adaptive.enabled": "true"}
            adapter._wait_for_session_state = MagicMock()
            adapter._create_session()

            call_kwargs = mock_requests.post.call_args
            session_conf = call_kwargs.kwargs["json"]["conf"]
            assert session_conf["spark.sql.adaptive.enabled"] == "false"


def _create_fabric_session_conf(**adapter_kwargs):
    with (
        patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
        patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
        patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
        patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        patch("benchbox.platforms.azure.fabric_spark_adapter.requests") as mock_requests,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 1}
        mock_requests.post.return_value = mock_response

        from benchbox.platforms.azure import FabricSparkAdapter

        adapter = FabricSparkAdapter(
            workspace_id="ws-123",
            lakehouse_id="lh-456",
            **adapter_kwargs,
        )
        adapter._wait_for_session_state = MagicMock()
        adapter._create_session()

        return mock_requests.post.call_args.kwargs["json"]["conf"]


class TestFabricAdaptiveToggle:
    AQE_KEYS = (
        "spark.sql.adaptive.enabled",
        "spark.sql.adaptive.coalescePartitions.enabled",
        "spark.sql.adaptive.skewJoin.enabled",
    )

    def test_toggle_off_renders_all_false(self):
        conf = _create_fabric_session_conf(adaptive_enabled=False)
        assert [conf[key] for key in self.AQE_KEYS] == ["false"] * 3

    def test_default_renders_all_true(self):
        conf = _create_fabric_session_conf()
        assert [conf[key] for key in self.AQE_KEYS] == ["true"] * 3

    def test_user_override_beats_toggle_off(self):
        conf = _create_fabric_session_conf(
            adaptive_enabled=False,
            spark_config={"spark.sql.adaptive.enabled": "true"},
        )
        assert conf["spark.sql.adaptive.enabled"] == "true"
        assert conf["spark.sql.adaptive.coalescePartitions.enabled"] == "false"

    def test_toggle_flows_into_optimizer_config(self):
        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging") as mock_staging,
        ):
            mock_staging.from_uri.return_value = MagicMock()

            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter(
                workspace_id="ws-123",
                lakehouse_id="lh-456",
                adaptive_enabled=False,
            )
            adapter.configure_for_benchmark(connection=None, benchmark_type="tpch", scale_factor=None)

            assert adapter._spark_config["spark.sql.adaptive.enabled"] == "false"
            assert adapter._spark_config["spark.sql.adaptive.skewJoin.enabled"] == "false"


