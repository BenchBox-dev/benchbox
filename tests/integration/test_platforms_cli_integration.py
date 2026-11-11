from __future__ import annotations

import pytest

from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


@pytest.mark.integration
def test_platforms_command_help():

    result = run_cli_command(["platforms", "--help"])

    assert result.returncode == 0
    assert "Manage database platform adapters" in result.stdout
    assert "Commands:" in result.stdout

    assert "list" in result.stdout
    assert "status" in result.stdout
    assert "enable" in result.stdout
    assert "disable" in result.stdout
    assert "install" in result.stdout
    assert "check" in result.stdout
    assert "setup" in result.stdout


@pytest.mark.integration
def test_platforms_list_command():

    result = run_cli_command(["platforms", "list"])

    assert result.returncode == 0

    assert "DuckDB" in result.stdout or "duckdb" in result.stdout.lower()

    assert "Platform" in result.stdout or "Status" in result.stdout or "✅" in result.stdout


@pytest.mark.integration
def test_platforms_list_help():

    result = run_cli_command(["platforms", "list", "--help"])

    assert result.returncode == 0
    assert "List all available platforms" in result.stdout
    assert "--all" in result.stdout
    assert "--format" in result.stdout


@pytest.mark.integration
def test_platforms_status_all():

    result = run_cli_command(["platforms", "status"])

    assert result.returncode == 0

    assert "DuckDB" in result.stdout or "Platform" in result.stdout


@pytest.mark.integration
def test_platforms_status_specific():

    result = run_cli_command(["platforms", "status", "duckdb"])

    assert result.returncode == 0
    assert "DuckDB" in result.stdout

    assert "duckdb" in result.stdout.lower()


@pytest.mark.integration
def test_platforms_status_help():

    result = run_cli_command(["platforms", "status", "--help"])

    assert result.returncode == 0
    assert "Show detailed status" in result.stdout


@pytest.mark.integration
def test_platforms_check_command():

    result = run_cli_command(["platforms", "check"])

    assert result.returncode in (0, 1)

    assert "Ready" in result.stdout or "Platform Check Results" in result.stdout


@pytest.mark.integration
def test_platforms_check_help():

    result = run_cli_command(["platforms", "check", "--help"])

    assert result.returncode == 0
    assert "Check platform availability" in result.stdout
    assert "--enabled-only" in result.stdout


@pytest.mark.integration
def test_platforms_enable_help():

    result = run_cli_command(["platforms", "enable", "--help"])

    assert result.returncode == 0
    assert "Enable a database platform" in result.stdout
    assert "--force" in result.stdout


@pytest.mark.integration
def test_platforms_disable_help():

    result = run_cli_command(["platforms", "disable", "--help"])

    assert result.returncode == 0
    assert "Disable a database platform" in result.stdout


@pytest.mark.integration
def test_platforms_install_help():

    result = run_cli_command(["platforms", "install", "--help"])

    assert result.returncode == 0
    assert "Guide installation" in result.stdout or "installation" in result.stdout.lower()
    assert "--dry-run" in result.stdout


@pytest.mark.integration
def test_platforms_setup_help():

    result = run_cli_command(["platforms", "setup", "--help"])

    assert result.returncode == 0
    assert "--interactive" in result.stdout
    assert "--non-interactive" in result.stdout
    assert "--platform" in result.stdout

    assert "benchbox setup" in result.stdout


@pytest.mark.integration
def test_platforms_install_specific():

    result = run_cli_command(["platforms", "install", "duckdb"])

    assert result.returncode == 0

    assert "duckdb" in result.stdout.lower() or "DuckDB" in result.stdout


@pytest.mark.integration
def test_platforms_command_appears_in_main_help():

    result = run_cli_command(["--help"])

    assert result.returncode == 0
    assert "platforms" in result.stdout
    assert "Manage database platform adapters" in result.stdout


@pytest.mark.integration
def test_platforms_check_specific_platform():

    result = run_cli_command(["platforms", "check", "duckdb"])

    assert result.returncode == 0
    assert "Ready" in result.stdout or "ready" in result.stdout.lower()


@pytest.mark.integration
def test_platforms_list_with_format_option():

    result_table = run_cli_command(["platforms", "list", "--format", "table"])
    assert result_table.returncode == 0

    result_simple = run_cli_command(["platforms", "list", "--format", "simple"])
    assert result_simple.returncode == 0

    assert "duckdb" in result_table.stdout.lower()
    assert "duckdb" in result_simple.stdout.lower()


@pytest.mark.integration
def test_platforms_list_with_all_flag():

    result = run_cli_command(["platforms", "list", "--all"])

    assert result.returncode == 0

    assert "Platform" in result.stdout or "duckdb" in result.stdout.lower()


@pytest.mark.integration
def test_platforms_check_enabled_only():

    result = run_cli_command(["platforms", "check", "--enabled-only"])

    assert result.returncode in (0, 1)

    assert len(result.stdout) > 0


@pytest.mark.integration
def test_platforms_status_unknown_platform():

    result = run_cli_command(["platforms", "status", "nonexistent-platform"])

    assert "Unknown platform" in result.stdout or "not found" in result.stdout.lower() or result.returncode != 0


