# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.credentials.athena import (
    _auto_detect_athena,
    setup_athena_credentials,
    validate_athena_credentials,
)
from benchbox.security.credentials import CredentialStatus

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAthenaCredentialDefaults:
    @patch("benchbox.platforms.credentials.athena.validate_athena_credentials")
    @patch("benchbox.platforms.credentials.athena.prompt_secure_field")
    @patch("benchbox.platforms.credentials.athena.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_shows_existing_values_as_defaults(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
    ):

        mock_manager = Mock()
        existing_creds = {
            "region": "us-west-2",
            "workgroup": "my-workgroup",
            "s3_staging_dir": "s3://my-bucket/staging/",
            "s3_output_location": "s3://my-bucket/results/",
            "aws_profile": "production",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_int_prompt.return_value = 1
        mock_prompt_default.side_effect = [
            "us-west-2",
            "my-workgroup",
            "s3://my-bucket/staging/",
            "s3://my-bucket/results/",
            "production",
        ]

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_athena_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] == "us-west-2"
        assert calls[1][1]["current_value"] == "my-workgroup"
        assert calls[2][1]["current_value"] == "s3://my-bucket/staging/"
        assert calls[3][1]["current_value"] == "s3://my-bucket/results/"
        assert calls[4][1]["current_value"] == "production"

    @patch("benchbox.platforms.credentials.athena.validate_athena_credentials")
    @patch("benchbox.platforms.credentials.athena.prompt_secure_field")
    @patch("benchbox.platforms.credentials.athena.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_works_with_no_existing_credentials(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.return_value = False
        mock_int_prompt.return_value = 1
        mock_prompt_default.side_effect = [
            "us-east-1",
            "primary",
            "s3://new-bucket/data/",
            "",
            "default",
        ]

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_athena_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] is None
        assert calls[0][1]["default_if_none"] == "us-east-1"
        assert calls[1][1]["current_value"] is None
        assert calls[1][1]["default_if_none"] == "primary"

    @patch("benchbox.platforms.credentials.athena.validate_athena_credentials")
    @patch("benchbox.platforms.credentials.athena.prompt_secure_field")
    @patch("benchbox.platforms.credentials.athena.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_access_key_auth_shows_existing_values(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
    ):

        mock_manager = Mock()
        existing_creds = {
            "region": "us-east-1",
            "workgroup": "primary",
            "s3_staging_dir": "s3://my-bucket/data/",
            "s3_output_location": "s3://my-bucket/results/",
            "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
            "aws_secret_access_key": "secret_key",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_int_prompt.return_value = 2
        mock_prompt_default.side_effect = [
            "us-east-1",
            "primary",
            "s3://my-bucket/data/",
            "s3://my-bucket/results/",
            "AKIAIOSFODNN7EXAMPLE",
        ]
        mock_prompt_secure.return_value = "secret_key"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_athena_credentials(mock_manager, console)

        default_calls = mock_prompt_default.call_args_list
        assert default_calls[4][1]["current_value"] == "AKIAIOSFODNN7EXAMPLE"

        mock_prompt_secure.assert_called_once_with(
            "AWS Secret Access Key",
            current_value="secret_key",
            console=console,
        )

    @patch("benchbox.platforms.credentials.athena.validate_athena_credentials")
    @patch("benchbox.platforms.credentials.athena.prompt_secure_field")
    @patch("benchbox.platforms.credentials.athena.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_s3_output_location_derived_from_staging(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
    ):
        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.return_value = False
        mock_int_prompt.return_value = 1
        mock_prompt_default.side_effect = [
            "us-east-1",
            "primary",
            "s3://my-bucket/data/",
            "",
            "default",
        ]

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_athena_credentials(mock_manager, console)

        saved_creds = mock_manager.set_platform_credentials.call_args[0][1]
        assert saved_creds["s3_output_location"] == "s3://my-bucket/data/athena-results/"

    @patch("benchbox.platforms.credentials.athena._auto_detect_athena")
    @patch("benchbox.platforms.credentials.athena.validate_athena_credentials")
    @patch("benchbox.platforms.credentials.athena.prompt_secure_field")
    @patch("benchbox.platforms.credentials.athena.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_skips_auto_detection_when_credentials_exist(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
        mock_auto_detect,
    ):

        mock_manager = Mock()
        existing_creds = {
            "region": "us-west-2",
            "workgroup": "my-workgroup",
            "s3_staging_dir": "s3://my-bucket/staging/",
            "aws_profile": "production",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_int_prompt.return_value = 1
        mock_prompt_default.side_effect = [
            "us-west-2",
            "my-workgroup",
            "s3://my-bucket/staging/",
            "",
            "production",
        ]
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_athena_credentials(mock_manager, console)

        mock_auto_detect.assert_not_called()

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "Existing credentials found" in console_output
        assert "updating configuration" in console_output

    @patch("benchbox.platforms.credentials.athena._auto_detect_athena")
    @patch("benchbox.platforms.credentials.athena.validate_athena_credentials")
    @patch("benchbox.platforms.credentials.athena.prompt_secure_field")
    @patch("benchbox.platforms.credentials.athena.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_offers_auto_detection_when_no_credentials_exist(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
        mock_auto_detect,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.return_value = False
        mock_auto_detect.return_value = None

        mock_int_prompt.return_value = 1
        mock_prompt_default.side_effect = [
            "us-east-1",
            "primary",
            "s3://bucket/data/",
            "",
            "default",
        ]
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_athena_credentials(mock_manager, console)

        auto_detect_call = mock_confirm.call_args_list[0]
        assert "auto-detection" in str(auto_detect_call).lower()


class TestAthenaValidation:
    def test_validation_fails_without_credentials(self):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        success, error = validate_athena_credentials(mock_manager)

        assert success is False
        assert "No credentials found" in error

    def test_validation_fails_without_s3_config(self):
        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = {
            "region": "us-east-1",
            "workgroup": "primary",
            "aws_profile": "default",
        }

        success, error = validate_athena_credentials(mock_manager)

        assert success is False
        assert "S3 staging directory" in error

    @patch("benchbox.platforms.credentials.athena.os.path.exists")
    @patch("benchbox.platforms.credentials.athena.os.environ.get")
    def test_validation_fails_without_aws_auth(self, mock_environ_get, mock_path_exists):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = {
            "region": "us-east-1",
            "workgroup": "primary",
            "s3_staging_dir": "s3://bucket/data/",
        }

        mock_environ_get.return_value = None
        mock_path_exists.return_value = False

        success, error = validate_athena_credentials(mock_manager)

        assert success is False
        assert "No AWS authentication configured" in error

    @patch("benchbox.platforms.credentials.athena.athena_connect", create=True)
    def test_validation_succeeds_with_valid_connection(self, mock_connect):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = {
            "region": "us-east-1",
            "workgroup": "primary",
            "s3_staging_dir": "s3://bucket/data/",
            "aws_profile": "default",
        }

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        with (
            patch.dict("sys.modules", {"pyathena": Mock(connect=mock_connect), "boto3": Mock()}),
            patch("benchbox.platforms.credentials.athena.athena_connect", mock_connect),
        ):
            from importlib import reload

            import benchbox.platforms.credentials.athena as athena_mod

            reload(athena_mod)

            success, error = athena_mod.validate_athena_credentials(mock_manager)

        assert mock_manager.get_platform_credentials.called

    @patch("benchbox.platforms.credentials.athena.athena_connect", create=True)
    def test_validation_handles_access_denied(self, mock_connect):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = {
            "region": "us-east-1",
            "s3_staging_dir": "s3://bucket/data/",
            "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
            "aws_secret_access_key": "secret",
        }

        mock_connect.side_effect = Exception("AccessDenied: User is not authorized")

        with (
            patch.dict("sys.modules", {"pyathena": Mock(connect=mock_connect), "boto3": Mock()}),
            patch("benchbox.platforms.credentials.athena.athena_connect", mock_connect),
        ):
            from importlib import reload

            import benchbox.platforms.credentials.athena as athena_mod

            reload(athena_mod)

            success, error = athena_mod.validate_athena_credentials(mock_manager)

        assert success is False
        assert "Access denied" in error or "AccessDenied" in str(error)


class TestAthenaAutoDetection:
    @patch.dict(
        "os.environ",
        {
            "AWS_DEFAULT_REGION": "us-west-2",
            "ATHENA_S3_STAGING_DIR": "s3://my-bucket/staging/",
            "AWS_PROFILE": "my-profile",
        },
    )
    def test_auto_detect_from_env_vars(self):

        console = Mock()

        result = _auto_detect_athena(console)

        assert result is not None
        assert result["region"] == "us-west-2"
        assert result["s3_staging_dir"] == "s3://my-bucket/staging/"
        assert result["aws_profile"] == "my-profile"

    @patch.dict(
        "os.environ",
        {
            "AWS_REGION": "eu-west-1",
            "ATHENA_S3_OUTPUT_LOCATION": "s3://output-bucket/results/",
            "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "secret_key",
        },
    )
    def test_auto_detect_alternative_env_vars(self):

        console = Mock()

        result = _auto_detect_athena(console)

        assert result is not None
        assert result["region"] == "eu-west-1"
        assert result["s3_output_location"] == "s3://output-bucket/results/"
        assert result["aws_access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
        assert result["aws_secret_access_key"] == "secret_key"

    @patch.dict("os.environ", {}, clear=True)
    @patch("os.path.exists")
    def test_auto_detect_returns_none_without_s3(self, mock_exists):
        mock_exists.return_value = False
        console = Mock()

        result = _auto_detect_athena(console)

        assert result is None
        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "ATHENA_S3_STAGING_DIR" in console_output or "Missing" in console_output

    @patch.dict(
        "os.environ",
        {"ATHENA_S3_STAGING_DIR": "s3://bucket/data/"},
        clear=True,
    )
    @patch("os.path.exists")
    def test_auto_detect_returns_none_without_auth(self, mock_exists):

        mock_exists.return_value = False
        console = Mock()

        result = _auto_detect_athena(console)

        assert result is None
        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "AWS credentials" in console_output or "Missing" in console_output

    @patch.dict(
        "os.environ",
        {"ATHENA_S3_STAGING_DIR": "s3://bucket/data/"},
        clear=True,
    )
    @patch("os.path.exists")
    def test_auto_detect_uses_aws_credentials_file(self, mock_exists):

        mock_exists.return_value = True
        console = Mock()

        result = _auto_detect_athena(console)

        assert result is not None
        assert result["s3_staging_dir"] == "s3://bucket/data/"
        assert result["region"] == "us-east-1"
        assert result["workgroup"] == "primary"


class TestAthenaCredentialStorage:
    @patch("benchbox.platforms.credentials.athena.validate_athena_credentials")
    @patch("benchbox.platforms.credentials.athena.prompt_secure_field")
    @patch("benchbox.platforms.credentials.athena.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_credentials_saved_on_successful_validation(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.return_value = False
        mock_int_prompt.return_value = 1
        mock_prompt_default.side_effect = [
            "us-east-1",
            "primary",
            "s3://bucket/data/",
            "",
            "default",
        ]
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_athena_credentials(mock_manager, console)

        mock_manager.save_credentials.assert_called()
        mock_manager.update_validation_status.assert_called_with("athena", CredentialStatus.VALID)

    @patch("benchbox.platforms.credentials.athena.validate_athena_credentials")
    @patch("benchbox.platforms.credentials.athena.prompt_secure_field")
    @patch("benchbox.platforms.credentials.athena.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_credentials_marked_invalid_on_failed_validation(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.return_value = False
        mock_int_prompt.return_value = 1
        mock_prompt_default.side_effect = [
            "us-east-1",
            "primary",
            "s3://bucket/data/",
            "",
            "default",
        ]
        mock_validate.return_value = (False, "Connection failed")
        console = Mock()

        setup_athena_credentials(mock_manager, console)

        mock_manager.update_validation_status.assert_called_with(
            "athena", CredentialStatus.INVALID, "Connection failed"
        )
        mock_manager.save_credentials.assert_called()

    @patch("benchbox.platforms.credentials.athena.validate_athena_credentials")
    @patch("benchbox.platforms.credentials.athena.prompt_secure_field")
    @patch("benchbox.platforms.credentials.athena.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_secret_key_not_logged(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.return_value = False
        mock_int_prompt.return_value = 2
        mock_prompt_default.side_effect = [
            "us-east-1",
            "primary",
            "s3://bucket/data/",
            "",
            "AKIAIOSFODNN7EXAMPLE",
        ]
        mock_prompt_secure.return_value = "supersecretkey12345"
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_athena_credentials(mock_manager, console)

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "supersecretkey12345" not in console_output

    @patch("benchbox.platforms.credentials.athena.validate_athena_credentials")
    @patch("benchbox.platforms.credentials.athena.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_missing_s3_staging_dir_aborts_setup(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_validate,
    ):
        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "us-east-1",
            "primary",
            "",
        ]
        console = Mock()

        setup_athena_credentials(mock_manager, console)

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "S3 staging directory is required" in console_output

        mock_manager.save_credentials.assert_not_called()


