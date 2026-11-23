from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.cli.config import DirectoryManager
from benchbox.core.runtime_paths import resolve_results_dir
from tests.fixtures.result_dict_fixtures import make_benchmark_results

__import__("benchbox.cli.commands.run")
_run_module = sys.modules["benchbox.cli.commands.run"]

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _export(runs_root: Path):
    orchestrator = SimpleNamespace(directory_manager=DirectoryManager(str(runs_root)))
    result = make_benchmark_results(benchmark_id="tpch", scale_factor=0.01)
    exported = _run_module._export_orchestrated_result(
        orchestrator=orchestrator,
        result=result,
        benchmark="tpch",
        scale=0.01,
        platform_label="duckdb",
        mode_label="sql",
        quiet=True,
    )
    return exported


def test_run_export_honours_results_dir_env(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    results = tmp_path / "elsewhere" / "results"
    monkeypatch.setenv("BENCHBOX_RESULTS_DIR", str(results))
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)

    exported = _export(runs_root)

    paths = [Path(str(p)) for p in exported.values()]
    assert paths
    assert all(p.parent == results for p in paths)
    assert list(results.glob("*.json"))
    assert not (runs_root / "results").exists()


def test_run_export_default_location_unchanged(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    monkeypatch.delenv("BENCHBOX_RESULTS_DIR", raising=False)
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)

    exported = _export(runs_root)

    assert all(Path(str(p)).parent == runs_root / "results" for p in exported.values())


def test_directory_manager_results_dir_precedence(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    env_results = tmp_path / "env_results"
    monkeypatch.delenv("BENCHBOX_RESULTS_DIR", raising=False)
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
    assert DirectoryManager(str(runs_root)).results_dir == runs_root / "results"

    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "out"))
    assert DirectoryManager().results_dir == tmp_path / "out" / "results"

    monkeypatch.setenv("BENCHBOX_RESULTS_DIR", str(env_results))
    assert DirectoryManager().results_dir == env_results
    assert DirectoryManager(str(runs_root)).results_dir == env_results

    explicit = tmp_path / "explicit"
    assert resolve_results_dir(explicit, env={"BENCHBOX_RESULTS_DIR": str(env_results)}) == explicit
