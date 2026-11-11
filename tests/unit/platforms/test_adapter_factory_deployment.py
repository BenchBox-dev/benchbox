# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.platforms.adapter_factory import (
    _normalize_platform_name,
    get_available_deployments,
    get_available_modes,
    get_default_deployment,
    is_dataframe_mode,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestNormalizePlatformName:
    def test_simple_platform_name(self):
        result = _normalize_platform_name("duckdb")
        assert result == ("duckdb", False, None)

    def test_df_suffix_detection(self):
        result = _normalize_platform_name("polars-df")
        assert result == ("polars", True, None)

    def test_deployment_suffix_detection(self):
        result = _normalize_platform_name("clickhouse:cloud")
        assert result == ("clickhouse", False, "cloud")

    def test_local_deployment(self):
        result = _normalize_platform_name("clickhouse:local")
        assert result == ("clickhouse", False, "local")

    def test_server_deployment(self):
        result = _normalize_platform_name("clickhouse:server")
        assert result == ("clickhouse", False, "server")

    def test_firebolt_core_deployment(self):
        result = _normalize_platform_name("firebolt:core")
        assert result == ("firebolt", False, "core")

    def test_combined_df_and_deployment(self):
        result = _normalize_platform_name("databricks-df:serverless")
        assert result == ("databricks", True, "serverless")

    def test_case_insensitive(self):
        result = _normalize_platform_name("ClickHouse:CLOUD")
        assert result == ("clickhouse", False, "cloud")

    def test_uppercase_df_suffix(self):
        result = _normalize_platform_name("POLARS-DF")
        assert result == ("polars", True, None)

    def test_multiple_colons_uses_last(self):
        result = _normalize_platform_name("some:thing:cloud")
        assert result == ("some:thing", False, "cloud")


class TestGetAvailableDeployments:
    def test_clickhouse_deployments(self):
        result = get_available_deployments("clickhouse")
        assert "local" in result
        assert "server" in result
        assert "cloud" not in result

    def test_firebolt_deployments(self):
        result = get_available_deployments("firebolt")
        assert "core" in result
        assert "cloud" in result

    def test_duckdb_single_deployment(self):
        result = get_available_deployments("duckdb")
        assert result == ["local"]

    def test_timescaledb_deployments(self):
        result = get_available_deployments("timescaledb")
        assert "self-hosted" in result
        assert "cloud" in result

    def test_platform_without_deployments(self):
        result = get_available_deployments("polars")
        assert result == []

    def test_strips_df_suffix(self):
        result = get_available_deployments("polars-df")
        assert result == []

    def test_strips_deployment_suffix(self):
        result = get_available_deployments("clickhouse:server")
        assert "local" in result
        assert "server" in result


class TestGetDefaultDeployment:
    def test_clickhouse_default_is_local(self):
        result = get_default_deployment("clickhouse")
        assert result == "local"

    def test_firebolt_default_is_core(self):
        result = get_default_deployment("firebolt")
        assert result == "core"

    def test_duckdb_default_is_local(self):
        result = get_default_deployment("duckdb")
        assert result == "local"

    def test_timescaledb_default_is_selfhosted(self):
        result = get_default_deployment("timescaledb")
        assert result == "self-hosted"

    def test_platform_without_deployments_returns_none(self):
        result = get_default_deployment("polars")
        assert result is None


class TestIsDataframeModeWithDeployment:
    def test_df_suffix_still_works(self):
        assert is_dataframe_mode("duckdb-df") is True
        assert is_dataframe_mode("duckdb") is False
        assert is_dataframe_mode("polars-df") is True
        assert is_dataframe_mode("polars") is True

    def test_deployment_suffix_doesnt_affect_df_mode(self):
        assert is_dataframe_mode("clickhouse:cloud") is False
        assert is_dataframe_mode("clickhouse") is False

    def test_combined_suffixes(self):
        assert is_dataframe_mode("pyspark-df:cluster") is True


