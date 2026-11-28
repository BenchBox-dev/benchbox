from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.cli.commands.compare import _discover_result_files_with_metadata
from benchbox.cli.commands.publish import _resolve_source
from benchbox.core.runtime_paths import resolve_results_dir, resolve_results_dir_from_env
from benchbox.core.visualization.result_plotter import ResultPlotter
from tests.fixtures.result_dict_fixtures import make_v2_result_dict

pytestmark = [pytest.mark.unit, pytest.mark.fast]

OLD_NAME = "old_default_run.json"
NEW_NAME = "new_configured_run.json"


def _write_result(directory: Path, name: str, timestamp: str, execution_id: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(make_v2_result_dict(timestamp=timestamp, execution_id=execution_id)))
    return path


@pytest.fixture
def configured_results(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
    configured = tmp_path / "configured" / "results"
    monkeypatch.setenv("BENCHBOX_RESULTS_DIR", str(configured))
    _write_result(work / "benchmark_runs" / "results", OLD_NAME, "2026-01-01T12:00:00", "old-run")
    new = _write_result(configured, NEW_NAME, "2026-02-01T12:00:00", "new-run")
    return new


def test_export_last_uses_configured_results_dir(configured_results, tmp_path):
    out = tmp_path / "exported"
    result = CliRunner().invoke(cli, ["export", "--last", "--force", "--output-dir", str(out)])

    assert NEW_NAME in result.output
    assert OLD_NAME not in result.output


def test_export_last_default_output_is_configured_results_dir(configured_results):
    result = CliRunner().invoke(cli, ["export", "--last", "--force"])

    assert result.exit_code == 0, result.output
    assert NEW_NAME in result.output
    assert any(
        p.parent == configured_results.parent and p.name != NEW_NAME for p in configured_results.parent.iterdir()
    )


def test_submit_last_uses_configured_results_dir(configured_results):
    result = CliRunner().invoke(cli, ["submit", "--last", "--dry-run"])

    assert NEW_NAME in result.output
    assert OLD_NAME not in result.output


def test_publish_last_uses_configured_results_dir(configured_results):
    assert _resolve_source(None, True, None, None) == configured_results


def test_visualize_default_source_uses_configured_results_dir(configured_results):
    from benchbox.cli.commands.visualize import _resolve_source_paths

    assert _resolve_source_paths(None, []) == [str(configured_results)]


def test_result_plotter_default_source_uses_configured_results_dir(configured_results):
    assert ResultPlotter._expand_sources(None) == [configured_results.resolve()]


def test_compare_default_search_prefers_configured_results_dir(configured_results):
    found = _discover_result_files_with_metadata()

    assert found
    assert found[0].path.name == NEW_NAME
    assert {m.path.name for m in found} >= {NEW_NAME, OLD_NAME}


def test_results_dir_precedence(tmp_path, monkeypatch):
    env = {"BENCHBOX_RESULTS_DIR": str(tmp_path / "env"), "BENCHBOX_OUTPUT_DIR": str(tmp_path / "root")}
    assert resolve_results_dir(tmp_path / "explicit", env=env) == tmp_path / "explicit"
    assert resolve_results_dir(env=env) == tmp_path / "env"
    assert resolve_results_dir(env={"BENCHBOX_OUTPUT_DIR": str(tmp_path / "root")}) == tmp_path / "root" / "results"
    monkeypatch.delenv("BENCHBOX_RESULTS_DIR", raising=False)
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
    assert resolve_results_dir_from_env() == resolve_results_dir(env={})
