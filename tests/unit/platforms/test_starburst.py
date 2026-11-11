# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestStarburstAdapter:
    def test_starburst_requires_host(self):
        with patch.dict(os.environ, {}, clear=True):
            for key in [
                "STARBURST_HOST",
                "STARBURST_USER",
                "STARBURST_USERNAME",
                "STARBURST_PASSWORD",
                "STARBURST_ROLE",
                "STARBURST_CATALOG",
            ]:
                os.environ.pop(key, None)

            with patch.dict(
                "sys.modules",
                {
                    "trino": MagicMock(),
                    "trino.auth": MagicMock(),
                },
            ):
                from benchbox.platforms.starburst import StarburstAdapter

                with pytest.raises(ValueError) as exc_info:
                    StarburstAdapter(
                        username="test@example.com/accountadmin",
                        password="test-password",
                    )

                assert "host" in str(exc_info.value).lower()
                assert "STARBURST_HOST" in str(exc_info.value)

    def test_starburst_requires_username(self):
        with patch.dict(os.environ, {}, clear=True):
            for key in ["STARBURST_USER", "STARBURST_USERNAME"]:
                os.environ.pop(key, None)

            with patch.dict(
                "sys.modules",
                {
                    "trino": MagicMock(),
                    "trino.auth": MagicMock(),
                },
            ):
                from benchbox.platforms.starburst import StarburstAdapter

                with pytest.raises(ValueError) as exc_info:
                    StarburstAdapter(
                        host="test-cluster.trino.galaxy.starburst.io",
                        password="test-password",
                    )

                assert "username" in str(exc_info.value).lower()
                assert "STARBURST_USER" in str(exc_info.value)

    def test_starburst_requires_password(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("STARBURST_PASSWORD", None)

            with patch.dict(
                "sys.modules",
                {
                    "trino": MagicMock(),
                    "trino.auth": MagicMock(),
                },
            ):
                from benchbox.platforms.starburst import StarburstAdapter

                with pytest.raises(ValueError) as exc_info:
                    StarburstAdapter(
                        host="test-cluster.trino.galaxy.starburst.io",
                        username="test@example.com/accountadmin",
                    )

                assert "password" in str(exc_info.value).lower()
                assert "STARBURST_PASSWORD" in str(exc_info.value)

    def test_starburst_defaults_to_https_port_443(self):
        with patch.dict(
            "sys.modules",
            {
                "trino": MagicMock(),
                "trino.auth": MagicMock(),
            },
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter(
                host="test-cluster.trino.galaxy.starburst.io",
                username="test@example.com/accountadmin",
                password="test-password",
            )

            assert adapter.port == 443
            assert adapter.http_scheme == "https"
            assert adapter.verify_ssl is True

    def test_starburst_accepts_env_vars(self):
        with (
            patch.dict(
                os.environ,
                {
                    "STARBURST_HOST": "env-cluster.trino.galaxy.starburst.io",
                    "STARBURST_USER": "env-user@example.com/accountadmin",
                    "STARBURST_PASSWORD": "env-password",
                    "STARBURST_CATALOG": "env-catalog",
                },
            ),
            patch.dict(
                "sys.modules",
                {
                    "trino": MagicMock(),
                    "trino.auth": MagicMock(),
                },
            ),
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter()

            assert adapter.host == "env-cluster.trino.galaxy.starburst.io"
            assert adapter.username == "env-user@example.com/accountadmin"
            assert adapter.password == "env-password"
            assert adapter.catalog == "env-catalog"

    def test_starburst_accepts_starburst_username_env_var(self):
        with (
            patch.dict(
                os.environ,
                {
                    "STARBURST_HOST": "test-cluster.trino.galaxy.starburst.io",
                    "STARBURST_USERNAME": "username-user@example.com/admin",
                    "STARBURST_PASSWORD": "test-password",
                },
            ),
            patch.dict(
                "sys.modules",
                {
                    "trino": MagicMock(),
                    "trino.auth": MagicMock(),
                },
            ),
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter()

            assert adapter.username == "username-user@example.com/admin"

    def test_starburst_config_overrides_env(self):
        with (
            patch.dict(
                os.environ,
                {
                    "STARBURST_HOST": "env-cluster.trino.galaxy.starburst.io",
                    "STARBURST_USER": "env-user@example.com/accountadmin",
                    "STARBURST_PASSWORD": "env-password",
                },
            ),
            patch.dict(
                "sys.modules",
                {
                    "trino": MagicMock(),
                    "trino.auth": MagicMock(),
                },
            ),
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter(
                host="config-cluster.trino.galaxy.starburst.io",
                username="config-user@example.com/admin",
                password="config-password",
            )

            assert adapter.host == "config-cluster.trino.galaxy.starburst.io"
            assert adapter.username == "config-user@example.com/admin"
            assert adapter.password == "config-password"

    def test_starburst_appends_role_to_username(self):
        with (
            patch.dict(
                os.environ,
                {
                    "STARBURST_HOST": "test-cluster.trino.galaxy.starburst.io",
                    "STARBURST_USER": "test@example.com",
                    "STARBURST_PASSWORD": "test-password",
                    "STARBURST_ROLE": "accountadmin",
                },
            ),
            patch.dict(
                "sys.modules",
                {
                    "trino": MagicMock(),
                    "trino.auth": MagicMock(),
                },
            ),
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter()

            assert adapter.username == "test@example.com/accountadmin"

    def test_starburst_does_not_duplicate_role(self):
        with (
            patch.dict(
                os.environ,
                {
                    "STARBURST_HOST": "test-cluster.trino.galaxy.starburst.io",
                    "STARBURST_USER": "test@example.com/admin",
                    "STARBURST_PASSWORD": "test-password",
                    "STARBURST_ROLE": "accountadmin",
                },
            ),
            patch.dict(
                "sys.modules",
                {
                    "trino": MagicMock(),
                    "trino.auth": MagicMock(),
                },
            ),
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter()

            assert adapter.username == "test@example.com/admin"

    def test_starburst_custom_port(self):
        with (
            patch.dict(
                os.environ,
                {
                    "STARBURST_HOST": "test-cluster.trino.galaxy.starburst.io",
                    "STARBURST_USER": "test@example.com/admin",
                    "STARBURST_PASSWORD": "test-password",
                    "STARBURST_PORT": "8443",
                },
            ),
            patch.dict(
                "sys.modules",
                {
                    "trino": MagicMock(),
                    "trino.auth": MagicMock(),
                },
            ),
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter()

            assert adapter.port == 8443

    def test_starburst_platform_name(self):
        with patch.dict(
            "sys.modules",
            {
                "trino": MagicMock(),
                "trino.auth": MagicMock(),
            },
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter(
                host="test-cluster.trino.galaxy.starburst.io",
                username="test@example.com/admin",
                password="test-password",
            )

            assert adapter.platform_name == "Starburst"

    def test_starburst_dialect_is_trino(self):
        with patch.dict(
            "sys.modules",
            {
                "trino": MagicMock(),
                "trino.auth": MagicMock(),
            },
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter(
                host="test-cluster.trino.galaxy.starburst.io",
                username="test@example.com/admin",
                password="test-password",
            )

            assert adapter.get_target_dialect() == "trino"


class TestStarburstDeploymentRegistry:
    def test_starburst_has_deployment_modes(self):
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("starburst")
        assert caps is not None
        assert caps.deployment_modes is not None
        assert "managed" in caps.deployment_modes

    def test_default_deployment_is_managed(self):
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("starburst")
        assert caps.default_deployment == "managed"

    def test_starburst_inherits_from_trino(self):
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("starburst")
        assert caps.inherits_from == "trino"
        assert caps.platform_family == "trino"

    def test_managed_deployment_requires_credentials(self):
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("starburst")
        managed_cap = caps.deployment_modes["managed"]
        assert managed_cap.requires_credentials is True
        assert managed_cap.requires_network is True

    def test_starburst_is_cloud_category(self):
        from benchbox.core.platform_registry import PlatformRegistry

        info = PlatformRegistry.get_platform_info("starburst")
        assert info is not None
        assert info.category == "cloud"


class TestStarburstErrorMessages:
    def test_friendly_auth_error(self):
        with patch.dict(
            "sys.modules",
            {
                "trino": MagicMock(),
                "trino.auth": MagicMock(),
            },
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter(
                host="test-cluster.trino.galaxy.starburst.io",
                username="test@example.com/admin",
                password="test-password",
            )

            error = Exception("HTTP 401 Unauthorized")
            message = adapter._build_friendly_connection_error(error)

            assert message is not None
            assert "authentication" in message.lower()
            assert "email/role" in message.lower()

    def test_friendly_connection_error(self):
        with patch.dict(
            "sys.modules",
            {
                "trino": MagicMock(),
                "trino.auth": MagicMock(),
            },
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter(
                host="test-cluster.trino.galaxy.starburst.io",
                username="test@example.com/admin",
                password="test-password",
            )

            error = Exception("Connection refused")
            message = adapter._build_friendly_connection_error(error)

            assert message is not None
            assert "cannot connect" in message.lower()

    def test_friendly_ssl_error(self):
        with patch.dict(
            "sys.modules",
            {
                "trino": MagicMock(),
                "trino.auth": MagicMock(),
            },
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter(
                host="test-cluster.trino.galaxy.starburst.io",
                username="test@example.com/admin",
                password="test-password",
            )

            error = Exception("SSL certificate verify failed")
            message = adapter._build_friendly_connection_error(error)

            assert message is not None
            assert "ssl" in message.lower()


class TestStarburstGetPlatformInfo:
    def _make_adapter(self):
        with patch.dict(
            "sys.modules",
            {"trino": MagicMock(), "trino.auth": MagicMock()},
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            return StarburstAdapter(
                host="cluster.trino.galaxy.starburst.io",
                username="user@example.com/admin",
                password="secret",
            )

    def test_get_platform_info_returns_starburst_keys(self):
        adapter = self._make_adapter()
        with patch.object(
            adapter.__class__.__mro__[1],
            "get_platform_info",
            return_value={"configuration": {}},
        ):
            info = adapter.get_platform_info()

        assert info["platform_type"] == "starburst"
        assert info["platform_name"] == "Starburst Galaxy"
        assert info["connection_mode"] == "cloud"
        assert info["configuration"]["deployment"] == "managed"

    def test_get_target_dialect_returns_trino(self):
        adapter = self._make_adapter()
        assert adapter.get_target_dialect() == "trino"


class TestStarburstAddCliArguments:
    def test_add_cli_arguments_host(self):
        import argparse

        with patch.dict(
            "sys.modules",
            {"trino": MagicMock(), "trino.auth": MagicMock()},
        ):
            from benchbox.platforms.starburst import StarburstAdapter

        parser = argparse.ArgumentParser()
        StarburstAdapter.add_cli_arguments(parser)
        args = parser.parse_args(["--host", "my-cluster.trino.galaxy.starburst.io"])
        assert args.host == "my-cluster.trino.galaxy.starburst.io"

    def test_add_cli_arguments_default_port_443(self):
        import argparse

        with patch.dict(
            "sys.modules",
            {"trino": MagicMock(), "trino.auth": MagicMock()},
        ):
            from benchbox.platforms.starburst import StarburstAdapter

        parser = argparse.ArgumentParser()
        StarburstAdapter.add_cli_arguments(parser)
        args = parser.parse_args([])
        assert args.port == 443

    def test_add_cli_arguments_role(self):
        import argparse

        with patch.dict(
            "sys.modules",
            {"trino": MagicMock(), "trino.auth": MagicMock()},
        ):
            from benchbox.platforms.starburst import StarburstAdapter

        parser = argparse.ArgumentParser()
        StarburstAdapter.add_cli_arguments(parser)
        args = parser.parse_args(["--host", "h", "--role", "analyst"])
        assert args.role == "analyst"


class TestStarburstFromConfig:
    def test_from_config_passes_connection_params(self):
        with patch.dict(
            "sys.modules",
            {"trino": MagicMock(), "trino.auth": MagicMock()},
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter.from_config(
                {
                    "host": "cluster.trino.galaxy.starburst.io",
                    "username": "user@example.com/admin",
                    "password": "secret",
                    "catalog": "tpch",
                    "benchmark": "tpch",
                    "scale_factor": 0.01,
                }
            )

        assert adapter.host == "cluster.trino.galaxy.starburst.io"
        assert adapter.catalog == "tpch"

    def test_from_config_generates_schema_when_not_provided(self):
        with patch.dict(
            "sys.modules",
            {"trino": MagicMock(), "trino.auth": MagicMock()},
        ):
            from benchbox.platforms.starburst import StarburstAdapter

            adapter = StarburstAdapter.from_config(
                {
                    "host": "cluster.trino.galaxy.starburst.io",
                    "username": "user@example.com/admin",
                    "password": "secret",
                    "benchmark": "tpch",
                    "scale_factor": 0.01,
                }
            )

        assert adapter.schema is not None
        assert len(adapter.schema) > 0


