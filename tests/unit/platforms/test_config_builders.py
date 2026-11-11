# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAzureConfigUtils:
    def _call(self, **kwargs):
        from benchbox.platforms.azure.config_utils import build_platform_config

        defaults = {
            "platform_type": "synapse",
            "credential_key": "synapse",
            "default_display_name": "Azure Synapse",
            "default_driver_package": "pyodbc",
            "platform_fields": [],
            "options": {},
            "overrides": {"benchmark": "tpch", "scale_factor": 1},
            "info": None,
        }
        defaults.update(kwargs)
        return build_platform_config(**defaults)

    @patch("benchbox.security.credentials.CredentialManager")
    def test_minimal_args_sets_type_and_name(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {}
        config = self._call()
        assert config.type == "synapse"
        assert config.name == "Azure Synapse"

    @patch("benchbox.security.credentials.CredentialManager")
    def test_info_not_none_uses_info_display_name(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {}
        info = MagicMock()
        info.display_name = "Custom Synapse"
        info.driver_package = "custom-driver"
        config = self._call(info=info)
        assert config.name == "Custom Synapse"
        assert config.driver_package == "custom-driver"

    @patch("benchbox.security.credentials.CredentialManager")
    def test_option_merge_precedence(self, MockCM):

        MockCM.return_value.get_platform_credentials.return_value = {"host": "from-saved"}
        config = self._call(
            platform_fields=["host"],
            options={"host": "from-options"},
            overrides={"host": "from-overrides", "benchmark": "tpch", "scale_factor": 1},
        )
        assert config.host == "from-overrides"

    @patch("benchbox.security.credentials.CredentialManager")
    def test_platform_fields_extracted(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {}
        config = self._call(
            platform_fields=["host", "port"],
            options={"host": "myhost", "port": 1433},
        )
        assert config.host == "myhost"
        assert config.port == 1433

    @patch("benchbox.security.credentials.CredentialManager")
    def test_driver_version_from_overrides(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {}
        config = self._call(overrides={"driver_version": "1.2.3", "benchmark": "tpch", "scale_factor": 1})
        assert config.driver_version == "1.2.3"

    @patch("benchbox.security.credentials.CredentialManager")
    def test_driver_auto_install_true(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {}
        config = self._call(options={"driver_auto_install": "true"})
        assert config.driver_auto_install is True

    @patch("benchbox.security.credentials.CredentialManager")
    def test_database_override(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {}
        config = self._call(overrides={"database": "mydb", "benchmark": "tpch", "scale_factor": 1})
        assert config.database == "mydb"

    @patch("benchbox.security.credentials.CredentialManager")
    def test_base_options_seed_value_appears_in_result(self, MockCM):

        MockCM.return_value.get_platform_credentials.return_value = {}
        config = self._call(
            platform_fields=["schema"],
            base_options={"schema": "public"},
            options={},
            overrides={"benchmark": "tpch", "scale_factor": 1},
        )
        assert config.model_dump()["schema"] == "public"

    @patch("benchbox.security.credentials.CredentialManager")
    def test_base_options_overridden_by_saved_creds(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {"schema": "custom"}
        config = self._call(
            platform_fields=["schema"],
            base_options={"schema": "public"},
            options={},
            overrides={"benchmark": "tpch", "scale_factor": 1},
        )
        assert config.model_dump()["schema"] == "custom"

    @patch("benchbox.security.credentials.CredentialManager")
    def test_base_options_overridden_by_options(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {}
        config = self._call(
            platform_fields=["schema"],
            base_options={"schema": "public"},
            options={"schema": "registry_default"},
            overrides={"benchmark": "tpch", "scale_factor": 1},
        )
        assert config.model_dump()["schema"] == "registry_default"


class TestDatabendConfigBuilder:
    def _call(self, **kwargs):
        from benchbox.platforms.databend import _build_databend_config

        defaults = {
            "platform": "databend",
            "options": {},
            "overrides": {"benchmark": "tpch", "scale_factor": 1},
            "info": None,
        }
        defaults.update(kwargs)
        return _build_databend_config(**defaults)

    @patch("benchbox.security.credentials.CredentialManager")
    def test_info_none_defaults(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {}
        config = self._call()
        assert config.name == "Databend"
        assert config.driver_package == "databend-driver"

    @patch("benchbox.security.credentials.CredentialManager")
    def test_all_databend_fields_extracted(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {}
        opts = {
            "host": "cloud.databend.com",
            "port": 443,
            "username": "admin",
            "password": "secret",
            "database": "default",
            "dsn": "databend://...",
            "warehouse": "wh1",
            "ssl": True,
            "disable_result_cache": True,
        }
        config = self._call(options=opts)
        assert config.host == "cloud.databend.com"
        assert config.port == 443
        assert config.username == "admin"
        assert config.password == "secret"
        assert config.database == "default"
        assert config.dsn == "databend://..."
        assert config.warehouse == "wh1"
        assert config.ssl is True
        assert config.disable_result_cache is True

    @patch("benchbox.security.credentials.CredentialManager")
    def test_merge_precedence(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {"host": "saved-host"}
        config = self._call(
            options={"host": "opt-host"},
            overrides={"host": "override-host", "benchmark": "tpch", "scale_factor": 1},
        )
        assert config.host == "override-host"

    @patch("benchbox.security.credentials.CredentialManager")
    def test_saved_creds_lower_precedence(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {"host": "saved-host"}
        config = self._call(options={}, overrides={"benchmark": "tpch", "scale_factor": 1})
        assert config.host == "saved-host"

    @patch("benchbox.security.credentials.CredentialManager")
    def test_info_provides_name_and_driver(self, MockCM):
        MockCM.return_value.get_platform_credentials.return_value = {}
        info = MagicMock()
        info.display_name = "Databend Cloud"
        info.driver_package = "databend-sqlalchemy"
        config = self._call(info=info)
        assert config.name == "Databend Cloud"
        assert config.driver_package == "databend-sqlalchemy"


