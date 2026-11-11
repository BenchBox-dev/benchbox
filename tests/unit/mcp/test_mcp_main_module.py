from __future__ import annotations

import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import benchbox.mcp
import benchbox.mcp.cli

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_module_main_invokes_run_server(monkeypatch):
    run_server = MagicMock()
    monkeypatch.setattr(benchbox.mcp.cli, "run_server", run_server)
    monkeypatch.setattr(sys, "argv", ["benchbox-mcp"])

    runpy.run_module("benchbox.mcp.__main__", run_name="__main__")

    run_server.assert_called_once()


def test_mcp_cli_help_lists_configuration_options():
    from benchbox.mcp.cli import build_parser

    help_text = build_parser().format_help()
    assert "--results-dir" in help_text
    assert "--charts-dir" in help_text
    assert "--log-level" in help_text


def test_mcp_cli_env_fallback(monkeypatch):
    from benchbox.mcp import cli

    captured = {}

    def _capture_run_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "run_server", _capture_run_server)
    monkeypatch.setenv("BENCHBOX_RESULTS_DIR", "/tmp/mcp-results")
    monkeypatch.setenv("BENCHBOX_CHARTS_DIR", "/tmp/mcp-charts")
    monkeypatch.setenv("BENCHBOX_LOG_LEVEL", "WARNING")

    cli.main([])

    assert captured["results_dir"] == Path("/tmp/mcp-results")
    assert captured["charts_dir"] == Path("/tmp/mcp-charts")

    assert captured["log_level"] is None


def test_mcp_cli_explicit_args_override_env(monkeypatch):
    from benchbox.mcp import cli

    captured = {}

    def _capture_run_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "run_server", _capture_run_server)
    monkeypatch.setenv("BENCHBOX_RESULTS_DIR", "/tmp/ignored-results")
    monkeypatch.setenv("BENCHBOX_CHARTS_DIR", "/tmp/ignored-charts")

    cli.main(
        [
            "--results-dir",
            "/tmp/explicit-results",
            "--charts-dir",
            "/tmp/explicit-charts",
            "--log-level",
            "DEBUG",
        ]
    )

    assert captured["results_dir"] == Path("/tmp/explicit-results")
    assert captured["charts_dir"] == Path("/tmp/explicit-charts")
    assert captured["log_level"] == "DEBUG"


