# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
from unittest.mock import MagicMock, patch

import pytest

from benchbox.cli.platform import (
    NumberedSelectPrompt,
    numbered_platform_select,
)
from benchbox.core.schemas import PlatformInfo

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestNumberedSelectPrompt:
    def test_process_response_number_selection(self):

        options = [
            ("enable", "Enable platform"),
            ("disable", "Disable platform"),
            ("done", "Done"),
        ]
        prompt = NumberedSelectPrompt("Test", options=options)

        assert prompt.process_response("1") == "enable"
        assert prompt.process_response("2") == "disable"
        assert prompt.process_response("3") == "done"

    def test_process_response_name_selection(self):

        options = [
            ("enable", "Enable platform"),
            ("disable", "Disable platform"),
            ("done", "Done"),
        ]
        prompt = NumberedSelectPrompt("Test", options=options)

        assert prompt.process_response("enable") == "enable"
        assert prompt.process_response("disable") == "disable"
        assert prompt.process_response("done") == "done"

    def test_process_response_case_insensitive(self):

        options = [
            ("enable", "Enable platform"),
            ("disable", "Disable platform"),
        ]
        prompt = NumberedSelectPrompt("Test", options=options)

        assert prompt.process_response("ENABLE") == "enable"
        assert prompt.process_response("Enable") == "enable"
        assert prompt.process_response("eNaBlE") == "enable"

    def test_process_response_default_on_empty(self):

        options = [
            ("enable", "Enable platform"),
            ("done", "Done"),
        ]
        prompt = NumberedSelectPrompt("Test", options=options, default="done")

        assert prompt.process_response("") == "done"
        assert prompt.process_response("  ") == "done"

    def test_process_response_invalid_number(self):

        from rich.prompt import InvalidResponse

        options = [
            ("enable", "Enable platform"),
            ("disable", "Disable platform"),
        ]
        prompt = NumberedSelectPrompt("Test", options=options)

        with pytest.raises(InvalidResponse):
            prompt.process_response("0")

        with pytest.raises(InvalidResponse):
            prompt.process_response("3")

        with pytest.raises(InvalidResponse):
            prompt.process_response("99")

    def test_process_response_invalid_name(self):

        from rich.prompt import InvalidResponse

        options = [
            ("enable", "Enable platform"),
            ("disable", "Disable platform"),
        ]
        prompt = NumberedSelectPrompt("Test", options=options)

        with pytest.raises(InvalidResponse):
            prompt.process_response("invalid")

        with pytest.raises(InvalidResponse):
            prompt.process_response("enab")

    def test_process_response_whitespace_handling(self):

        options = [
            ("enable", "Enable platform"),
            ("disable", "Disable platform"),
        ]
        prompt = NumberedSelectPrompt("Test", options=options)

        assert prompt.process_response("  1  ") == "enable"
        assert prompt.process_response("  enable  ") == "enable"

    def test_value_to_number_mapping(self):

        options = [
            ("enable", "Enable platform"),
            ("disable", "Disable platform"),
            ("done", "Done"),
        ]
        prompt = NumberedSelectPrompt("Test", options=options)

        assert prompt._value_to_number["enable"] == 1
        assert prompt._value_to_number["disable"] == 2
        assert prompt._value_to_number["done"] == 3

    def test_number_to_value_mapping(self):

        options = [
            ("enable", "Enable platform"),
            ("disable", "Disable platform"),
            ("done", "Done"),
        ]
        prompt = NumberedSelectPrompt("Test", options=options)

        assert prompt._number_to_value[1] == "enable"
        assert prompt._number_to_value[2] == "disable"
        assert prompt._number_to_value[3] == "done"