class TestGetAvailableModesWithDeployment:
    def test_deployment_suffix_stripped_for_mode_check(self):
        modes = get_available_modes("clickhouse:cloud")
        assert "sql" in modes

    def test_both_suffixes_stripped(self):
        modes = get_available_modes("pyspark-df:cluster")
        assert "dataframe" in modes


class TestGetAdapterDeploymentValidation:
    def test_invalid_deployment_for_platform_raises_error(self):
        from benchbox.platforms.adapter_factory import get_adapter

        with pytest.raises(ValueError) as exc_info:
            get_adapter("duckdb:cloud")

        assert "duckdb" in str(exc_info.value).lower()
        assert "cloud" in str(exc_info.value).lower()
        assert "local" in str(exc_info.value).lower()

    def test_unknown_deployment_for_platform_with_modes(self):
        from benchbox.platforms.adapter_factory import get_adapter

        with pytest.raises(ValueError) as exc_info:
            get_adapter("clickhouse:nonexistent")

        assert "nonexistent" in str(exc_info.value)
        assert "local" in str(exc_info.value)
        assert "server" in str(exc_info.value)

    def test_deployment_on_platform_without_modes_raises_error(self):
        from benchbox.platforms.adapter_factory import get_adapter

        with pytest.raises(ValueError) as exc_info:
            get_adapter("polars:managed")

        assert "does not support deployment modes" in str(exc_info.value)
        assert ":managed" in str(exc_info.value)

    def test_explicit_deployment_overrides_name_suffix(self):
        from benchbox.core.platform_registry import DeploymentCapability, PlatformCapability
        from benchbox.platforms.adapter_factory import _resolve_deployment_mode

        caps = PlatformCapability(
            supports_sql=True,
            deployment_modes={
                "local": DeploymentCapability(mode="local"),
                "server": DeploymentCapability(mode="self-hosted"),
                "cloud": DeploymentCapability(mode="managed"),
            },
            default_deployment="local",
        )

        result = _resolve_deployment_mode(
            platform="clickhouse",
            explicit_deployment="cloud",
            deployment_from_name="local",
            caps=caps,
        )
        assert result == "cloud"

    def test_name_suffix_overrides_default(self):
        from benchbox.core.platform_registry import DeploymentCapability, PlatformCapability
        from benchbox.platforms.adapter_factory import _resolve_deployment_mode

        caps = PlatformCapability(
            supports_sql=True,
            deployment_modes={
                "local": DeploymentCapability(mode="local"),
                "server": DeploymentCapability(mode="self-hosted"),
                "cloud": DeploymentCapability(mode="managed"),
            },
            default_deployment="local",
        )

        result = _resolve_deployment_mode(
            platform="clickhouse",
            explicit_deployment=None,
            deployment_from_name="server",
            caps=caps,
        )
        assert result == "server"

    def test_default_used_when_no_explicit_or_suffix(self):
        from benchbox.core.platform_registry import DeploymentCapability, PlatformCapability
        from benchbox.platforms.adapter_factory import _resolve_deployment_mode

        caps = PlatformCapability(
            supports_sql=True,
            deployment_modes={
                "local": DeploymentCapability(mode="local"),
                "server": DeploymentCapability(mode="self-hosted"),
            },
            default_deployment="local",
        )

        result = _resolve_deployment_mode(
            platform="clickhouse",
            explicit_deployment=None,
            deployment_from_name=None,
            caps=caps,
        )
        assert result == "local"

    def test_platform_without_deployment_modes_returns_none(self):
        from benchbox.core.platform_registry import PlatformCapability
        from benchbox.platforms.adapter_factory import _resolve_deployment_mode

        caps = PlatformCapability(
            supports_sql=True,
            supports_dataframe=True,
            deployment_modes={},
        )

        result = _resolve_deployment_mode(
            platform="polars",
            explicit_deployment=None,
            deployment_from_name=None,
            caps=caps,
        )
        assert result is None


