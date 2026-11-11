# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
from unittest.mock import patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTimescaleDBCloudMode:
    def test_cloud_mode_requires_host(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                TimescaleDBAdapter(deployment_mode="cloud")

            assert "host" in str(exc_info.value).lower()
            assert "TigerData" in str(exc_info.value)
            assert "TIGERDATA_HOST" in str(exc_info.value)
            assert "TIMESCALE_HOST" in str(exc_info.value)

    def test_cloud_mode_requires_password(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        with patch.dict(os.environ, {}, clear=True):
            for key in ["TIGERDATA_PASSWORD", "TIMESCALE_PASSWORD", "PGPASSWORD", "TIGERDATA_SERVICE_URL"]:
                os.environ.pop(key, None)

            with pytest.raises(ValueError) as exc_info:
                TimescaleDBAdapter(
                    deployment_mode="cloud",
                    host="test.tsdb.cloud.timescale.com",
                )

            assert "password" in str(exc_info.value).lower()
            assert "TigerData" in str(exc_info.value)

    def test_cloud_mode_uses_ssl(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        adapter = TimescaleDBAdapter(
            deployment_mode="cloud",
            host="test.tsdb.cloud.timescale.com",
            password="test-password",
            port=30985,
        )

        assert adapter.sslmode == "require"

    def test_cloud_mode_skips_database_management(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        adapter = TimescaleDBAdapter(
            deployment_mode="cloud",
            host="test.tsdb.cloud.timescale.com",
            password="test-password",
            port=30985,
        )

        assert adapter.skip_database_management is True

    def test_cloud_mode_parses_service_url(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        adapter = TimescaleDBAdapter(
            deployment_mode="cloud",
            service_url="postgres://myuser:mypass@myhost.tsdb.cloud.timescale.com:12345/mydb?sslmode=require",
        )

        assert adapter.host == "myhost.tsdb.cloud.timescale.com"
        assert adapter.port == 12345
        assert adapter.username == "myuser"
        assert adapter.password == "mypass"
        assert adapter.database == "mydb"
        assert adapter.sslmode == "require"

    def test_cloud_mode_accepts_tigerdata_env_vars(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        with patch.dict(
            os.environ,
            {
                "TIGERDATA_HOST": "env-host.tsdb.cloud.timescale.com",
                "TIGERDATA_PASSWORD": "env-password",
                "TIGERDATA_USER": "env-user",
                "TIGERDATA_PORT": "54321",
                "TIGERDATA_DATABASE": "env-db",
            },
        ):
            adapter = TimescaleDBAdapter(deployment_mode="cloud")

            assert adapter.host == "env-host.tsdb.cloud.timescale.com"
            assert adapter.password == "env-password"
            assert adapter.username == "env-user"
            assert adapter.port == 54321
            assert adapter.database == "env-db"

    def test_cloud_mode_tigerdata_env_vars_take_priority_over_timescale(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        with patch.dict(
            os.environ,
            {
                "TIGERDATA_HOST": "tigerdata-host.tsdb.cloud.timescale.com",
                "TIGERDATA_PASSWORD": "tigerdata-password",
                "TIGERDATA_USER": "tigerdata-user",
                "TIGERDATA_PORT": "54444",
                "TIGERDATA_DATABASE": "tigerdata-db",
                "TIMESCALE_HOST": "timescale-host.tsdb.cloud.timescale.com",
                "TIMESCALE_PASSWORD": "timescale-password",
                "TIMESCALE_USER": "timescale-user",
                "TIMESCALE_PORT": "53333",
                "TIMESCALE_DATABASE": "timescale-db",
            },
        ):
            adapter = TimescaleDBAdapter(deployment_mode="cloud")

            assert adapter.host == "tigerdata-host.tsdb.cloud.timescale.com"
            assert adapter.password == "tigerdata-password"
            assert adapter.username == "tigerdata-user"
            assert adapter.port == 54444
            assert adapter.database == "tigerdata-db"

    def test_cloud_mode_accepts_timescale_fallback_env_vars(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        with patch.dict(
            os.environ,
            {
                "TIMESCALE_HOST": "fallback-host.tsdb.cloud.timescale.com",
                "TIMESCALE_PASSWORD": "fallback-password",
                "TIMESCALE_USER": "fallback-user",
                "TIMESCALE_PORT": "55432",
                "TIMESCALE_DATABASE": "fallback-db",
            },
            clear=True,
        ):
            adapter = TimescaleDBAdapter(deployment_mode="cloud")

            assert adapter.host == "fallback-host.tsdb.cloud.timescale.com"
            assert adapter.password == "fallback-password"
            assert adapter.username == "fallback-user"
            assert adapter.port == 55432
            assert adapter.database == "fallback-db"

    def test_cloud_mode_config_overrides_env(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        with patch.dict(
            os.environ,
            {
                "TIMESCALE_HOST": "env-host.tsdb.cloud.timescale.com",
                "TIMESCALE_PASSWORD": "env-password",
            },
        ):
            adapter = TimescaleDBAdapter(
                deployment_mode="cloud",
                host="config-host.tsdb.cloud.timescale.com",
                password="config-password",
            )

            assert adapter.host == "config-host.tsdb.cloud.timescale.com"
            assert adapter.password == "config-password"

    def test_cloud_mode_default_username(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        adapter = TimescaleDBAdapter(
            deployment_mode="cloud",
            host="test.tsdb.cloud.timescale.com",
            password="test-password",
        )

        assert adapter.username == "tsdbadmin"

    def test_cloud_mode_default_database(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        adapter = TimescaleDBAdapter(
            deployment_mode="cloud",
            host="test.tsdb.cloud.timescale.com",
            password="test-password",
        )

        assert adapter.database == "tsdb"

    def test_self_hosted_mode_default(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        adapter = TimescaleDBAdapter(
            host="localhost",
            password="test-password",
        )

        assert adapter.deployment_mode == "self-hosted"
        assert getattr(adapter, "skip_database_management", False) is False

    def test_invalid_deployment_mode(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        with pytest.raises(ValueError) as exc_info:
            TimescaleDBAdapter(
                deployment_mode="invalid",
                host="localhost",
                password="test",
            )

        assert "invalid" in str(exc_info.value).lower()
        assert "self-hosted" in str(exc_info.value)
        assert "cloud" in str(exc_info.value)

    def test_service_url_from_env(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        with patch.dict(
            os.environ,
            {
                "TIGERDATA_SERVICE_URL": "postgres://envuser:envpass@envhost.com:9999/envdb?sslmode=require",
            },
        ):
            adapter = TimescaleDBAdapter(deployment_mode="cloud")

            assert adapter.host == "envhost.com"
            assert adapter.port == 9999
            assert adapter.username == "envuser"
            assert adapter.password == "envpass"
            assert adapter.database == "envdb"

    def test_service_url_rejects_invalid_scheme(self):

        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        with pytest.raises(ValueError, match="Unsupported scheme"):
            TimescaleDBAdapter(
                deployment_mode="cloud",
                service_url="http://user:pass@host.com:5432/db",
            )


class TestTimescaleDBDeploymentRegistry:
    def test_timescaledb_has_deployment_modes(self):

        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("timescaledb")
        assert caps is not None
        assert caps.deployment_modes is not None
        assert "self-hosted" in caps.deployment_modes
        assert "cloud" in caps.deployment_modes

    def test_default_deployment_is_self_hosted(self):

        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("timescaledb")
        assert caps.default_deployment == "self-hosted"

    def test_cloud_deployment_requires_credentials(self):

        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("timescaledb")
        cloud_cap = caps.deployment_modes["cloud"]
        assert cloud_cap.requires_credentials is True
        assert cloud_cap.requires_network is True
        assert cloud_cap.display_name == "TigerData"


