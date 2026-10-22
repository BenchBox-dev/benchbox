# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.run import _resolve_platform_mode, run
from benchbox.cli.config import ConfigManager
from benchbox.cli.platform import enable_platform, install_platform, platform_status

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize("selector", ["modin", "modin-df"])
def test_cli_run_rejects_removed_platform(selector: str) -> None:
    runner = CliRunner()
    result = runner.invoke(
        run,
        ["--platform", selector, "--benchmark", "tpch"],
        obj={"config": ConfigManager()},
    )
    assert result.exit_code == 1
    assert "Platform 'modin' has been removed" in result.output
    assert "pandas-df" in result.output
    assert "dask-df" in result.output


@pytest.mark.parametrize("selector", ["modin", "modin-df"])
def test_cli_dry_run_rejects_removed_platform_without_emitting_artifacts(selector: str, tmp_path) -> None:
    preview_dir = tmp_path / "preview"
    runner = CliRunner()
    result = runner.invoke(
        run,
        ["--dry-run", str(preview_dir), "--platform", selector, "--benchmark", "tpch"],
        obj={"config": ConfigManager()},
    )
    assert result.exit_code == 1
    assert "Platform 'modin' has been removed" in result.output
    assert "pandas-df" in result.output
    assert "dask-df" in result.output
    assert not preview_dir.exists() or list(preview_dir.iterdir()) == []


@pytest.mark.parametrize("selector", ["modin", "modin-df"])
def test_resolve_platform_mode_exits_for_removed_platform(selector: str) -> None:
    mock_ctx = MagicMock()
    s = SimpleNamespace(
        platform=selector,
        platform_key=selector,
        resolved_mode=None,
        logger=None,
        dry_run=False,
        ctx=mock_ctx,
    )
    _resolve_platform_mode(s)
    mock_ctx.exit.assert_called_once_with(1)


@pytest.mark.parametrize("cmd", [platform_status, enable_platform, install_platform])
def test_cli_platforms_commands_reject_removed_platform(cmd) -> None:
    runner = CliRunner()
    result = runner.invoke(cmd, ["modin"])
    assert result.exit_code == 1
    assert "Platform 'modin' has been removed" in result.output
    assert "pandas-df" in result.output
    assert "dask-df" in result.output


@pytest.mark.parametrize("selector", ["nosuchplatform", "snowflak"])
def test_cli_run_unknown_platform_reports_unavailable_and_exits_one(selector: str) -> None:
    runner = CliRunner()
    result = runner.invoke(
        run,
        ["--platform", selector, "--benchmark", "tpch"],
        obj={"config": ConfigManager()},
    )
    assert result.exit_code == 1
    assert f"Platform '{selector}' is not available (missing dependencies)" in result.output
    assert f"uv sync --extra {selector}" in result.output