class TestClickHouseCloudAdapter:
    def test_cloud_adapter_requires_host(self):
        import os
        from unittest.mock import patch

        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CLICKHOUSE_CLOUD_HOST", None)
            os.environ.pop("CLICKHOUSE_CLOUD_PASSWORD", None)

            with pytest.raises(ValueError) as exc_info:
                ClickHouseCloudAdapter()

            assert "host" in str(exc_info.value).lower()
            assert "CLICKHOUSE_CLOUD_HOST" in str(exc_info.value)

    def test_cloud_adapter_requires_password(self):
        import os
        from unittest.mock import patch

        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CLICKHOUSE_CLOUD_HOST", None)
            os.environ.pop("CLICKHOUSE_CLOUD_PASSWORD", None)

            with pytest.raises(ValueError) as exc_info:
                ClickHouseCloudAdapter(host="test.clickhouse.cloud")

            assert "password" in str(exc_info.value).lower()
            assert "CLICKHOUSE_CLOUD_PASSWORD" in str(exc_info.value)

    def test_cloud_adapter_uses_secure_connection(self):
        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        adapter = ClickHouseCloudAdapter(
            host="test.clickhouse.cloud",
            password="test-password",
        )

        assert adapter.secure is True
        assert adapter.port == 8443

    def test_cloud_adapter_accepts_env_vars(self):
        import os
        from unittest.mock import patch

        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        with patch.dict(
            os.environ,
            {
                "CLICKHOUSE_CLOUD_HOST": "env-host.clickhouse.cloud",
                "CLICKHOUSE_CLOUD_PASSWORD": "env-password",
                "CLICKHOUSE_CLOUD_USER": "env-user",
            },
        ):
            adapter = ClickHouseCloudAdapter()

            assert adapter.host == "env-host.clickhouse.cloud"
            assert adapter.password == "env-password"
            assert adapter.username == "env-user"

    def test_cloud_adapter_config_overrides_env(self):
        import os
        from unittest.mock import patch

        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        with patch.dict(
            os.environ,
            {
                "CLICKHOUSE_CLOUD_HOST": "env-host.clickhouse.cloud",
                "CLICKHOUSE_CLOUD_PASSWORD": "env-password",
            },
        ):
            adapter = ClickHouseCloudAdapter(
                host="config-host.clickhouse.cloud",
                password="config-password",
            )

            assert adapter.host == "config-host.clickhouse.cloud"
            assert adapter.password == "config-password"

    def test_cloud_adapter_default_username(self):
        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        adapter = ClickHouseCloudAdapter(
            host="test.clickhouse.cloud",
            password="test-password",
        )

        assert adapter.username == "default"

    def test_base_clickhouse_adapter_rejects_cloud_mode(self):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        with pytest.raises(ValueError) as exc_info:
            ClickHouseAdapter(deployment_mode="cloud")

        error_msg = str(exc_info.value)
        assert "first-class platform" in error_msg
        assert "clickhouse-cloud" in error_msg

    def test_cloud_adapter_platform_name(self):
        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        adapter = ClickHouseCloudAdapter(
            host="test.clickhouse.cloud",
            password="test-password",
        )

        assert adapter.platform_name == "ClickHouse Cloud"

    def test_cloud_client_interface(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.clickhouse.client import ClickHouseCloudClient

        mock_client = MagicMock()
        mock_client.query.return_value.result_set = [(1,)]

        with patch("benchbox.platforms.clickhouse._dependencies.clickhouse_connect") as mock_cc:
            mock_cc.get_client.return_value = mock_client

            client = ClickHouseCloudClient(
                host="test.clickhouse.cloud",
                password="test-password",
            )

            assert hasattr(client, "execute")
            assert hasattr(client, "command")
            assert hasattr(client, "close")
            assert hasattr(client, "disconnect")
            assert hasattr(client, "commit")

            result = client.execute("SELECT 1")
            assert result == [(1,)]

            client.close()


