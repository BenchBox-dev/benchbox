from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

ph = importlib.import_module("benchbox.cli.commands.plan_history")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@dataclass
class _Entry:
    run_id: str
    timestamp: str
    fingerprint: str
    execution_time_ms: float
    platform: str = "duckdb"


class _HistoryNoRuns:
    def __init__(self, _history_dir: Path):
        pass

    def get_run_count(self) -> int:
        return 0


class _HistoryNoEntries:
    def __init__(self, _history_dir: Path):
        pass

    def get_run_count(self) -> int:
        return 2

    def query_plan_history(self, _query_id: str, platform: str | None = None):
        return []


class _HistoryOk:
    def __init__(self, _history_dir: Path):
        self._entries = [
            _Entry("r1", "2026-02-08T10:00:00", "a" * 32, 12.0),
            _Entry("r2", "2026-02-08T11:00:00", "b" * 32, 14.5),
        ]

    def get_run_count(self) -> int:
        return len(self._entries)

    def query_plan_history(self, _query_id: str, platform: str | None = None):
        return self._entries

    def get_plan_version_history(self, _query_id: str, platform: str | None = None):
        return [("r1", 1), ("r2", 2)]

    def count_unique_plans(self, _query_id: str, platform: str | None = None) -> int:
        return 2

    def detect_plan_flapping(self, _query_id: str, platform: str | None = None) -> bool:
        return True


class _HistoryRaises:
    def __init__(self, _history_dir: Path):
        pass

    def get_run_count(self) -> int:
        raise RuntimeError("boom")


def test_plan_history_no_history_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ph, "PlanHistory", _HistoryNoRuns)
    result = CliRunner().invoke(
        ph.plan_history,
        ["--query-id", "q1", "--history-dir", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "No history data found" in result.output


def test_plan_history_query_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ph, "PlanHistory", _HistoryNoEntries)
    result = CliRunner().invoke(
        ph.plan_history,
        ["--query-id", "q99", "--history-dir", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "No history found for query 'q99'" in result.output


def test_plan_history_success_with_flapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ph, "PlanHistory", _HistoryOk)
    result = CliRunner().invoke(
        ph.plan_history,
        ["--query-id", "q05", "--history-dir", str(tmp_path), "--check-flapping", "--limit", "1"],
    )
    assert result.exit_code == 0
    assert "Plan History for q05" in result.output
    assert "WARNING: Plan flapping detected" in result.output


def test_plan_history_handles_exception_non_verbose(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ph, "PlanHistory", _HistoryRaises)
    result = CliRunner().invoke(
        ph.plan_history,
        ["--query-id", "q1", "--history-dir", str(tmp_path)],
        obj={},
    )
    assert result.exit_code == 1
    assert "Error:" in result.output


def test_plan_history_reraises_exception_in_verbose_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ph, "PlanHistory", _HistoryRaises)
    result = CliRunner().invoke(
        ph.plan_history,
        ["--query-id", "q1", "--history-dir", str(tmp_path)],
        obj={"verbose": True},
    )
    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)


def _write_history_file(history_dir: Path, run_id: str, timestamp: str, fingerprint: str) -> None:
    (history_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "timestamp": timestamp,
                "platform": "duckdb",
                "plan_fingerprints": {
                    "1": {"fingerprint": fingerprint, "estimated_cost": None, "execution_time_ms": 100.0}
                },
            }
        ),
        encoding="utf-8",
    )


def test_plan_history_real_history_store_end_to_end(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    _write_history_file(history_dir, "run0", "2024-01-01T00:00:00", "a" * 64)
    _write_history_file(history_dir, "run1", "2024-01-02T00:00:00", "a" * 64)
    _write_history_file(history_dir, "run2", "2024-01-03T00:00:00", "b" * 64)

    result = CliRunner().invoke(ph.plan_history, ["--query-id", "1", "--history-dir", str(history_dir)])

    assert result.exit_code == 0, result.output
    assert "Plan History for 1" in result.output
    assert "run0" in result.output
    assert "run2" in result.output
    assert "Unique plans: 2" in result.output


