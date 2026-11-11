# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, mock_open, patch

import pytest

from benchbox.platforms.credentials.bigquery import setup_bigquery_credentials

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBigQueryCredentialDefaults:
    @patch("benchbox.platforms.credentials.bigquery._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.bigquery.validate_bigquery_credentials")
    @patch("benchbox.platforms.credentials.bigquery.Path")
    @patch("builtins.open", new_callable=mock_open, read_data='{"type": "service_account"}')
    @patch("benchbox.platforms.credentials.bigquery.prompt_with_default")
    @patch("benchbox.platforms.credentials.bigquery.Confirm.ask")
    def test_shows_existing_values_as_defaults(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_file,
        mock_path,
        mock_validate,
        mock_output_location,
    ):

        mock_manager = Mock()
        existing_creds = {
            "project_id": "my-gcp-project",
            "credentials_path": "/path/to/key.json",
            "dataset_id": "my_dataset",
            "location": "US",
            "storage_bucket": "my-bucket",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_path_obj = Mock()
        mock_path_obj.exists.return_value = True
        mock_path_obj.is_file.return_value = True
        mock_path.return_value = mock_path_obj

        mock_confirm.return_value = True
        mock_prompt_default.side_effect = [
            "my-gcp-project",
            "/path/to/key.json",
            "my_dataset",
            "US",
            "my-bucket",
        ]

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_bigquery_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] == "my-gcp-project"
        assert calls[1][1]["current_value"] == "/path/to/key.json"
        assert calls[2][1]["current_value"] == "my_dataset"
        assert calls[3][1]["current_value"] == "US"
        assert calls[4][1]["current_value"] == "my-bucket"

    @patch("benchbox.platforms.credentials.bigquery._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.bigquery.validate_bigquery_credentials")
    @patch("benchbox.platforms.credentials.bigquery.Path")
    @patch("builtins.open", new_callable=mock_open, read_data='{"type": "service_account"}')
    @patch("benchbox.platforms.credentials.bigquery.prompt_with_default")
    @patch("benchbox.platforms.credentials.bigquery.Confirm.ask")
    def test_works_with_no_existing_credentials(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_file,
        mock_path,
        mock_validate,
        mock_output_location,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_path_obj = Mock()
        mock_path_obj.exists.return_value = True
        mock_path_obj.is_file.return_value = True
        mock_path.return_value = mock_path_obj

        mock_confirm.side_effect = [
            False,
            False,
        ]
        mock_prompt_default.side_effect = [
            "new-project",
            "/new/path/key.json",
            "benchbox",
            "US",
        ]

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_bigquery_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] is None
        assert calls[1][1]["current_value"] is None
        assert calls[2][1]["current_value"] is None
        assert calls[3][1]["current_value"] is None

    @patch("benchbox.platforms.credentials.bigquery._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.bigquery.validate_bigquery_credentials")
    @patch("benchbox.platforms.credentials.bigquery.Path")
    @patch("builtins.open", new_callable=mock_open, read_data='{"type": "service_account"}')
    @patch("benchbox.platforms.credentials.bigquery.prompt_with_default")
    @patch("benchbox.platforms.credentials.bigquery.Confirm.ask")
    def test_partial_existing_credentials(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_file,
        mock_path,
        mock_validate,
        mock_output_location,
    ):

        mock_manager = Mock()
        existing_creds = {
            "project_id": "my-gcp-project",
            "credentials_path": "/path/to/key.json",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_path_obj = Mock()
        mock_path_obj.exists.return_value = True
        mock_path_obj.is_file.return_value = True
        mock_path.return_value = mock_path_obj

        mock_confirm.side_effect = [
            False,
            False,
        ]
        mock_prompt_default.side_effect = [
            "my-gcp-project",
            "/path/to/key.json",
            "benchbox",
            "US",
        ]

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_bigquery_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] == "my-gcp-project"
        assert calls[1][1]["current_value"] == "/path/to/key.json"
        assert calls[2][1]["current_value"] is None
        assert calls[2][1]["default_if_none"] == "benchbox"
        assert calls[3][1]["current_value"] is None
        assert calls[3][1]["default_if_none"] == "US"

    @patch("benchbox.platforms.credentials.bigquery._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.bigquery._auto_detect_bigquery")
    @patch("benchbox.platforms.credentials.bigquery.validate_bigquery_credentials")
    @patch("benchbox.platforms.credentials.bigquery.Path")
    @patch("builtins.open", new_callable=mock_open, read_data='{"type": "service_account"}')
    @patch("benchbox.platforms.credentials.bigquery.prompt_with_default")
    @patch("benchbox.platforms.credentials.bigquery.Confirm.ask")
    def test_auto_detection_bypasses_existing_defaults(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_file,
        mock_path,
        mock_validate,
        mock_auto_detect,
        mock_output_location,
    ):

        mock_manager = Mock()
        existing_creds = {
            "project_id": "old-project",
            "credentials_path": "/old/path.json",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_path_obj = Mock()
        mock_path_obj.exists.return_value = True
        mock_path_obj.is_file.return_value = True
        mock_path.return_value = mock_path_obj

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "old-project",
            "/old/path.json",
            "benchbox",
            "US",
        ]
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_bigquery_credentials(mock_manager, console)

        assert mock_confirm.call_count == 1
        storage_call = mock_confirm.call_args_list[0]
        assert "storage" in str(storage_call).lower()
        mock_auto_detect.assert_not_called()

    @patch("benchbox.platforms.credentials.bigquery._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.bigquery._auto_detect_bigquery")
    @patch("benchbox.platforms.credentials.bigquery.validate_bigquery_credentials")
    @patch("benchbox.platforms.credentials.bigquery.Path")
    @patch("builtins.open", new_callable=mock_open, read_data='{"type": "service_account"}')
    @patch("benchbox.platforms.credentials.bigquery.prompt_with_default")
    @patch("benchbox.platforms.credentials.bigquery.Confirm.ask")
    def test_skips_auto_detection_when_credentials_exist(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_file,
        mock_path,
        mock_validate,
        mock_auto_detect,
        mock_output_location,
    ):

        mock_manager = Mock()
        existing_creds = {
            "project_id": "my-gcp-project",
            "credentials_path": "/path/to/key.json",
            "dataset_id": "my_dataset",
            "location": "US",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_path_obj = Mock()
        mock_path_obj.exists.return_value = True
        mock_path_obj.is_file.return_value = True
        mock_path.return_value = mock_path_obj

        mock_confirm.return_value = False
        mock_prompt_default.side_effect = [
            "my-gcp-project",
            "/path/to/key.json",
            "my_dataset",
            "US",
        ]
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_bigquery_credentials(mock_manager, console)

        assert mock_confirm.call_count == 1
        mock_auto_detect.assert_not_called()

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "Existing credentials found" in console_output
        assert "updating configuration" in console_output

    @patch("benchbox.platforms.credentials.bigquery._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.bigquery._auto_detect_bigquery")
    @patch("benchbox.platforms.credentials.bigquery.validate_bigquery_credentials")
    @patch("benchbox.platforms.credentials.bigquery.Path")
    @patch("builtins.open", new_callable=mock_open, read_data='{"type": "service_account"}')
    @patch("benchbox.platforms.credentials.bigquery.prompt_with_default")
    @patch("benchbox.platforms.credentials.bigquery.Confirm.ask")
    def test_offers_auto_detection_when_no_credentials_exist(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_file,
        mock_path,
        mock_validate,
        mock_auto_detect,
        mock_output_location,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_path_obj = Mock()
        mock_path_obj.exists.return_value = True
        mock_path_obj.is_file.return_value = True
        mock_path.return_value = mock_path_obj

        mock_confirm.side_effect = [False, False]
        mock_prompt_default.side_effect = [
            "new-project",
            "/new/path.json",
            "benchbox",
            "US",
        ]
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_bigquery_credentials(mock_manager, console)

        auto_detect_call = mock_confirm.call_args_list[0]
        assert "auto-detection" in str(auto_detect_call).lower()

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "Existing credentials found" not in console_output

    @patch("benchbox.platforms.credentials.bigquery._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.bigquery.validate_bigquery_credentials")
    @patch("benchbox.platforms.credentials.bigquery.Path")
    @patch("builtins.open", new_callable=mock_open, read_data='{"type": "service_account"}')
    @patch("benchbox.platforms.credentials.bigquery.prompt_with_default")
    @patch("benchbox.platforms.credentials.bigquery.Confirm.ask")
    def test_storage_bucket_shows_existing_value(
        self,
        mock_confirm,
        mock_prompt_default,
        mock_file,
        mock_path,
        mock_validate,
        mock_output_location,
    ):

        mock_manager = Mock()
        existing_creds = {
            "project_id": "my-gcp-project",
            "credentials_path": "/path/to/key.json",
            "dataset_id": "my_dataset",
            "location": "US",
            "storage_bucket": "existing-bucket",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_path_obj = Mock()
        mock_path_obj.exists.return_value = True
        mock_path_obj.is_file.return_value = True
        mock_path.return_value = mock_path_obj

        mock_confirm.return_value = True
        mock_prompt_default.side_effect = [
            "my-gcp-project",
            "/path/to/key.json",
            "my_dataset",
            "US",
            "existing-bucket",
        ]

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_bigquery_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[4][1]["current_value"] == "existing-bucket"


