from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


def _operator_timings(node: dict[str, Any]) -> list[Any]:
    timings: list[Any] = []
    physical = node.get("physical_operator")
    if physical:
        timings.append((physical.get("properties") or {}).get("timing"))
    for child in node.get("children") or []:
        timings.extend(_operator_timings(child))
    return timings


def _run_capture(monkeypatch: pytest.MonkeyPatch, output_dir: Path, *flags: str, newer_than: set[Path]) -> list[Any]:
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("BENCHBOX_MIN_FREE_DISK_PERCENT", "0")
    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--queries",
            "6",
            "--phases",
            "power",
            "--capture-plans",
            "--non-interactive",
            "--force",
            *flags,
        ],
    )
    assert result.exit_code == 0, result.output
    sidecars = set((output_dir / "results").glob("*.plans.json")) - newer_than
    assert len(sidecars) == 1
    newer_than |= sidecars
    plans = json.loads(sidecars.pop().read_text(encoding="utf-8"))
    assert plans["plans_captured"] == 1
    (entry,) = plans["queries"].values()
    timings = _operator_timings(entry["plan"]["logical_root"])
    assert timings
    return timings


def test_duckdb_capture_plans_cli_analyze_flag_controls_operator_timing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: set[Path] = set()

    omitted = _run_capture(monkeypatch, tmp_path, newer_than=seen)
    explicit_off = _run_capture(monkeypatch, tmp_path, "--no-analyze-plans", newer_than=seen)
    analyzed = _run_capture(monkeypatch, tmp_path, "--analyze-plans", newer_than=seen)

    assert all(timing is None for timing in omitted)
    assert all(timing is None for timing in explicit_off)
    assert any(timing is not None for timing in analyzed)
