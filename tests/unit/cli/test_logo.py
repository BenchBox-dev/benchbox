# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import io
import json
import re
from pathlib import Path
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner
from rich.panel import Panel
from rich.text import Text

from benchbox.cli.app import cli
from benchbox.cli.logo import LOGO, logo_supported, print_startup_logo, styled_logo
from benchbox.cli.onboarding import _show_welcome_message

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
README = ROOT / "README.md"
LOGO_FIRST_ROW = LOGO.splitlines()[1]


def test_logo_matches_readme() -> None:
    match = re.search(r"```(?:text)?\n(.*?)\n```", README.read_text(encoding="utf-8"), re.DOTALL)
    assert match is not None
    assert match.group(1) == LOGO


@pytest.mark.parametrize(("encoding", "expected"), [("utf-8", True), ("cp1252", False), ("ascii", False)])
def test_logo_supported_depends_on_encoding(encoding: str, expected: bool) -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding=encoding)
    assert logo_supported(stream) is expected


def test_logo_unsupported_without_encoding() -> None:
    assert logo_supported(io.StringIO()) is False


def test_root_help_shows_logo() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert LOGO_FIRST_ROW in result.output
    assert result.output.index(LOGO_FIRST_ROW) < result.output.index("Usage:")


def test_subgroup_help_has_no_logo() -> None:
    result = CliRunner().invoke(cli, ["results", "--help"])
    assert result.exit_code == 0
    assert LOGO_FIRST_ROW not in result.output


def test_root_help_skips_logo_when_unsupported() -> None:
    with patch("benchbox.cli.logo.logo_supported", return_value=False):
        result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert LOGO_FIRST_ROW not in result.output
    assert "Interactive database benchmark runner" in result.output


def test_version_shows_logo_then_report() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith(LOGO)
    assert "BenchBox Version:" in result.output


def test_version_json_has_no_logo() -> None:
    result = CliRunner().invoke(cli, ["--version-json"])
    assert result.exit_code == 0
    json.loads(result.output)


def test_startup_logo_prints_before_command_output() -> None:
    result = CliRunner().invoke(cli, ["platforms", "list"])
    assert result.exit_code == 0
    assert LOGO_FIRST_ROW in result.stderr
    assert result.output.index(LOGO_FIRST_ROW) < result.output.index("DuckDB")


def test_startup_logo_skipped_for_subgroup_help() -> None:
    result = CliRunner().invoke(cli, ["auth", "status", "--help"])
    assert result.exit_code == 0
    assert LOGO_FIRST_ROW not in result.output
    assert LOGO_FIRST_ROW not in result.stderr


def test_startup_logo_skipped_when_unsupported() -> None:
    with patch("benchbox.cli.logo.logo_supported", return_value=False):
        result = CliRunner().invoke(cli, ["platforms", "list"])
    assert result.exit_code == 0
    assert LOGO_FIRST_ROW not in result.output
    assert LOGO_FIRST_ROW not in result.stderr


def _startup_ctx(params: dict[str, object]) -> click.Context:
    ctx = click.Context(click.Command("dummy"))
    ctx.params = dict(params)
    return ctx


def test_print_startup_logo_skips_quiet_param() -> None:
    with patch.object(click, "echo") as mock_echo:
        print_startup_logo(_startup_ctx({"quiet": True}))
    mock_echo.assert_not_called()


def test_print_startup_logo_skips_global_quiet() -> None:
    with (
        patch("benchbox.utils.printing.is_quiet", return_value=True),
        patch("benchbox.cli.logo.styled_logo", return_value="logo") as mock_styled,
    ):
        print_startup_logo(_startup_ctx({}))
    mock_styled.assert_not_called()


def test_print_startup_logo_skips_resilient_parsing() -> None:
    ctx = click.Context(click.Command("dummy"), resilient_parsing=True)
    ctx.params = {}
    with patch("benchbox.cli.logo.styled_logo", return_value="logo") as mock_styled:
        print_startup_logo(ctx)
    mock_styled.assert_not_called()


def test_styled_logo_respects_stream_encoding() -> None:
    ascii_stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    assert styled_logo(ascii_stream) == ""
    utf8_stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    assert LOGO_FIRST_ROW in styled_logo(utf8_stream)


def test_welcome_prints_logo_before_panel() -> None:
    with (
        patch("benchbox.cli.onboarding.rich_logo", return_value=Text(LOGO)),
        patch("benchbox.cli.onboarding.console.print") as mock_print,
    ):
        _show_welcome_message()

    printed = [call.args[0] for call in mock_print.call_args_list if call.args]
    assert isinstance(printed[0], Text)
    assert printed[0].plain == LOGO
    assert isinstance(printed[-1], Panel)
