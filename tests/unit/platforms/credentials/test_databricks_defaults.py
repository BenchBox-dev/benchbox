# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.databricks.credentials import setup_databricks_credentials

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDatabricksCredentialDefaults:
    @patch("benchbox.platforms.databricks.credentials.validate_databricks_credentials")
    @patch("benchbox.platforms.databricks.credentials._prompt_default_output_location")
    @patch("benchbox.platforms.databricks.credentials.prompt_secure_field")
    @patch("benchbox.platforms.databricks.credentials.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_shows_existing_values_as_defaults(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_prompt_secure,
        mock_output_location,
        mock_validate,
    ):

        mock_manager = Mock()
        existing_creds = {
            "server_hostname": "myworkspace.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc123",
            "access_token": "secret_token",
            "catalog": "main",
            "schema": "benchbox",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "myworkspace.cloud.databricks.com",
            "/sql/1.0/warehouses/abc123",
            "main",
            "benchbox",
        ]
        mock_prompt_secure.return_value = "secret_token"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_databricks_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] == "myworkspace.cloud.databricks.com"
        assert calls[1][1]["current_value"] == "/sql/1.0/warehouses/abc123"
        assert calls[2][1]["current_value"] == "main"
        assert calls[3][1]["current_value"] == "benchbox"

        mock_prompt_secure.assert_called_once_with("Access token", current_value="secret_token", console=console)

    @patch("benchbox.platforms.databricks.credentials.validate_databricks_credentials")
    @patch("benchbox.platforms.databricks.credentials._prompt_default_output_location")
    @patch("benchbox.platforms.databricks.credentials.prompt_secure_field")
    @patch("benchbox.platforms.databricks.credentials.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_works_with_no_existing_credentials(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_prompt_secure,
        mock_output_location,
        mock_validate,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "newworkspace.cloud.databricks.com",
            "/sql/1.0/warehouses/new123",
            "main",
            "benchbox",
        ]
        mock_prompt_secure.return_value = "new_token"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_databricks_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] is None
        assert calls[1][1]["current_value"] is None
        assert calls[2][1]["current_value"] is None
        assert calls[3][1]["current_value"] is None

        mock_prompt_secure.assert_called_once_with("Access token", current_value=None, console=console)

    @patch("benchbox.platforms.databricks.credentials.validate_databricks_credentials")
    @patch("benchbox.platforms.databricks.credentials._prompt_default_output_location")
    @patch("benchbox.platforms.databricks.credentials.prompt_secure_field")
    @patch("benchbox.platforms.databricks.credentials.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_token_preserved_on_empty_input(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_prompt_secure,
        mock_output_location,
        mock_validate,
    ):

        mock_manager = Mock()
        existing_creds = {
            "server_hostname": "myworkspace.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc123",
            "access_token": "existing_token",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "myworkspace.cloud.databricks.com",
            "/sql/1.0/warehouses/abc123",
            "main",
            "benchbox",
        ]
        mock_prompt_secure.return_value = "existing_token"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_databricks_credentials(mock_manager, console)

        saved_creds = mock_manager.set_platform_credentials.call_args[0][1]
        assert saved_creds["access_token"] == "existing_token"

    @patch("benchbox.platforms.databricks.credentials._auto_detect_databricks")
    @patch("benchbox.platforms.databricks.credentials.validate_databricks_credentials")
    @patch("benchbox.platforms.databricks.credentials._prompt_default_output_location")
    @patch("benchbox.platforms.databricks.credentials.prompt_secure_field")
    @patch("benchbox.platforms.databricks.credentials.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_auto_detection_bypasses_existing_defaults(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_prompt_secure,
        mock_output_location,
        mock_validate,
        mock_auto_detect,
    ):

        mock_manager = Mock()
        existing_creds = {
            "server_hostname": "old_workspace.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/old123",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_prompt_default.side_effect = [
            "old_workspace.cloud.databricks.com",
            "/sql/1.0/warehouses/old123",
            "main",
            "benchbox",
        ]
        mock_prompt_secure.return_value = "token"
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_databricks_credentials(mock_manager, console)

        mock_confirm.assert_not_called()
        mock_auto_detect.assert_not_called()

    @patch("benchbox.platforms.databricks.credentials._auto_detect_databricks")
    @patch("benchbox.platforms.databricks.credentials.validate_databricks_credentials")
    @patch("benchbox.platforms.databricks.credentials._prompt_default_output_location")
    @patch("benchbox.platforms.databricks.credentials.prompt_secure_field")
    @patch("benchbox.platforms.databricks.credentials.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_skips_auto_detection_when_credentials_exist(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_prompt_secure,
        mock_output_location,
        mock_validate,
        mock_auto_detect,
    ):

        mock_manager = Mock()
        existing_creds = {
            "server_hostname": "myworkspace.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc123",
            "access_token": "secret_token",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_prompt_default.side_effect = [
            "myworkspace.cloud.databricks.com",
            "/sql/1.0/warehouses/abc123",
            "main",
            "benchbox",
        ]
        mock_prompt_secure.return_value = "secret_token"
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_databricks_credentials(mock_manager, console)

        mock_confirm.assert_not_called()
        mock_auto_detect.assert_not_called()

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "Existing credentials found" in console_output
        assert "updating configuration" in console_output

    @patch("benchbox.platforms.databricks.credentials._auto_detect_databricks")
    @patch("benchbox.platforms.databricks.credentials.validate_databricks_credentials")
    @patch("benchbox.platforms.databricks.credentials._prompt_default_output_location")
    @patch("benchbox.platforms.databricks.credentials.prompt_secure_field")
    @patch("benchbox.platforms.databricks.credentials.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_offers_auto_detection_when_no_credentials_exist(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_prompt_secure,
        mock_output_location,
        mock_validate,
        mock_auto_detect,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "newworkspace.cloud.databricks.com",
            "/sql/1.0/warehouses/new123",
            "main",
            "benchbox",
        ]
        mock_prompt_secure.return_value = "new_token"
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_databricks_credentials(mock_manager, console)

        mock_confirm.assert_called_once_with("🔍 Attempt auto-detection using Databricks SDK?", default=True)

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "Existing credentials found" not in console_output

    @patch("benchbox.platforms.databricks.credentials.validate_databricks_credentials")
    @patch("benchbox.platforms.databricks.credentials._prompt_default_output_location")
    @patch("benchbox.platforms.databricks.credentials.prompt_secure_field")
    @patch("benchbox.platforms.databricks.credentials.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_partial_existing_credentials(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_prompt_secure,
        mock_output_location,
        mock_validate,
    ):

        mock_manager = Mock()
        existing_creds = {
            "server_hostname": "myworkspace.cloud.databricks.com",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "myworkspace.cloud.databricks.com",
            "/sql/1.0/warehouses/new123",
            "main",
            "benchbox",
        ]
        mock_prompt_secure.return_value = "new_token"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_databricks_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] == "myworkspace.cloud.databricks.com"
        assert calls[1][1]["current_value"] is None
        assert calls[2][1]["current_value"] is None
        assert calls[2][1]["default_if_none"] == "main"


