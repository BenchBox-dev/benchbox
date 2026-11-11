from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

import benchbox.utils.printing as printing
from benchbox.cli.commands.results import results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def test_results_group_without_subcommand_shows_summary(cli_runner, monkeypatch):
    results_module = import_module("benchbox.cli.commands.results")
    exporter = MagicMock()
    monkeypatch.setattr(results_module, "ResultExporter", lambda: exporter)

    result = cli_runner.invoke(results, [])
    assert result.exit_code == 0
    exporter.show_results_summary.assert_called_once_with()


def test_results_paths_emits_pipeable_paths_to_stdout(cli_runner, monkeypatch, tmp_path):
    results_module = import_module("benchbox.cli.commands.results")
    result_a = tmp_path / "tpch_duckdb.json"
    result_b = tmp_path / "tpch_clickhouse.json"
    exporter = SimpleNamespace(
        show_results_summary=MagicMock(),
        list_results=MagicMock(
            return_value=[
                {"file": result_a, "timestamp": "2026-05-03T12:00:00"},
                {"file": result_b, "timestamp": "2026-05-03T11:00:00"},
            ]
        ),
    )
    monkeypatch.setattr(results_module, "ResultExporter", lambda: exporter)

    result = cli_runner.invoke(results, ["--paths", "--limit", "1"])

    assert result.exit_code == 0
    exporter.show_results_summary.assert_not_called()
    exporter.list_results.assert_called_once_with()
    stdout_lines = result.stdout.splitlines()
    assert stdout_lines == [str(result_a)]
    assert "Showing 1 of 2" in result.stderr
    assert "benchbox submit <path> --output ./submission" in result.stderr


def test_results_paths_omits_overflow_hint_when_within_limit(cli_runner, monkeypatch, tmp_path):
    results_module = import_module("benchbox.cli.commands.results")
    result_a = tmp_path / "tpch_duckdb.json"
    exporter = SimpleNamespace(
        show_results_summary=MagicMock(),
        list_results=MagicMock(return_value=[{"file": result_a, "timestamp": "2026-05-03T12:00:00"}]),
    )
    monkeypatch.setattr(results_module, "ResultExporter", lambda: exporter)

    result = cli_runner.invoke(results, ["--paths", "--limit", "10"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [str(result_a)]
    assert "Showing" not in result.stderr


def test_results_paths_emits_nothing_when_no_results(cli_runner, monkeypatch):
    results_module = import_module("benchbox.cli.commands.results")
    exporter = SimpleNamespace(
        show_results_summary=MagicMock(),
        list_results=MagicMock(return_value=[]),
    )
    monkeypatch.setattr(results_module, "ResultExporter", lambda: exporter)

    result = cli_runner.invoke(results, ["--paths"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    exporter.show_results_summary.assert_not_called()


def test_results_paths_emits_paths_with_rich_markup_chars_unescaped(cli_runner, monkeypatch, tmp_path):

    results_module = import_module("benchbox.cli.commands.results")
    tricky = tmp_path / "results [archive] / r.json"
    exporter = SimpleNamespace(
        show_results_summary=MagicMock(),
        list_results=MagicMock(return_value=[{"file": tricky, "timestamp": "2026-05-03T12:00:00"}]),
    )
    monkeypatch.setattr(results_module, "ResultExporter", lambda: exporter)

    result = cli_runner.invoke(results, ["--paths"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [str(tricky)]


def test_results_paths_and_submitted_are_mutually_exclusive(cli_runner, monkeypatch):
    results_module = import_module("benchbox.cli.commands.results")
    exporter = SimpleNamespace(
        show_results_summary=MagicMock(),
        list_results=MagicMock(),
    )
    monkeypatch.setattr(results_module, "ResultExporter", lambda: exporter)

    result = cli_runner.invoke(results, ["--paths", "--submitted"])

    assert result.exit_code != 0
    assert "mutually exclusive" in result.stderr
    exporter.show_results_summary.assert_not_called()
    exporter.list_results.assert_not_called()


def test_results_group_with_submitted_shows_submission_history(cli_runner, monkeypatch, tmp_path):
    results_module = import_module("benchbox.cli.commands.results")
    prints = []
    sidecar = tmp_path / "tpch_duckdb.submission.json"
    sidecar.write_text(
        json.dumps(
            {
                "submitted_at": "2026-05-01T12:00:00+00:00",
                "benchmark": "tpch",
                "platform": "duckdb",
                "scale_factor": 0.01,
                "status": "published",
                "public_url": "https://benchbox.dev/results/r/r1",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(results_module, "ResultExporter", lambda: SimpleNamespace(output_dir=Path(tmp_path)))

    def fake_print(*args, **_kwargs):
        for arg in args:
            if hasattr(arg, "columns"):
                for column in arg.columns:
                    prints.extend(str(cell) for cell in column._cells)
            else:
                prints.append(str(arg))

    monkeypatch.setattr(results_module, "console", SimpleNamespace(print=fake_print))

    result = cli_runner.invoke(results, ["--submitted"])

    assert result.exit_code == 0
    printed = "\n".join(prints)
    assert "Hosted Submissions" in printed
    assert "https://benchbox.dev/results/r/r1" in printed


def test_results_group_with_submitted_reports_empty(cli_runner, monkeypatch, tmp_path):
    results_module = import_module("benchbox.cli.commands.results")
    prints = []
    monkeypatch.setattr(results_module, "ResultExporter", lambda: SimpleNamespace(output_dir=Path(tmp_path)))
    monkeypatch.setattr(results_module, "console", SimpleNamespace(print=lambda *args, **_kwargs: prints.append(args)))

    result = cli_runner.invoke(results, ["--submitted"])

    assert result.exit_code == 0
    assert any("No hosted submissions found" in str(args) for args in prints)


def test_show_cli_reconstructs_legacy_command(cli_runner, tmp_path, monkeypatch):

    from rich.console import Console

    monkeypatch.setattr(printing, "_STD_CONSOLE", Console(width=40))
    monkeypatch.setattr(printing, "_QUIET", False)

    result_file = tmp_path / "result.json"
    result_file.write_text(
        json.dumps(
            {
                "platform": "duckdb",
                "benchmark_name": "tpch",
                "scale_factor": 0.01,
                "run": {"query_subset": ["Q1", "Q2"]},
            }
        )
    )

    result = cli_runner.invoke(results, ["show-cli", str(result_file)])
    assert result.exit_code == 0
    assert "benchbox run --platform duckdb --benchmark tpch --scale 0.01 --queries Q1,Q2" in result.output


def test_show_cli_reports_missing_fields(cli_runner, tmp_path, monkeypatch):
    monkeypatch.setattr(printing, "_STD_CONSOLE", None)
    monkeypatch.setattr(printing, "_QUIET", False)

    result_file = tmp_path / "bad.json"
    result_file.write_text(json.dumps({"platform": "duckdb"}))

    result = cli_runner.invoke(results, ["show-cli", str(result_file)])
    assert result.exit_code == 0
    assert "Could not extract required fields" in result.output


def test_show_cli_reports_invalid_json(cli_runner, tmp_path, monkeypatch):
    monkeypatch.setattr(printing, "_STD_CONSOLE", None)
    monkeypatch.setattr(printing, "_QUIET", False)

    result_file = tmp_path / "invalid.json"
    result_file.write_text("{not valid json")

    result = cli_runner.invoke(results, ["show-cli", str(result_file)])
    assert result.exit_code == 0
    assert "Invalid JSON file" in result.output