class TestNumberedPlatformSelect:
    def _create_platform_info(
        self, name: str, display_name: str, available: bool = True, enabled: bool = False
    ) -> PlatformInfo:
        return PlatformInfo(
            name=name,
            display_name=display_name,
            description=f"Test platform {name}",
            libraries=[],
            available=available,
            enabled=enabled,
            requirements=[],
            installation_command=f"uv add {name}",
            category="analytical",
        )

    def test_returns_none_for_empty_platforms(self):

        mock_console = MagicMock()
        result = numbered_platform_select(
            "Select",
            {},
            console_instance=mock_console,
        )
        assert result is None
        mock_console.print.assert_called()

    def test_returns_none_when_filter_excludes_all(self):

        platforms = {
            "duckdb": self._create_platform_info("duckdb", "DuckDB", available=True),
            "sqlite": self._create_platform_info("sqlite", "SQLite", available=True),
        }
        mock_console = MagicMock()
        result = numbered_platform_select(
            "Select",
            platforms,
            filter_func=lambda info: False,
            console_instance=mock_console,
        )
        assert result is None

    @patch("benchbox.cli.platform.Prompt.ask")
    def test_select_by_number(self, mock_ask):

        platforms = {
            "duckdb": self._create_platform_info("duckdb", "DuckDB"),
            "sqlite": self._create_platform_info("sqlite", "SQLite"),
        }
        mock_console = MagicMock()
        mock_ask.return_value = "1"

        result = numbered_platform_select(
            "Select",
            platforms,
            group_by_status=False,
            console_instance=mock_console,
        )

        assert result == "duckdb"

    @patch("benchbox.cli.platform.Prompt.ask")
    def test_select_by_name(self, mock_ask):

        platforms = {
            "duckdb": self._create_platform_info("duckdb", "DuckDB"),
            "sqlite": self._create_platform_info("sqlite", "SQLite"),
        }
        mock_console = MagicMock()
        mock_ask.return_value = "sqlite"

        result = numbered_platform_select(
            "Select",
            platforms,
            group_by_status=False,
            console_instance=mock_console,
        )

        assert result == "sqlite"

    @patch("benchbox.cli.platform.Prompt.ask")
    def test_select_by_alias(self, mock_ask):

        platforms = {
            "postgresql": self._create_platform_info("postgresql", "PostgreSQL"),
            "duckdb": self._create_platform_info("duckdb", "DuckDB"),
        }
        mock_console = MagicMock()
        mock_ask.return_value = "pg"

        result = numbered_platform_select(
            "Select",
            platforms,
            group_by_status=False,
            console_instance=mock_console,
        )

        assert result == "postgresql"

    @patch("benchbox.cli.platform.Prompt.ask")
    def test_filter_function_applied(self, mock_ask):

        platforms = {
            "duckdb": self._create_platform_info("duckdb", "DuckDB", available=True),
            "missing": self._create_platform_info("missing", "Missing", available=False),
        }
        mock_console = MagicMock()
        mock_ask.return_value = "1"

        result = numbered_platform_select(
            "Select",
            platforms,
            filter_func=lambda info: info.available,
            group_by_status=False,
            console_instance=mock_console,
        )

        assert result == "duckdb"

    @patch("benchbox.cli.platform.Prompt.ask")
    def test_returns_none_on_empty_input(self, mock_ask):

        platforms = {
            "duckdb": self._create_platform_info("duckdb", "DuckDB"),
        }
        mock_console = MagicMock()
        mock_ask.return_value = ""

        result = numbered_platform_select(
            "Select",
            platforms,
            group_by_status=False,
            console_instance=mock_console,
        )

        assert result is None

    @patch("benchbox.cli.platform.Prompt.ask")
    def test_grouped_by_status_displays_sections(self, mock_ask):

        platforms = {
            "enabled1": self._create_platform_info("enabled1", "Enabled1", available=True, enabled=True),
            "available1": self._create_platform_info("available1", "Available1", available=True, enabled=False),
            "missing1": self._create_platform_info("missing1", "Missing1", available=False, enabled=False),
        }
        mock_console = MagicMock()
        mock_ask.return_value = "1"

        numbered_platform_select(
            "Select",
            platforms,
            group_by_status=True,
            console_instance=mock_console,
        )

        print_calls = [str(call) for call in mock_console.print.call_args_list]
        print_output = " ".join(print_calls)
        assert "Enabled" in print_output
        assert "Available" in print_output
        assert "Missing" in print_output


class TestSetupWizardIntegration:
    @patch("benchbox.cli.platform.get_platform_manager")
    @patch("benchbox.cli.platform.NumberedSelectPrompt.ask")
    def test_setup_wizard_uses_numbered_prompt(self, mock_ask, mock_get_manager):

        from click.testing import CliRunner

        from benchbox.cli.platform import setup_platforms

        mock_manager = MagicMock()
        mock_manager.detect_platforms.return_value = {}
        mock_get_manager.return_value = mock_manager

        mock_ask.return_value = "done"

        runner = CliRunner()
        runner.invoke(setup_platforms, ["--interactive"])

        mock_ask.assert_called()

        call_args = mock_ask.call_args
        assert call_args is not None
        assert "options" in call_args.kwargs
        options = call_args.kwargs["options"]
        option_values = [v for v, _ in options]
        assert "enable" in option_values
        assert "disable" in option_values
        assert "install" in option_values
        assert "status" in option_values
        assert "done" in option_values


