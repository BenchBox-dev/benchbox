# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from benchbox.security.credentials import CredentialManager, CredentialStatus

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCredentialManager:
    @pytest.fixture
    def temp_creds_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            path = Path(f.name)
        yield path
        if path.exists():
            path.unlink()

    @pytest.fixture
    def manager(self, temp_creds_file):
        return CredentialManager(credentials_path=temp_creds_file)

    def test_init_with_default_path(self):
        manager = CredentialManager()
        expected_path = Path.home() / ".benchbox" / "credentials.yaml"
        assert manager.credentials_path == expected_path

    def test_init_with_custom_path(self, temp_creds_file):
        manager = CredentialManager(credentials_path=temp_creds_file)
        assert manager.credentials_path == temp_creds_file

    def test_load_nonexistent_file(self, manager):
        assert manager.credentials == {}

    def test_set_and_get_platform_credentials(self, manager):
        creds = {
            "server_hostname": "test.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc123",
            "access_token": "secret_token",
        }

        manager.set_platform_credentials("databricks", creds)
        retrieved = manager.get_platform_credentials("databricks")

        assert retrieved is not None
        assert retrieved["server_hostname"] == creds["server_hostname"]
        assert retrieved["http_path"] == creds["http_path"]
        assert retrieved["access_token"] == creds["access_token"]
        assert "last_updated" in retrieved
        assert retrieved["status"] == CredentialStatus.NOT_VALIDATED.value

    def test_set_credentials_with_custom_status(self, manager):
        creds = {"user": "test_user", "password": "secret"}

        manager.set_platform_credentials("snowflake", creds, CredentialStatus.VALID)
        retrieved = manager.get_platform_credentials("snowflake")

        assert retrieved["status"] == CredentialStatus.VALID.value

    def test_get_nonexistent_platform(self, manager):
        result = manager.get_platform_credentials("nonexistent")
        assert result is None

    def test_has_credentials(self, manager):
        assert not manager.has_credentials("databricks")

        manager.set_platform_credentials("databricks", {"test": "value"})
        assert manager.has_credentials("databricks")

    def test_remove_platform_credentials(self, manager):
        manager.set_platform_credentials("databricks", {"test": "value"})
        assert manager.has_credentials("databricks")

        result = manager.remove_platform_credentials("databricks")
        assert result is True
        assert not manager.has_credentials("databricks")

    def test_remove_nonexistent_platform(self, manager):
        result = manager.remove_platform_credentials("nonexistent")
        assert result is False

    def test_update_validation_status(self, manager):
        manager.set_platform_credentials("databricks", {"test": "value"})

        manager.update_validation_status("databricks", CredentialStatus.VALID)
        creds = manager.get_platform_credentials("databricks")

        assert creds["status"] == CredentialStatus.VALID.value
        assert "last_validated" in creds
        assert "error_message" not in creds

    def test_update_validation_status_with_error(self, manager):
        manager.set_platform_credentials("databricks", {"test": "value"})

        manager.update_validation_status("databricks", CredentialStatus.INVALID, "Authentication failed")
        creds = manager.get_platform_credentials("databricks")

        assert creds["status"] == CredentialStatus.INVALID.value
        assert creds["error_message"] == "Authentication failed"

    def test_update_validation_clears_previous_error(self, manager):
        manager.set_platform_credentials("databricks", {"test": "value"})

        manager.update_validation_status("databricks", CredentialStatus.INVALID, "Authentication failed")
        manager.update_validation_status("databricks", CredentialStatus.VALID)

        creds = manager.get_platform_credentials("databricks")
        assert "error_message" not in creds

    def test_get_credential_status(self, manager):
        status = manager.get_credential_status("databricks")
        assert status == CredentialStatus.MISSING

        manager.set_platform_credentials("databricks", {"test": "value"})
        status = manager.get_credential_status("databricks")
        assert status == CredentialStatus.NOT_VALIDATED

        manager.update_validation_status("databricks", CredentialStatus.VALID)
        status = manager.get_credential_status("databricks")
        assert status == CredentialStatus.VALID

    def test_list_platforms(self, manager):
        assert len(manager.list_platforms()) == 0

        manager.set_platform_credentials("databricks", {"test": "value"})
        manager.set_platform_credentials("snowflake", {"test": "value"})

        platforms = manager.list_platforms()
        assert len(platforms) == 2
        assert "databricks" in platforms
        assert "snowflake" in platforms
        assert platforms["databricks"] == CredentialStatus.NOT_VALIDATED
        assert platforms["snowflake"] == CredentialStatus.NOT_VALIDATED

    def test_save_and_load_credentials(self, temp_creds_file):
        manager1 = CredentialManager(credentials_path=temp_creds_file)

        creds = {
            "server_hostname": "test.databricks.com",
            "access_token": "secret",
        }
        manager1.set_platform_credentials("databricks", creds, CredentialStatus.VALID)
        manager1.save_credentials()

        manager2 = CredentialManager(credentials_path=temp_creds_file)
        loaded_creds = manager2.get_platform_credentials("databricks")

        assert loaded_creds is not None
        assert loaded_creds["server_hostname"] == creds["server_hostname"]
        assert loaded_creds["access_token"] == creds["access_token"]
        assert loaded_creds["status"] == CredentialStatus.VALID.value

    def test_saved_file_has_secure_permissions(self, temp_creds_file):
        if os.name == "nt":
            pytest.skip("File permission check not applicable on Windows")

        manager = CredentialManager(credentials_path=temp_creds_file)
        manager.set_platform_credentials("databricks", {"test": "value"})
        manager.save_credentials()

        stat_info = temp_creds_file.stat()
        permissions = stat_info.st_mode & 0o777
        assert permissions == 0o600

    def test_saved_file_includes_metadata(self, temp_creds_file):
        manager = CredentialManager(credentials_path=temp_creds_file)
        manager.set_platform_credentials("databricks", {"test": "value"})
        manager.save_credentials()

        with open(temp_creds_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert "_metadata" in data
        assert "version" in data["_metadata"]
        assert "last_updated" in data["_metadata"]
        assert data["_metadata"]["version"] == "1.0"

    def test_get_display_credentials_masks_sensitive_fields(self, manager):
        creds = {
            "server_hostname": "test.databricks.com",
            "access_token": "very_long_secret_token_12345678",
            "user": "test_user",
        }

        manager.set_platform_credentials("databricks", creds)
        display_creds = manager.get_display_credentials("databricks")

        assert display_creds["server_hostname"] == "test.databricks.com"
        assert display_creds["user"] == "test_user"
        assert display_creds["access_token"] == "very...5678"
        assert "very_long_secret" not in str(display_creds)

    def test_get_display_credentials_masks_short_secrets(self, manager):
        creds = {
            "password": "short",
            "api_key": "key123",
        }

        manager.set_platform_credentials("platform", creds)
        display_creds = manager.get_display_credentials("platform")

        assert display_creds["password"] == "****"
        assert display_creds["api_key"] == "****"

    def test_get_display_credentials_for_missing_platform(self, manager):
        display_creds = manager.get_display_credentials("nonexistent")
        assert display_creds == {}


class TestEnvironmentVariableSubstitution:
    @pytest.fixture
    def temp_creds_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            path = Path(f.name)
        yield path
        if path.exists():
            path.unlink()

    def test_substitute_dollar_brace_syntax(self, temp_creds_file):
        os.environ["TEST_HOSTNAME"] = "test.databricks.com"
        os.environ["TEST_TOKEN"] = "secret_token_123"

        with open(temp_creds_file, "w", encoding="utf-8") as f:
            yaml.dump(
                {
                    "databricks": {
                        "server_hostname": "${TEST_HOSTNAME}",
                        "access_token": "${TEST_TOKEN}",
                    }
                },
                f,
            )

        manager = CredentialManager(credentials_path=temp_creds_file)
        creds = manager.get_platform_credentials("databricks")

        assert creds["server_hostname"] == "test.databricks.com"
        assert creds["access_token"] == "secret_token_123"

        del os.environ["TEST_HOSTNAME"]
        del os.environ["TEST_TOKEN"]

    def test_substitute_dollar_syntax(self, temp_creds_file):
        os.environ["TEST_USER"] = "test_user"

        with open(temp_creds_file, "w", encoding="utf-8") as f:
            yaml.dump({"snowflake": {"user": "$TEST_USER"}}, f)

        manager = CredentialManager(credentials_path=temp_creds_file)
        creds = manager.get_platform_credentials("snowflake")

        assert creds["user"] == "test_user"

        del os.environ["TEST_USER"]

    def test_missing_env_var_keeps_original(self, temp_creds_file):
        with open(temp_creds_file, "w", encoding="utf-8") as f:
            yaml.dump({"platform": {"value": "${NONEXISTENT_VAR}"}}, f)

        manager = CredentialManager(credentials_path=temp_creds_file)
        creds = manager.get_platform_credentials("platform")

        assert creds["value"] == "${NONEXISTENT_VAR}"

    def test_nested_env_var_substitution(self, temp_creds_file):
        os.environ["TEST_CATALOG"] = "production"

        with open(temp_creds_file, "w", encoding="utf-8") as f:
            yaml.dump(
                {
                    "databricks": {
                        "config": {
                            "catalog": "${TEST_CATALOG}",
                            "schema": "benchbox",
                        }
                    }
                },
                f,
            )

        manager = CredentialManager(credentials_path=temp_creds_file)
        creds = manager.get_platform_credentials("databricks")

        assert creds["config"]["catalog"] == "production"
        assert creds["config"]["schema"] == "benchbox"

        del os.environ["TEST_CATALOG"]

    def test_env_var_substitution_in_list(self, temp_creds_file):
        os.environ["TEST_ROLE"] = "ROLE1"

        with open(temp_creds_file, "w", encoding="utf-8") as f:
            yaml.dump({"snowflake": {"roles": ["${TEST_ROLE}", "ROLE2"]}}, f)

        manager = CredentialManager(credentials_path=temp_creds_file)
        creds = manager.get_platform_credentials("snowflake")

        assert creds["roles"][0] == "ROLE1"
        assert creds["roles"][1] == "ROLE2"

        del os.environ["TEST_ROLE"]


class TestCredentialStatus:
    def test_enum_values(self):
        assert CredentialStatus.VALID.value == "valid"
        assert CredentialStatus.INVALID.value == "invalid"
        assert CredentialStatus.EXPIRED.value == "expired"
        assert CredentialStatus.NOT_VALIDATED.value == "not_validated"
        assert CredentialStatus.MISSING.value == "missing"

    def test_enum_from_value(self):
        status = CredentialStatus("valid")
        assert status == CredentialStatus.VALID

        status = CredentialStatus("invalid")
        assert status == CredentialStatus.INVALID

    def test_invalid_enum_value(self):
        with pytest.raises(ValueError):
            CredentialStatus("unknown")


class TestCredentialManagerEdgeCases:
    @pytest.fixture
    def temp_creds_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            path = Path(f.name)
        yield path
        if path.exists():
            path.unlink()

    def test_load_invalid_yaml(self, temp_creds_file):
        with open(temp_creds_file, "w", encoding="utf-8") as f:
            f.write("invalid: yaml: content:\n  - broken")

        with pytest.raises(ValueError):
            CredentialManager(credentials_path=temp_creds_file)

    def test_platform_name_case_insensitive(self, temp_creds_file):
        manager = CredentialManager(credentials_path=temp_creds_file)

        manager.set_platform_credentials("Databricks", {"test": "value"})

        assert manager.has_credentials("databricks")
        assert manager.has_credentials("DATABRICKS")
        assert manager.has_credentials("Databricks")

    def test_update_validation_for_nonexistent_platform(self, temp_creds_file):
        manager = CredentialManager(credentials_path=temp_creds_file)

        manager.update_validation_status("nonexistent", CredentialStatus.VALID)

        assert not manager.has_credentials("nonexistent")

    def test_list_platforms_excludes_metadata(self, temp_creds_file):
        manager = CredentialManager(credentials_path=temp_creds_file)

        manager.set_platform_credentials("databricks", {"test": "value"})
        manager.save_credentials()

        manager.credentials["_internal_key"] = "should_be_excluded"

        platforms = manager.list_platforms()

        assert "databricks" in platforms
        assert "_internal_key" not in platforms
        assert "_metadata" not in platforms


