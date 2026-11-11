# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

from benchbox.cli.platform_checks import (
    check_and_setup_platform_credentials,
    check_platform_credential_status,
)
from benchbox.security.credentials import CredentialStatus

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCheckAndSetupPlatformCredentials:
    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_returns_true_when_credentials_exist(self, mock_cred_manager_class):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = {"account": "test"}
        mock_cred_manager_class.return_value = mock_manager

        console = Mock()

        result = check_and_setup_platform_credentials("snowflake", console, interactive=True)

        assert result is True
        mock_manager.get_platform_credentials.assert_called_once_with("snowflake")
        assert not console.print.called

    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_returns_false_when_credentials_missing_non_interactive(self, mock_cred_manager_class):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None
        mock_cred_manager_class.return_value = mock_manager

        console = Mock()

        result = check_and_setup_platform_credentials("databricks", console, interactive=False)

        assert result is False
        assert not console.print.called

    @patch("benchbox.cli.commands.setup.run_platform_credential_setup")
    @patch("rich.prompt.Confirm.ask")
    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_offers_setup_when_credentials_missing_interactive(self, mock_cred_manager_class, mock_confirm, mock_setup):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None
        mock_cred_manager_class.return_value = mock_manager

        mock_confirm.return_value = True
        mock_setup.return_value = True

        console = Mock()

        result = check_and_setup_platform_credentials("bigquery", console, interactive=True)

        assert result is True
        assert console.print.called
        mock_setup.assert_called_once_with("bigquery", console, show_welcome=True)

    @patch("benchbox.cli.commands.setup.run_platform_credential_setup")
    @patch("rich.prompt.Confirm.ask")
    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_returns_false_when_user_declines_setup(self, mock_cred_manager_class, mock_confirm, mock_setup):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None
        mock_cred_manager_class.return_value = mock_manager

        mock_confirm.return_value = False

        console = Mock()

        result = check_and_setup_platform_credentials("redshift", console, interactive=True)

        assert result is False
        mock_setup.assert_not_called()

    @patch("benchbox.cli.commands.setup.run_platform_credential_setup")
    @patch("rich.prompt.Confirm.ask")
    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_returns_false_when_setup_fails(self, mock_cred_manager_class, mock_confirm, mock_setup):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None
        mock_cred_manager_class.return_value = mock_manager

        mock_confirm.return_value = True
        mock_setup.return_value = False

        console = Mock()

        result = check_and_setup_platform_credentials("snowflake", console, interactive=True)

        assert result is False
        mock_setup.assert_called_once()

    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_capitalizes_platform_name_in_messages(self, mock_cred_manager_class):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None
        mock_cred_manager_class.return_value = mock_manager

        console = Mock()

        check_and_setup_platform_credentials("snowflake", console, interactive=False)

        with patch("rich.prompt.Confirm.ask", return_value=False):
            console_interactive = Mock()
            check_and_setup_platform_credentials("databricks", console_interactive, interactive=True)

            calls = [str(call) for call in console_interactive.print.call_args_list]
            all_output = " ".join(calls)
            assert "Databricks" in all_output

    @patch("benchbox.cli.commands.setup.run_platform_credential_setup")
    @patch("rich.prompt.Confirm.ask")
    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_shows_setup_command_when_user_declines(self, mock_cred_manager_class, mock_confirm, mock_setup):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None
        mock_cred_manager_class.return_value = mock_manager

        mock_confirm.return_value = False

        console = Mock()

        check_and_setup_platform_credentials("snowflake", console, interactive=True)

        calls = [str(call) for call in console.print.call_args_list]
        all_output = " ".join(calls)
        assert "benchbox setup --platform snowflake" in all_output


class TestCheckPlatformCredentialStatus:
    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_returns_missing_when_no_credentials(self, mock_cred_manager_class):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None
        mock_cred_manager_class.return_value = mock_manager

        exists, status = check_platform_credential_status("snowflake")

        assert exists is False
        assert status == CredentialStatus.MISSING

    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_returns_not_validated_when_credentials_exist_without_validation(self, mock_cred_manager_class):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = {"account": "test"}
        mock_manager.get_credential_status.return_value = CredentialStatus.NOT_VALIDATED
        mock_cred_manager_class.return_value = mock_manager

        exists, status = check_platform_credential_status("databricks")

        assert exists is True
        assert status == CredentialStatus.NOT_VALIDATED

    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_returns_valid_status_from_validation_metadata(self, mock_cred_manager_class):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = {"account": "test"}
        mock_manager.get_credential_status.return_value = CredentialStatus.VALID
        mock_cred_manager_class.return_value = mock_manager

        exists, status = check_platform_credential_status("bigquery")

        assert exists is True
        assert status == CredentialStatus.VALID

    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_returns_invalid_status_from_validation_metadata(self, mock_cred_manager_class):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = {"account": "test"}
        mock_manager.get_credential_status.return_value = CredentialStatus.INVALID
        mock_cred_manager_class.return_value = mock_manager

        exists, status = check_platform_credential_status("redshift")

        assert exists is True
        assert status == CredentialStatus.INVALID

    @patch("benchbox.cli.platform_checks.CredentialManager")
    def test_handles_unknown_status_gracefully(self, mock_cred_manager_class):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = {"account": "test"}
        mock_manager.get_credential_status.return_value = CredentialStatus.NOT_VALIDATED
        mock_cred_manager_class.return_value = mock_manager

        exists, status = check_platform_credential_status("snowflake")

        assert exists is True
        assert status == CredentialStatus.NOT_VALIDATED


