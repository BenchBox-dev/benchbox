# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import patch

import pytest

from benchbox.cli.cloud_storage import (
    _display_platform_guidance,
    prompt_cloud_output_location,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestPromptCloudOutputLocation:
    @patch("benchbox.cli.cloud_storage.PlatformRegistry.requires_cloud_storage")
    def test_returns_none_for_local_platforms(self, mock_requires):

        mock_requires.return_value = False

        result = prompt_cloud_output_location(
            platform_name="duckdb",
            benchmark_name="tpch",
            scale_factor=1.0,
            non_interactive=False,
        )

        assert result is None
        mock_requires.assert_called_once_with("duckdb")

    @patch("benchbox.cli.cloud_storage.PlatformRegistry.requires_cloud_storage")
    def test_returns_none_in_non_interactive_mode(self, mock_requires):

        mock_requires.return_value = True

        result = prompt_cloud_output_location(
            platform_name="databricks",
            benchmark_name="ssb",
            scale_factor=10.0,
            non_interactive=True,
        )

        assert result is None

    @patch("benchbox.cli.cloud_storage.sys.stdin.isatty")
    @patch("benchbox.cli.cloud_storage.PlatformRegistry.requires_cloud_storage")
    def test_returns_none_in_non_tty_environment(self, mock_requires, mock_isatty):

        mock_requires.return_value = True
        mock_isatty.return_value = False

        result = prompt_cloud_output_location(
            platform_name="databricks",
            benchmark_name="ssb",
            scale_factor=10.0,
            non_interactive=False,
        )

        assert result is None

    @patch("benchbox.cli.cloud_storage.Confirm.ask")
    @patch("benchbox.cli.cloud_storage.sys.stdin.isatty")
    @patch("benchbox.cli.cloud_storage.PlatformRegistry")
    def test_returns_none_when_user_declines_prompt(self, mock_registry, mock_isatty, mock_confirm):

        mock_registry.requires_cloud_storage.return_value = True
        mock_registry.get_cloud_path_examples.return_value = ["s3://bucket/path"]
        mock_isatty.return_value = True
        mock_confirm.return_value = False

        result = prompt_cloud_output_location(
            platform_name="databricks",
            benchmark_name="ssb",
            scale_factor=10.0,
            non_interactive=False,
        )

        assert result is None

    @patch("benchbox.cli.cloud_storage.Confirm.ask")
    @patch("benchbox.cli.cloud_storage.Prompt.ask")
    @patch("benchbox.cli.cloud_storage.is_cloud_path")
    @patch("benchbox.cli.cloud_storage.sys.stdin.isatty")
    @patch("benchbox.cli.cloud_storage.PlatformRegistry")
    def test_accepts_valid_cloud_path(self, mock_registry, mock_isatty, mock_is_cloud, mock_prompt, mock_confirm):

        mock_registry.requires_cloud_storage.return_value = True
        mock_registry.get_cloud_path_examples.return_value = ["s3://bucket/path"]
        mock_isatty.return_value = True
        mock_is_cloud.return_value = True
        mock_confirm.side_effect = [True, True]
        mock_prompt.return_value = "s3://my-bucket/benchbox/data"

        result = prompt_cloud_output_location(
            platform_name="databricks",
            benchmark_name="ssb",
            scale_factor=10.0,
            non_interactive=False,
        )

        assert result == "s3://my-bucket/benchbox/data"

    @patch("benchbox.cli.cloud_storage.Confirm.ask")
    @patch("benchbox.cli.cloud_storage.Prompt.ask")
    @patch("benchbox.cli.cloud_storage.is_cloud_path")
    @patch("benchbox.cli.cloud_storage.sys.stdin.isatty")
    @patch("benchbox.cli.cloud_storage.PlatformRegistry")
    def test_rejects_empty_path(self, mock_registry, mock_isatty, mock_is_cloud, mock_prompt, mock_confirm):

        mock_registry.requires_cloud_storage.return_value = True
        mock_registry.get_cloud_path_examples.return_value = ["s3://bucket/path"]
        mock_isatty.return_value = True
        mock_is_cloud.return_value = True
        mock_confirm.side_effect = [True, False]
        mock_prompt.return_value = ""

        result = prompt_cloud_output_location(
            platform_name="databricks",
            benchmark_name="ssb",
            scale_factor=10.0,
            non_interactive=False,
        )

        assert result is None

    @patch("benchbox.cli.cloud_storage.Confirm.ask")
    @patch("benchbox.cli.cloud_storage.Prompt.ask")
    @patch("benchbox.cli.cloud_storage.is_cloud_path")
    @patch("benchbox.cli.cloud_storage.sys.stdin.isatty")
    @patch("benchbox.cli.cloud_storage.PlatformRegistry")
    def test_warns_on_non_cloud_path_format(self, mock_registry, mock_isatty, mock_is_cloud, mock_prompt, mock_confirm):

        mock_registry.requires_cloud_storage.return_value = True
        mock_registry.get_cloud_path_examples.return_value = ["s3://bucket/path"]
        mock_isatty.return_value = True
        mock_is_cloud.return_value = False
        mock_confirm.side_effect = [True, False, False]
        mock_prompt.side_effect = ["/local/path", ""]

        result = prompt_cloud_output_location(
            platform_name="databricks",
            benchmark_name="ssb",
            scale_factor=10.0,
            non_interactive=False,
        )

        assert result is None

    @patch("benchbox.cli.cloud_storage.Confirm.ask")
    @patch("benchbox.cli.cloud_storage.Prompt.ask")
    @patch("benchbox.cli.cloud_storage.is_cloud_path")
    @patch("benchbox.cli.cloud_storage.sys.stdin.isatty")
    @patch("benchbox.cli.cloud_storage.PlatformRegistry")
    def test_allows_override_for_non_cloud_path(
        self, mock_registry, mock_isatty, mock_is_cloud, mock_prompt, mock_confirm
    ):

        mock_registry.requires_cloud_storage.return_value = True
        mock_registry.get_cloud_path_examples.return_value = ["s3://bucket/path"]
        mock_isatty.return_value = True
        mock_is_cloud.return_value = False
        mock_confirm.side_effect = [True, True, True]
        mock_prompt.return_value = "/local/path"

        result = prompt_cloud_output_location(
            platform_name="databricks",
            benchmark_name="ssb",
            scale_factor=10.0,
            non_interactive=False,
        )

        assert result == "/local/path"

    @patch("benchbox.cli.cloud_storage.Confirm.ask")
    @patch("benchbox.cli.cloud_storage.Prompt.ask")
    @patch("benchbox.cli.cloud_storage.is_cloud_path")
    @patch("benchbox.cli.cloud_storage.sys.stdin.isatty")
    @patch("benchbox.cli.cloud_storage.PlatformRegistry")
    def test_supports_dbfs_volumes_path(self, mock_registry, mock_isatty, mock_is_cloud, mock_prompt, mock_confirm):

        mock_registry.requires_cloud_storage.return_value = True
        mock_registry.get_cloud_path_examples.return_value = ["dbfs:/Volumes/catalog/schema/volume/benchbox"]
        mock_isatty.return_value = True
        mock_is_cloud.return_value = True
        mock_confirm.side_effect = [True, True]
        mock_prompt.return_value = "dbfs:/Volumes/main/default/data/ssb"

        result = prompt_cloud_output_location(
            platform_name="databricks",
            benchmark_name="ssb",
            scale_factor=10.0,
            non_interactive=False,
        )

        assert result == "dbfs:/Volumes/main/default/data/ssb"

    @patch("benchbox.cli.cloud_storage.Confirm.ask")
    @patch("benchbox.cli.cloud_storage.Prompt.ask")
    @patch("benchbox.cli.cloud_storage.is_cloud_path")
    @patch("benchbox.cli.cloud_storage.sys.stdin.isatty")
    @patch("benchbox.cli.cloud_storage.PlatformRegistry")
    def test_displays_platform_examples(self, mock_registry, mock_isatty, mock_is_cloud, mock_prompt, mock_confirm):

        mock_registry.requires_cloud_storage.return_value = True
        mock_registry.get_cloud_path_examples.return_value = [
            "s3://bucket/path",
            "gs://bucket/path",
        ]
        mock_isatty.return_value = True
        mock_is_cloud.return_value = True
        mock_confirm.side_effect = [True, True]
        mock_prompt.return_value = "s3://test/path"

        result = prompt_cloud_output_location(
            platform_name="databricks",
            benchmark_name="ssb",
            scale_factor=10.0,
            non_interactive=False,
        )

        mock_registry.get_cloud_path_examples.assert_called_with("databricks")
        assert result == "s3://test/path"


class TestDisplayPlatformGuidance:
    @patch("benchbox.cli.cloud_storage.console")
    def test_displays_databricks_guidance(self, mock_console):

        _display_platform_guidance("databricks")

        assert mock_console.print.called
        calls = [str(call) for call in mock_console.print.call_args_list]
        all_output = " ".join(calls)
        assert "Unity Catalog" in all_output or "dbfs:" in all_output.lower()

    @patch("benchbox.cli.cloud_storage.console")
    def test_displays_bigquery_guidance(self, mock_console):

        _display_platform_guidance("bigquery")

        assert mock_console.print.called
        calls = [str(call) for call in mock_console.print.call_args_list]
        all_output = " ".join(calls)
        assert "gs://" in all_output or "Google Cloud" in all_output

    @patch("benchbox.cli.cloud_storage.console")
    def test_displays_snowflake_guidance(self, mock_console):

        _display_platform_guidance("snowflake")

        assert mock_console.print.called
        calls = [str(call) for call in mock_console.print.call_args_list]
        all_output = " ".join(calls)
        assert "stage" in all_output.lower() or "s3://" in all_output

    @patch("benchbox.cli.cloud_storage.console")
    def test_displays_redshift_guidance(self, mock_console):

        _display_platform_guidance("redshift")

        assert mock_console.print.called
        calls = [str(call) for call in mock_console.print.call_args_list]
        all_output = " ".join(calls)
        assert "s3://" in all_output or "S3" in all_output

    @patch("benchbox.cli.cloud_storage.console")
    def test_handles_unknown_platform(self, mock_console):
        _display_platform_guidance("unknown_platform")

    @patch("benchbox.cli.cloud_storage.console")
    def test_case_insensitive_platform_names(self, mock_console):

        _display_platform_guidance("DATABRICKS")

        assert mock_console.print.called

        _display_platform_guidance("Databricks")

        assert mock_console.print.called


