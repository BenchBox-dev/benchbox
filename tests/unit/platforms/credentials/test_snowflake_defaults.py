# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.credentials.snowflake import setup_snowflake_credentials

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSnowflakeCredentialDefaults:
    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.snowflake.prompt_secure_field")
    @patch("benchbox.platforms.credentials.snowflake.prompt_with_default")
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
            "account": "myorg-account123",
            "username": "JOEHARRIS76",
            "password": "secret_password",
            "warehouse": "MY_WAREHOUSE",
            "database": "MY_DATABASE",
            "schema": "MY_SCHEMA",
            "role": "MY_ROLE",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "myorg-account123",
            "JOEHARRIS76",
            "MY_WAREHOUSE",
            "MY_DATABASE",
            "MY_SCHEMA",
            "MY_ROLE",
        ]
        mock_prompt_secure.return_value = "secret_password"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] == "myorg-account123"
        assert calls[1][1]["current_value"] == "JOEHARRIS76"
        assert calls[2][1]["current_value"] == "MY_WAREHOUSE"
        assert calls[3][1]["current_value"] == "MY_DATABASE"
        assert calls[4][1]["current_value"] == "MY_SCHEMA"
        assert calls[5][1]["current_value"] == "MY_ROLE"

        mock_prompt_secure.assert_called_once_with("Password", current_value="secret_password", console=console)

    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.snowflake.prompt_secure_field")
    @patch("benchbox.platforms.credentials.snowflake.prompt_with_default")
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
            "newaccount",
            "newuser",
            "COMPUTE_WH",
            "BENCHBOX",
            "PUBLIC",
            "",
        ]
        mock_prompt_secure.return_value = "newpassword"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] is None
        assert calls[1][1]["current_value"] is None
        assert calls[2][1]["current_value"] is None
        assert calls[3][1]["current_value"] is None
        assert calls[4][1]["current_value"] is None
        assert calls[5][1]["current_value"] is None

        mock_prompt_secure.assert_called_once_with("Password", current_value=None, console=console)

    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.snowflake.prompt_secure_field")
    @patch("benchbox.platforms.credentials.snowflake.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_password_preserved_on_empty_input(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_prompt_secure,
        mock_output_location,
        mock_validate,
    ):

        mock_manager = Mock()
        existing_creds = {
            "account": "myorg-account123",
            "username": "JOEHARRIS76",
            "password": "existing_secret",
            "warehouse": "MY_WAREHOUSE",
            "database": "MY_DATABASE",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "myorg-account123",
            "JOEHARRIS76",
            "MY_WAREHOUSE",
            "MY_DATABASE",
            "PUBLIC",
            "",
        ]
        mock_prompt_secure.return_value = "existing_secret"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        saved_creds = mock_manager.set_platform_credentials.call_args[0][1]
        assert saved_creds["password"] == "existing_secret"

    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.snowflake.prompt_secure_field")
    @patch("benchbox.platforms.credentials.snowflake.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_new_password_overrides_existing(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_prompt_secure,
        mock_output_location,
        mock_validate,
    ):

        mock_manager = Mock()
        existing_creds = {
            "account": "myorg-account123",
            "username": "JOEHARRIS76",
            "password": "old_password",
            "warehouse": "MY_WAREHOUSE",
            "database": "MY_DATABASE",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "myorg-account123",
            "JOEHARRIS76",
            "MY_WAREHOUSE",
            "MY_DATABASE",
            "PUBLIC",
            "",
        ]
        mock_prompt_secure.return_value = "new_password"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        saved_creds = mock_manager.set_platform_credentials.call_args[0][1]
        assert saved_creds["password"] == "new_password"

    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.snowflake.prompt_secure_field")
    @patch("benchbox.platforms.credentials.snowflake.prompt_with_default")
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
            "account": "myorg-account123",
            "username": "JOEHARRIS76",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "myorg-account123",
            "JOEHARRIS76",
            "COMPUTE_WH",
            "BENCHBOX",
            "PUBLIC",
            "",
        ]
        mock_prompt_secure.return_value = "new_password"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] == "myorg-account123"
        assert calls[1][1]["current_value"] == "JOEHARRIS76"
        assert calls[2][1]["current_value"] is None
        assert calls[2][1]["default_if_none"] == "COMPUTE_WH"

    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.snowflake.prompt_secure_field")
    @patch("benchbox.platforms.credentials.snowflake.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_optional_fields_show_existing_values(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_prompt_secure,
        mock_output_location,
        mock_validate,
    ):
        mock_manager = Mock()
        existing_creds = {
            "account": "myorg-account123",
            "username": "JOEHARRIS76",
            "password": "secret",
            "warehouse": "MY_WAREHOUSE",
            "database": "MY_DATABASE",
            "schema": "CUSTOM_SCHEMA",
            "role": "CUSTOM_ROLE",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "myorg-account123",
            "JOEHARRIS76",
            "MY_WAREHOUSE",
            "MY_DATABASE",
            "CUSTOM_SCHEMA",
            "CUSTOM_ROLE",
        ]
        mock_prompt_secure.return_value = "secret"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[4][1]["current_value"] == "CUSTOM_SCHEMA"
        assert calls[5][1]["current_value"] == "CUSTOM_ROLE"

    @patch("benchbox.platforms.credentials.snowflake._auto_detect_snowflake")
    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.snowflake.prompt_secure_field")
    @patch("benchbox.platforms.credentials.snowflake.prompt_with_default")
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
            "account": "old_account",
            "username": "old_user",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_prompt_default.side_effect = [
            "old_account",
            "old_user",
            "COMPUTE_WH",
            "BENCHBOX",
            "PUBLIC",
            "",
        ]
        mock_prompt_secure.return_value = "password"
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        mock_confirm.assert_not_called()
        mock_auto_detect.assert_not_called()

    @patch("benchbox.platforms.credentials.snowflake._auto_detect_snowflake")
    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.snowflake.prompt_secure_field")
    @patch("benchbox.platforms.credentials.snowflake.prompt_with_default")
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
            "account": "myorg-account123",
            "username": "JOEHARRIS76",
            "password": "secret",
            "warehouse": "MY_WAREHOUSE",
            "database": "MY_DATABASE",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_prompt_default.side_effect = [
            "myorg-account123",
            "JOEHARRIS76",
            "MY_WAREHOUSE",
            "MY_DATABASE",
            "PUBLIC",
            "",
        ]
        mock_prompt_secure.return_value = "secret"
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        mock_confirm.assert_not_called()
        mock_auto_detect.assert_not_called()

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "Existing credentials found" in console_output
        assert "updating configuration" in console_output

    @patch("benchbox.platforms.credentials.snowflake._auto_detect_snowflake")
    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.snowflake.prompt_secure_field")
    @patch("benchbox.platforms.credentials.snowflake.prompt_with_default")
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
            "newaccount",
            "newuser",
            "COMPUTE_WH",
            "BENCHBOX",
            "PUBLIC",
            "",
        ]
        mock_prompt_secure.return_value = "newpassword"
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        mock_confirm.assert_called_once_with("🔍 Attempt auto-detection from environment variables?", default=True)

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "Existing credentials found" not in console_output

    @patch("benchbox.platforms.credentials.snowflake._auto_detect_snowflake")
    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("rich.prompt.Confirm.ask")
    def test_auto_detection_success_skips_manual_prompts(
        self,
        mock_confirm,
        mock_output_location,
        mock_validate,
        mock_auto_detect,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.return_value = True
        mock_auto_detect.return_value = {
            "account": "auto-account",
            "username": "auto-user",
            "password": "auto-pass",
            "warehouse": "AUTO_WH",
            "database": "AUTO_DB",
            "schema": "AUTO_SCHEMA",
            "role": "AUTO_ROLE",
        }
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        saved_creds = mock_manager.set_platform_credentials.call_args[0][1]
        assert saved_creds["account"] == "auto-account"
        assert saved_creds["username"] == "auto-user"
        assert saved_creds["compute_resource"] == "AUTO_WH"
        assert "warehouse" not in saved_creds
        assert saved_creds["database"] == "AUTO_DB"

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "auto-account" in console_output
        assert "auto-user" in console_output

    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    @patch("benchbox.platforms.credentials.snowflake._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.snowflake.prompt_secure_field")
    @patch("benchbox.platforms.credentials.snowflake.prompt_with_default")
    @patch("rich.prompt.Confirm.ask")
    def test_validation_failure_saves_invalid_credentials(
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
            "badaccount",
            "user",
            "WH",
            "DB",
            "PUBLIC",
            "",
        ]
        mock_prompt_secure.return_value = "wrongpassword"
        mock_validate.return_value = (False, "Authentication failed. Check your username and password.")
        console = Mock()

        setup_snowflake_credentials(mock_manager, console)

        mock_output_location.assert_not_called()

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "Validation failed" in console_output


class TestPromptDefaultOutputLocation:
    def _make_manager(self):
        mgr = Mock()
        mgr.credentials_path = "/fake/path"
        return mgr

    @patch("benchbox.platforms.credentials.snowflake.Confirm.ask")
    def test_user_declines_output_location(self, mock_confirm):
        from benchbox.platforms.credentials.snowflake import _prompt_default_output_location

        mgr = self._make_manager()
        console = Mock()
        mock_confirm.return_value = False

        _prompt_default_output_location(mgr, console, {"account": "acct"})

        mgr.set_platform_credentials.assert_not_called()

    @patch("benchbox.platforms.credentials.snowflake.Prompt.ask")
    @patch("benchbox.platforms.credentials.snowflake.Confirm.ask")
    def test_user_stage_path_saved(self, mock_confirm, mock_prompt):
        from benchbox.platforms.credentials.snowflake import _prompt_default_output_location

        mgr = self._make_manager()
        console = Mock()
        mock_confirm.side_effect = [True, True]
        mock_prompt.return_value = "@~/benchbox"

        creds = {"account": "acct"}
        _prompt_default_output_location(mgr, console, creds)

        assert creds["default_output_location"] == "@~/benchbox"
        mgr.set_platform_credentials.assert_called_once()
        mgr.save_credentials.assert_called()

    @patch("benchbox.platforms.credentials.snowflake.Prompt.ask")
    @patch("benchbox.platforms.credentials.snowflake.Confirm.ask")
    @patch("benchbox.utils.cloud_storage.is_cloud_path")
    def test_invalid_path_with_confirmation_proceeds(self, mock_is_cloud, mock_confirm, mock_prompt):
        from benchbox.platforms.credentials.snowflake import _prompt_default_output_location

        mgr = self._make_manager()
        console = Mock()
        mock_confirm.side_effect = [True, True, True]
        mock_prompt.return_value = "not-a-valid-path"
        mock_is_cloud.return_value = False

        creds = {"account": "acct"}
        _prompt_default_output_location(mgr, console, creds)

        assert creds["default_output_location"] == "not-a-valid-path"

    @patch("benchbox.platforms.credentials.snowflake.Prompt.ask")
    @patch("benchbox.platforms.credentials.snowflake.Confirm.ask")
    @patch("benchbox.utils.cloud_storage.is_cloud_path")
    def test_invalid_path_declined_retries_then_valid(self, mock_is_cloud, mock_confirm, mock_prompt):
        from benchbox.platforms.credentials.snowflake import _prompt_default_output_location

        mgr = self._make_manager()
        console = Mock()
        mock_confirm.side_effect = [True, False, True]
        mock_prompt.side_effect = ["bad-path", "@~/good"]
        mock_is_cloud.side_effect = [False, True]

        creds = {"account": "acct"}
        _prompt_default_output_location(mgr, console, creds)

        assert creds["default_output_location"] == "@~/good"

    @patch("benchbox.platforms.credentials.snowflake.Prompt.ask")
    @patch("benchbox.platforms.credentials.snowflake.Confirm.ask")
    def test_valid_cloud_path_saved(self, mock_confirm, mock_prompt):
        from benchbox.platforms.credentials.snowflake import _prompt_default_output_location

        mgr = self._make_manager()
        console = Mock()
        mock_confirm.side_effect = [True, True]
        mock_prompt.return_value = "s3://my-bucket/data"

        creds = {"account": "acct"}
        _prompt_default_output_location(mgr, console, creds)

        assert creds["default_output_location"] == "s3://my-bucket/data"


class TestValidateSnowflakeErrorTranslation:
    def _make_mgr_with_creds(self):
        mgr = Mock()
        mgr.get_platform_credentials.return_value = {
            "account": "acct",
            "username": "u",
            "password": "p",
            "warehouse": "WH",
            "database": "DB",
            "role": "MYROLE",
        }
        return mgr

    def _run_with_error(self, error_message: str):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.snowflake import validate_snowflake_credentials

        mock_connector = MagicMock()
        mock_connector.connect.side_effect = Exception(error_message)
        mock_snowflake = MagicMock()
        mock_snowflake.connector = mock_connector

        mgr = self._make_mgr_with_creds()
        with patch.dict("sys.modules", {"snowflake": mock_snowflake, "snowflake.connector": mock_connector}):
            return validate_snowflake_credentials(mgr)

    def test_authentication_error_message(self):
        ok, err = self._run_with_error("incorrect username or password")
        assert ok is False
        assert "Authentication failed" in err

    def test_authentication_keyword_error(self):
        ok, err = self._run_with_error("authentication token expired")
        assert ok is False
        assert "Authentication failed" in err

    def test_account_not_exist_error(self):
        ok, err = self._run_with_error("account xyz does not exist")
        assert ok is False
        assert "Account identifier is invalid" in err

    def test_warehouse_error_message(self):
        ok, err = self._run_with_error("warehouse WH not found")
        assert ok is False
        assert "WH" in err

    def test_database_not_exist_error(self):
        ok, err = self._run_with_error("database DB does not exist")
        assert ok is False
        assert "DB" in err

    def test_role_error_message(self):
        ok, err = self._run_with_error("role MYROLE does not exist")
        assert ok is False
        assert "MYROLE" in err

    def test_generic_error_message(self):
        ok, err = self._run_with_error("network timeout")
        assert ok is False
        assert "Connection failed" in err
        assert "network timeout" in err


class TestAutoDetectSnowflake:
    def _console(self):
        from unittest.mock import MagicMock

        return MagicMock()

    def test_returns_dict_when_all_required_vars_set(self):
        from unittest.mock import patch

        from benchbox.platforms.credentials.snowflake import _auto_detect_snowflake

        env = {
            "SNOWFLAKE_ACCOUNT": "myorg-acct",
            "SNOWFLAKE_USERNAME": "joe",
            "SNOWFLAKE_PASSWORD": "secret",
            "SNOWFLAKE_WAREHOUSE": "COMPUTE_WH",
            "SNOWFLAKE_DATABASE": "BENCHBOX",
        }
        with patch.dict("os.environ", env, clear=True):
            result = _auto_detect_snowflake(self._console())

        assert result is not None
        assert result["account"] == "myorg-acct"
        assert result["username"] == "joe"
        assert result["database"] == "BENCHBOX"

    def test_returns_none_when_required_vars_missing(self):
        from unittest.mock import patch

        from benchbox.platforms.credentials.snowflake import _auto_detect_snowflake

        with patch.dict("os.environ", {}, clear=True):
            result = _auto_detect_snowflake(self._console())

        assert result is None

    def test_normalizes_account_strips_snowflakecomputing_com(self):
        from unittest.mock import patch

        from benchbox.platforms.credentials.snowflake import _auto_detect_snowflake

        env = {
            "SNOWFLAKE_ACCOUNT": "acct.snowflakecomputing.com",
            "SNOWFLAKE_USERNAME": "joe",
            "SNOWFLAKE_PASSWORD": "secret",
            "SNOWFLAKE_WAREHOUSE": "COMPUTE_WH",
            "SNOWFLAKE_DATABASE": "BENCHBOX",
        }
        with patch.dict("os.environ", env, clear=True):
            result = _auto_detect_snowflake(self._console())

        assert result is not None
        assert result["account"] == "acct"

    def test_plain_account_unchanged(self):
        from unittest.mock import patch

        from benchbox.platforms.credentials.snowflake import _auto_detect_snowflake

        env = {
            "SNOWFLAKE_ACCOUNT": "myorg-account123",
            "SNOWFLAKE_USERNAME": "joe",
            "SNOWFLAKE_PASSWORD": "secret",
            "SNOWFLAKE_WAREHOUSE": "COMPUTE_WH",
            "SNOWFLAKE_DATABASE": "BENCHBOX",
        }
        with patch.dict("os.environ", env, clear=True):
            result = _auto_detect_snowflake(self._console())

        assert result is not None
        assert result["account"] == "myorg-account123"


class TestValidateSnowflakeCredentials:
    def _make_cred_manager(self, creds=None):
        from unittest.mock import MagicMock

        mgr = MagicMock()
        mgr.get_platform_credentials.return_value = creds
        return mgr

    def test_returns_false_when_no_credentials(self):
        from benchbox.platforms.credentials.snowflake import validate_snowflake_credentials

        mgr = self._make_cred_manager(None)
        ok, err = validate_snowflake_credentials(mgr)
        assert ok is False
        assert err is not None

    def test_returns_false_when_missing_required_fields(self):
        from benchbox.platforms.credentials.snowflake import validate_snowflake_credentials

        mgr = self._make_cred_manager({"account": "acct"})
        ok, err = validate_snowflake_credentials(mgr)
        assert ok is False
        assert "Missing required fields" in err

    def test_returns_false_when_connector_missing(self):
        import sys
        from unittest.mock import patch

        from benchbox.platforms.credentials.snowflake import validate_snowflake_credentials

        creds = {
            "account": "acct",
            "username": "u",
            "password": "p",
            "warehouse": "WH",
            "database": "DB",
        }
        mgr = self._make_cred_manager(creds)

        with patch.dict(sys.modules, {"snowflake": None, "snowflake.connector": None}):
            ok, err = validate_snowflake_credentials(mgr)

        assert ok is False
        assert err is not None

    def test_returns_true_on_successful_connection(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.snowflake import validate_snowflake_credentials

        creds = {
            "account": "acct",
            "username": "u",
            "password": "p",
            "warehouse": "WH",
            "database": "DB",
        }
        mgr = self._make_cred_manager(creds)

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_connector = MagicMock()
        mock_connector.connect.return_value = mock_conn

        mock_snowflake = MagicMock()
        mock_snowflake.connector = mock_connector

        with patch.dict("sys.modules", {"snowflake": mock_snowflake, "snowflake.connector": mock_connector}):
            ok, err = validate_snowflake_credentials(mgr)

        assert ok is True
        assert err is None

    def test_returns_false_on_connection_exception(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.snowflake import validate_snowflake_credentials

        creds = {
            "account": "acct",
            "username": "u",
            "password": "p",
            "warehouse": "WH",
            "database": "DB",
        }
        mgr = self._make_cred_manager(creds)

        mock_connector = MagicMock()
        mock_connector.connect.side_effect = RuntimeError("authentication failed")

        mock_snowflake = MagicMock()
        mock_snowflake.connector = mock_connector

        with patch.dict("sys.modules", {"snowflake": mock_snowflake, "snowflake.connector": mock_connector}):
            ok, err = validate_snowflake_credentials(mgr)

        assert ok is False
        assert err is not None
