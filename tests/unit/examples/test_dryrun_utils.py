from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.schemas import BenchmarkConfig, DryRunResult
from benchbox.examples import dryrun_utils

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeExecutor:
    def __init__(self, _destination: Path) -> None:
        self.destination = _destination

    def execute_dry_run(self, **_kwargs):
        return DryRunResult(
            benchmark_config={},
            database_config={},
            system_profile={},
            platform_config={},
            queries={},
            warnings=["Skipped benchmark 'other'", "Skipped benchmark 'tpch'"],
            query_preview={},
        )

    def save_dry_run_results(self, _result, _prefix: str):
        return {"summary": self.destination / "summary.txt"}


class _FakeProfiler:
    def get_system_profile(self):
        return {"cpu": 8}


def test_ensure_output_directory_creates_missing_path(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "out"
    created = dryrun_utils.ensure_output_directory(target)
    assert created.exists()
    assert created.is_dir()


def test_ensure_output_directory_rejects_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a directory"):
        dryrun_utils.ensure_output_directory(target)


def test_execute_example_dry_run_filters_warnings_and_uses_fallback_queries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dryrun_utils, "DryRunExecutor", _FakeExecutor)
    monkeypatch.setattr(dryrun_utils, "SystemProfiler", lambda: _FakeProfiler())
    monkeypatch.setattr(dryrun_utils, "print_dry_run_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(dryrun_utils, "_load_fallback_queries", lambda _cfg: {"1": "SELECT 1"})

    benchmark_config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01)

    result, saved = dryrun_utils.execute_example_dry_run(
        benchmark_config=benchmark_config,
        database_config=None,
        output_dir=tmp_path,
        filename_prefix="demo",
    )

    assert result.warnings == ["Skipped benchmark 'tpch'"]
    assert result.queries == {"1": "SELECT 1"}
    assert saved["summary"].name == "summary.txt"


