from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.cli.exit_codes import ExitCode
from benchbox.utils import runtime_env

pytestmark = [pytest.mark.unit, pytest.mark.fast]

MISMATCH_OPTIONS = [
    "--platform-option",
    "driver_version=0.0.1",
    "--platform-option",
    "driver_auto_install=true",
]


@pytest.fixture
def installer(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(runtime_env, "_run_install_command", lambda command: commands.append(command))
    monkeypatch.setattr(runtime_env, "discover_isolated_runtime", lambda **_: None)
    monkeypatch.setattr(runtime_env, "_get_installed_version", lambda _: "9.9.9")
    monkeypatch.delenv("BENCHBOX_DRIVER_AUTO_INSTALL", raising=False)
    monkeypatch.delenv("BENCHBOX_NON_INTERACTIVE", raising=False)
    return commands


def test_dry_run_never_installs_even_when_install_is_enabled_everywhere(installer, monkeypatch, tmp_path):
    monkeypatch.setenv("BENCHBOX_DRIVER_AUTO_INSTALL", "1")

    result = CliRunner().invoke(
        cli,
        ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--dry-run", str(tmp_path)]
        + MISMATCH_OPTIONS,
    )

    assert result.exit_code == 0, result.output
    assert installer == []


def test_run_refuses_option_only_install_non_interactively_with_exit_six(installer):
    result = CliRunner().invoke(
        cli, ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01"] + MISMATCH_OPTIONS
    )

    assert result.exit_code == ExitCode.ACKNOWLEDGEMENT_REQUIRED, result.output
    assert "BENCHBOX_DRIVER_AUTO_INSTALL=1" in result.output
    assert installer == []


def test_run_reports_invalid_driver_version_as_usage_error(installer, monkeypatch):
    monkeypatch.setenv("BENCHBOX_DRIVER_AUTO_INSTALL", "1")

    result = CliRunner().invoke(
        cli,
        ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01"]
        + ["--platform-option", "driver_version=1.0 --index-url=http://x"],
    )

    assert result.exit_code == ExitCode.USAGE, result.output
    assert "not a valid package version" in " ".join(result.output.split())
    assert installer == []


def test_non_interactive_flag_refuses_install_in_a_terminal_without_the_environment_variable(installer, monkeypatch):
    monkeypatch.setenv("BENCHBOX_NON_INTERACTIVE", "")
    terminal = SimpleNamespace(isatty=lambda: True)
    monkeypatch.setattr("benchbox.utils.session.sys", SimpleNamespace(stdin=terminal, stdout=terminal))

    result = CliRunner().invoke(
        cli,
        ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
        + MISMATCH_OPTIONS,
    )

    assert result.exit_code == ExitCode.ACKNOWLEDGEMENT_REQUIRED, result.output
    assert "BENCHBOX_DRIVER_AUTO_INSTALL=1" in result.output
    assert installer == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_invalid_driver_version_exits_usage_in_dry_run_and_real_run(installer, monkeypatch, tmp_path, dry_run):
    monkeypatch.setenv("BENCHBOX_DRIVER_AUTO_INSTALL", "1")
    args = ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01"]
    if dry_run:
        args += ["--dry-run", str(tmp_path)]

    result = CliRunner().invoke(cli, args + ["--platform-option", "driver_version=1.0 --index-url=http://x"])

    assert result.exit_code == ExitCode.USAGE, result.output
    assert "not a valid package version" in " ".join(result.output.split())
    assert installer == []
